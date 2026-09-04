"""Dependências compartilhadas da API.

get_session vive aqui, e não em routes.py, para que apikeys.py possa usá-la
sem fechar um ciclo de import com routes.py.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from juris.db.engine import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
