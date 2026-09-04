import hashlib
from datetime import date

from sqlalchemy import text

from juris.pipeline.fontes import (
    calcular_content_hash,
    formatar_referencia_citavel,
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


def test_formatar_referencia_citavel_completa():
    assert formatar_referencia_citavel(
        tribunal="TJSP",
        classe="Apelação Cível",
        numero_processo="1135221-60.2022.8.26.0100",
        relator="Caio Marcelo Mendes de Oliveira",
        orgao_julgador="32ª Câmara de Direito Privado",
        data_julgamento=date(2024, 10, 8),
    ) == (
        "TJSP, Apelação Cível 1135221-60.2022.8.26.0100, "
        "Rel. Caio Marcelo Mendes de Oliveira, "
        "32ª Câmara de Direito Privado, j. 08/10/2024"
    )


def test_formatar_referencia_citavel_omite_o_que_falta():
    """Componente ausente sai junto com sua pontuação, sem buraco na frase."""
    assert formatar_referencia_citavel(
        tribunal="TJSP",
        classe=None,
        numero_processo="1135221-60.2022.8.26.0100",
        relator=None,
        orgao_julgador=None,
        data_julgamento=None,
    ) == "TJSP, 1135221-60.2022.8.26.0100"


def test_formatar_referencia_citavel_nao_inventa_tratamento():
    """O dado traz o nome puro; inferir 'Des.'/'Desa.' erraria o gênero."""
    saida = formatar_referencia_citavel(
        tribunal="TJSP",
        classe="Agravo de Instrumento",
        numero_processo="2079286-85.2026.8.26.0000",
        relator="Vito Guglielmi",
        orgao_julgador="6ª Câmara de Direito Privado",
        data_julgamento=date(2026, 6, 26),
    )
    assert "Rel. Vito Guglielmi," in saida
    assert "Des." not in saida
    assert "Desa." not in saida
