"""Cria as tabelas no banco: python -m juris.db.init"""

import asyncio

from sqlalchemy import text

from juris.db.engine import engine
from juris.db.models import Base


async def criar_tabelas() -> None:
    async with engine.begin() as conn:
        # pg_trgm: casamento por substring (índice GIN) usado na busca em
        # ementa_texto, complementando o full-text (que às vezes não unifica
        # variações morfológicas, ex.: "repercussão" x "repercussões").
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        # create_all só cria tabelas que ainda não existem — não adiciona
        # índices novos numa tabela já existente. Como o projeto não usa
        # Alembic (create_all é a única migração), índices adicionados depois
        # do primeiro deploy precisam ser criados aqui manualmente.
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_decisoes_ementa_trgm "
                "ON decisoes USING gin (ementa_texto gin_trgm_ops)"
            )
        )
    print("Tabelas criadas/atualizadas.")


if __name__ == "__main__":
    asyncio.run(criar_tabelas())
