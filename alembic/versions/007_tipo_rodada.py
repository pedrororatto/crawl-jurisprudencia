"""Tipo de rodada nas métricas de coleta.

Revision ID: 007
Revises: 006
"""

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `server_default` preenche as linhas já existentes: a tabela é pequena e
    # tudo que há nela hoje veio da rodada incremental diária. O default fica
    # na coluna de propósito — é a rodada incremental que o schema deve supor
    # quando ninguém disser o contrário.
    op.add_column(
        "metricas_coleta",
        sa.Column(
            "tipo_rodada",
            sa.String(length=20),
            nullable=False,
            server_default="incremental",
        ),
    )


def downgrade() -> None:
    op.drop_column("metricas_coleta", "tipo_rodada")
