"""Alerta operacional por Telegram, com cooldown por chave.

Bloqueio silencioso é o pior modo de falha deste sistema: a coleta para,
ninguém percebe, e semanas depois a base está desatualizada sem que nada
tenha quebrado visivelmente.

O envio é best-effort. Falha de entrega é registrada e engolida — a rede de
segurança é o watchdog (juris.watchdog), que roda fora do processo vigiado.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from juris.config import settings
from juris.db.models import AlertaEnviado

logger = logging.getLogger(__name__)

_TIMEOUT_SEGUNDOS = 10.0


async def _enviar_telegram(mensagem: str) -> None:
    """Envia de fato. Isolado numa função para o teste substituir.

    Atenção: se algum dia se habilitar logging DEBUG das bibliotecas httpx
    ou httpcore, elas próprias podem registrar a URL da requisição — e a URL
    carrega o token do bot. O cuidado abaixo com o logger deste módulo não
    cobre esse caminho.
    """
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS) as client:
        resposta = await client.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": mensagem},
        )
        resposta.raise_for_status()


async def _em_cooldown(session: AsyncSession, chave: str) -> bool:
    limite = datetime.now(timezone.utc) - timedelta(hours=settings.alerta_cooldown_horas)
    registro = await session.scalar(
        select(AlertaEnviado).where(AlertaEnviado.chave == chave)
    )
    return registro is not None and registro.enviado_em > limite


async def alertar(session: AsyncSession, chave: str, mensagem: str) -> bool:
    """Envia o alerta se a chave não estiver em cooldown.

    Devolve True se enviou, False se suprimiu, se não há bot configurado ou
    se a entrega falhou. Nunca levanta exceção: o chamador é uma rodada de
    coleta, e alerta não pode derrubar coleta.

    Pré-condição: `session` não pode ter trabalho não commitado do chamador
    no momento da chamada. Uma falha de banco aqui é recuperada com
    `session.rollback()`, que descartaria esse trabalho junto — a função é
    feita para ser chamada repetidamente sobre uma sessão compartilhada
    dentro de uma mesma rodada (ex.: um loop de Task 7 chamando alertar()
    várias vezes), e cada chamada precisa deixar a sessão utilizável para a
    próxima, mesmo depois de um erro.
    """
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        logger.warning("Alerta não enviado (bot do Telegram não configurado): %s", mensagem)
        return False

    try:
        em_cooldown = await _em_cooldown(session, chave)
    except Exception:
        # Não conseguimos saber se a chave está em cooldown (conexão caiu,
        # pool esgotado, etc.). Entre arriscar um alerta duplicado e arriscar
        # engolir um alerta de verdade, o duplicado é o erro mais barato
        # neste sistema — o alerta perdido é exatamente o modo de falha que
        # este módulo existe para evitar. Por isso seguimos como se não
        # estivesse em cooldown.
        #
        # O rollback é obrigatório aqui: sem ele, a sessão fica marcada como
        # exigindo rollback (PendingRollbackError) e toda chamada seguinte a
        # alertar() nesta mesma sessão passa a falhar do mesmo jeito — o que
        # inverte o propósito deste módulo, trocando um erro alto e limitado
        # (a rodada quebra num ponto conhecido) por um silencioso e sem
        # limite (nenhum cooldown mais se sustenta, todo alerta passa a
        # sair sem filtro).
        await session.rollback()
        logger.error("Falha ao consultar cooldown de %r; enviando mesmo assim", chave)
        em_cooldown = False

    if em_cooldown:
        logger.info("Alerta %r suprimido por cooldown", chave)
        return False

    try:
        await _enviar_telegram(mensagem)
    except Exception:
        # Sem exc_info: a mensagem de erro do httpx pode conter a URL, e a URL
        # carrega o token do bot.
        logger.error("Falha ao enviar alerta %r; watchdog é a rede de segurança", chave)
        return False

    try:
        registro = await session.get(AlertaEnviado, chave)
        if registro is None:
            session.add(AlertaEnviado(chave=chave, enviado_em=datetime.now(timezone.utc)))
        else:
            registro.enviado_em = datetime.now(timezone.utc)
        await session.commit()
    except Exception:
        # A mensagem já foi entregue nesse ponto. Uma falha ao gravar o
        # cooldown não pode virar "a entrega falhou" para quem chamou —
        # o preço é um possível alerta duplicado na próxima rodada, não um
        # alerta engolido nesta. Mesmo motivo do rollback acima: sem ele, a
        # sessão fica inutilizável para a próxima chamada de alertar().
        await session.rollback()
        logger.error("Alerta %r entregue, mas falha ao gravar o cooldown", chave)
        return True

    logger.info("Alerta %r enviado", chave)
    return True
