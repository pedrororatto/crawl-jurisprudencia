from dataclasses import dataclass
from pathlib import Path

import yaml


CAMPOS_VALIDOS = ("inteiro_teor", "ementa")


@dataclass(frozen=True)
class Tema:
    slug: str
    nome: str
    busca: str
    # Onde a busca é aplicada no CJSG: "inteiro_teor" (padrão) ou "ementa".
    # Use "ementa" para termos que aparecem como boilerplate no corpo de
    # qualquer acórdão (ex.: "assinatura digital").
    campo: str = "inteiro_teor"


def carregar_temas(path: Path) -> list[Tema]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    temas = [Tema(**t) for t in data["temas"]]
    slugs = [t.slug for t in temas]
    if len(slugs) != len(set(slugs)):
        raise ValueError(f"Slugs duplicados em {path}")
    for t in temas:
        if t.campo not in CAMPOS_VALIDOS:
            raise ValueError(f"Tema {t.slug!r}: campo {t.campo!r} inválido {CAMPOS_VALIDOS}")
    return temas
