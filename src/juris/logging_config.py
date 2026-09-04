"""Configuração de log da aplicação.

Escreve em stdout. A rotação fica com o driver json-file do Docker
(max-size/max-file no compose) — em container, RotatingFileHandler duplica
um mecanismo que a plataforma já oferece.
"""

import logging
import sys

_FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configurar_logging(nivel: int = logging.INFO) -> None:
    logging.basicConfig(
        level=nivel,
        format=_FORMATO,
        stream=sys.stdout,
        force=True,
    )
    # O crawl4ai emite três linhas por página coletada ([FETCH], [SCRAPE],
    # [COMPLETE]). Eram a maior parte dos 13 MB do log anterior e não têm
    # valor operacional: o que interessa já sai no INFO do scheduler.
    logging.getLogger("crawl4ai").setLevel(logging.WARNING)
