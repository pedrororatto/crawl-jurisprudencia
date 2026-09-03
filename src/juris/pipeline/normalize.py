import re
from datetime import date, datetime

_RE_ESPACOS = re.compile(r"[ \t\xa0]+")


def limpar_texto(s: str | None) -> str | None:
    """Colapsa espaços em texto de uma linha (labels, nomes, datas)."""
    if s is None:
        return None
    s = _RE_ESPACOS.sub(" ", s.replace("\n", " ")).strip()
    return s or None


def limpar_multilinha(s: str | None) -> str | None:
    """Normaliza texto longo (ementa): quebras simples viram espaço,
    linhas em branco viram separador de parágrafo."""
    if s is None:
        return None
    paragrafos = [
        _RE_ESPACOS.sub(" ", p.replace("\n", " ")).strip() for p in re.split(r"\n\s*\n", s)
    ]
    return "\n\n".join(p for p in paragrafos if p) or None


def parse_data_br(s: str | None) -> date | None:
    """dd/mm/aaaa -> date."""
    s = limpar_texto(s)
    if not s:
        return None
    m = re.search(r"\d{2}/\d{2}/\d{4}", s)
    if not m:
        return None
    return datetime.strptime(m.group(), "%d/%m/%Y").date()


def dividir_classe_assunto(s: str | None) -> tuple[str | None, str | None]:
    """'Apelação Cível / Prestação de Serviços' -> ('Apelação Cível', 'Prestação de Serviços')."""
    s = limpar_texto(s)
    if not s:
        return None, None
    partes = s.split("/", 1)
    classe = partes[0].strip() or None
    assunto = partes[1].strip() if len(partes) > 1 else None
    return classe, assunto or None
