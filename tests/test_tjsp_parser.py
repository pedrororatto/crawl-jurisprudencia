from datetime import date
from pathlib import Path

import pytest

from juris.collectors.tjsp_cjsg import parse_pagina, parse_total_resultados

FIXTURE = Path(__file__).parent / "fixtures" / "cjsg_resultado.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_total_resultados(html):
    assert parse_total_resultados(html) == 7252


def test_parse_total_resultados_ausente():
    assert parse_total_resultados("<html><body>sem resultados</body></html>") is None


def test_parse_pagina_extrai_todos_os_campos(html):
    decisoes = parse_pagina(html)
    assert len(decisoes) == 2

    d = decisoes[0]
    assert d.tribunal == "TJSP"
    assert d.cd_acordao == "20787657"
    assert d.numero_processo == "1008204-16.2024.8.26.0506"
    assert d.classe == "Apelação Cível"
    assert d.assunto == "Prestação de Serviços"
    assert d.relator == "José Augusto Genofre Martins"
    assert d.comarca == "Ribeirão Preto"
    assert d.orgao_julgador == "29ª Câmara de Direito Privado"
    assert d.data_julgamento == date(2026, 7, 30)
    assert d.data_publicacao == date(2026, 7, 30)
    # Ementa vem do div sem formatação (texto completo), não do trecho visível.
    assert d.ementa_texto.startswith("APELAÇÃO – PRESTAÇÃO DE SERVIÇOS")
    assert "Juízo de retratação não exercido" in d.ementa_texto
    assert "trecho truncado" not in d.ementa_texto
    assert d.raw_html.startswith('<tr class="fundocinza1">')

    d2 = decisoes[1]
    assert d2.cd_acordao == "20786687"
    assert d2.classe == "Apelação Cível"
    assert d2.assunto == "Bancários"
    assert d2.relator == "Alexandre David Malfatti"
    # Parágrafos da ementa preservados.
    assert "\n\n" in d2.ementa_texto


def test_parse_pagina_sem_resultados():
    assert parse_pagina("<html><body>Não foi encontrado resultado</body></html>") == []
