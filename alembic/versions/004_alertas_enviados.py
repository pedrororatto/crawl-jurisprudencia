"""Tabela de cooldown de alerta.

Revision ID: 004
Revises: 003
"""

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alertas_enviados",
        sa.Column("chave", sa.Text(), nullable=False),
        sa.Column(
            "enviado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("chave"),
    )


def downgrade() -> None:
    op.drop_table("alertas_enviados")
