"""O token do bot do Telegram não pode chegar ao log.

A API do Telegram carrega o token no path da URL e o httpx registra toda
requisição bem-sucedida em INFO com a URL completa. O destino desse stdout
está definido no compose de produção (driver json-file) e no cron do runbook
(`logs/watchdog.log`), então um vazamento aqui é um segredo em texto claro
em disco, alcançável por quem tem os logs mas não tem o `.env`.
"""

import logging

import httpx
import pytest

from juris.logging_config import configurar_logging

TOKEN = "123456789:AAFalso_TOKEN_para_teste-xyz"


@pytest.fixture
def log_capturado():
    """Captura o que os handlers da raiz realmente escreveriam.

    `caplog` não serve aqui: ele instala um handler próprio e o que está sob
    teste é justamente o filtro pendurado nos handlers que
    `configurar_logging` cria.
    """
    configurar_logging()
    linhas: list[str] = []

    class _Coletor(logging.Handler):
        def emit(self, record):
            linhas.append(self.format(record))

    coletor = _Coletor()
    coletor.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    for f in logging.getLogger().handlers[0].filters:
        coletor.addFilter(f)
    logging.getLogger().addHandler(coletor)
    yield linhas
    logging.getLogger().removeHandler(coletor)


def test_httpx_nao_registra_a_url_da_requisicao(log_capturado):
    """O caminho de sucesso: httpx em INFO gravaria a URL inteira."""
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING

    try:
        httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", timeout=5)
    except httpx.HTTPError:
        pass  # sem rede no CI; o que importa é o que foi (não) logado

    assert not any(TOKEN in linha for linha in log_capturado)


def test_token_e_redigido_venha_de_onde_vier(log_capturado):
    """A rede embaixo: mesmo que alguém logue a URL daqui, ou uma dependência
    futura a registre acima de INFO, o token não passa."""
    logging.getLogger("juris.teste").error(
        "falha ao chamar https://api.telegram.org/bot%s/sendMessage", TOKEN
    )
    assert log_capturado
    assert not any(TOKEN in linha for linha in log_capturado)
    assert any("bot<REDIGIDO>" in linha for linha in log_capturado)


def test_o_resto_da_mensagem_sobrevive(log_capturado):
    """Redigir não pode comer o diagnóstico em volta."""
    logging.getLogger("juris.teste").warning(
        "erro %s ao falar com bot%s/sendMessage", "ConnectTimeout", TOKEN
    )
    assert any(
        "ConnectTimeout" in linha and "/sendMessage" in linha for linha in log_capturado
    )


def test_mensagem_sem_token_passa_intacta(log_capturado):
    logging.getLogger("juris.teste").info("Coletando tema %r", "digital-lgpd")
    assert any("Coletando tema 'digital-lgpd'" in linha for linha in log_capturado)
