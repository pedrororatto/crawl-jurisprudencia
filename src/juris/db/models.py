from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
    # URL do acórdão no sistema de origem, gravada no momento da coleta.
    url_origem: Mapped[str | None] = mapped_column(Text)
    # sha256 da ementa, no formato "sha256:<hex>". Calculado no ingest.
    content_hash: Mapped[str | None] = mapped_column(Text)
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
        # (veja pg_trgm em alembic/versions/001_baseline.py).
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


class MetricaColeta(Base):
    """Histórico de coleta, uma linha por tema por rodada.

    A tabela `execucoes` responde "está rodando?". Esta responde "está
    rendendo o de sempre?" — sem histórico não há como distinguir uma rodada
    tranquila de um parser quebrado.
    """

    __tablename__ = "metricas_coleta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rodada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tribunal: Mapped[str] = mapped_column(String(10))
    tema_slug: Mapped[str] = mapped_column(String(60))
    novas: Mapped[int] = mapped_column(Integer, default=0)
    atualizadas: Mapped[int] = mapped_column(Integer, default=0)
    paginas: Mapped[int] = mapped_column(Integer, default=0)
    resultados: Mapped[int] = mapped_column(Integer, default=0)
    falhou: Mapped[bool] = mapped_column(Boolean, default=False)
    erro: Mapped[str | None] = mapped_column(Text)
    duracao_s: Mapped[float | None] = mapped_column(Numeric(10, 2))

    __table_args__ = (
        Index("ix_metricas_rodada", "tribunal", "tema_slug", "rodada_em"),
    )


class AlertaEnviado(Base):
    """Cooldown de alerta.

    Sem isto o canal se autodestrói: um tribunal bloqueado geraria centenas
    de mensagens até o operador silenciar o bot — e o canal morre justamente
    quando importa.
    """

    __tablename__ = "alertas_enviados"

    chave: Mapped[str] = mapped_column(Text, primary_key=True)
    enviado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApiKey(Base):
    """Chave de acesso à API.

    O banco guarda somente o sha256 da chave; o valor em claro é exibido uma
    única vez, na criação.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # "publico": jurisprudência e legislação. "interno": acrescenta o acervo
    # doutrinário do escritório (onda 5).
    escopo: Mapped[str] = mapped_column(String(20), default="publico")
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    limite_diario: Mapped[int | None] = mapped_column(Integer)
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ultimo_uso_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKeyUso(Base):
    """Contagem diária por chave.

    A chave primária (api_key_id, dia) faz a contagem se reiniciar sozinha a
    cada dia, sem cron de reset, e já serve de base para cobrança.
    """

    __tablename__ = "api_key_uso"

    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    )
    dia: Mapped[date] = mapped_column(Date, primary_key=True)
    requisicoes: Mapped[int] = mapped_column(Integer, default=0)
