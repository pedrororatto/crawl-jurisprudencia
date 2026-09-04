from datetime import timedelta

import pytest
from sqlalchemy import text

from juris import alertas
from juris.alertas import alertar


@pytest.fixture
def telegram_falso(monkeypatch):
    """Captura o que teria sido enviado, sem tocar a rede.

    Fixa também as credenciais: o teste não pode depender de um .env local
    que só existe na máquina de quem escreveu o código.
    """
    monkeypatch.setattr(alertas.settings, "telegram_bot_token", "token-de-teste")
    monkeypatch.setattr(alertas.settings, "telegram_chat_id", "chat-de-teste")
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


async def test_cooldown_pinado_na_fronteira_da_janela(session, telegram_falso):
    """Um minuto de cada lado da janela configurada, não uma margem folgada.

    Pega tanto um `>=` acidental quanto um sinal invertido na comparação de
    `alertas.py`, que uma margem de horas inteiras deixaria passar.
    """
    janela_horas = alertas.settings.alerta_cooldown_horas
    await alertar(session, "bloqueio:TJSP", "primeira")

    # Um minuto DENTRO da janela: ainda deve suprimir.
    await session.execute(
        text(
            "UPDATE alertas_enviados SET enviado_em = now() - make_interval(hours => :horas) "
            "+ interval '1 minute' WHERE chave = 'bloqueio:TJSP'"
        ),
        {"horas": janela_horas},
    )
    assert await alertar(session, "bloqueio:TJSP", "dentro da janela") is False

    # Um minuto FORA da janela: deve voltar a enviar.
    await session.execute(
        text(
            "UPDATE alertas_enviados SET enviado_em = now() - make_interval(hours => :horas) "
            "- interval '1 minute' WHERE chave = 'bloqueio:TJSP'"
        ),
        {"horas": janela_horas},
    )
    assert await alertar(session, "bloqueio:TJSP", "fora da janela") is True
    assert telegram_falso == ["primeira", "fora da janela"]


async def test_falha_de_entrega_nao_propaga(session, monkeypatch):
    """Telegram fora do ar não pode derrubar a coleta."""
    monkeypatch.setattr(alertas.settings, "telegram_bot_token", "token-de-teste")
    monkeypatch.setattr(alertas.settings, "telegram_chat_id", "chat-de-teste")

    async def _explode(mensagem: str) -> None:
        raise RuntimeError("timeout")

    monkeypatch.setattr(alertas, "_enviar_telegram", _explode)
    assert await alertar(session, "bloqueio:TJSP", "msg") is False


async def test_sem_token_configurado_nao_quebra(session, monkeypatch):
    monkeypatch.setattr(alertas.settings, "telegram_bot_token", None)
    assert await alertar(session, "bloqueio:TJSP", "msg") is False


async def test_falha_ao_consultar_cooldown_nao_propaga(session, telegram_falso, monkeypatch):
    """Erro de banco ao ler o cooldown não pode derrubar a coleta.

    A leitura falha, então tratamos como "não está em cooldown" — o alerta
    perdido é o modo de falha que este módulo existe para evitar, então um
    possível duplicado é o erro mais barato.
    """

    async def _scalar_explode(*args, **kwargs):
        raise RuntimeError("conexão caiu")

    monkeypatch.setattr(session, "scalar", _scalar_explode)
    assert await alertar(session, "bloqueio:TJSP", "msg") is True
    assert telegram_falso == ["msg"]


async def test_falha_ao_gravar_cooldown_apos_envio_nao_propaga(
    session, telegram_falso, monkeypatch
):
    """Falha ao gravar o cooldown depois do envio não pode virar "não enviei".

    Nesse ponto a mensagem já foi entregue ao Telegram; dizer False ao
    chamador seria mentir sobre o que aconteceu.
    """

    async def _commit_explode():
        raise RuntimeError("conexão caiu")

    monkeypatch.setattr(session, "commit", _commit_explode)
    assert await alertar(session, "bloqueio:TJSP", "msg") is True
    assert telegram_falso == ["msg"]


async def test_falha_ao_consultar_cooldown_recupera_a_sessao(
    session, telegram_falso, monkeypatch
):
    """alertar() é chamada repetidamente sobre uma sessão compartilhada numa
    mesma rodada (é assim que a Task 7 vai usá-la). Uma falha de banco de
    verdade na leitura do cooldown — não um RuntimeError sintético — precisa
    deixar a sessão utilizável para a chamada seguinte; sem rollback, o erro
    real do Postgres deixa a sessão marcada como exigindo rollback e toda
    chamada seguinte de alertar() nessa sessão passa a falhar do mesmo jeito,
    perdendo o cooldown para sempre (é isso que a segunda chamada, abaixo,
    verifica de fato — não apenas que a primeira não propagou).
    """
    _em_cooldown_original = alertas._em_cooldown

    async def _em_cooldown_quebrada(session, chave):
        # Erro real do banco (divisão por zero no Postgres), para reproduzir
        # o estado de sessão que um DBAPIError de verdade deixa — um mock
        # que só levanta RuntimeError nunca chega a sujar a sessão real.
        await session.execute(text("SELECT 1/0"))
        return False  # nunca alcançado

    monkeypatch.setattr(alertas, "_em_cooldown", _em_cooldown_quebrada)
    assert await alertar(session, "bloqueio:TJSP", "primeira") is True

    monkeypatch.setattr(alertas, "_em_cooldown", _em_cooldown_original)
    # Se a sessão não foi recuperada, esta chamada levantaria
    # PendingRollbackError (capturado pelo guard de cooldown, que então
    # trataria "não sei" como "não está em cooldown" e enviaria de novo) —
    # o cooldown gravado pela primeira chamada deixaria de valer.
    assert await alertar(session, "bloqueio:TJSP", "segunda") is False
    assert telegram_falso == ["primeira"]


async def test_falha_ao_gravar_cooldown_recupera_a_sessao(session, telegram_falso, monkeypatch):
    """Mesma garantia do teste acima, para a região de persistência pós-envio.

    Pré-existe um cooldown válido (commitado) para uma chave; uma falha real
    de banco ao gravar o cooldown de OUTRA chave não pode deixar a sessão
    incapaz de continuar respeitando o cooldown já commitado.
    """
    await session.execute(
        text("INSERT INTO alertas_enviados (chave, enviado_em) VALUES ('bloqueio:TJSP', now())")
    )
    await session.commit()

    commit_original = session.commit

    async def _commit_quebrado():
        # Erro real do banco, pelo mesmo motivo do teste acima.
        await session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(session, "commit", _commit_quebrado)
    assert await alertar(session, "bloqueio:TJCE", "nova") is True

    monkeypatch.setattr(session, "commit", commit_original)
    # Se a sessão não foi recuperada, o guard de cooldown desta chamada
    # levantaria PendingRollbackError ao consultar 'bloqueio:TJSP', seria
    # engolido do mesmo jeito e o cooldown já commitado deixaria de ser
    # respeitado.
    assert await alertar(session, "bloqueio:TJSP", "nao deveria enviar") is False
    assert telegram_falso == ["nova"]
