from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from juris.watchdog import coleta_parada, verificar


@pytest.fixture
def alertas_capturados(monkeypatch):
    enviados: list[tuple[str, str]] = []

    async def _alertar(session, chave, mensagem):
        enviados.append((chave, mensagem))
        return True

    monkeypatch.setattr("juris.watchdog.alertar", _alertar)
    return enviados


async def _registrar_execucao(session, horas_atras: int) -> None:
    await session.execute(
        text(
            "INSERT INTO execucoes (tribunal, tema_slug, ultima_execucao, ultima_qtd) "
            "VALUES ('TJSP','digital-lgpd', now() - make_interval(hours => :h), 10)"
        ),
        {"h": horas_atras},
    )


async def test_coleta_recente_nao_e_considerada_parada(session):
    await _registrar_execucao(session, horas_atras=2)
    assert await coleta_parada(session) is None


async def test_coleta_envelhecida_e_detectada(session):
    await _registrar_execucao(session, horas_atras=40)
    atraso = await coleta_parada(session)
    assert atraso is not None and atraso > timedelta(hours=36)


async def test_banco_sem_execucao_alguma_nao_alerta(session):
    """Instalação nova ainda não coletou; alertar aqui seria ruído."""
    assert await coleta_parada(session) is None


async def test_verificar_alerta_quando_parada(session, alertas_capturados):
    await _registrar_execucao(session, horas_atras=40)
    await verificar(session)
    assert len(alertas_capturados) == 1
    assert alertas_capturados[0][0] == "coleta-parada"


async def test_verificar_nao_alerta_quando_saudavel(session, alertas_capturados):
    await _registrar_execucao(session, horas_atras=1)
    await verificar(session)
    assert alertas_capturados == []
