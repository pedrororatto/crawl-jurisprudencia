from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, computed_field

from juris.pipeline.fontes import formatar_referencia_citavel
from juris.pipeline.fontes import url_requer_login as _url_requer_login


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

    url_origem: str | None
    content_hash: str | None

    @computed_field
    @property
    def url_requer_login(self) -> bool | None:
        """None — e não um palpite — para tribunal fora do mapa."""
        return _url_requer_login(self.tribunal)

    @computed_field
    @property
    def referencia_citavel(self) -> str:
        return formatar_referencia_citavel(
            tribunal=self.tribunal,
            classe=self.classe,
            numero_processo=self.numero_processo,
            relator=self.relator,
            orgao_julgador=self.orgao_julgador,
            data_julgamento=self.data_julgamento,
        )

    @computed_field
    @property
    def retrieved_at(self) -> datetime:
        """Quando o conteúdo servido foi visto na origem pela última vez.

        É o updated_at: o upsert o reescreve a cada rodada que revê a decisão.
        Coluna própria seria duplicata.
        """
        return self.updated_at


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
    total_decisoes: int | None = None
    ultima_coleta: datetime | None = None
