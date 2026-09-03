from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


class BloqueioDetectadoError(RuntimeError):
    """O tribunal apresentou captcha/desafio anti-robô. A coleta para aqui:
    não tentamos burlar bloqueios — o erro sobe para o operador decidir."""


@dataclass
class DecisaoColetada:
    tribunal: str
    cd_acordao: str
    numero_processo: str | None = None
    classe: str | None = None
    assunto: str | None = None
    relator: str | None = None
    comarca: str | None = None
    orgao_julgador: str | None = None
    data_julgamento: date | None = None
    data_publicacao: date | None = None
    ementa_texto: str | None = None
    ementa_markdown: str | None = None
    raw_html: str | None = None


@dataclass
class PaginaResultado:
    pagina: int
    total_resultados: int | None
    decisoes: list[DecisaoColetada] = field(default_factory=list)


class Collector(Protocol):
    """Interface comum dos coletores (TJSP hoje; TST na fase 2)."""

    tribunal: str

    def paginas(
        self,
        busca: str,
        publicacao_inicio: date | None,
        publicacao_fim: date | None,
        max_paginas: int,
        campo: str = "inteiro_teor",
    ) -> AsyncIterator[PaginaResultado]: ...
