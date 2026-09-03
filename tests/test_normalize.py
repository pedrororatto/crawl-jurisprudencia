from datetime import date

from juris.pipeline.normalize import (
    dividir_classe_assunto,
    limpar_multilinha,
    limpar_texto,
    parse_data_br,
)


def test_limpar_texto():
    assert limpar_texto("  José \xa0 Augusto \n Martins ") == "José Augusto Martins"
    assert limpar_texto("") is None
    assert limpar_texto(None) is None


def test_limpar_multilinha_preserva_paragrafos():
    texto = "  PRIMEIRO   parágrafo. \n\n\n  Segundo    parágrafo.  "
    assert limpar_multilinha(texto) == "PRIMEIRO parágrafo.\n\nSegundo parágrafo."


def test_parse_data_br():
    assert parse_data_br(" 30/07/2026 ") == date(2026, 7, 30)
    assert parse_data_br("sem data") is None
    assert parse_data_br(None) is None


def test_dividir_classe_assunto():
    assert dividir_classe_assunto("Apelação Cível / Prestação de Serviços") == (
        "Apelação Cível",
        "Prestação de Serviços",
    )
    assert dividir_classe_assunto("Habeas Corpus") == ("Habeas Corpus", None)
    assert dividir_classe_assunto(None) == (None, None)
