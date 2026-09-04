from dataclasses import asdict

from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from juris.collectors.base import DecisaoColetada
from juris.pipeline.fontes import calcular_content_hash, montar_url_origem

# Upsert pela chave natural (tribunal, cd_acordao). Em conflito, atualiza os
# campos e mescla o array de temas sem duplicar. RETURNING xmax = 0 distingue
# insert (True) de update (False).
_SQL_UPSERT = text(
    """
    INSERT INTO decisoes (
        tribunal, cd_acordao, numero_processo, classe, assunto, relator, comarca,
        orgao_julgador, data_julgamento, data_publicacao, ementa_texto,
        ementa_markdown, raw_html, temas, url_origem, content_hash
    ) VALUES (
        :tribunal, :cd_acordao, :numero_processo, :classe, :assunto, :relator, :comarca,
        :orgao_julgador, :data_julgamento, :data_publicacao, :ementa_texto,
        :ementa_markdown, :raw_html, :temas, :url_origem, :content_hash
    )
    ON CONFLICT (tribunal, cd_acordao) DO UPDATE SET
        numero_processo = EXCLUDED.numero_processo,
        classe = EXCLUDED.classe,
        assunto = EXCLUDED.assunto,
        relator = EXCLUDED.relator,
        comarca = EXCLUDED.comarca,
        orgao_julgador = EXCLUDED.orgao_julgador,
        data_julgamento = EXCLUDED.data_julgamento,
        data_publicacao = EXCLUDED.data_publicacao,
        ementa_texto = EXCLUDED.ementa_texto,
        ementa_markdown = EXCLUDED.ementa_markdown,
        raw_html = EXCLUDED.raw_html,
        temas = ARRAY(
            SELECT DISTINCT t FROM unnest(decisoes.temas || EXCLUDED.temas) AS t ORDER BY t
        ),
        url_origem = EXCLUDED.url_origem,
        content_hash = EXCLUDED.content_hash,
        updated_at = now()
    RETURNING (xmax = 0) AS inserted
    """
).bindparams(bindparam("temas", type_=ARRAY(String)))


async def ingerir(
    session: AsyncSession, decisoes: list[DecisaoColetada], tema_slug: str
) -> tuple[int, int]:
    """Grava as decisões e retorna (novas, atualizadas)."""
    novas = atualizadas = 0
    for d in decisoes:
        params = asdict(d) | {
            "temas": [tema_slug],
            "url_origem": montar_url_origem(d.tribunal, d.cd_acordao),
            "content_hash": calcular_content_hash(d.ementa_texto),
        }
        inserted = (await session.execute(_SQL_UPSERT, params)).scalar_one()
        if inserted:
            novas += 1
        else:
            atualizadas += 1
    return novas, atualizadas
