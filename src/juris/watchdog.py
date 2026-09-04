"""Dead man's switch da coleta.

O gatilho "nenhuma rodada bem-sucedida em N horas" não pode viver dentro do
coletor: se o processo morreu, ele não avisa que morreu. Este módulo roda
fora, por cron, e só precisa do banco — coletor e watchdog compartilham o
Postgres, então o watchdog não sabe nada sobre o processo vigiado.

Uso:
    python -m juris.watchdog

No cron, de hora em hora:
    0 * * * * cd /opt/juris && docker compose run --rm crawler python -m juris.watchdog
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from juris.alertas import alertar
from juris.config import settings
from juris.db.engine import SessionLocal
from juris.db.models import Execucao
from juris.logging_config import configurar_logging

logger = logging.getLogger(__name__)


async def coleta_parada(session: AsyncSession) -> timedelta | None:
    """Há quanto tempo a coleta parou, ou None se está saudável.

    Banco sem execução alguma devolve None: instalação nova ainda não coletou,
    e alertar nesse caso seria ruído no primeiro deploy.
    """
    ultima = await session.scalar(select(func.max(Execucao.ultima_execucao)))
    if ultima is None:
        return None

    atraso = datetime.now(timezone.utc) - ultima
    limite = timedelta(hours=settings.watchdog_limite_horas)
    return atraso if atraso > limite else None


async def verificar(session: AsyncSession) -> None:
    atraso = await coleta_parada(session)
    if atraso is None:
        logger.info("Coleta saudável")
        return

    horas = int(atraso.total_seconds() // 3600)
    await alertar(
        session,
        "coleta-parada",
        f"Nenhuma rodada de coleta bem-sucedida há {horas} horas "
        f"(limite: {settings.watchdog_limite_horas}h). "
        "Verifique se o processo do scheduler está de pé.",
    )


async def _main() -> None:
    async with SessionLocal() as session:
        await verificar(session)


def main() -> None:
    configurar_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
