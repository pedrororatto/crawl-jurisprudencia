from sqlalchemy import text

from juris.collectors.base import DecisaoColetada
from juris.pipeline.fontes import calcular_content_hash
from juris.pipeline.ingest import ingerir


async def test_ingest_grava_url_de_origem_e_hash(session):
    decisao = DecisaoColetada(
        tribunal="TJSP",
        cd_acordao="99999001",
        numero_processo="1234567-89.2024.8.26.0100",
        ementa_texto="RESPONSABILIDADE CIVIL — marketplace.",
    )

    novas, atualizadas = await ingerir(session, [decisao], "digital-comercio-eletronico")
    assert (novas, atualizadas) == (1, 0)

    linha = (
        await session.execute(
            text(
                "SELECT url_origem, content_hash FROM decisoes "
                "WHERE tribunal='TJSP' AND cd_acordao='99999001'"
            )
        )
    ).one()

    assert linha.url_origem == (
        "https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao=99999001&cdForo=0"
    )
    assert linha.content_hash == calcular_content_hash(
        "RESPONSABILIDADE CIVIL — marketplace."
    )
