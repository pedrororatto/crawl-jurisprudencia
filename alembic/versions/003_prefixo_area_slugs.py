"""Prefixo de área nos slugs de tema: lgpd -> digital-lgpd.

Feito enquanto existe uma única área e nenhum consumidor usa o contrato da
API — o momento mais barato possível.

Revision ID: 003
Revises: 002
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # O guard NOT LIKE torna a migração inofensiva se reexecutada: nada vira
    # digital-digital-lgpd.
    op.execute(
        """
        UPDATE decisoes
           SET temas = ARRAY(
                 SELECT DISTINCT
                        CASE WHEN t LIKE 'digital-%' THEN t ELSE 'digital-' || t END
                   FROM unnest(temas) AS t
                  ORDER BY 1)
        """
    )
    op.execute(
        "UPDATE execucoes SET tema_slug = 'digital-' || tema_slug "
        "WHERE tema_slug NOT LIKE 'digital-%'"
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE decisoes
           SET temas = ARRAY(
                 SELECT DISTINCT regexp_replace(t, '^digital-', '')
                   FROM unnest(temas) AS t
                  ORDER BY 1)
        """
    )
    op.execute("UPDATE execucoes SET tema_slug = regexp_replace(tema_slug, '^digital-', '')")
