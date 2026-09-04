from datetime import date, datetime, timezone

from juris.api.schemas import DecisaoDetalhe


def _detalhe(**extra) -> DecisaoDetalhe:
    base = dict(
        id=1,
        tribunal="TJSP",
        cd_acordao="20819001",
        numero_processo="1135221-60.2022.8.26.0100",
        classe="Apelação Cível",
        assunto=None,
        relator="Caio Marcelo Mendes de Oliveira",
        comarca="São Paulo",
        orgao_julgador="32ª Câmara de Direito Privado",
        data_julgamento=date(2024, 10, 8),
        data_publicacao=date(2024, 10, 10),
        temas=["digital-lgpd"],
        ementa_texto="EMENTA",
        ementa_markdown="EMENTA",
        created_at=datetime(2024, 10, 11, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        url_origem="https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao=20819001&cdForo=0",
        content_hash="sha256:abc",
    )
    return DecisaoDetalhe(**(base | extra))


def test_detalhe_expoe_referencia_citavel():
    assert _detalhe().referencia_citavel.startswith(
        "TJSP, Apelação Cível 1135221-60.2022.8.26.0100, Rel."
    )


def test_detalhe_marca_que_a_url_exige_login():
    assert _detalhe().url_requer_login is True
    assert _detalhe(tribunal="TJXX").url_requer_login is None


def test_retrieved_at_espelha_updated_at():
    d = _detalhe()
    assert d.retrieved_at == d.updated_at
