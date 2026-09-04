"""A trava que impede models.py e as migrations divergirem."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from juris.db.models import Base


async def test_migrations_conferem_com_os_modelos(banco_de_teste):
    """Depois de `upgrade head`, um autogenerate não pode ter o que fazer."""
    engine = create_async_engine(banco_de_teste)

    def _diferencas(conn):
        contexto = MigrationContext.configure(conn)
        return compare_metadata(contexto, Base.metadata)

    async with engine.connect() as conn:
        diffs = await conn.run_sync(_diferencas)
    await engine.dispose()

    assert diffs == [], f"Schema divergiu de models.py: {diffs}"
