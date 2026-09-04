import pytest
from sqlalchemy import text

from juris import alertas
from juris.scheduler import ResultadoTema, avaliar_gatilhos


@pytest.fixture
def alertas_capturados(monkeypatch):
    enviados: list[tuple[str, str]] = []

    async def _alertar(session, chave, mensagem):
        enviados.append((chave, mensagem))
        return True

    monkeypatch.setattr("juris.scheduler.alertar", _alertar)
    return enviados


def _ok(slug: str, resultados: int = 50) -> ResultadoTema:
    return ResultadoTema(
        tema_slug=slug, novas=0, atualizadas=resultados, paginas=3,
        resultados=resultados, falhou=False, erro=None, duracao_s=12.0,
    )


def _vazio(slug: str) -> ResultadoTema:
    return ResultadoTema(
        tema_slug=slug, novas=0, atualizadas=0, paginas=1,
        resultados=0, falhou=False, erro=None, duracao_s=4.0,
    )


async def test_rodada_sem_decisoes_novas_nao_alerta(session, alertas_capturados):
    """O falso positivo que mataria o canal.

    Numa rodada incremental saudável `novas` é zero o tempo todo: a janela vai
    de watermark-2d até hoje e revê decisões já conhecidas. O gatilho mede
    resultados retornados, não decisões novas.
    """
    await avaliar_gatilhos(session, "TJSP", [_ok("digital-lgpd"), _ok("digital-marco-civil")])
    assert alertas_capturados == []


async def test_rodada_sem_resultado_algum_alerta(session, alertas_capturados):
    await avaliar_gatilhos(
        session, "TJSP", [_vazio("digital-lgpd"), _vazio("digital-marco-civil")]
    )
    assert len(alertas_capturados) == 1
    assert alertas_capturados[0][0] == "rodada-vazia:TJSP"


async def test_bloqueio_alerta_de_imediato(session, alertas_capturados):
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="BloqueioDetectadoError: e-SAJ apresentou desafio",
        duracao_s=3.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho, _ok("digital-marco-civil")])
    chaves = [c for c, _ in alertas_capturados]
    assert "bloqueio:TJSP:digital-lgpd" in chaves


async def test_tres_rodadas_falhas_seguidas_alertam(session, alertas_capturados):
    # rodada() grava a métrica da rodada atual e só então chama
    # avaliar_gatilhos (veja o docstring de alertar() e a ordem em
    # scheduler.rodada) — na hora em que o gatilho roda, a própria rodada
    # atual já está registrada em metricas_coleta. Por isso são 3 linhas
    # aqui (as 2 anteriores + a atual), não 2: são elas que _falhas_seguidas
    # efetivamente encontra ao consultar as últimas 3 rodadas registradas.
    for _ in range(3):
        await session.execute(
            text(
                "INSERT INTO metricas_coleta "
                "(tribunal, tema_slug, novas, atualizadas, paginas, resultados, falhou) "
                "VALUES ('TJSP','digital-lgpd',0,0,0,0,true)"
            )
        )
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="timeout", duracao_s=1.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho])
    chaves = [c for c, _ in alertas_capturados]
    assert "falhas-seguidas:TJSP:digital-lgpd" in chaves
