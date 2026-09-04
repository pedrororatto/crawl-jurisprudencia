"""Banco de teste.

Recria o banco `juris_test` do zero a cada sessão de pytest e aplica as
migrations. Exige o Postgres de desenvolvimento no ar:

    docker compose up -d db

A fixture `session` entrega uma AsyncSession presa a uma transação externa
que é revertida ao fim de cada teste — commits dentro do código sob teste
não vazam para o teste seguinte.
"""

import asyncio
import os

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ADMIN_URL = os.environ.get(
    "JURIS_TEST_ADMIN_URL", "postgresql+asyncpg://juris:juris@localhost:5432/postgres"
)
TEST_URL = os.environ.get(
    "JURIS_TEST_DATABASE_URL",
    "postgresql+asyncpg://juris:juris@localhost:5432/juris_test",
)


async def _recriar_banco() -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS juris_test WITH (FORCE)"))
        await conn.execute(text("CREATE DATABASE juris_test"))
    await engine.dispose()


@pytest.fixture(scope="session")
def banco_de_teste() -> str:
    # Fixture síncrona de propósito: command.upgrade abre o próprio event loop
    # dentro do env.py assíncrono do Alembic, e aninhar loops quebraria.
    # Exige pytest rodando a partir da raiz do repositório (alembic.ini é
    # caminho relativo).
    asyncio.run(_recriar_banco())
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest_asyncio.fixture
async def session(banco_de_teste: str):
    engine = create_async_engine(banco_de_teste)
    async with engine.connect() as conn:
        trans = await conn.begin()
        # join_transaction_mode="create_savepoint" é obrigatório: sem ele, um
        # session.commit() dentro do código sob teste encerra a transação
        # externa e o rollback abaixo não desfaz nada — os testes passariam a
        # sujar uns aos outros de forma difícil de diagnosticar.
        sessao = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield sessao
        await sessao.close()
        await trans.rollback()
    await engine.dispose()
