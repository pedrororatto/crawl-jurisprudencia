"""Chaves de API: geração, verificação e contagem de uso.

CLI:
    python -m juris.apikeys criar --nome squad --escopo publico
    python -m juris.apikeys listar
    python -m juris.apikeys revogar --nome squad
"""

import argparse
import asyncio
import hashlib
import secrets
from datetime import date, datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from juris.api.deps import get_session
from juris.db.engine import SessionLocal
from juris.db.models import ApiKey
from juris.logging_config import configurar_logging

PREFIXO = "juris_"
# "interno" alcança tudo que "publico" alcança.
_ABRANGENCIA = {"publico": {"publico"}, "interno": {"publico", "interno"}}
_RECUSA = HTTPException(status_code=401, detail="Chave de API ausente ou inválida")


def hash_da_chave(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def gerar_chave() -> tuple[str, str]:
    """Devolve (chave em claro, hash). A chave em claro não é persistida."""
    chave = PREFIXO + secrets.token_urlsafe(24)
    return chave, hash_da_chave(chave)


async def criar_chave(
    session: AsyncSession,
    nome: str,
    escopo: str = "publico",
    limite_diario: int | None = None,
) -> tuple[str, ApiKey]:
    if escopo not in _ABRANGENCIA:
        raise ValueError(f"Escopo {escopo!r} inválido; use {sorted(_ABRANGENCIA)}")
    chave, key_hash = gerar_chave()
    registro = ApiKey(
        nome=nome,
        key_hash=key_hash,
        escopo=escopo,
        ativa=True,
        limite_diario=limite_diario,
    )
    session.add(registro)
    await session.commit()
    await session.refresh(registro)
    return chave, registro


async def _buscar_chave(session: AsyncSession, apresentada: str) -> ApiKey | None:
    """Busca pelo hash e confirma com comparação de tempo constante.

    A busca indexada por hash já é o filtro; o compare_digest fecha a porta
    para o caso patológico de colisão de índice.
    """
    esperado = hash_da_chave(apresentada)
    # populate_existing: sem isto, um objeto já carregado no identity map da
    # sessão (ex.: a revogação mais recente feita por SQL bruto na mesma
    # sessão, como nos testes) fica com o valor antigo de `ativa` em memória
    # — a query bate no banco, mas o ORM devolve o objeto velho em cache.
    registro = await session.scalar(
        select(ApiKey)
        .where(ApiKey.key_hash == esperado)
        .execution_options(populate_existing=True)
    )
    if registro is None:
        return None
    if not secrets.compare_digest(registro.key_hash, esperado):
        return None
    return registro if registro.ativa else None


async def _registrar_uso(session: AsyncSession, registro: ApiKey) -> int:
    """Incrementa e devolve a contagem do dia."""
    hoje = date.today()
    usadas = await session.scalar(
        text(
            """
            INSERT INTO api_key_uso (api_key_id, dia, requisicoes)
            VALUES (:id, :dia, 1)
            ON CONFLICT (api_key_id, dia)
            DO UPDATE SET requisicoes = api_key_uso.requisicoes + 1
            RETURNING requisicoes
            """
        ),
        {"id": registro.id, "dia": hoje},
    )
    registro.ultimo_uso_em = datetime.now(timezone.utc)
    await session.commit()
    return usadas


def exigir_chave(escopo: str = "publico"):
    """Fábrica de dependência FastAPI que exige chave com o escopo dado."""

    async def _dependencia(
        authorization: str | None = Header(default=None),
        session: AsyncSession = Depends(get_session),
    ) -> ApiKey:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise _RECUSA
        registro = await _buscar_chave(session, authorization[7:].strip())
        if registro is None:
            raise _RECUSA
        if escopo not in _ABRANGENCIA[registro.escopo]:
            raise HTTPException(
                status_code=403, detail=f"Esta chave não alcança o escopo {escopo!r}"
            )
        usadas = await _registrar_uso(session, registro)
        if registro.limite_diario is not None and usadas > registro.limite_diario:
            raise HTTPException(
                status_code=429, detail="Limite diário de requisições excedido"
            )
        return registro

    return _dependencia


async def chave_opcional(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> ApiKey | None:
    """Não recusa: apenas informa se há chave válida. Usada por /v1/health.

    Recebe a sessão por injeção como qualquer outra dependência — abrir uma
    sessão própria aqui faria o endpoint escapar do dependency_overrides dos
    testes e ir ao banco real.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return await _buscar_chave(session, authorization[7:].strip())


async def _cli_criar(nome: str, escopo: str, limite_diario: int | None) -> None:
    async with SessionLocal() as session:
        chave, registro = await criar_chave(session, nome, escopo, limite_diario)
    print(f"Chave criada para {registro.nome!r} (escopo {registro.escopo}).")
    print("Guarde agora — ela não será exibida de novo:\n")
    print(f"  {chave}\n")


async def _cli_listar() -> None:
    async with SessionLocal() as session:
        registros = (await session.scalars(select(ApiKey).order_by(ApiKey.nome))).all()
    for r in registros:
        estado = "ativa" if r.ativa else "revogada"
        limite = r.limite_diario if r.limite_diario is not None else "sem limite"
        print(f"{r.nome:<20} {r.escopo:<10} {estado:<10} {limite}")


async def _cli_revogar(nome: str) -> None:
    async with SessionLocal() as session:
        registro = await session.scalar(select(ApiKey).where(ApiKey.nome == nome))
        if registro is None:
            raise SystemExit(f"Chave {nome!r} não existe")
        registro.ativa = False
        await session.commit()
    print(f"Chave {nome!r} revogada.")


def main() -> None:
    configurar_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    p_criar = sub.add_parser("criar")
    p_criar.add_argument("--nome", required=True)
    p_criar.add_argument("--escopo", default="publico", choices=sorted(_ABRANGENCIA))
    p_criar.add_argument("--limite-diario", type=int, default=None)

    sub.add_parser("listar")

    p_revogar = sub.add_parser("revogar")
    p_revogar.add_argument("--nome", required=True)

    args = parser.parse_args()
    if args.comando == "criar":
        asyncio.run(_cli_criar(args.nome, args.escopo, args.limite_diario))
    elif args.comando == "listar":
        asyncio.run(_cli_listar())
    else:
        asyncio.run(_cli_revogar(args.nome))


if __name__ == "__main__":
    main()
