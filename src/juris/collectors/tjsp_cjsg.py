"""Coletor da Consulta de Jurisprudência de Segundo Grau (CJSG) do e-SAJ/TJSP.

Fluxo (validado inspecionando o site em 31/07/2026):
1. Abre https://esaj.tjsp.jus.br/cjsg/consultaCompleta.do?f=1 em sessão de browser
   (crawl4ai/Playwright). O hook `after_goto` preenche `dados.buscaInteiroTeor`
   (+ datas de publicação) e clica em #pbSubmit — o POST vai para
   resultadoCompleta.do e o reCAPTCHA invisível da página roda naturalmente no
   browser. O servidor VALIDA esse token: POST http puro é rejeitado com
   "Esta página é protegida por reCAPTCHA", por isso o browser é obrigatório.
   Se o site apresentar um desafio interativo, a coleta é interrompida com
   BloqueioDetectadoError (não burlamos captcha).
2. Pagina via GET trocaDePagina.do?tipoDeDecisao=A&pagina=N na mesma sessão.

Estrutura de cada resultado (20 por página):
- linha `tr.fundocinza1`
- `a[cdacordao]`: código do acórdão + número do processo no texto
- `tr.ementaClass2`: pares `<strong>Label:</strong> valor`
- `div#dadosSemFormatacao_<cd> .mensagemSemFormatacao`: ementa completa sem formatação
- total só na 1ª página: "Resultados <strong>1 a 20</strong> de N"
"""

import asyncio
import logging
import math
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date

from bs4 import BeautifulSoup

from juris.collectors.base import BloqueioDetectadoError, DecisaoColetada, PaginaResultado
from juris.config import settings
from juris.pipeline.normalize import (
    dividir_classe_assunto,
    limpar_multilinha,
    limpar_texto,
    parse_data_br,
)

logger = logging.getLogger(__name__)

BASE = "https://esaj.tjsp.jus.br/cjsg"
RESULTADOS_POR_PAGINA = 20

_RE_TOTAL = re.compile(r"Resultados\s+[\d.]+\s+a\s+[\d.]+\s+de\s+([\d.]+)")
_RE_BLOQUEIO = re.compile(r"cf-turnstile|challenge-platform", re.I)


def parse_total_resultados(html: str) -> int | None:
    # O total vem com tags no meio ("Resultados <strong>1 a 20</strong> de N"),
    # então a regex roda sobre o texto extraído, não sobre o HTML.
    texto = BeautifulSoup(html, "lxml").get_text(" ")
    m = _RE_TOTAL.search(re.sub(r"\s+", " ", texto))
    return int(m.group(1).replace(".", "")) if m else None


def parse_mensagem_retorno(html: str) -> str | None:
    """Mensagem de alerta do e-SAJ (sem resultados, captcha rejeitado etc.)."""
    el = BeautifulSoup(html, "lxml").select_one("#mensagemRetorno")
    return limpar_texto(el.get_text()) if el else None


def parse_pagina(html: str, tribunal: str = "TJSP") -> list[DecisaoColetada]:
    soup = BeautifulSoup(html, "lxml")
    decisoes: list[DecisaoColetada] = []
    for row in soup.select("tr.fundocinza1"):
        link = row.select_one("a[cdacordao]")
        if link is None:
            continue
        cd_acordao = link["cdacordao"].strip()

        campos: dict[str, str] = {}
        for tr in row.select("tr.ementaClass2"):
            strong = tr.find("strong")
            if strong is None:
                continue
            label = (limpar_texto(strong.get_text()) or "").rstrip(":")
            valor = limpar_texto(tr.get_text().replace(strong.get_text(), "", 1))
            if label and label not in campos:
                campos[label] = valor or ""

        ementa_div = row.select_one(f'div[id="dadosSemFormatacao_{cd_acordao}"]') or row.select_one(
            "div.mensagemSemFormatacao"
        )
        ementa = limpar_multilinha(ementa_div.get_text()) if ementa_div else campos.get("Ementa")

        classe, assunto = dividir_classe_assunto(campos.get("Classe/Assunto"))
        decisoes.append(
            DecisaoColetada(
                tribunal=tribunal,
                cd_acordao=cd_acordao,
                numero_processo=limpar_texto(link.get_text()),
                classe=classe,
                assunto=assunto,
                relator=limpar_texto(campos.get("Relator(a)")),
                comarca=limpar_texto(campos.get("Comarca")),
                orgao_julgador=limpar_texto(campos.get("Órgão julgador")),
                data_julgamento=parse_data_br(campos.get("Data do julgamento")),
                data_publicacao=parse_data_br(campos.get("Data de publicação")),
                ementa_texto=ementa,
                ementa_markdown=ementa,
                raw_html=str(row),
            )
        )
    return decisoes


def _checar_bloqueio(html: str) -> None:
    if _RE_BLOQUEIO.search(html):
        raise BloqueioDetectadoError(
            "O e-SAJ apresentou um desafio anti-robô; coleta interrompida. "
            "Tente mais tarde ou reduza a frequência."
        )


def _data_br(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


class TjspCjsgCollector:
    tribunal = "TJSP"

    def __init__(self, intervalo_segundos: float | None = None):
        self.intervalo = (
            settings.req_intervalo_segundos if intervalo_segundos is None else intervalo_segundos
        )
        self._busca: str = ""
        self._campo: str = "inteiro_teor"
        self._publicacao_inicio: date | None = None
        self._publicacao_fim: date | None = None
        self._form_pendente = False

    async def _after_goto(self, page, context=None, url=None, response=None, **kwargs):
        """Hook do crawl4ai: ao carregar o formulário, preenche e submete a
        busca com Playwright puro, esperando a navegação de resultado."""
        if not (self._form_pendente and url and "consultaCompleta.do" in url):
            return page

        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        self._form_pendente = False
        await page.wait_for_selector("#pbSubmit", timeout=30_000)
        # O submit depende do reCAPTCHA invisível da própria página; espera o
        # script terminar de carregar antes de clicar, senão o servidor rejeita
        # com "problema de validação".
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeoutError:
            pass
        try:
            await page.wait_for_function(
                "typeof grecaptcha !== 'undefined' && typeof grecaptcha.execute === 'function'",
                timeout=15_000,
            )
        except PlaywrightTimeoutError:
            logger.warning("CJSG: grecaptcha não inicializou em 15s; tentando mesmo assim")
        await page.wait_for_timeout(1_500)

        campo_id = (
            "iddados.buscaEmenta" if self._campo == "ementa" else "iddados.buscaInteiroTeor"
        )
        await page.locator(f'[id="{campo_id}"]').fill(self._busca)
        if self._publicacao_inicio:
            await page.locator('[id="iddados.dtPublicacaoInicio"]').fill(
                _data_br(self._publicacao_inicio)
            )
        if self._publicacao_fim:
            await page.locator('[id="iddados.dtPublicacaoFim"]').fill(
                _data_br(self._publicacao_fim)
            )
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
            await page.click("#pbSubmit")

        try:
            await page.wait_for_selector("tr.fundocinza1, #mensagemRetorno", timeout=30_000)
        except PlaywrightTimeoutError:
            logger.warning("CJSG: resultado não apareceu em 30s; seguindo com o DOM atual")
        return page

    async def _arun_com_retry(self, crawler, url: str, config, tentativas: int = 3):
        """Repete carregamentos que falharem por erro transitório de rede
        (ex.: net::ERR_ABORTED no meio de uma paginação longa)."""
        result = None
        for tentativa in range(1, tentativas + 1):
            result = await crawler.arun(url, config=config)
            if result.success:
                return result
            if tentativa < tentativas:
                logger.warning(
                    "Falha ao carregar %s (tentativa %s/%s): %s",
                    url, tentativa, tentativas, result.error_message,
                )
                await asyncio.sleep(5 * tentativa)
        return result

    async def paginas(
        self,
        busca: str,
        publicacao_inicio: date | None = None,
        publicacao_fim: date | None = None,
        max_paginas: int = 50,
        campo: str = "inteiro_teor",
    ) -> AsyncIterator[PaginaResultado]:
        # Import adiado: o crawl4ai (Playwright) é pesado e desnecessário
        # para quem só usa o parser (testes, API).
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

        self._busca = busca
        self._campo = campo
        self._publicacao_inicio = publicacao_inicio
        self._publicacao_fim = publicacao_fim
        self._form_pendente = True
        session_id = f"cjsg-{uuid.uuid4().hex[:8]}"

        browser_config = BrowserConfig(headless=True, verbose=False)
        async with AsyncWebCrawler(config=browser_config) as crawler:
            crawler.crawler_strategy.set_hook("after_goto", self._after_goto)
            run_config = CrawlerRunConfig(
                session_id=session_id, cache_mode=CacheMode.BYPASS, page_timeout=120_000
            )

            # A validação do reCAPTCHA invisível falha esporadicamente logo
            # após o carregamento ("Aguarde alguns segundos e tente novamente");
            # nesses casos repetimos a busca poucas vezes, com pausa.
            for tentativa in range(1, 4):
                self._form_pendente = True
                result = await crawler.arun(f"{BASE}/consultaCompleta.do?f=1", config=run_config)
                if not result.success:
                    if tentativa == 3:
                        raise RuntimeError(f"Falha ao consultar CJSG: {result.error_message}")
                    logger.warning(
                        "CJSG %r: falha ao carregar o formulário (tentativa %s): %s",
                        busca, tentativa, result.error_message,
                    )
                    await asyncio.sleep(10 * tentativa)
                    continue
                _checar_bloqueio(result.html)

                decisoes = parse_pagina(result.html, self.tribunal)
                total = parse_total_resultados(result.html)
                if decisoes:
                    break
                mensagem = parse_mensagem_retorno(result.html)
                if mensagem and "recaptcha" in mensagem.lower():
                    if tentativa == 3:
                        raise BloqueioDetectadoError(f"e-SAJ rejeitou a busca: {mensagem}")
                    logger.warning(
                        "CJSG %r: validação do reCAPTCHA falhou (tentativa %s); aguardando",
                        busca, tentativa,
                    )
                    await asyncio.sleep(10 * tentativa)
                    continue
                logger.info("CJSG %r: sem resultados (%s)", busca, mensagem or "sem mensagem")
                yield PaginaResultado(pagina=1, total_resultados=total or 0, decisoes=[])
                return

            logger.info("CJSG %r: %s resultados no total", busca, total)
            yield PaginaResultado(pagina=1, total_resultados=total, decisoes=decisoes)
            if total is None:
                return

            ultima_pagina = min(math.ceil(total / RESULTADOS_POR_PAGINA), max_paginas)
            for pagina in range(2, ultima_pagina + 1):
                await asyncio.sleep(self.intervalo)
                result = await self._arun_com_retry(
                    crawler,
                    f"{BASE}/trocaDePagina.do?tipoDeDecisao=A&pagina={pagina}",
                    run_config,
                )
                if not result.success:
                    raise RuntimeError(
                        f"Falha na página {pagina} de {busca!r}: {result.error_message}"
                    )
                _checar_bloqueio(result.html)
                decisoes = parse_pagina(result.html, self.tribunal)
                if not decisoes:
                    logger.warning("CJSG %r: página %s veio vazia; encerrando", busca, pagina)
                    return
                yield PaginaResultado(pagina=pagina, total_resultados=total, decisoes=decisoes)
