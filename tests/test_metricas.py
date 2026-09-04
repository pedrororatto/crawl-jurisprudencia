import pytest
from sqlalchemy import text

from juris.collectors.base import BloqueioDetectadoError
from juris.scheduler import ResultadoTema, _tipo_de_rodada, avaliar_gatilhos


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
        resultados=resultados, falhou=False, erro=None, tipo_erro=None,
        duracao_s=12.0,
    )


def _vazio(slug: str) -> ResultadoTema:
    return ResultadoTema(
        tema_slug=slug, novas=0, atualizadas=0, paginas=1,
        resultados=0, falhou=False, erro=None, tipo_erro=None, duracao_s=4.0,
    )


async def _metrica(session, *, falhou: bool, tipo_rodada: str) -> None:
    await session.execute(
        text(
            "INSERT INTO metricas_coleta "
            "(tribunal, tema_slug, tipo_rodada, novas, atualizadas, paginas, "
            " resultados, falhou) "
            "VALUES ('TJSP','digital-lgpd',:tipo,0,0,0,0,:falhou)"
        ),
        {"tipo": tipo_rodada, "falhou": falhou},
    )


async def test_rodada_sem_decisoes_novas_nao_alerta(session, alertas_capturados):
    """O falso positivo que mataria o canal.

    Numa rodada incremental saudável `novas` é zero o tempo todo: a janela vai
    de watermark-2d até hoje e revê decisões já conhecidas. O gatilho mede
    resultados retornados, não decisões novas.
    """
    await avaliar_gatilhos(
        session, "TJSP", [_ok("digital-lgpd"), _ok("digital-marco-civil")], "incremental"
    )
    assert alertas_capturados == []


async def test_rodada_sem_resultado_algum_alerta(session, alertas_capturados):
    await avaliar_gatilhos(
        session, "TJSP", [_vazio("digital-lgpd"), _vazio("digital-marco-civil")],
        "incremental",
    )
    assert len(alertas_capturados) == 1
    assert alertas_capturados[0][0] == "rodada-vazia:TJSP"


@pytest.mark.parametrize("tipo_rodada", ["tema", "periodo"])
async def test_rodada_parcial_vazia_nao_alerta(session, alertas_capturados, tipo_rodada):
    """O teste de fumaça do runbook (`--once --tema digital-lgpd
    --max-paginas 2`) não pode virar um alerta de "parser quebrado" — nem
    queimar por seis horas o cooldown compartilhado de `rodada-vazia:TJSP`,
    mascarando uma rodada incremental genuinamente vazia no intervalo. Só a
    rodada inteira responde por "nenhum tema devolveu nada".
    """
    await avaliar_gatilhos(session, "TJSP", [_vazio("digital-lgpd")], tipo_rodada)
    assert alertas_capturados == []


async def test_bloqueio_alerta_de_imediato(session, alertas_capturados):
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="BloqueioDetectadoError: e-SAJ apresentou desafio",
        tipo_erro=BloqueioDetectadoError.__name__, duracao_s=3.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho, _ok("digital-marco-civil")], "incremental")
    chaves = [c for c, _ in alertas_capturados]
    assert "bloqueio:TJSP:digital-lgpd" in chaves


async def test_bloqueio_nao_depende_da_mensagem_de_erro(session, alertas_capturados):
    """O gatilho de maior prioridade do projeto não pode depender de casar
    substring na mensagem achatada: renomear a classe rebaixaria em silêncio
    o alerta "imediato, sempre" para o caminho de três rodadas seguidas. Aqui
    a mensagem não contém o nome da classe em lugar nenhum.
    """
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="e-SAJ apresentou desafio anti-robô",
        tipo_erro=BloqueioDetectadoError.__name__, duracao_s=3.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho, _ok("digital-marco-civil")], "incremental")
    assert "bloqueio:TJSP:digital-lgpd" in [c for c, _ in alertas_capturados]


async def test_erro_comum_nao_vira_alerta_de_bloqueio(session, alertas_capturados):
    """E o contrário: um erro qualquer não pode ser confundido com bloqueio só
    porque a mensagem menciona o termo."""
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="TimeoutError: BloqueioDetectadoError não ocorreu",
        tipo_erro="TimeoutError", duracao_s=3.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho, _ok("digital-marco-civil")], "incremental")
    assert "bloqueio:TJSP:digital-lgpd" not in [c for c, _ in alertas_capturados]


async def test_tres_rodadas_falhas_seguidas_alertam(session, alertas_capturados):
    # rodada() grava a métrica da rodada atual e só então chama
    # avaliar_gatilhos (veja o docstring de alertar() e a ordem em
    # scheduler.rodada) — na hora em que o gatilho roda, a própria rodada
    # atual já está registrada em metricas_coleta. Por isso são 3 linhas
    # aqui (as 2 anteriores + a atual), não 2: são elas que _falhas_seguidas
    # efetivamente encontra ao consultar as últimas 3 rodadas registradas.
    for _ in range(3):
        await _metrica(session, falhou=True, tipo_rodada="incremental")
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="timeout", tipo_erro="TimeoutError", duracao_s=1.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho], "incremental")
    chaves = [c for c, _ in alertas_capturados]
    assert "falhas-seguidas:TJSP:digital-lgpd" in chaves


async def test_falhas_seguidas_ignora_backfill_intercalado(session, alertas_capturados):
    """Com o backfill ativo entram ~144 linhas de período por tema por dia
    contra UMA da incremental. Sem filtrar por tipo, três falhas incrementais
    seguidas nunca ocupariam a janela das últimas 3 linhas — e o gatilho 2
    ficaria inalcançável justamente para a coleta que ele existe para vigiar.
    """
    for _ in range(3):
        await _metrica(session, falhou=True, tipo_rodada="incremental")
        # O passo do backfill roda a cada 10 min entre duas incrementais.
        for _ in range(5):
            await _metrica(session, falhou=False, tipo_rodada="periodo")
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="timeout", tipo_erro="TimeoutError", duracao_s=1.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho], "incremental")
    assert "falhas-seguidas:TJSP:digital-lgpd" in [c for c, _ in alertas_capturados]


async def test_falhas_seguidas_nao_conta_backfill_como_falha(session, alertas_capturados):
    """E o simétrico: falhas de backfill não devem preencher a janela da
    incremental. O backfill que falha já tem seu próprio tratamento (não
    avança o ponteiro e refaz o mês no passo seguinte)."""
    for _ in range(3):
        await _metrica(session, falhou=True, tipo_rodada="periodo")
    await _metrica(session, falhou=True, tipo_rodada="incremental")
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="timeout", tipo_erro="TimeoutError", duracao_s=1.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho], "incremental")
    assert "falhas-seguidas:TJSP:digital-lgpd" not in [c for c, _ in alertas_capturados]


@pytest.mark.parametrize("tipo_rodada", ["tema", "periodo"])
async def test_falhas_seguidas_nao_alerta_em_rodada_parcial(
    session, alertas_capturados, tipo_rodada
):
    """Filtrar o histórico em `_falhas_seguidas` não basta: o gatilho também
    precisa ser guardado. "Falhou em 3 rodadas seguidas" é uma afirmação sobre
    a coleta diária; disparada por uma rodada `--tema` que deu timeout, ela
    não descreve a rodada que acabou de acontecer e ainda queima a chave de
    cooldown compartilhada, suprimindo o alerta da incremental de verdade nas
    seis horas seguintes.
    """
    for _ in range(3):
        await _metrica(session, falhou=True, tipo_rodada="incremental")
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="timeout", tipo_erro="TimeoutError", duracao_s=1.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho], tipo_rodada)
    assert "falhas-seguidas:TJSP:digital-lgpd" not in [c for c, _ in alertas_capturados]


async def test_bloqueio_alerta_mesmo_em_rodada_parcial(session, alertas_capturados):
    """O gatilho 1 é o oposto dos outros dois: a spec o define como "imediato,
    sempre". Um bloqueio anti-robô durante o teste de fumaça é tão real quanto
    um durante a rodada das 03:00 — este projeto já foi bloqueado uma vez."""
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="e-SAJ apresentou desafio",
        tipo_erro=BloqueioDetectadoError.__name__, duracao_s=3.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho], "tema")
    assert "bloqueio:TJSP:digital-lgpd" in [c for c, _ in alertas_capturados]


def test_tipo_de_rodada_sai_dos_argumentos():
    from datetime import date

    assert _tipo_de_rodada(None, None) == "incremental"
    assert _tipo_de_rodada("digital-lgpd", None) == "tema"
    assert _tipo_de_rodada(None, date(2026, 6, 30)) == "periodo"
    # --tema com período é o backfill de um tema só: parcial de todo jeito, e
    # o que importa aos gatilhos é que não é a rodada inteira.
    assert _tipo_de_rodada("digital-lgpd", date(2026, 6, 30)) == "tema"
