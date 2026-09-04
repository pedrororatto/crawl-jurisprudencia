from datetime import timedelta

import pytest
from sqlalchemy import text

from juris import alertas
from juris.alertas import alertar


@pytest.fixture
def telegram_falso(monkeypatch):
    """Captura o que teria sido enviado, sem tocar a rede."""
    enviadas: list[str] = []

    async def _enviar(mensagem: str) -> None:
        enviadas.append(mensagem)

    monkeypatch.setattr(alertas, "_enviar_telegram", _enviar)
    return enviadas


async def test_primeiro_alerta_envia(session, telegram_falso):
    assert await alertar(session, "bloqueio:TJSP", "e-SAJ bloqueou") is True
    assert telegram_falso == ["e-SAJ bloqueou"]


async def test_segundo_alerta_na_janela_e_suprimido(session, telegram_falso):
    await alertar(session, "bloqueio:TJSP", "primeira")
    assert await alertar(session, "bloqueio:TJSP", "segunda") is False
    assert telegram_falso == ["primeira"]


async def test_chaves_diferentes_nao_se_bloqueiam(session, telegram_falso):
    await alertar(session, "bloqueio:TJSP", "uma")
    assert await alertar(session, "bloqueio:TJCE", "outra") is True
    assert len(telegram_falso) == 2


async def test_alerta_volta_a_enviar_depois_do_cooldown(session, telegram_falso):
    await alertar(session, "bloqueio:TJSP", "primeira")
    await session.execute(
        text(
            "UPDATE alertas_enviados SET enviado_em = now() - interval '7 hours' "
            "WHERE chave = 'bloqueio:TJSP'"
        )
    )
    assert await alertar(session, "bloqueio:TJSP", "de novo") is True


async def test_falha_de_entrega_nao_propaga(session, monkeypatch):
    """Telegram fora do ar não pode derrubar a coleta."""

    async def _explode(mensagem: str) -> None:
        raise RuntimeError("timeout")

    monkeypatch.setattr(alertas, "_enviar_telegram", _explode)
    assert await alertar(session, "bloqueio:TJSP", "msg") is False


async def test_sem_token_configurado_nao_quebra(session, monkeypatch):
    monkeypatch.setattr(alertas.settings, "telegram_bot_token", None)
    assert await alertar(session, "bloqueio:TJSP", "msg") is False
