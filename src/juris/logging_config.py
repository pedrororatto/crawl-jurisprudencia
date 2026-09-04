"""Configuração de log da aplicação.

Escreve em stdout. A rotação fica com o driver json-file do Docker
(max-size/max-file no compose) — em container, RotatingFileHandler duplica
um mecanismo que a plataforma já oferece.
"""

import logging
import re
import sys

_FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# A API do Telegram carrega o token no PATH da URL
# (`api.telegram.org/bot<token>/sendMessage`) — não há variante por header,
# então não dá para tirá-lo de lá. O formato é `<id numérico>:<segredo>`.
_TOKEN_TELEGRAM = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


class RedigirSegredos(logging.Filter):
    """Apaga o token do bot de qualquer linha de log, venha de onde vier.

    O `httpx` registra toda requisição bem-sucedida em INFO com a URL
    completa; sem isto, cada alerta entregue gravaria o token em claro no
    stdout do container, no `logs/watchdog.log` do cron e em todo backup ou
    encaminhamento desses arquivos. Reduzir o nível do logger `httpx` (feito
    abaixo) já resolve esse caminho; este filtro é a rede embaixo, para o
    caso de uma dependência futura registrar a URL em WARNING ou acima, ou
    de alguém logar a URL daqui mesmo.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        mensagem = record.getMessage()
        if "bot" in mensagem:
            redigida = _TOKEN_TELEGRAM.sub("bot<REDIGIDO>", mensagem)
            if redigida != mensagem:
                # Formata na hora: só assim a substituição alcança também o
                # que está nos args, não apenas no template.
                record.msg = redigida
                record.args = ()
        return True


def configurar_logging(nivel: int = logging.INFO) -> None:
    logging.basicConfig(
        level=nivel,
        format=_FORMATO,
        stream=sys.stdout,
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedigirSegredos())
    # O crawl4ai emite três linhas por página coletada ([FETCH], [SCRAPE],
    # [COMPLETE]). Eram a maior parte dos 13 MB do log anterior e não têm
    # valor operacional: o que interessa já sai no INFO do scheduler.
    logging.getLogger("crawl4ai").setLevel(logging.WARNING)
    # O httpx registra `HTTP Request: POST <url completa> "200 OK"` em INFO a
    # cada requisição — e a URL do Telegram contém o token do bot. É o
    # caminho de SUCESSO: o cuidado que alertas.py já toma com `exc_info` só
    # cobre o caminho de exceção.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
