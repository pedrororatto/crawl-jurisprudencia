"""Contrato de fonte verificável.

Reúne o que a Fase 4 do Squad Digital exige de toda decisão servida: uma
URL de origem, um hash do conteúdo e uma referência citável. A regra que
decide o que é coluna e o que é derivado está na spec da onda 0: armazenar
o que é fato sobre a coleta, derivar o que é fato sobre o sistema de origem.
"""

import hashlib

# Molde da URL do acórdão em cada tribunal. Na onda 1 isto migra para
# config/tribunais.yaml, junto com os demais dados por tribunal.
_URL_ACORDAO = {
    "TJSP": "https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao={cd}&cdForo=0",
}

# O e-SAJ redireciona getArquivo.do para verificarLoginArquivo.jsp: o inteiro
# teor só abre em sessão autenticada. Quem consome precisa saber disso antes
# de clicar.
_REQUER_LOGIN = {"TJSP": True}


def calcular_content_hash(ementa_texto: str | None) -> str:
    """Hash do conteúdo servido, para o consumidor provar que a fonte não mudou.

    Aplica-se sobre a ementa, não sobre o raw_html: mudança cosmética de
    markup no tribunal não deve invalidar a fonte.

    Fica em Python, e não numa coluna gerada, porque convert_to() é STABLE e
    o Postgres recusa a expressão de geração. Forçar IMMUTABLE numa função
    que não é seria mentir para o planejador sobre um valor armazenado.
    """
    return "sha256:" + hashlib.sha256((ementa_texto or "").encode("utf-8")).hexdigest()


def montar_url_origem(tribunal: str, cd_acordao: str) -> str | None:
    """URL do acórdão no sistema de origem, ou None para tribunal desconhecido."""
    molde = _URL_ACORDAO.get(tribunal)
    return molde.format(cd=cd_acordao) if molde else None


def url_requer_login(tribunal: str) -> bool | None:
    """True quando a URL de origem exige sessão autenticada.

    Devolve None — e não um palpite — para tribunal fora do mapa.
    """
    return _REQUER_LOGIN.get(tribunal)
