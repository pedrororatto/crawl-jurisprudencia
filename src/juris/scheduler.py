"""Coleta incremental por tema.

Uso:
  python -m juris.scheduler --once                  # uma rodada completa agora
  python -m juris.scheduler --once --tema digital-lgpd  # só um tema
  python -m juris.scheduler --once --max-paginas 3  # limitar páginas (teste)
  python -m juris.scheduler --once --desde 2026-01-01
  python -m juris.scheduler                         # daemon: roda todo dia às 03:00

Job por período (backfill de um mês, percorre TODAS as páginas retornadas):
  python -m juris.scheduler --once --desde 2026-06-01 --ate 2026-06-30
  python -m juris.scheduler --once --tema digital-lgpd --desde 2026-06-01 --ate 2026-06-30

Backfill histórico automático (mês a mês, do mês atual até backfill_inicio):
  python -m juris.scheduler --backfill-passo   # um mês por execução, e sai
  python -m juris.scheduler                    # daemon: incremental diário às
                                               # 03:00 + um passo do backfill a
                                               # cada 10 minutos
"""

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from juris.alertas import alertar
from juris.collectors.base import BloqueioDetectadoError
from juris.collectors.tjsp_cjsg import TjspCjsgCollector
from juris.config import settings
from juris.db.engine import SessionLocal
from juris.db.models import BackfillProgresso, Execucao, MetricaColeta
from juris.lock import adquirir_trava_unica
from juris.logging_config import configurar_logging
from juris.pipeline.ingest import ingerir
from juris.temas import Tema, carregar_temas

logger = logging.getLogger(__name__)


async def _obter_inicio(tribunal: str, tema_slug: str, desde: date | None) -> date:
    if desde:
        return desde
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Execucao).where(
                Execucao.tribunal == tribunal, Execucao.tema_slug == tema_slug
            )
        )
    if row and row.watermark:
        return row.watermark - timedelta(days=settings.watermark_margem_dias)
    return settings.backfill_inicio


async def _salvar_watermark(
    tribunal: str, tema_slug: str, watermark: date | None, qtd: int
) -> None:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Execucao).where(
                Execucao.tribunal == tribunal, Execucao.tema_slug == tema_slug
            )
        )
        if row is None:
            row = Execucao(tribunal=tribunal, tema_slug=tema_slug)
            session.add(row)
        if watermark and (row.watermark is None or watermark > row.watermark):
            row.watermark = watermark
        row.ultima_execucao = datetime.now(timezone.utc)
        row.ultima_qtd = qtd
        await session.commit()


# Teto efetivamente "sem limite" para o job por período: o coletor já para na
# última página real (ceil(total / 20)).
TODAS_AS_PAGINAS = 1_000_000


@dataclass
class ResultadoTema:
    """O que uma coleta de tema rendeu. `resultados` é o total devolvido pela
    busca (novas + já conhecidas) — é ele, não `novas`, que distingue uma
    rodada tranquila de um parser quebrado."""

    tema_slug: str
    novas: int
    atualizadas: int
    paginas: int
    resultados: int
    falhou: bool
    erro: str | None
    # Nome da classe da exceção, guardado à parte de `erro`. Os gatilhos
    # comparam por igualdade contra `BloqueioDetectadoError.__name__`; casar
    # substring dentro da mensagem achatada faria um rename da classe
    # rebaixar em silêncio o alerta de maior prioridade do projeto.
    tipo_erro: str | None
    duracao_s: float


async def coletar_tema(
    tema: Tema,
    max_paginas: int | None = None,
    desde: date | None = None,
    ate: date | None = None,
) -> ResultadoTema:
    collector = TjspCjsgCollector()
    inicio_dt = await _obter_inicio(collector.tribunal, tema.slug, desde)
    fim = ate or date.today()
    if max_paginas is None:
        # Com período explícito (--desde/--ate) percorre tudo; na rodada
        # incremental vale o teto de configuração.
        max_paginas = TODAS_AS_PAGINAS if ate else settings.max_paginas_por_rodada
    logger.info(
        "Coletando tema %r (%s a %s, até %s páginas): %s",
        tema.slug, inicio_dt, fim, max_paginas, tema.busca,
    )

    inicio = time.monotonic()
    total = novas_total = atualizadas_total = paginas_total = 0
    maior_publicacao: date | None = None
    async for pagina in collector.paginas(tema.busca, inicio_dt, fim, max_paginas, tema.campo):
        async with SessionLocal() as session:
            novas, atualizadas = await ingerir(session, pagina.decisoes, tema.slug)
            await session.commit()
        novas_total += novas
        atualizadas_total += atualizadas
        paginas_total += 1
        total += len(pagina.decisoes)
        datas = [d.data_publicacao for d in pagina.decisoes if d.data_publicacao]
        if datas:
            maior = max(datas)
            maior_publicacao = max(maior_publicacao, maior) if maior_publicacao else maior
        logger.info(
            "Tema %r página %s/%s: %s novas, %s atualizadas",
            tema.slug, pagina.pagina, pagina.total_resultados, novas, atualizadas,
        )

    await _salvar_watermark(collector.tribunal, tema.slug, maior_publicacao, total)
    return ResultadoTema(
        tema_slug=tema.slug,
        novas=novas_total,
        atualizadas=atualizadas_total,
        paginas=paginas_total,
        resultados=total,
        falhou=False,
        erro=None,
        tipo_erro=None,
        duracao_s=time.monotonic() - inicio,
    )


async def _falhas_seguidas(session, tribunal: str, tema_slug: str, quantas: int) -> bool:
    """True se as últimas `quantas` rodadas incrementais deste tema falharam,
    todas elas.

    O filtro por `incremental` não é cosmético: com o backfill ativo (um passo
    a cada 10 minutos) entram ~144 linhas de período por tema por dia contra
    UMA da incremental. Sem o filtro, três falhas incrementais seguidas nunca
    ocupariam a janela — centenas de linhas de backfill bem-sucedidas ficam
    entre elas — e o gatilho ficaria inalcançável para a coleta que ele existe
    para vigiar.
    """
    linhas = (
        await session.execute(
            select(MetricaColeta.falhou)
            .where(
                MetricaColeta.tribunal == tribunal,
                MetricaColeta.tema_slug == tema_slug,
                MetricaColeta.tipo_rodada == "incremental",
            )
            .order_by(MetricaColeta.rodada_em.desc())
            .limit(quantas)
        )
    ).scalars().all()
    return len(linhas) == quantas and all(linhas)


async def avaliar_gatilhos(
    session, tribunal: str, resultados: list[ResultadoTema], tipo_rodada: str
) -> None:
    """Gatilhos 1 a 3 do alerta. O gatilho 4 vive no juris.watchdog, fora
    deste processo: se ele morrer, não avisa que morreu.

    `tipo_rodada` é obrigatório de propósito: os gatilhos 2 e 3 falam sobre a
    rodada incremental completa, e um chamador que o omitisse cairia
    justamente no defeito que o parâmetro corrige.
    """
    if not resultados:
        return

    for r in resultados:
        if r.falhou and r.tipo_erro == BloqueioDetectadoError.__name__:
            await alertar(
                session,
                f"bloqueio:{tribunal}:{r.tema_slug}",
                f"[{tribunal}] Bloqueio anti-robô no tema {r.tema_slug}. "
                f"Coleta interrompida. Detalhe: {r.erro}",
            )
        elif r.falhou and await _falhas_seguidas(session, tribunal, r.tema_slug, 3):
            await alertar(
                session,
                f"falhas-seguidas:{tribunal}:{r.tema_slug}",
                f"[{tribunal}] Tema {r.tema_slug} falhou em 3 rodadas seguidas. "
                f"Último erro: {r.erro}",
            )

    # A spec define o gatilho 3 como "rodada INTEIRA em que nenhum tema
    # retornou resultado algum". Uma rodada de tema único ou de período não é
    # uma rodada inteira: avaliá-la aqui transformaria o teste de fumaça do
    # runbook num alerta de "parser quebrado" e ainda queimaria por seis horas
    # o cooldown compartilhado da chave, mascarando uma rodada de verdade.
    if tipo_rodada == "incremental" and all(r.resultados == 0 for r in resultados):
        await alertar(
            session,
            f"rodada-vazia:{tribunal}",
            f"[{tribunal}] Nenhum dos {len(resultados)} temas devolveu resultado "
            "algum nesta rodada. Suspeita de parser quebrado ou layout alterado.",
        )


def _tipo_de_rodada(tema_slug: str | None, ate: date | None) -> str:
    """Classifica a rodada a partir dos próprios argumentos: filtrou tema, é
    `tema`; delimitou fim de período (inclui o backfill), é `periodo`; senão é
    a incremental diária. Função separada porque dois gatilhos de alerta
    dependem da classificação e ela precisa ser testável sem subir uma
    coleta."""
    if tema_slug:
        return "tema"
    if ate:
        return "periodo"
    return "incremental"


async def rodada(
    tema_slug: str | None = None,
    max_paginas: int | None = None,
    desde: date | None = None,
    ate: date | None = None,
) -> list[ResultadoTema]:
    """Executa a coleta dos temas, grava as métricas e avalia os gatilhos de
    alerta. Retorna o resultado de cada tema."""
    temas = carregar_temas(settings.temas_path)
    if tema_slug:
        temas = [t for t in temas if t.slug == tema_slug]
        if not temas:
            raise SystemExit(f"Tema {tema_slug!r} não existe em {settings.temas_path}")

    tipo_rodada = _tipo_de_rodada(tema_slug, ate)
    tribunal = TjspCjsgCollector.tribunal
    resultados: list[ResultadoTema] = []
    for tema in temas:
        inicio = time.monotonic()
        try:
            resultado = await coletar_tema(tema, max_paginas, desde, ate)
            logger.info("Tema %r: %s decisões processadas", tema.slug, resultado.resultados)
        except Exception as exc:
            logger.exception("Falha ao coletar tema %r; seguindo para o próximo", tema.slug)
            resultado = ResultadoTema(
                tema_slug=tema.slug, novas=0, atualizadas=0, paginas=0, resultados=0,
                falhou=True, erro=f"{type(exc).__name__}: {exc}",
                tipo_erro=type(exc).__name__,
                duracao_s=time.monotonic() - inicio,
            )
        resultados.append(resultado)

    async with SessionLocal() as session:
        session.add_all(
            MetricaColeta(
                tribunal=tribunal, tema_slug=r.tema_slug, tipo_rodada=tipo_rodada,
                novas=r.novas, atualizadas=r.atualizadas, paginas=r.paginas,
                resultados=r.resultados, falhou=r.falhou, erro=r.erro,
                duracao_s=r.duracao_s,
            )
            for r in resultados
        )
        await session.commit()
        await avaliar_gatilhos(session, tribunal, resultados, tipo_rodada)

    return resultados


def _mes_anterior(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def _fim_do_mes(d: date) -> date:
    proximo = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return proximo - timedelta(days=1)


async def backfill_passo(tribunal: str = "TJSP") -> None:
    """Um passo do backfill histórico: coleta UM mês (todos os temas, todas as
    páginas) e retrocede o ponteiro. O daemon chama isto a cada 10 minutos até
    chegar em settings.backfill_inicio (jan/2020 por padrão)."""
    hoje = date.today()
    async with SessionLocal() as session:
        row = await session.scalar(
            select(BackfillProgresso).where(BackfillProgresso.tribunal == tribunal)
        )
        if row is None:
            row = BackfillProgresso(
                tribunal=tribunal, proximo_mes=date(hoje.year, hoje.month, 1)
            )
            session.add(row)
            await session.commit()
        proximo_mes = row.proximo_mes

    if proximo_mes < settings.backfill_inicio.replace(day=1):
        logger.info(
            "Backfill %s concluído (chegou a %s); nada a fazer",
            tribunal, settings.backfill_inicio,
        )
        return

    inicio, fim = proximo_mes, _fim_do_mes(proximo_mes)
    logger.info("Backfill %s: coletando %s a %s", tribunal, inicio, fim)
    resultados = await rodada(desde=inicio, ate=fim)
    falhas = sum(1 for r in resultados if r.falhou)
    if falhas:
        # Não avança o ponteiro: o próximo passo tenta o mesmo mês de novo.
        logger.error(
            "Backfill %s: %s tema(s) falharam no mês %s; será refeito no próximo passo",
            tribunal, falhas, proximo_mes,
        )
        return

    async with SessionLocal() as session:
        row = await session.scalar(
            select(BackfillProgresso).where(BackfillProgresso.tribunal == tribunal)
        )
        row.proximo_mes = _mes_anterior(proximo_mes)
        row.atualizado_em = datetime.now(timezone.utc)
        await session.commit()
    logger.info("Backfill %s: mês %s concluído; próximo: %s", tribunal, inicio, row.proximo_mes)


def _daemon() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    async def _run() -> None:
        # Na subida, roda a incremental uma vez ANTES de agendar o resto, para
        # não abrir duas coletas paralelas contra o e-SAJ no boot.
        logger.info("Rodada incremental inicial...")
        await rodada()

        # O AsyncIOScheduler precisa ser iniciado com o event loop já rodando.
        scheduler = AsyncIOScheduler()
        scheduler.add_job(rodada, "cron", hour=3, minute=0)
        # Backfill histórico: um mês por passo, a cada 10 min, até
        # backfill_inicio. max_instances=1 garante que um passo lento não
        # acumule execuções; next_run_time dispara o primeiro passo já.
        scheduler.add_job(
            backfill_passo,
            "interval",
            minutes=10,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        scheduler.start()
        logger.info(
            "Scheduler ativo: coleta incremental diária às 03:00 + backfill "
            "mensal a cada 10 min (primeiro passo já disparado)"
        )
        await asyncio.Event().wait()

    asyncio.run(_run())


def main() -> None:
    configurar_logging()
    # Trava primeiro, antes de qualquer outra coisa: nenhuma invocação do
    # scheduler (daemon, --once ou --backfill-passo) pode rodar em paralelo
    # com outra — foi rodar isso em duplicidade que causou o bloqueio no e-SAJ.
    adquirir_trava_unica()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="executa uma rodada e sai")
    parser.add_argument(
        "--backfill-passo",
        action="store_true",
        help="executa UM passo do backfill histórico (um mês) e sai",
    )
    parser.add_argument("--tema", help="slug de um único tema do temas.yaml")
    parser.add_argument("--max-paginas", type=int, help="limite de páginas por tema")
    parser.add_argument(
        "--desde", type=date.fromisoformat, help="ignora watermark e coleta desde esta data (ISO)"
    )
    parser.add_argument(
        "--ate",
        type=date.fromisoformat,
        help="fim do período (ISO); exige --desde e limita o intervalo a 1 mês, "
        "mas percorre todas as páginas retornadas",
    )
    args = parser.parse_args()

    if args.ate:
        if not args.desde:
            parser.error("--ate exige --desde")
        if args.ate < args.desde:
            parser.error("--ate não pode ser anterior a --desde")
        if (args.ate - args.desde).days > 31:
            parser.error("o período entre --desde e --ate é limitado a 1 mês (31 dias)")

    if args.backfill_passo:
        asyncio.run(backfill_passo())
    elif args.once:
        asyncio.run(rodada(args.tema, args.max_paginas, args.desde, args.ate))
    else:
        _daemon()


if __name__ == "__main__":
    main()
