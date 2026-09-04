"""url_origem e content_hash em decisoes, com backfill.

Revision ID: 002
Revises: 001
"""

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisoes", sa.Column("url_origem", sa.Text(), nullable=True))
    op.add_column("decisoes", sa.Column("content_hash", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE decisoes
           SET url_origem = 'https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao='
                            || cd_acordao || '&cdForo=0'
         WHERE tribunal = 'TJSP' AND url_origem IS NULL
        """
    )
    # Mesmo dígito que juris.pipeline.fontes.calcular_content_hash produz;
    # tests/test_fontes.py::test_hash_sql_confere_com_python garante isso.
    op.execute(
        """
        UPDATE decisoes
           SET content_hash = 'sha256:'
               || encode(sha256(convert_to(coalesce(ementa_texto,''),'UTF8')),'hex')
         WHERE content_hash IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("decisoes", "content_hash")
    op.drop_column("decisoes", "url_origem")
