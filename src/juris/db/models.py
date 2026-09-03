from datetime import date, datetime

from sqlalchemy import (
    Computed,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Decisao(Base):
    __tablename__ = "decisoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tribunal: Mapped[str] = mapped_column(String(10), index=True)
    # Identificador do acórdão no sistema de origem (cdAcordao no e-SAJ).
    cd_acordao: Mapped[str] = mapped_column(String(40))
    numero_processo: Mapped[str | None] = mapped_column(String(40), index=True)
    classe: Mapped[str | None] = mapped_column(String(200))
    assunto: Mapped[str | None] = mapped_column(String(400))
    relator: Mapped[str | None] = mapped_column(String(200), index=True)
    comarca: Mapped[str | None] = mapped_column(String(200))
    orgao_julgador: Mapped[str | None] = mapped_column(String(200), index=True)
    data_julgamento: Mapped[date | None] = mapped_column(Date, index=True)
    data_publicacao: Mapped[date | None] = mapped_column(Date, index=True)
    ementa_texto: Mapped[str | None] = mapped_column(Text)
    ementa_markdown: Mapped[str | None] = mapped_column(Text)
    # HTML bruto do bloco do resultado, preservado para reprocessamento futuro.
    raw_html: Mapped[str | None] = mapped_column(Text)
    # Slugs dos temas (config/temas.yaml) cujas buscas trouxeram esta decisão.
    temas: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    busca: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('portuguese', "
            "coalesce(ementa_texto, '') || ' ' || coalesce(classe, '') || ' ' || "
            "coalesce(assunto, '') || ' ' || coalesce(relator, '') || ' ' || "
            "coalesce(orgao_julgador, '') || ' ' || coalesce(comarca, ''))",
            persisted=True,
        ),
    )

    __table_args__ = (
        UniqueConstraint("tribunal", "cd_acordao", name="uq_decisoes_tribunal_cd_acordao"),
        Index("ix_decisoes_busca", "busca", postgresql_using="gin"),
        Index("ix_decisoes_temas", "temas", postgresql_using="gin"),
        # Casamento por similaridade (trigram: ILIKE, %, %>) rápido em
        # ementa_texto, para não depender só do stemmer do full-text
        # (veja pg_trgm em db/init.py).
        Index(
            "ix_decisoes_ementa_trgm",
            "ementa_texto",
            postgresql_using="gin",
            postgresql_ops={"ementa_texto": "gin_trgm_ops"},
        ),
    )


class BackfillProgresso(Base):
    """Progresso do backfill histórico: caminha mês a mês para trás, do mês
    atual até settings.backfill_inicio. Uma linha por tribunal."""

    __tablename__ = "backfill_progresso"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tribunal: Mapped[str] = mapped_column(String(10), unique=True)
    # Primeiro dia do próximo mês a processar (retrocedendo no tempo).
    proximo_mes: Mapped[date] = mapped_column(Date)
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Execucao(Base):
    """Watermark de coleta incremental por (tribunal, tema)."""

    __tablename__ = "execucoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tribunal: Mapped[str] = mapped_column(String(10))
    tema_slug: Mapped[str] = mapped_column(String(60))
    # Maior data_publicacao já coletada para este tema.
    watermark: Mapped[date | None] = mapped_column(Date)
    ultima_execucao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_qtd: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("tribunal", "tema_slug", name="uq_execucoes_tribunal_tema"),
    )
