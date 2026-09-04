"""Baseline: schema tal como db/init.py o criava.

Revision ID: 001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

_BUSCA = (
    "to_tsvector('portuguese', "
    "coalesce(ementa_texto, '') || ' ' || coalesce(classe, '') || ' ' || "
    "coalesce(assunto, '') || ' ' || coalesce(relator, '') || ' ' || "
    "coalesce(orgao_julgador, '') || ' ' || coalesce(comarca, ''))"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "decisoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("cd_acordao", sa.String(length=40), nullable=False),
        sa.Column("numero_processo", sa.String(length=40), nullable=True),
        sa.Column("classe", sa.String(length=200), nullable=True),
        sa.Column("assunto", sa.String(length=400), nullable=True),
        sa.Column("relator", sa.String(length=200), nullable=True),
        sa.Column("comarca", sa.String(length=200), nullable=True),
        sa.Column("orgao_julgador", sa.String(length=200), nullable=True),
        sa.Column("data_julgamento", sa.Date(), nullable=True),
        sa.Column("data_publicacao", sa.Date(), nullable=True),
        sa.Column("ementa_texto", sa.Text(), nullable=True),
        sa.Column("ementa_markdown", sa.Text(), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column(
            "temas",
            postgresql.ARRAY(sa.String()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "busca",
            postgresql.TSVECTOR(),
            sa.Computed(_BUSCA, persisted=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tribunal", "cd_acordao", name="uq_decisoes_tribunal_cd_acordao"
        ),
    )
    op.create_index("ix_decisoes_tribunal", "decisoes", ["tribunal"])
    op.create_index("ix_decisoes_numero_processo", "decisoes", ["numero_processo"])
    op.create_index("ix_decisoes_relator", "decisoes", ["relator"])
    op.create_index("ix_decisoes_orgao_julgador", "decisoes", ["orgao_julgador"])
    op.create_index("ix_decisoes_data_julgamento", "decisoes", ["data_julgamento"])
    op.create_index("ix_decisoes_data_publicacao", "decisoes", ["data_publicacao"])
    op.create_index("ix_decisoes_busca", "decisoes", ["busca"], postgresql_using="gin")
    op.create_index("ix_decisoes_temas", "decisoes", ["temas"], postgresql_using="gin")
    op.create_index(
        "ix_decisoes_ementa_trgm",
        "decisoes",
        ["ementa_texto"],
        postgresql_using="gin",
        postgresql_ops={"ementa_texto": "gin_trgm_ops"},
    )

    op.create_table(
        "backfill_progresso",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("proximo_mes", sa.Date(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tribunal"),
    )

    op.create_table(
        "execucoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("tema_slug", sa.String(length=60), nullable=False),
        sa.Column("watermark", sa.Date(), nullable=True),
        sa.Column("ultima_execucao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_qtd", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tribunal", "tema_slug", name="uq_execucoes_tribunal_tema"),
    )


def downgrade() -> None:
    op.drop_table("execucoes")
    op.drop_table("backfill_progresso")
    op.drop_table("decisoes")
