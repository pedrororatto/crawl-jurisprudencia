import hashlib

from sqlalchemy import text

from juris.pipeline.fontes import (
    calcular_content_hash,
    montar_url_origem,
    url_requer_login,
)


def test_calcular_content_hash():
    esperado = hashlib.sha256("EMENTA".encode("utf-8")).hexdigest()
    assert calcular_content_hash("EMENTA") == f"sha256:{esperado}"


def test_calcular_content_hash_trata_nulo_como_vazio():
    vazio = hashlib.sha256(b"").hexdigest()
    assert calcular_content_hash(None) == f"sha256:{vazio}"
    assert calcular_content_hash("") == f"sha256:{vazio}"


def test_montar_url_origem_tjsp():
    assert montar_url_origem("TJSP", "20819001") == (
        "https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao=20819001&cdForo=0"
    )


def test_montar_url_origem_tribunal_desconhecido_nao_chuta():
    assert montar_url_origem("TJXX", "123") is None


def test_url_requer_login():
    assert url_requer_login("TJSP") is True
    assert url_requer_login("TJXX") is None


async def test_hash_sql_confere_com_python(session):
    """O backfill em SQL e o ingest em Python precisam produzir o mesmo dígito."""
    for ementa in ["RESPONSABILIDADE CIVIL — provedor.", "acentuação çãõ", ""]:
        no_banco = await session.scalar(
            text(
                "SELECT 'sha256:' || encode("
                "sha256(convert_to(coalesce(:e,''),'UTF8')),'hex')"
            ),
            {"e": ementa},
        )
        assert no_banco == calcular_content_hash(ementa)
