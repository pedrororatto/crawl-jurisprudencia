"""Trava de instância única do scheduler.

Impede duas execuções simultâneas (dois daemons, ou um daemon + um --once/
--backfill-passo manual) — foi exatamente isso que causou um bloqueio por
"suspeita de acesso via robô" no e-SAJ: 4 daemons esquecidos rodando ao mesmo
tempo multiplicaram a taxa de requisições contra o mesmo tribunal.

Usa flock (trava de arquivo do SO): o lock fica preso ao processo e o kernel
libera sozinho se o processo morrer de qualquer jeito (kill -9, queda de
energia), sem precisar de lógica para detectar "lock travado".
"""

import fcntl
import os
import sys
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent.parent.parent / "run" / "scheduler.lock"

# Mantido como referência de módulo: se o file object for coletado pelo GC,
# o descritor fecha e a trava é liberada — por isso não pode ser variável local.
_lock_file = None


def adquirir_trava_unica() -> None:
    """Sai do processo com código 1 se outra instância já estiver rodando."""
    global _lock_file
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(
            f"Já existe uma instância do juris.scheduler rodando (trava: {LOCK_PATH}).\n"
            "Verifique com:  pgrep -fl 'juris.scheduler'\n"
            "Encerre-a antes de rodar de novo:  pkill -f 'juris.scheduler'",
            file=sys.stderr,
        )
        sys.exit(1)
    _lock_file.truncate(0)
    _lock_file.write(str(os.getpid()))
    _lock_file.flush()
