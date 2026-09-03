from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from juris.api.schemas import (
    DecisaoDetalhe,
    DecisaoResumo,
    HealthInfo,
    PaginaDecisoes,
    TemaInfo,
)
from juris.config import settings
from juris.db.engine import SessionLocal
from juris.db.models import Decisao, Execucao
from juris.temas import carregar_temas

router = APIRouter(prefix="/v1")


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def api_key_auth(x_api_key: str | None = Header(default=None)) -> None:
    """Ponto de encaixe da autenticação. No MVP a API é aberta; quando a venda
    começar, valida-se a chave aqui sem mudar o contrato dos endpoints."""
    return None


@router.get("/decisoes", response_model=PaginaDecisoes, dependencies=[Depends(api_key_auth)])
async def listar_decisoes(
    q: str | None = Query(
        default=None,
        description=(
            "Busca por texto na ementa (e em classe/assunto/relator/comarca/órgão). "
            "Aceita sintaxe websearch (aspas para frase exata, OR, -exclusão) e "
            "também casa qualquer trecho literal digitado, mesmo que o dicionário "
            "de português não unifique a variação da palavra."
        ),
    ),
    tribunal: str | None = None,
    relator: str | None = Query(default=None, description="Filtro por parte do nome"),
    orgao_julgador: str | None = None,
    tema: str | None = Query(default=None, description="Slug de config/temas.yaml"),
    data_julgamento_de: date | None = None,
    data_julgamento_ate: date | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> PaginaDecisoes:
    filtros = []
    if q:
        tsquery = func.websearch_to_tsquery("portuguese", q)
        # OR com word_similarity (trigram, operador %>) em ementa_texto: o
        # stemmer do dicionário português nem sempre unifica variações da
        # mesma palavra (ex.: "repercussão" e "repercussões" viram radicais
        # diferentes — 'repercussã' x 'repercussõ' — problema comum em
        # palavras jurídicas terminadas em -são/-ssão: decisão, prisão,
        # omissão...). O trigram cobre isso por similaridade, sem depender de
        # sufixo exato, e usa o mesmo índice GIN (ix_decisoes_ementa_trgm).
        filtros.append(Decisao.busca.op("@@")(tsquery) | Decisao.ementa_texto.op("%>")(q))
    if tribunal:
        filtros.append(Decisao.tribunal == tribunal.upper())
    if relator:
        filtros.append(Decisao.relator.ilike(f"%{relator}%"))
    if orgao_julgador:
        filtros.append(Decisao.orgao_julgador.ilike(f"%{orgao_julgador}%"))
    if tema:
        filtros.append(Decisao.temas.any(tema))
    if data_julgamento_de:
        filtros.append(Decisao.data_julgamento >= data_julgamento_de)
    if data_julgamento_ate:
        filtros.append(Decisao.data_julgamento <= data_julgamento_ate)

    total = await session.scalar(select(func.count()).select_from(Decisao).where(*filtros))

    stmt = select(Decisao).where(*filtros)
    if q:
        # Rank 0 agrupa as ementas que só bateram pelo ILIKE (não pelo
        # full-text); o desempate por data mantém essas em ordem sensata.
        stmt = stmt.order_by(
            func.ts_rank(Decisao.busca, func.websearch_to_tsquery("portuguese", q)).desc(),
            Decisao.data_publicacao.desc().nulls_last(),
        )
    else:
        stmt = stmt.order_by(Decisao.data_publicacao.desc().nulls_last(), Decisao.id.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    decisoes = (await session.scalars(stmt)).all()
    return PaginaDecisoes(
        total=total or 0,
        page=page,
        per_page=per_page,
        items=[DecisaoResumo.model_validate(d) for d in decisoes],
    )


@router.get(
    "/decisoes/{decisao_id}", response_model=DecisaoDetalhe, dependencies=[Depends(api_key_auth)]
)
async def obter_decisao(
    decisao_id: int, session: AsyncSession = Depends(get_session)
) -> DecisaoDetalhe:
    decisao = await session.get(Decisao, decisao_id)
    if decisao is None:
        raise HTTPException(status_code=404, detail="Decisão não encontrada")
    return DecisaoDetalhe.model_validate(decisao)


@router.get("/temas", response_model=list[TemaInfo], dependencies=[Depends(api_key_auth)])
async def listar_temas(session: AsyncSession = Depends(get_session)) -> list[TemaInfo]:
    temas = carregar_temas(settings.temas_path)
    contagens = dict(
        (
            await session.execute(
                select(func.unnest(Decisao.temas).label("slug"), func.count())
                .select_from(Decisao)
                .group_by("slug")
            )
        ).all()
    )
    return [
        TemaInfo(slug=t.slug, nome=t.nome, total_decisoes=contagens.get(t.slug, 0))
        for t in temas
    ]


@router.get("/health", response_model=HealthInfo)
async def health(session: AsyncSession = Depends(get_session)) -> HealthInfo:
    total = await session.scalar(select(func.count()).select_from(Decisao))
    ultima = await session.scalar(select(func.max(Execucao.ultima_execucao)))
    return HealthInfo(status="ok", total_decisoes=total or 0, ultima_coleta=ultima)
