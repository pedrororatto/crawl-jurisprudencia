"""Histórico de coleta por tema.

Revision ID: 005
Revises: 004
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metricas_coleta",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "rodada_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("tema_slug", sa.String(length=60), nullable=False),
        sa.Column("novas", sa.Integer(), nullable=False),
        sa.Column("atualizadas", sa.Integer(), nullable=False),
        sa.Column("paginas", sa.Integer(), nullable=False),
        sa.Column("resultados", sa.Integer(), nullable=False),
        sa.Column("falhou", sa.Boolean(), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("duracao_s", sa.Numeric(10, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metricas_rodada", "metricas_coleta", ["tribunal", "tema_slug", "rodada_em"]
    )


def downgrade() -> None:
    op.drop_index("ix_metricas_rodada", table_name="metricas_coleta")
    op.drop_table("metricas_coleta")
