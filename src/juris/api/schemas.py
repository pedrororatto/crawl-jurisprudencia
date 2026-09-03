from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class DecisaoResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tribunal: str
    numero_processo: str | None
    classe: str | None
    assunto: str | None
    relator: str | None
    comarca: str | None
    orgao_julgador: str | None
    data_julgamento: date | None
    data_publicacao: date | None
    temas: list[str]
    ementa_texto: str | None


class DecisaoDetalhe(DecisaoResumo):
    cd_acordao: str
    ementa_markdown: str | None
    created_at: datetime
    updated_at: datetime


class PaginaDecisoes(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[DecisaoResumo]


class TemaInfo(BaseModel):
    slug: str
    nome: str
    total_decisoes: int


class HealthInfo(BaseModel):
    status: str
    total_decisoes: int
    ultima_coleta: datetime | None
