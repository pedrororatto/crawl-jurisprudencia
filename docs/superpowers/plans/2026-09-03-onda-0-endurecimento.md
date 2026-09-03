# Onda 0 — Endurecimento para operação 24/7 — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar seguro deixar o coletor de jurisprudência rodando sem supervisão numa VPS, sem ampliar fonte nenhuma.

**Architecture:** O coletor, o pipeline e a API existentes permanecem estruturalmente intactos. Acrescentam-se sete camadas ausentes: migrations versionadas, contrato de fonte verificável, log rotacionável, métrica histórica, alerta com dead man's switch externo, autenticação por chave e deploy com backup testado. Nenhuma fila, nenhum coletor novo, nenhum embedding — tudo isso é onda 1 ou posterior.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (asyncio) + asyncpg, Alembic, FastAPI, Pydantic v2, PostgreSQL 16, pytest + pytest-asyncio, Docker Compose, Caddy, uv.

**Spec:** `docs/superpowers/specs/2026-09-03-onda-0-endurecimento-design.md`

## Global Constraints

- Python `>=3.12,<3.13`. Dependências gerenciadas por `uv`; toda adição vai em `pyproject.toml` seguida de `uv sync`.
- Prefixo de variável de ambiente: `JURIS_`. Configuração nova entra em `src/juris/config.py` na classe `Settings`.
- `asyncio_mode = "auto"` já está em `pyproject.toml`; testes assíncronos não levam decorator.
- Código, nomes de função, comentários, docstrings e mensagens de commit em português, com acentuação correta. Identificadores técnicos (`content_hash`, `upsert`) permanecem na forma original.
- Nenhum segredo em código, em arquivo versionado ou em log. Token do Telegram e credenciais de backup vêm exclusivamente de variável de ambiente.
- A política existente de **não burlar captcha** é preservada: `BloqueioDetectadoError` continua interrompendo a coleta do tema.
- O comentário em `docker-compose.yml` que explica o bind do Postgres em `127.0.0.1` é preservado.
- Commits frequentes, um por task no mínimo. Trabalho na branch `onda-0-endurecimento`.

**Numeração das migrations:** a §4 da spec agrupou as tabelas novas numa revisão 003. Este plano distribui cada migration na task cuja entrega precisa dela, conforme a orientação de decomposição — resultando em 001 baseline, 002 colunas, 003 slugs, 004 alertas, 005 métricas, 006 chaves. É refinamento de implementação, não mudança de design.

---

### Task 1: Alembic, banco de teste e trava contra drift

**Files:**
- Modify: `pyproject.toml` (dependências)
- Create: `alembic.ini`
- Create: `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`
- Create: `alembic/versions/001_baseline.py`
- Create: `tests/conftest.py`
- Create: `tests/test_migrations.py`
- Delete: `src/juris/db/init.py`
- Modify: `README.md:60-63` (comando de criação de tabelas)

**Interfaces:**
- Consumes: `juris.config.settings.database_url`, `juris.db.models.Base`
- Produces: fixture `banco_de_teste` (str, URL do banco de teste com migrations aplicadas) e fixture `session` (`AsyncSession` isolada por transação), consumidas por todas as tasks seguintes que tocam banco.

- [ ] **Step 1: Adicionar Alembic às dependências**

Em `pyproject.toml`, na lista `dependencies`, após `"apscheduler>=3.10,<4",`:

```toml
    "alembic>=1.13",
```

Rodar: `uv sync`

- [ ] **Step 2: Inicializar o Alembic com template assíncrono**

Run: `uv run alembic init -t async alembic`

- [ ] **Step 3: Apontar o Alembic para as configurações do projeto**

Em `alembic.ini`, remover a linha `sqlalchemy.url = ...` (a URL vem de `Settings`).

Substituir o conteúdo de `alembic/env.py` por:

```python
"""Ambiente do Alembic: lê a URL de juris.config e o metadata de juris.db.models."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from juris.config import settings
from juris.db.models import Base

config = context.config
# Só cai para settings se ninguém já definiu a URL. O conftest dos testes
# aponta para o banco juris_test antes de chamar command.upgrade, e
# sobrescrever aqui destruiria esse apontamento.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Escrever a revisão 001 (baseline)**

Criar `alembic/versions/001_baseline.py`. A extensão `pg_trgm` precisa existir **antes** do índice trigram — é a razão de o `op.execute` vir primeiro.

```python
"""Baseline: schema tal como db/init.py o criava.

Revision ID: 001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

_BUSCA = (
    "to_tsvector('portuguese', "
    "coalesce(ementa_texto, '') || ' ' || coalesce(classe, '') || ' ' || "
    "coalesce(assunto, '') || ' ' || coalesce(relator, '') || ' ' || "
    "coalesce(orgao_julgador, '') || ' ' || coalesce(comarca, ''))"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "decisoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("cd_acordao", sa.String(length=40), nullable=False),
        sa.Column("numero_processo", sa.String(length=40), nullable=True),
        sa.Column("classe", sa.String(length=200), nullable=True),
        sa.Column("assunto", sa.String(length=400), nullable=True),
        sa.Column("relator", sa.String(length=200), nullable=True),
        sa.Column("comarca", sa.String(length=200), nullable=True),
        sa.Column("orgao_julgador", sa.String(length=200), nullable=True),
        sa.Column("data_julgamento", sa.Date(), nullable=True),
        sa.Column("data_publicacao", sa.Date(), nullable=True),
        sa.Column("ementa_texto", sa.Text(), nullable=True),
        sa.Column("ementa_markdown", sa.Text(), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column(
            "temas",
            postgresql.ARRAY(sa.String()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "busca",
            postgresql.TSVECTOR(),
            sa.Computed(_BUSCA, persisted=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tribunal", "cd_acordao", name="uq_decisoes_tribunal_cd_acordao"
        ),
    )
    op.create_index("ix_decisoes_tribunal", "decisoes", ["tribunal"])
    op.create_index("ix_decisoes_numero_processo", "decisoes", ["numero_processo"])
    op.create_index("ix_decisoes_relator", "decisoes", ["relator"])
    op.create_index("ix_decisoes_orgao_julgador", "decisoes", ["orgao_julgador"])
    op.create_index("ix_decisoes_data_julgamento", "decisoes", ["data_julgamento"])
    op.create_index("ix_decisoes_data_publicacao", "decisoes", ["data_publicacao"])
    op.create_index("ix_decisoes_busca", "decisoes", ["busca"], postgresql_using="gin")
    op.create_index("ix_decisoes_temas", "decisoes", ["temas"], postgresql_using="gin")
    op.create_index(
        "ix_decisoes_ementa_trgm",
        "decisoes",
        ["ementa_texto"],
        postgresql_using="gin",
        postgresql_ops={"ementa_texto": "gin_trgm_ops"},
    )

    op.create_table(
        "backfill_progresso",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("proximo_mes", sa.Date(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tribunal"),
    )

    op.create_table(
        "execucoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("tema_slug", sa.String(length=60), nullable=False),
        sa.Column("watermark", sa.Date(), nullable=True),
        sa.Column("ultima_execucao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_qtd", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tribunal", "tema_slug", name="uq_execucoes_tribunal_tema"),
    )


def downgrade() -> None:
    op.drop_table("execucoes")
    op.drop_table("backfill_progresso")
    op.drop_table("decisoes")
```

- [ ] **Step 5: Criar o conftest com banco de teste**

Criar `tests/conftest.py`:

```python
"""Banco de teste.

Recria o banco `juris_test` do zero a cada sessão de pytest e aplica as
migrations. Exige o Postgres de desenvolvimento no ar:

    docker compose up -d db

A fixture `session` entrega uma AsyncSession presa a uma transação externa
que é revertida ao fim de cada teste — commits dentro do código sob teste
não vazam para o teste seguinte.
"""

import asyncio
import os

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ADMIN_URL = os.environ.get(
    "JURIS_TEST_ADMIN_URL", "postgresql+asyncpg://juris:juris@localhost:5432/postgres"
)
TEST_URL = os.environ.get(
    "JURIS_TEST_DATABASE_URL",
    "postgresql+asyncpg://juris:juris@localhost:5432/juris_test",
)


async def _recriar_banco() -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS juris_test WITH (FORCE)"))
        await conn.execute(text("CREATE DATABASE juris_test"))
    await engine.dispose()


@pytest.fixture(scope="session")
def banco_de_teste() -> str:
    # Fixture síncrona de propósito: command.upgrade abre o próprio event loop
    # dentro do env.py assíncrono do Alembic, e aninhar loops quebraria.
    # Exige pytest rodando a partir da raiz do repositório (alembic.ini é
    # caminho relativo).
    asyncio.run(_recriar_banco())
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest_asyncio.fixture
async def session(banco_de_teste: str):
    engine = create_async_engine(banco_de_teste)
    async with engine.connect() as conn:
        trans = await conn.begin()
        # join_transaction_mode="create_savepoint" é obrigatório: sem ele, um
        # session.commit() dentro do código sob teste encerra a transação
        # externa e o rollback abaixo não desfaz nada — os testes passariam a
        # sujar uns aos outros de forma difícil de diagnosticar.
        sessao = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield sessao
        await sessao.close()
        await trans.rollback()
    await engine.dispose()
```

- [ ] **Step 6: Escrever o teste de drift (vai falhar)**

Criar `tests/test_migrations.py`:

```python
"""A trava que impede models.py e as migrations divergirem."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from juris.db.models import Base


async def test_migrations_conferem_com_os_modelos(banco_de_teste):
    """Depois de `upgrade head`, um autogenerate não pode ter o que fazer."""
    engine = create_async_engine(banco_de_teste)

    def _diferencas(conn):
        contexto = MigrationContext.configure(conn)
        return compare_metadata(contexto, Base.metadata)

    async with engine.connect() as conn:
        diffs = await conn.run_sync(_diferencas)
    await engine.dispose()

    assert diffs == [], f"Schema divergiu de models.py: {diffs}"
```

- [ ] **Step 7: Rodar o teste e conferir que ele falha ou passa por motivo certo**

Run: `uv run pytest tests/test_migrations.py -v`

Expected: PASS se a revisão 001 estiver fiel a `models.py`. Se FAIL, a saída lista exatamente o que difere — ajustar `001_baseline.py` até o diff vir vazio. **Não ajustar `models.py` para caber na migration**; a migration é que deve reproduzir o modelo.

- [ ] **Step 8: Marcar o banco de desenvolvimento existente como já migrado**

O banco atual tem 49.036 registros criados por `create_all`. O `stamp` registra a posição sem executar DDL.

Run:
```bash
uv run alembic stamp 001
uv run alembic current
```
Expected: `001 (head)`

Conferir que nada se perdeu:
```bash
docker compose exec -T db psql -U juris -d juris -c "select count(*) from decisoes;"
```
Expected: `49036`

- [ ] **Step 9: Remover o db/init.py e atualizar o README**

Run: `rm src/juris/db/init.py`

Em `README.md`, substituir a linha `uv run python -m juris.db.init` por:

```
uv run alembic upgrade head
```

- [ ] **Step 10: Rodar a suíte inteira**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 11: Commit**

```bash
git checkout -b onda-0-endurecimento
git add pyproject.toml uv.lock alembic.ini alembic tests/conftest.py tests/test_migrations.py README.md
git rm src/juris/db/init.py
git commit -m "Alembic no lugar de create_all, com trava contra drift de schema"
```

---

### Task 2: `url_origem` e `content_hash`

**Files:**
- Create: `src/juris/pipeline/fontes.py`
- Create: `tests/test_fontes.py`
- Modify: `src/juris/db/models.py` (classe `Decisao`)
- Modify: `src/juris/pipeline/ingest.py` (SQL de upsert e função `ingerir`)
- Create: `alembic/versions/002_url_origem_content_hash.py`

**Interfaces:**
- Consumes: fixture `session` (Task 1)
- Produces:
  - `calcular_content_hash(ementa_texto: str | None) -> str`
  - `montar_url_origem(tribunal: str, cd_acordao: str) -> str | None`
  - `url_requer_login(tribunal: str) -> bool | None`
  - Colunas `decisoes.url_origem` e `decisoes.content_hash`, consumidas pela Task 3.

- [ ] **Step 1: Escrever os testes do módulo de fontes**

Criar `tests/test_fontes.py`:

```python
import hashlib

from juris.pipeline.fontes import (
    calcular_content_hash,
    montar_url_origem,
    url_requer_login,
)


def test_calcular_content_hash():
    esperado = hashlib.sha256("EMENTA".encode("utf-8")).hexdigest()
    assert calcular_content_hash("EMENTA") == f"sha256:{esperado}"


def test_calcular_content_hash_trata_nulo_como_vazio():
    vazio = hashlib.sha256(b"").hexdigest()
    assert calcular_content_hash(None) == f"sha256:{vazio}"
    assert calcular_content_hash("") == f"sha256:{vazio}"


def test_montar_url_origem_tjsp():
    assert montar_url_origem("TJSP", "20819001") == (
        "https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao=20819001&cdForo=0"
    )


def test_montar_url_origem_tribunal_desconhecido_nao_chuta():
    assert montar_url_origem("TJXX", "123") is None


def test_url_requer_login():
    assert url_requer_login("TJSP") is True
    assert url_requer_login("TJXX") is None
```

- [ ] **Step 2: Rodar e verificar que falha**

Run: `uv run pytest tests/test_fontes.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'juris.pipeline.fontes'`

- [ ] **Step 3: Implementar o módulo de fontes**

Criar `src/juris/pipeline/fontes.py`:

```python
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
```

- [ ] **Step 4: Rodar e verificar que passa**

Run: `uv run pytest tests/test_fontes.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Acrescentar as colunas ao modelo**

Em `src/juris/db/models.py`, na classe `Decisao`, após a linha `raw_html: Mapped[str | None] = mapped_column(Text)`:

```python
    # URL do acórdão no sistema de origem, gravada no momento da coleta.
    url_origem: Mapped[str | None] = mapped_column(Text)
    # sha256 da ementa, no formato "sha256:<hex>". Calculado no ingest.
    content_hash: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 6: Escrever a migração 002**

Criar `alembic/versions/002_url_origem_content_hash.py`:

```python
"""url_origem e content_hash em decisoes, com backfill.

Revision ID: 002
Revises: 001
"""

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisoes", sa.Column("url_origem", sa.Text(), nullable=True))
    op.add_column("decisoes", sa.Column("content_hash", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE decisoes
           SET url_origem = 'https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao='
                            || cd_acordao || '&cdForo=0'
         WHERE tribunal = 'TJSP' AND url_origem IS NULL
        """
    )
    # Mesmo dígito que juris.pipeline.fontes.calcular_content_hash produz;
    # tests/test_fontes.py::test_hash_sql_confere_com_python garante isso.
    op.execute(
        """
        UPDATE decisoes
           SET content_hash = 'sha256:'
               || encode(sha256(convert_to(coalesce(ementa_texto,''),'UTF8')),'hex')
         WHERE content_hash IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("decisoes", "content_hash")
    op.drop_column("decisoes", "url_origem")
```

- [ ] **Step 7: Escrever o teste que amarra o hash do SQL ao do Python**

Acrescentar a `tests/test_fontes.py`:

```python
from sqlalchemy import text

from juris.pipeline.fontes import calcular_content_hash


async def test_hash_sql_confere_com_python(session):
    """O backfill em SQL e o ingest em Python precisam produzir o mesmo dígito."""
    for ementa in ["RESPONSABILIDADE CIVIL — provedor.", "acentuação çãõ", ""]:
        no_banco = await session.scalar(
            text(
                "SELECT 'sha256:' || encode("
                "sha256(convert_to(coalesce(:e,''),'UTF8')),'hex')"
            ),
            {"e": ementa},
        )
        assert no_banco == calcular_content_hash(ementa)
```

- [ ] **Step 8: Aplicar a migração e rodar os testes**

Run:
```bash
uv run alembic upgrade head
uv run pytest tests/test_fontes.py tests/test_migrations.py -v
```
Expected: PASS.

Conferir o backfill:
```bash
docker compose exec -T db psql -U juris -d juris -c "
select count(*) total,
       count(url_origem) com_url,
       count(content_hash) com_hash
  from decisoes;"
```
Expected: as três colunas com `49036`.

- [ ] **Step 9: Gravar os campos no ingest**

Em `src/juris/pipeline/ingest.py`, no `_SQL_UPSERT`, acrescentar `url_origem, content_hash` à lista de colunas do `INSERT`, `:url_origem, :content_hash` aos valores, e ao bloco `DO UPDATE SET`:

```sql
        url_origem = EXCLUDED.url_origem,
        content_hash = EXCLUDED.content_hash,
```

E na função `ingerir`, substituir a montagem de `params` por:

```python
    for d in decisoes:
        params = asdict(d) | {
            "temas": [tema_slug],
            "url_origem": montar_url_origem(d.tribunal, d.cd_acordao),
            "content_hash": calcular_content_hash(d.ementa_texto),
        }
```

Acrescentar ao topo do arquivo:

```python
from juris.pipeline.fontes import calcular_content_hash, montar_url_origem
```

- [ ] **Step 10: Escrever o teste do ingest**

Criar `tests/test_ingest.py`:

```python
from sqlalchemy import text

from juris.collectors.base import DecisaoColetada
from juris.pipeline.fontes import calcular_content_hash
from juris.pipeline.ingest import ingerir


async def test_ingest_grava_url_de_origem_e_hash(session):
    decisao = DecisaoColetada(
        tribunal="TJSP",
        cd_acordao="99999001",
        numero_processo="1234567-89.2024.8.26.0100",
        ementa_texto="RESPONSABILIDADE CIVIL — marketplace.",
    )

    novas, atualizadas = await ingerir(session, [decisao], "digital-comercio-eletronico")
    assert (novas, atualizadas) == (1, 0)

    linha = (
        await session.execute(
            text(
                "SELECT url_origem, content_hash FROM decisoes "
                "WHERE tribunal='TJSP' AND cd_acordao='99999001'"
            )
        )
    ).one()

    assert linha.url_origem == (
        "https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao=99999001&cdForo=0"
    )
    assert linha.content_hash == calcular_content_hash(
        "RESPONSABILIDADE CIVIL — marketplace."
    )
```

- [ ] **Step 11: Rodar os testes**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 12: Commit**

```bash
git add src/juris/pipeline/fontes.py src/juris/pipeline/ingest.py src/juris/db/models.py \
        alembic/versions/002_url_origem_content_hash.py tests/test_fontes.py tests/test_ingest.py
git commit -m "url_origem e content_hash: contrato de fonte verificável da Fase 4"
```

---

### Task 3: Campos derivados na resposta da API

**Files:**
- Modify: `src/juris/pipeline/fontes.py` (acrescentar `formatar_referencia_citavel`)
- Modify: `tests/test_fontes.py`
- Modify: `src/juris/api/schemas.py`
- Create: `tests/test_schemas.py`

**Interfaces:**
- Consumes: `calcular_content_hash`, `montar_url_origem`, `url_requer_login` (Task 2)
- Produces: `formatar_referencia_citavel(...) -> str`; campos `referencia_citavel`, `url_origem`, `url_requer_login`, `content_hash`, `retrieved_at` em `DecisaoDetalhe`.

- [ ] **Step 1: Escrever os testes da referência citável**

Acrescentar a `tests/test_fontes.py`:

```python
from datetime import date

from juris.pipeline.fontes import formatar_referencia_citavel


def test_formatar_referencia_citavel_completa():
    assert formatar_referencia_citavel(
        tribunal="TJSP",
        classe="Apelação Cível",
        numero_processo="1135221-60.2022.8.26.0100",
        relator="Caio Marcelo Mendes de Oliveira",
        orgao_julgador="32ª Câmara de Direito Privado",
        data_julgamento=date(2024, 10, 8),
    ) == (
        "TJSP, Apelação Cível 1135221-60.2022.8.26.0100, "
        "Rel. Caio Marcelo Mendes de Oliveira, "
        "32ª Câmara de Direito Privado, j. 08/10/2024"
    )


def test_formatar_referencia_citavel_omite_o_que_falta():
    """Componente ausente sai junto com sua pontuação, sem buraco na frase."""
    assert formatar_referencia_citavel(
        tribunal="TJSP",
        classe=None,
        numero_processo="1135221-60.2022.8.26.0100",
        relator=None,
        orgao_julgador=None,
        data_julgamento=None,
    ) == "TJSP, 1135221-60.2022.8.26.0100"


def test_formatar_referencia_citavel_nao_inventa_tratamento():
    """O dado traz o nome puro; inferir 'Des.'/'Desa.' erraria o gênero."""
    saida = formatar_referencia_citavel(
        tribunal="TJSP",
        classe="Agravo de Instrumento",
        numero_processo="2079286-85.2026.8.26.0000",
        relator="Vito Guglielmi",
        orgao_julgador="6ª Câmara de Direito Privado",
        data_julgamento=date(2026, 6, 26),
    )
    assert "Rel. Vito Guglielmi," in saida
    assert "Des." not in saida
    assert "Desa." not in saida
```

- [ ] **Step 2: Rodar e verificar que falha**

Run: `uv run pytest tests/test_fontes.py -v`
Expected: FAIL com `ImportError: cannot import name 'formatar_referencia_citavel'`

- [ ] **Step 3: Implementar a formatação**

Acrescentar a `src/juris/pipeline/fontes.py`:

```python
from datetime import date


def formatar_referencia_citavel(
    tribunal: str,
    classe: str | None,
    numero_processo: str | None,
    relator: str | None,
    orgao_julgador: str | None,
    data_julgamento: date | None,
) -> str:
    """Referência textual que prova a origem sem depender de sessão no e-SAJ.

    Formato: TRIBUNAL, Classe Número, Rel. Nome, Órgão, j. DD/MM/AAAA

    Componentes ausentes saem junto com sua pontuação. O nome do relator é
    reproduzido como está: o e-SAJ não traz o tratamento, e inferi-lo erraria
    o gênero de parte dos relatores. Não inventar o que a fonte não diz é a
    mesma regra que governa o excerpt.
    """
    partes: list[str] = [tribunal]

    processo = " ".join(p for p in (classe, numero_processo) if p)
    if processo:
        partes.append(processo)
    if relator:
        partes.append(f"Rel. {relator}")
    if orgao_julgador:
        partes.append(orgao_julgador)
    if data_julgamento:
        partes.append(f"j. {data_julgamento.strftime('%d/%m/%Y')}")

    return ", ".join(partes)
```

- [ ] **Step 4: Rodar e verificar que passa**

Run: `uv run pytest tests/test_fontes.py -v`
Expected: PASS.

- [ ] **Step 5: Expor os campos no schema**

Em `src/juris/api/schemas.py`, substituir a classe `DecisaoDetalhe` por:

```python
class DecisaoDetalhe(DecisaoResumo):
    cd_acordao: str
    ementa_markdown: str | None
    created_at: datetime
    updated_at: datetime

    url_origem: str | None
    content_hash: str | None

    @computed_field
    @property
    def url_requer_login(self) -> bool | None:
        """None — e não um palpite — para tribunal fora do mapa."""
        return _url_requer_login(self.tribunal)

    @computed_field
    @property
    def referencia_citavel(self) -> str:
        return formatar_referencia_citavel(
            tribunal=self.tribunal,
            classe=self.classe,
            numero_processo=self.numero_processo,
            relator=self.relator,
            orgao_julgador=self.orgao_julgador,
            data_julgamento=self.data_julgamento,
        )

    @computed_field
    @property
    def retrieved_at(self) -> datetime:
        """Quando o conteúdo servido foi visto na origem pela última vez.

        É o updated_at: o upsert o reescreve a cada rodada que revê a decisão.
        Coluna própria seria duplicata.
        """
        return self.updated_at
```

E no topo do arquivo:

```python
from pydantic import BaseModel, ConfigDict, computed_field

from juris.pipeline.fontes import formatar_referencia_citavel
from juris.pipeline.fontes import url_requer_login as _url_requer_login
```

- [ ] **Step 6: Escrever o teste do schema**

Criar `tests/test_schemas.py`:

```python
from datetime import date, datetime, timezone

from juris.api.schemas import DecisaoDetalhe


def _detalhe(**extra) -> DecisaoDetalhe:
    base = dict(
        id=1,
        tribunal="TJSP",
        cd_acordao="20819001",
        numero_processo="1135221-60.2022.8.26.0100",
        classe="Apelação Cível",
        assunto=None,
        relator="Caio Marcelo Mendes de Oliveira",
        comarca="São Paulo",
        orgao_julgador="32ª Câmara de Direito Privado",
        data_julgamento=date(2024, 10, 8),
        data_publicacao=date(2024, 10, 10),
        temas=["digital-lgpd"],
        ementa_texto="EMENTA",
        ementa_markdown="EMENTA",
        created_at=datetime(2024, 10, 11, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        url_origem="https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao=20819001&cdForo=0",
        content_hash="sha256:abc",
    )
    return DecisaoDetalhe(**(base | extra))


def test_detalhe_expoe_referencia_citavel():
    assert _detalhe().referencia_citavel.startswith(
        "TJSP, Apelação Cível 1135221-60.2022.8.26.0100, Rel."
    )


def test_detalhe_marca_que_a_url_exige_login():
    assert _detalhe().url_requer_login is True
    assert _detalhe(tribunal="TJXX").url_requer_login is None


def test_retrieved_at_espelha_updated_at():
    d = _detalhe()
    assert d.retrieved_at == d.updated_at
```

- [ ] **Step 7: Rodar os testes**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 8: Commit**

```bash
git add src/juris/pipeline/fontes.py src/juris/api/schemas.py tests/test_fontes.py tests/test_schemas.py
git commit -m "Referência citável, url_requer_login e retrieved_at na resposta da API"
```

---

### Task 4: Prefixo de área nos slugs de tema

**Files:**
- Modify: `config/temas.yaml` (10 slugs)
- Create: `alembic/versions/003_prefixo_area_slugs.py`
- Create: `tests/test_prefixo_area.py`
- Modify: `README.md` (exemplos com `--tema lgpd`)

**Interfaces:**
- Consumes: fixture `session` (Task 1)
- Produces: convenção de slug `digital-<tema>`, usada por todas as tasks seguintes que citam slug.

- [ ] **Step 1: Renomear os slugs no YAML**

Em `config/temas.yaml`, prefixar cada `slug` com `digital-`: `lgpd` → `digital-lgpd`, `marco-civil` → `digital-marco-civil`, `vazamento-dados` → `digital-vazamento-dados`, `fraude-eletronica` → `digital-fraude-eletronica`, `crimes-ciberneticos` → `digital-crimes-ciberneticos`, `remocao-conteudo` → `digital-remocao-conteudo`, `perfil-falso` → `digital-perfil-falso`, `imagens-intimas` → `digital-imagens-intimas`, `comercio-eletronico` → `digital-comercio-eletronico`, `assinatura-eletronica` → `digital-assinatura-eletronica`.

Acrescentar ao comentário do topo do arquivo:

```yaml
# Convenção de slug: <area>-<tema>. O prefixo de área evita colisão quando
# outras áreas do direito entrarem — "dano-moral" e "prescrição" existem em
# várias delas, e o slug fica gravado no array `temas` de cada decisão.
```

- [ ] **Step 2: Escrever a migração 003**

Criar `alembic/versions/003_prefixo_area_slugs.py`:

```python
"""Prefixo de área nos slugs de tema: lgpd -> digital-lgpd.

Feito enquanto existe uma única área e nenhum consumidor usa o contrato da
API — o momento mais barato possível.

Revision ID: 003
Revises: 002
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # O guard NOT LIKE torna a migração inofensiva se reexecutada: nada vira
    # digital-digital-lgpd.
    op.execute(
        """
        UPDATE decisoes
           SET temas = ARRAY(
                 SELECT DISTINCT
                        CASE WHEN t LIKE 'digital-%' THEN t ELSE 'digital-' || t END
                   FROM unnest(temas) AS t
                  ORDER BY 1)
        """
    )
    op.execute(
        "UPDATE execucoes SET tema_slug = 'digital-' || tema_slug "
        "WHERE tema_slug NOT LIKE 'digital-%'"
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE decisoes
           SET temas = ARRAY(
                 SELECT DISTINCT regexp_replace(t, '^digital-', '')
                   FROM unnest(temas) AS t
                  ORDER BY 1)
        """
    )
    op.execute("UPDATE execucoes SET tema_slug = regexp_replace(tema_slug, '^digital-', '')")
```

- [ ] **Step 3: Escrever o teste**

Criar `tests/test_prefixo_area.py`:

```python
from pathlib import Path

from sqlalchemy import text

from juris.temas import carregar_temas


def test_todos_os_slugs_do_yaml_tem_prefixo_de_area():
    temas = carregar_temas(Path("config/temas.yaml"))
    assert temas, "temas.yaml não pode estar vazio"
    sem_prefixo = [t.slug for t in temas if not t.slug.startswith("digital-")]
    assert sem_prefixo == [], f"slugs sem prefixo de área: {sem_prefixo}"


async def test_migracao_de_slug_e_idempotente(session):
    """Reexecutar não pode produzir digital-digital-lgpd."""
    await session.execute(
        text(
            "INSERT INTO decisoes (tribunal, cd_acordao, temas) "
            "VALUES ('TJSP', '99999002', ARRAY['digital-lgpd'])"
        )
    )
    await session.execute(
        text(
            """
            UPDATE decisoes
               SET temas = ARRAY(
                     SELECT DISTINCT
                            CASE WHEN t LIKE 'digital-%' THEN t
                                 ELSE 'digital-' || t END
                       FROM unnest(temas) AS t ORDER BY 1)
             WHERE cd_acordao = '99999002'
            """
        )
    )
    temas = await session.scalar(
        text("SELECT temas FROM decisoes WHERE cd_acordao = '99999002'")
    )
    assert temas == ["digital-lgpd"]
```

- [ ] **Step 4: Rodar e verificar que o teste do YAML falha se o Step 1 não foi feito**

Run: `uv run pytest tests/test_prefixo_area.py -v`
Expected: PASS se o Step 1 foi aplicado; caso contrário FAIL listando os slugs sem prefixo.

- [ ] **Step 5: Registrar a contagem por tema ANTES de migrar**

Run:
```bash
docker compose exec -T db psql -U juris -d juris -c "
select unnest(temas) tema, count(*) from decisoes group by 1 order by 1;" \
  > /tmp/temas-antes.txt
cat /tmp/temas-antes.txt
```

- [ ] **Step 6: Aplicar a migração e comparar**

Run:
```bash
uv run alembic upgrade head
docker compose exec -T db psql -U juris -d juris -c "
select unnest(temas) tema, count(*) from decisoes group by 1 order by 1;"
```
Expected: os mesmos 10 temas com as mesmas contagens de `/tmp/temas-antes.txt`, cada nome agora com `digital-` na frente. A soma total precisa ser idêntica.

- [ ] **Step 7: Atualizar os exemplos do README**

Em `README.md`, trocar `--tema lgpd` por `--tema digital-lgpd` em todas as ocorrências.

- [ ] **Step 8: Rodar a suíte**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 9: Commit**

```bash
git add config/temas.yaml alembic/versions/003_prefixo_area_slugs.py \
        tests/test_prefixo_area.py README.md
git commit -m "Prefixo de área nos slugs de tema, preparando outras áreas do direito"
```

---

### Task 5: Log em stdout e silêncio do crawl4ai

**Files:**
- Create: `src/juris/logging_config.py`
- Modify: `src/juris/scheduler.py` (função `main`)
- Modify: `src/juris/collectors/tjsp_cjsg.py` (`AsyncWebCrawler` e `CrawlerRunConfig`)
- Modify: `docker-compose.yml` (driver de log)

**Interfaces:**
- Produces: `configurar_logging() -> None`, chamada por `scheduler.main` e reutilizada pelo watchdog (Task 8).

**Nota sobre testabilidade:** o silêncio do crawl4ai não é verificável por teste unitário — as linhas `[FETCH]`/`[SCRAPE]`/`[COMPLETE]` vêm do logger próprio da biblioteca, que escreve direto em stdout, e reproduzi-las exigiria uma coleta real contra o tribunal. A verificação é manual, no Step 5. Fingir um teste unitário aqui seria pior que assumir a verificação manual.

- [ ] **Step 1: Criar o módulo de configuração de log**

Criar `src/juris/logging_config.py`:

```python
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
```

- [ ] **Step 2: Usar no scheduler**

Em `src/juris/scheduler.py`, na função `main`, substituir o bloco `logging.basicConfig(...)` por:

```python
    configurar_logging()
```

E acrescentar ao topo:

```python
from juris.logging_config import configurar_logging
```

- [ ] **Step 3: Desligar o verbose do crawl4ai no coletor**

Em `src/juris/collectors/tjsp_cjsg.py`, no método `paginas`, o `AsyncWebCrawler` já recebe `BrowserConfig(headless=True, verbose=False)`. Acrescentar `verbose=False` também ao `CrawlerRunConfig`:

```python
            run_config = CrawlerRunConfig(
                session_id=session_id,
                cache_mode=CacheMode.BYPASS,
                page_timeout=120_000,
                verbose=False,
            )
```

- [ ] **Step 4: Configurar rotação no compose de desenvolvimento**

Em `docker-compose.yml`, acrescentar a cada um dos serviços `api` e `crawler`:

```yaml
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
```

- [ ] **Step 5: Verificar manualmente o volume do log**

Run:
```bash
uv run python -m juris.scheduler --once --tema digital-imagens-intimas \
  --max-paginas 2 --desde 2026-08-01 --ate 2026-08-31 2>&1 | tee /tmp/rodada.log
wc -l /tmp/rodada.log
grep -c "\[FETCH\]\|\[SCRAPE\]\|\[COMPLETE\]" /tmp/rodada.log || echo "0 linhas de ruído"
```
Expected: dezenas de linhas no total, e zero linhas de `[FETCH]`/`[SCRAPE]`/`[COMPLETE]`.

- [ ] **Step 6: Rodar a suíte**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 7: Commit**

```bash
git add src/juris/logging_config.py src/juris/scheduler.py \
        src/juris/collectors/tjsp_cjsg.py docker-compose.yml
git commit -m "Log em stdout com rotação pelo Docker e silêncio do ruído do crawl4ai"
```

---

### Task 6: Alerta por Telegram com cooldown

**Files:**
- Create: `src/juris/alertas.py`
- Create: `alembic/versions/004_alertas_enviados.py`
- Create: `tests/test_alertas.py`
- Modify: `src/juris/db/models.py` (modelo `AlertaEnviado`)
- Modify: `src/juris/config.py` (`Settings`)
- Modify: `.env.example`

**Interfaces:**
- Consumes: fixture `session` (Task 1)
- Produces: `async def alertar(session, chave: str, mensagem: str) -> bool` — devolve `True` se enviou, `False` se suprimiu por cooldown. Consumida pelas Tasks 7 e 8.

- [ ] **Step 1: Acrescentar as configurações**

Em `src/juris/config.py`, na classe `Settings`, após `req_intervalo_segundos`:

```python
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    alerta_cooldown_horas: int = 6
    watchdog_limite_horas: int = 36
```

Em `.env.example`, acrescentar:

```
# Alerta operacional (bot do Telegram). Sem o token, o alerta fica desligado
# com aviso em log — o desenvolvimento local não exige um bot.
JURIS_TELEGRAM_BOT_TOKEN=
JURIS_TELEGRAM_CHAT_ID=
# Janela em que a mesma chave de alerta não se repete.
JURIS_ALERTA_COOLDOWN_HORAS=6
# Sem rodada bem-sucedida por este tempo, o watchdog alerta.
JURIS_WATCHDOG_LIMITE_HORAS=36
```

- [ ] **Step 2: Acrescentar o modelo**

Em `src/juris/db/models.py`, ao fim do arquivo:

```python
class AlertaEnviado(Base):
    """Cooldown de alerta.

    Sem isto o canal se autodestrói: um tribunal bloqueado geraria centenas
    de mensagens até o operador silenciar o bot — e o canal morre justamente
    quando importa.
    """

    __tablename__ = "alertas_enviados"

    chave: Mapped[str] = mapped_column(Text, primary_key=True)
    enviado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 3: Escrever a migração 004**

Criar `alembic/versions/004_alertas_enviados.py`:

```python
"""Tabela de cooldown de alerta.

Revision ID: 004
Revises: 003
"""

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alertas_enviados",
        sa.Column("chave", sa.Text(), nullable=False),
        sa.Column(
            "enviado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("chave"),
    )


def downgrade() -> None:
    op.drop_table("alertas_enviados")
```

- [ ] **Step 4: Escrever os testes (vão falhar)**

Criar `tests/test_alertas.py`:

```python
from datetime import timedelta

import pytest
from sqlalchemy import text

from juris import alertas
from juris.alertas import alertar


@pytest.fixture
def telegram_falso(monkeypatch):
    """Captura o que teria sido enviado, sem tocar a rede."""
    enviadas: list[str] = []

    async def _enviar(mensagem: str) -> None:
        enviadas.append(mensagem)

    monkeypatch.setattr(alertas, "_enviar_telegram", _enviar)
    return enviadas


async def test_primeiro_alerta_envia(session, telegram_falso):
    assert await alertar(session, "bloqueio:TJSP", "e-SAJ bloqueou") is True
    assert telegram_falso == ["e-SAJ bloqueou"]


async def test_segundo_alerta_na_janela_e_suprimido(session, telegram_falso):
    await alertar(session, "bloqueio:TJSP", "primeira")
    assert await alertar(session, "bloqueio:TJSP", "segunda") is False
    assert telegram_falso == ["primeira"]


async def test_chaves_diferentes_nao_se_bloqueiam(session, telegram_falso):
    await alertar(session, "bloqueio:TJSP", "uma")
    assert await alertar(session, "bloqueio:TJCE", "outra") is True
    assert len(telegram_falso) == 2


async def test_alerta_volta_a_enviar_depois_do_cooldown(session, telegram_falso):
    await alertar(session, "bloqueio:TJSP", "primeira")
    await session.execute(
        text(
            "UPDATE alertas_enviados SET enviado_em = now() - interval '7 hours' "
            "WHERE chave = 'bloqueio:TJSP'"
        )
    )
    assert await alertar(session, "bloqueio:TJSP", "de novo") is True


async def test_falha_de_entrega_nao_propaga(session, monkeypatch):
    """Telegram fora do ar não pode derrubar a coleta."""

    async def _explode(mensagem: str) -> None:
        raise RuntimeError("timeout")

    monkeypatch.setattr(alertas, "_enviar_telegram", _explode)
    assert await alertar(session, "bloqueio:TJSP", "msg") is False


async def test_sem_token_configurado_nao_quebra(session, monkeypatch):
    monkeypatch.setattr(alertas.settings, "telegram_bot_token", None)
    assert await alertar(session, "bloqueio:TJSP", "msg") is False
```

- [ ] **Step 5: Rodar e verificar que falha**

Run: `uv run pytest tests/test_alertas.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'juris.alertas'`

- [ ] **Step 6: Implementar o módulo de alertas**

Criar `src/juris/alertas.py`:

```python
"""Alerta operacional por Telegram, com cooldown por chave.

Bloqueio silencioso é o pior modo de falha deste sistema: a coleta para,
ninguém percebe, e semanas depois a base está desatualizada sem que nada
tenha quebrado visivelmente.

O envio é best-effort. Falha de entrega é registrada e engolida — a rede de
segurança é o watchdog (juris.watchdog), que roda fora do processo vigiado.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from juris.config import settings
from juris.db.models import AlertaEnviado

logger = logging.getLogger(__name__)

_TIMEOUT_SEGUNDOS = 10.0


async def _enviar_telegram(mensagem: str) -> None:
    """Envia de fato. Isolado numa função para o teste substituir."""
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS) as client:
        resposta = await client.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": mensagem},
        )
        resposta.raise_for_status()


async def _em_cooldown(session: AsyncSession, chave: str) -> bool:
    limite = datetime.now(timezone.utc) - timedelta(hours=settings.alerta_cooldown_horas)
    registro = await session.scalar(
        select(AlertaEnviado).where(AlertaEnviado.chave == chave)
    )
    return registro is not None and registro.enviado_em > limite


async def alertar(session: AsyncSession, chave: str, mensagem: str) -> bool:
    """Envia o alerta se a chave não estiver em cooldown.

    Devolve True se enviou, False se suprimiu, se não há bot configurado ou
    se a entrega falhou. Nunca levanta exceção: o chamador é uma rodada de
    coleta, e alerta não pode derrubar coleta.
    """
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        logger.warning("Alerta não enviado (bot do Telegram não configurado): %s", mensagem)
        return False

    if await _em_cooldown(session, chave):
        logger.info("Alerta %r suprimido por cooldown", chave)
        return False

    try:
        await _enviar_telegram(mensagem)
    except Exception:
        # Sem exc_info: a mensagem de erro do httpx pode conter a URL, e a URL
        # carrega o token do bot.
        logger.error("Falha ao enviar alerta %r; watchdog é a rede de segurança", chave)
        return False

    registro = await session.get(AlertaEnviado, chave)
    if registro is None:
        session.add(AlertaEnviado(chave=chave, enviado_em=datetime.now(timezone.utc)))
    else:
        registro.enviado_em = datetime.now(timezone.utc)
    await session.commit()
    logger.info("Alerta %r enviado", chave)
    return True
```

- [ ] **Step 7: Acrescentar httpx às dependências**

Em `pyproject.toml`, na lista `dependencies`:

```toml
    "httpx>=0.27",
```

Run: `uv sync`

- [ ] **Step 8: Aplicar a migração e rodar os testes**

Run:
```bash
uv run alembic upgrade head
uv run pytest tests/test_alertas.py tests/test_migrations.py -v
```
Expected: PASS (7 testes de alerta + o de drift).

- [ ] **Step 9: Commit**

```bash
git add src/juris/alertas.py src/juris/db/models.py src/juris/config.py \
        alembic/versions/004_alertas_enviados.py tests/test_alertas.py \
        .env.example pyproject.toml uv.lock
git commit -m "Alerta por Telegram com cooldown por chave"
```

---

### Task 7: Métricas de coleta e gatilhos 1 a 3

**Files:**
- Create: `alembic/versions/005_metricas_coleta.py`
- Create: `tests/test_metricas.py`
- Modify: `src/juris/db/models.py` (modelo `MetricaColeta`)
- Modify: `src/juris/scheduler.py` (`coletar_tema` e `rodada`)

**Interfaces:**
- Consumes: `alertar` (Task 6), fixture `session` (Task 1)
- Produces:
  - `ResultadoTema` (dataclass: `tema_slug: str`, `novas: int`, `atualizadas: int`, `paginas: int`, `resultados: int`, `falhou: bool`, `erro: str | None`, `duracao_s: float`)
  - `coletar_tema(...) -> ResultadoTema` (mudança de assinatura: antes devolvia `int`)
  - `rodada(...) -> list[ResultadoTema]` (antes devolvia `int` de falhas)
  - `async def avaliar_gatilhos(session, tribunal: str, resultados: list[ResultadoTema]) -> None`

- [ ] **Step 1: Acrescentar o modelo**

Em `src/juris/db/models.py`:

```python
class MetricaColeta(Base):
    """Histórico de coleta, uma linha por tema por rodada.

    A tabela `execucoes` responde "está rodando?". Esta responde "está
    rendendo o de sempre?" — sem histórico não há como distinguir uma rodada
    tranquila de um parser quebrado.
    """

    __tablename__ = "metricas_coleta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rodada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tribunal: Mapped[str] = mapped_column(String(10))
    tema_slug: Mapped[str] = mapped_column(String(60))
    novas: Mapped[int] = mapped_column(Integer, default=0)
    atualizadas: Mapped[int] = mapped_column(Integer, default=0)
    paginas: Mapped[int] = mapped_column(Integer, default=0)
    resultados: Mapped[int] = mapped_column(Integer, default=0)
    falhou: Mapped[bool] = mapped_column(Boolean, default=False)
    erro: Mapped[str | None] = mapped_column(Text)
    duracao_s: Mapped[float | None] = mapped_column(Numeric(10, 2))

    __table_args__ = (
        Index("ix_metricas_rodada", "tribunal", "tema_slug", "rodada_em"),
    )
```

Acrescentar aos imports de `sqlalchemy` no topo: `BigInteger`, `Boolean`, `Numeric`.

- [ ] **Step 2: Escrever a migração 005**

Criar `alembic/versions/005_metricas_coleta.py`:

```python
"""Histórico de coleta por tema.

Revision ID: 005
Revises: 004
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metricas_coleta",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "rodada_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("tema_slug", sa.String(length=60), nullable=False),
        sa.Column("novas", sa.Integer(), nullable=False),
        sa.Column("atualizadas", sa.Integer(), nullable=False),
        sa.Column("paginas", sa.Integer(), nullable=False),
        sa.Column("resultados", sa.Integer(), nullable=False),
        sa.Column("falhou", sa.Boolean(), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("duracao_s", sa.Numeric(10, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metricas_rodada", "metricas_coleta", ["tribunal", "tema_slug", "rodada_em"]
    )


def downgrade() -> None:
    op.drop_index("ix_metricas_rodada", table_name="metricas_coleta")
    op.drop_table("metricas_coleta")
```

- [ ] **Step 3: Escrever os testes dos gatilhos (vão falhar)**

Criar `tests/test_metricas.py`:

```python
import pytest
from sqlalchemy import text

from juris import alertas
from juris.scheduler import ResultadoTema, avaliar_gatilhos


@pytest.fixture
def alertas_capturados(monkeypatch):
    enviados: list[tuple[str, str]] = []

    async def _alertar(session, chave, mensagem):
        enviados.append((chave, mensagem))
        return True

    monkeypatch.setattr("juris.scheduler.alertar", _alertar)
    return enviados


def _ok(slug: str, resultados: int = 50) -> ResultadoTema:
    return ResultadoTema(
        tema_slug=slug, novas=0, atualizadas=resultados, paginas=3,
        resultados=resultados, falhou=False, erro=None, duracao_s=12.0,
    )


def _vazio(slug: str) -> ResultadoTema:
    return ResultadoTema(
        tema_slug=slug, novas=0, atualizadas=0, paginas=1,
        resultados=0, falhou=False, erro=None, duracao_s=4.0,
    )


async def test_rodada_sem_decisoes_novas_nao_alerta(session, alertas_capturados):
    """O falso positivo que mataria o canal.

    Numa rodada incremental saudável `novas` é zero o tempo todo: a janela vai
    de watermark-2d até hoje e revê decisões já conhecidas. O gatilho mede
    resultados retornados, não decisões novas.
    """
    await avaliar_gatilhos(session, "TJSP", [_ok("digital-lgpd"), _ok("digital-marco-civil")])
    assert alertas_capturados == []


async def test_rodada_sem_resultado_algum_alerta(session, alertas_capturados):
    await avaliar_gatilhos(
        session, "TJSP", [_vazio("digital-lgpd"), _vazio("digital-marco-civil")]
    )
    assert len(alertas_capturados) == 1
    assert alertas_capturados[0][0] == "rodada-vazia:TJSP"


async def test_bloqueio_alerta_de_imediato(session, alertas_capturados):
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="BloqueioDetectadoError: e-SAJ apresentou desafio",
        duracao_s=3.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho, _ok("digital-marco-civil")])
    chaves = [c for c, _ in alertas_capturados]
    assert "bloqueio:TJSP:digital-lgpd" in chaves


async def test_tres_rodadas_falhas_seguidas_alertam(session, alertas_capturados):
    for _ in range(2):
        await session.execute(
            text(
                "INSERT INTO metricas_coleta "
                "(tribunal, tema_slug, novas, atualizadas, paginas, resultados, falhou) "
                "VALUES ('TJSP','digital-lgpd',0,0,0,0,true)"
            )
        )
    falho = ResultadoTema(
        tema_slug="digital-lgpd", novas=0, atualizadas=0, paginas=0, resultados=0,
        falhou=True, erro="timeout", duracao_s=1.0,
    )
    await avaliar_gatilhos(session, "TJSP", [falho])
    chaves = [c for c, _ in alertas_capturados]
    assert "falhas-seguidas:TJSP:digital-lgpd" in chaves
```

- [ ] **Step 4: Rodar e verificar que falha**

Run: `uv run pytest tests/test_metricas.py -v`
Expected: FAIL com `ImportError: cannot import name 'ResultadoTema'`

- [ ] **Step 5: Refatorar `coletar_tema` para devolver um resultado**

Em `src/juris/scheduler.py`, acrescentar aos imports:

```python
import time
from dataclasses import dataclass

from juris.alertas import alertar
from juris.db.models import MetricaColeta
```

Acrescentar a dataclass antes de `coletar_tema`:

```python
@dataclass
class ResultadoTema:
    """O que uma coleta de tema rendeu. `resultados` é o total devolvido pela
    busca (novas + já conhecidas) — é ele, não `novas`, que distingue uma
    rodada tranquila de um parser quebrado."""

    tema_slug: str
    novas: int
    atualizadas: int
    paginas: int
    resultados: int
    falhou: bool
    erro: str | None
    duracao_s: float
```

Substituir a função `coletar_tema` inteira. A variável de data que se chamava `inicio` passa a ser `inicio_dt`, porque `inicio` agora é o cronômetro:

```python
async def coletar_tema(
    tema: Tema,
    max_paginas: int | None = None,
    desde: date | None = None,
    ate: date | None = None,
) -> ResultadoTema:
    collector = TjspCjsgCollector()
    inicio_dt = await _obter_inicio(collector.tribunal, tema.slug, desde)
    fim = ate or date.today()
    if max_paginas is None:
        # Com período explícito (--desde/--ate) percorre tudo; na rodada
        # incremental vale o teto de configuração.
        max_paginas = TODAS_AS_PAGINAS if ate else settings.max_paginas_por_rodada
    logger.info(
        "Coletando tema %r (%s a %s, até %s páginas): %s",
        tema.slug, inicio_dt, fim, max_paginas, tema.busca,
    )

    inicio = time.monotonic()
    total = novas_total = atualizadas_total = paginas_total = 0
    maior_publicacao: date | None = None
    async for pagina in collector.paginas(tema.busca, inicio_dt, fim, max_paginas, tema.campo):
        async with SessionLocal() as session:
            novas, atualizadas = await ingerir(session, pagina.decisoes, tema.slug)
            await session.commit()
        novas_total += novas
        atualizadas_total += atualizadas
        paginas_total += 1
        total += len(pagina.decisoes)
        datas = [d.data_publicacao for d in pagina.decisoes if d.data_publicacao]
        if datas:
            maior = max(datas)
            maior_publicacao = max(maior_publicacao, maior) if maior_publicacao else maior
        logger.info(
            "Tema %r página %s/%s: %s novas, %s atualizadas",
            tema.slug, pagina.pagina, pagina.total_resultados, novas, atualizadas,
        )

    await _salvar_watermark(collector.tribunal, tema.slug, maior_publicacao, total)
    return ResultadoTema(
        tema_slug=tema.slug,
        novas=novas_total,
        atualizadas=atualizadas_total,
        paginas=paginas_total,
        resultados=total,
        falhou=False,
        erro=None,
        duracao_s=time.monotonic() - inicio,
    )
```

- [ ] **Step 6: Implementar `avaliar_gatilhos`**

Acrescentar a `src/juris/scheduler.py`:

```python
async def _falhas_seguidas(session, tribunal: str, tema_slug: str, quantas: int) -> bool:
    """True se as últimas `quantas` rodadas registradas falharam, todas elas."""
    linhas = (
        await session.execute(
            select(MetricaColeta.falhou)
            .where(
                MetricaColeta.tribunal == tribunal,
                MetricaColeta.tema_slug == tema_slug,
            )
            .order_by(MetricaColeta.rodada_em.desc())
            .limit(quantas)
        )
    ).scalars().all()
    return len(linhas) == quantas and all(linhas)


async def avaliar_gatilhos(session, tribunal: str, resultados: list[ResultadoTema]) -> None:
    """Gatilhos 1 a 3 do alerta. O gatilho 4 vive no juris.watchdog, fora
    deste processo: se ele morrer, não avisa que morreu."""
    if not resultados:
        return

    for r in resultados:
        if r.falhou and r.erro and "BloqueioDetectado" in r.erro:
            await alertar(
                session,
                f"bloqueio:{tribunal}:{r.tema_slug}",
                f"[{tribunal}] Bloqueio anti-robô no tema {r.tema_slug}. "
                f"Coleta interrompida. Detalhe: {r.erro}",
            )
        elif r.falhou and await _falhas_seguidas(session, tribunal, r.tema_slug, 3):
            await alertar(
                session,
                f"falhas-seguidas:{tribunal}:{r.tema_slug}",
                f"[{tribunal}] Tema {r.tema_slug} falhou em 3 rodadas seguidas. "
                f"Último erro: {r.erro}",
            )

    if all(r.resultados == 0 for r in resultados):
        await alertar(
            session,
            f"rodada-vazia:{tribunal}",
            f"[{tribunal}] Nenhum dos {len(resultados)} temas devolveu resultado "
            "algum nesta rodada. Suspeita de parser quebrado ou layout alterado.",
        )
```

- [ ] **Step 7: Gravar as métricas e chamar os gatilhos em `rodada`**

Substituir o corpo de `rodada` por:

```python
    temas = carregar_temas(settings.temas_path)
    if tema_slug:
        temas = [t for t in temas if t.slug == tema_slug]
        if not temas:
            raise SystemExit(f"Tema {tema_slug!r} não existe em {settings.temas_path}")

    tribunal = TjspCjsgCollector.tribunal
    resultados: list[ResultadoTema] = []
    for tema in temas:
        inicio = time.monotonic()
        try:
            resultado = await coletar_tema(tema, max_paginas, desde, ate)
            logger.info("Tema %r: %s decisões processadas", tema.slug, resultado.resultados)
        except Exception as exc:
            logger.exception("Falha ao coletar tema %r; seguindo para o próximo", tema.slug)
            resultado = ResultadoTema(
                tema_slug=tema.slug, novas=0, atualizadas=0, paginas=0, resultados=0,
                falhou=True, erro=f"{type(exc).__name__}: {exc}",
                duracao_s=time.monotonic() - inicio,
            )
        resultados.append(resultado)

    async with SessionLocal() as session:
        session.add_all(
            MetricaColeta(
                tribunal=tribunal, tema_slug=r.tema_slug, novas=r.novas,
                atualizadas=r.atualizadas, paginas=r.paginas, resultados=r.resultados,
                falhou=r.falhou, erro=r.erro, duracao_s=r.duracao_s,
            )
            for r in resultados
        )
        await session.commit()
        await avaliar_gatilhos(session, tribunal, resultados)

    return resultados
```

- [ ] **Step 8: Ajustar `backfill_passo`, que dependia da contagem de falhas**

Em `backfill_passo`, substituir:

```python
    falhas = await rodada(desde=inicio, ate=fim)
    if falhas:
```

por:

```python
    resultados = await rodada(desde=inicio, ate=fim)
    falhas = sum(1 for r in resultados if r.falhou)
    if falhas:
```

- [ ] **Step 9: Rodar os testes**

Run:
```bash
uv run alembic upgrade head
uv run pytest -v
```
Expected: todos passam.

- [ ] **Step 10: Commit**

```bash
git add src/juris/scheduler.py src/juris/db/models.py \
        alembic/versions/005_metricas_coleta.py tests/test_metricas.py
git commit -m "Histórico de coleta em metricas_coleta e gatilhos 1 a 3 do alerta"
```

---

### Task 8: Watchdog — o dead man's switch

**Files:**
- Create: `src/juris/watchdog.py`
- Create: `tests/test_watchdog.py`

**Interfaces:**
- Consumes: `alertar` (Task 6), `configurar_logging` (Task 5), tabela `execucoes`
- Produces: `async def coleta_parada(session) -> timedelta | None` e o comando `python -m juris.watchdog`.

- [ ] **Step 1: Escrever os testes (vão falhar)**

Criar `tests/test_watchdog.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from juris.watchdog import coleta_parada, verificar


@pytest.fixture
def alertas_capturados(monkeypatch):
    enviados: list[tuple[str, str]] = []

    async def _alertar(session, chave, mensagem):
        enviados.append((chave, mensagem))
        return True

    monkeypatch.setattr("juris.watchdog.alertar", _alertar)
    return enviados


async def _registrar_execucao(session, horas_atras: int) -> None:
    await session.execute(
        text(
            "INSERT INTO execucoes (tribunal, tema_slug, ultima_execucao, ultima_qtd) "
            "VALUES ('TJSP','digital-lgpd', now() - make_interval(hours => :h), 10)"
        ),
        {"h": horas_atras},
    )


async def test_coleta_recente_nao_e_considerada_parada(session):
    await _registrar_execucao(session, horas_atras=2)
    assert await coleta_parada(session) is None


async def test_coleta_envelhecida_e_detectada(session):
    await _registrar_execucao(session, horas_atras=40)
    atraso = await coleta_parada(session)
    assert atraso is not None and atraso > timedelta(hours=36)


async def test_banco_sem_execucao_alguma_nao_alerta(session):
    """Instalação nova ainda não coletou; alertar aqui seria ruído."""
    assert await coleta_parada(session) is None


async def test_verificar_alerta_quando_parada(session, alertas_capturados):
    await _registrar_execucao(session, horas_atras=40)
    await verificar(session)
    assert len(alertas_capturados) == 1
    assert alertas_capturados[0][0] == "coleta-parada"


async def test_verificar_nao_alerta_quando_saudavel(session, alertas_capturados):
    await _registrar_execucao(session, horas_atras=1)
    await verificar(session)
    assert alertas_capturados == []
```

- [ ] **Step 2: Rodar e verificar que falha**

Run: `uv run pytest tests/test_watchdog.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'juris.watchdog'`

- [ ] **Step 3: Implementar o watchdog**

Criar `src/juris/watchdog.py`:

```python
"""Dead man's switch da coleta.

O gatilho "nenhuma rodada bem-sucedida em N horas" não pode viver dentro do
coletor: se o processo morreu, ele não avisa que morreu. Este módulo roda
fora, por cron, e só precisa do banco — coletor e watchdog compartilham o
Postgres, então o watchdog não sabe nada sobre o processo vigiado.

Uso:
    python -m juris.watchdog

No cron, de hora em hora:
    0 * * * * cd /opt/juris && docker compose run --rm crawler python -m juris.watchdog
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from juris.alertas import alertar
from juris.config import settings
from juris.db.engine import SessionLocal
from juris.db.models import Execucao
from juris.logging_config import configurar_logging

logger = logging.getLogger(__name__)


async def coleta_parada(session: AsyncSession) -> timedelta | None:
    """Há quanto tempo a coleta parou, ou None se está saudável.

    Banco sem execução alguma devolve None: instalação nova ainda não coletou,
    e alertar nesse caso seria ruído no primeiro deploy.
    """
    ultima = await session.scalar(select(func.max(Execucao.ultima_execucao)))
    if ultima is None:
        return None

    atraso = datetime.now(timezone.utc) - ultima
    limite = timedelta(hours=settings.watchdog_limite_horas)
    return atraso if atraso > limite else None


async def verificar(session: AsyncSession) -> None:
    atraso = await coleta_parada(session)
    if atraso is None:
        logger.info("Coleta saudável")
        return

    horas = int(atraso.total_seconds() // 3600)
    await alertar(
        session,
        "coleta-parada",
        f"Nenhuma rodada de coleta bem-sucedida há {horas} horas "
        f"(limite: {settings.watchdog_limite_horas}h). "
        "Verifique se o processo do scheduler está de pé.",
    )


async def _main() -> None:
    async with SessionLocal() as session:
        await verificar(session)


def main() -> None:
    configurar_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Rodar os testes**

Run: `uv run pytest tests/test_watchdog.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Rodar a suíte inteira**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 6: Commit**

```bash
git add src/juris/watchdog.py tests/test_watchdog.py
git commit -m "Watchdog: dead man's switch da coleta, fora do processo vigiado"
```

---

### Task 9: Autenticação por API key

**Files:**
- Create: `src/juris/api/deps.py`
- Create: `src/juris/apikeys.py`
- Create: `alembic/versions/006_api_keys.py`
- Create: `tests/test_apikeys.py`
- Modify: `src/juris/db/models.py` (`ApiKey`, `ApiKeyUso`)
- Modify: `src/juris/api/routes.py` (dependência de autenticação e `/v1/health`)
- Modify: `src/juris/api/schemas.py` (`HealthInfo`)
- Modify: `README.md` (seção de autenticação)

**Interfaces:**
- Consumes: fixture `session` (Task 1)
- Produces:
  - `gerar_chave() -> tuple[str, str]` (chave em claro, hash)
  - `hash_da_chave(chave: str) -> str`
  - `exigir_chave(escopo: str = "publico")` — fábrica de dependência FastAPI
  - `chave_opcional` — dependência que devolve `ApiKey | None`, usada por `/v1/health`

- [ ] **Step 1: Extrair `get_session` para um módulo neutro**

`routes.py` vai importar de `apikeys.py`, e `apikeys.py` precisa de `get_session`,
que hoje mora em `routes.py`. Importar um do outro fecha um ciclo. Criar
`src/juris/api/deps.py`:

```python
"""Dependências compartilhadas da API.

get_session vive aqui, e não em routes.py, para que apikeys.py possa usá-la
sem fechar um ciclo de import com routes.py.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from juris.db.engine import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

Em `src/juris/api/routes.py`, remover a definição local de `get_session` e
importá-la:

```python
from juris.api.deps import get_session
```

Run: `uv run pytest -v`
Expected: todos passam (refatoração sem mudança de comportamento).

- [ ] **Step 2: Acrescentar os modelos**

Em `src/juris/db/models.py`:

```python
class ApiKey(Base):
    """Chave de acesso à API.

    O banco guarda somente o sha256 da chave; o valor em claro é exibido uma
    única vez, na criação.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # "publico": jurisprudência e legislação. "interno": acrescenta o acervo
    # doutrinário do escritório (onda 5).
    escopo: Mapped[str] = mapped_column(String(20), default="publico")
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    limite_diario: Mapped[int | None] = mapped_column(Integer)
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ultimo_uso_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKeyUso(Base):
    """Contagem diária por chave.

    A chave primária (api_key_id, dia) faz a contagem se reiniciar sozinha a
    cada dia, sem cron de reset, e já serve de base para cobrança.
    """

    __tablename__ = "api_key_uso"

    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    )
    dia: Mapped[date] = mapped_column(Date, primary_key=True)
    requisicoes: Mapped[int] = mapped_column(Integer, default=0)
```

Acrescentar `Boolean` e `ForeignKey` aos imports de `sqlalchemy`.

- [ ] **Step 3: Escrever a migração 006**

Criar `alembic/versions/006_api_keys.py`:

```python
"""Chaves de API e contagem de uso.

Revision ID: 006
Revises: 005
"""

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=100), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("escopo", sa.String(length=20), nullable=False),
        sa.Column("ativa", sa.Boolean(), nullable=False),
        sa.Column("limite_diario", sa.Integer(), nullable=True),
        sa.Column(
            "criada_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ultimo_uso_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nome"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    op.create_table(
        "api_key_uso",
        sa.Column("api_key_id", sa.Integer(), nullable=False),
        sa.Column("dia", sa.Date(), nullable=False),
        sa.Column("requisicoes", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("api_key_id", "dia"),
    )


def downgrade() -> None:
    op.drop_table("api_key_uso")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
```

- [ ] **Step 4: Escrever os testes (vão falhar)**

Criar `tests/test_apikeys.py`:

```python
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from juris.apikeys import criar_chave, exigir_chave, hash_da_chave


async def test_chave_criada_nao_fica_em_claro_no_banco(session):
    chave, registro = await criar_chave(session, nome="squad", escopo="publico")
    assert chave.startswith("juris_")
    guardado = await session.scalar(
        text("SELECT key_hash FROM api_keys WHERE nome = 'squad'")
    )
    assert guardado == hash_da_chave(chave)
    assert chave not in guardado


@pytest.fixture
def app_de_teste(session):
    """App mínimo com uma rota pública e uma que exige escopo interno.

    A rota interna existe só aqui: nenhum endpoint de produção declara
    'interno' antes da onda 5, mas o mecanismo precisa ser testado.
    """
    from juris.api.deps import get_session

    app = FastAPI()

    @app.get("/publico", dependencies=[Depends(exigir_chave("publico"))])
    async def _publico():
        return {"ok": True}

    @app.get("/interno", dependencies=[Depends(exigir_chave("interno"))])
    async def _interno():
        return {"ok": True}

    async def _session_de_teste():
        yield session

    app.dependency_overrides[get_session] = _session_de_teste
    return app


async def _get(app, caminho: str, chave: str | None = None):
    headers = {"Authorization": f"Bearer {chave}"} if chave else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://teste") as client:
        return await client.get(caminho, headers=headers)


async def test_sem_chave_recusa(session, app_de_teste):
    assert (await _get(app_de_teste, "/publico")).status_code == 401


async def test_chave_invalida_recusa_com_a_mesma_mensagem(session, app_de_teste):
    """401 genérico nos dois casos: distinguir facilitaria enumeração."""
    sem = await _get(app_de_teste, "/publico")
    errada = await _get(app_de_teste, "/publico", chave="juris_naoexiste")
    assert errada.status_code == 401
    assert errada.json() == sem.json()


async def test_chave_valida_aceita(session, app_de_teste):
    chave, _ = await criar_chave(session, nome="squad", escopo="publico")
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 200


async def test_chave_inativa_recusa(session, app_de_teste):
    chave, _ = await criar_chave(session, nome="squad", escopo="publico")
    await session.execute(text("UPDATE api_keys SET ativa = false WHERE nome = 'squad'"))
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 401


async def test_escopo_insuficiente_recusa(session, app_de_teste):
    chave, _ = await criar_chave(session, nome="squad", escopo="publico")
    assert (await _get(app_de_teste, "/interno", chave=chave)).status_code == 403


async def test_escopo_interno_alcanca_o_publico(session, app_de_teste):
    chave, _ = await criar_chave(session, nome="escritorio", escopo="interno")
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 200


async def test_limite_diario_excedido_devolve_429(session, app_de_teste):
    chave, registro = await criar_chave(
        session, nome="squad", escopo="publico", limite_diario=1
    )
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 200
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 429
```

- [ ] **Step 5: Rodar e verificar que falha**

Run: `uv run pytest tests/test_apikeys.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'juris.apikeys'`

- [ ] **Step 6: Implementar o módulo de chaves**

Criar `src/juris/apikeys.py`:

```python
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
    registro = await session.scalar(select(ApiKey).where(ApiKey.key_hash == esperado))
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
```

- [ ] **Step 7: Rodar os testes de chave**

Run:
```bash
uv run alembic upgrade head
uv run pytest tests/test_apikeys.py -v
```
Expected: PASS (9 testes)

- [ ] **Step 8: Proteger os endpoints e cortar o `/v1/health` anônimo**

Em `src/juris/api/routes.py`, remover a função stub `api_key_auth` inteira e acrescentar ao topo:

```python
from juris.apikeys import chave_opcional, exigir_chave
from juris.db.models import ApiKey
```

Trocar `dependencies=[Depends(api_key_auth)]` por `dependencies=[Depends(exigir_chave("publico"))]` nos três endpoints `/decisoes`, `/decisoes/{decisao_id}` e `/temas`.

Substituir o endpoint `health` por:

```python
@router.get("/health", response_model=HealthInfo)
async def health(
    chave: ApiKey | None = Depends(chave_opcional),
    session: AsyncSession = Depends(get_session),
) -> HealthInfo:
    """Chamador anônimo recebe só o estado.

    Total de decisões e ritmo de coleta são informação de negócio: entregá-los
    a qualquer varredura num IP público não tem contrapartida.
    """
    if chave is None:
        return HealthInfo(status="ok")

    total = await session.scalar(select(func.count()).select_from(Decisao))
    ultima = await session.scalar(select(func.max(Execucao.ultima_execucao)))
    return HealthInfo(status="ok", total_decisoes=total or 0, ultima_coleta=ultima)
```

Em `src/juris/api/schemas.py`, tornar opcionais os campos de `HealthInfo`:

```python
class HealthInfo(BaseModel):
    status: str
    total_decisoes: int | None = None
    ultima_coleta: datetime | None = None
```

- [ ] **Step 9: Escrever o teste do health**

Acrescentar a `tests/test_apikeys.py`:

```python
async def test_health_anonimo_nao_revela_o_acervo(session):
    from juris.api.main import app
    from juris.api.deps import get_session

    async def _session_de_teste():
        yield session

    app.dependency_overrides[get_session] = _session_de_teste
    try:
        resposta = await _get(app, "/v1/health")
        assert resposta.status_code == 200
        assert resposta.json() == {
            "status": "ok",
            "total_decisoes": None,
            "ultima_coleta": None,
        }
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 10: Documentar no README**

Em `README.md`, substituir o parágrafo que começa com "Sem autenticação no MVP" por:

```markdown
## Autenticação

Todos os endpoints exigem `Authorization: Bearer <chave>`, exceto `/v1/health`,
que responde `{"status": "ok"}` a chamador anônimo e só revela contagem e
frescor da coleta para quem apresenta chave.

```bash
uv run python -m juris.apikeys criar --nome squad --escopo publico
uv run python -m juris.apikeys listar
uv run python -m juris.apikeys revogar --nome squad
```

A chave é exibida uma única vez, na criação; o banco guarda apenas o sha256.
```

- [ ] **Step 11: Rodar a suíte**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 12: Commit**

```bash
git add src/juris/api/deps.py src/juris/apikeys.py src/juris/db/models.py src/juris/api/routes.py \
        src/juris/api/schemas.py alembic/versions/006_api_keys.py \
        tests/test_apikeys.py README.md
git commit -m "Autenticação por API key com escopo, limite diário e health sem vazamento"
```

---

### Task 10: Compose de produção e segredos fora do repositório

**Files:**
- Create: `docker-compose.prod.yml`
- Create: `Caddyfile`
- Create: `.env.prod.example`
- Modify: `Dockerfile` (usuário não-root)
- Modify: `docker-compose.yml` (senha por variável)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nada de código; consome as variáveis de ambiente definidas nas Tasks 6 e 9.
- Produces: `docker-compose.prod.yml`, consumido pelo runbook (Task 12) e pelo backup (Task 11).

- [ ] **Step 1: Tirar a senha do arquivo versionado**

Em `docker-compose.yml`, no serviço `db`, substituir `POSTGRES_PASSWORD: juris` por:

```yaml
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-juris}
```

E nos serviços `api` e `crawler`, substituir a URL fixa por:

```yaml
      JURIS_DATABASE_URL: postgresql+asyncpg://juris:${POSTGRES_PASSWORD:-juris}@db:5432/juris
```

O padrão `juris` preserva a experiência de desenvolvimento local sem `.env`; produção usa `docker-compose.prod.yml`, onde não há padrão.

- [ ] **Step 2: Rodar o container como usuário não-root**

Em `Dockerfile`, antes da linha `ENV PATH=...`:

```dockerfile
# Usuário sem privilégio. O Chromium recusa rodar como root sem --no-sandbox,
# e o sandbox é justamente a defesa contra HTML hostil de terceiros — que é o
# que um crawler consome o dia inteiro.
RUN useradd --create-home --uid 10001 juris \
    && chown -R juris:juris /app /home/juris
USER juris
```

**Atenção:** o `playwright install --with-deps chromium` precisa continuar rodando como root (instala pacotes do sistema). Manter o `USER juris` **depois** dessa linha.

- [ ] **Step 3: Criar o Caddyfile**

Criar `Caddyfile`:

```
# TLS automático via Let's Encrypt. Substitua o domínio no deploy.
{$JURIS_DOMINIO} {
	encode gzip

	# A API não publica porta no host: só o Caddy a alcança, pela rede interna
	# do compose.
	reverse_proxy api:8000

	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer"
		-Server
	}

	log {
		output stdout
		format console
	}
}
```

- [ ] **Step 4: Criar o compose de produção**

Criar `docker-compose.prod.yml`:

```yaml
# Produção. Diferenças conscientes em relação ao docker-compose.yml de
# desenvolvimento:
#   - o Postgres NÃO publica porta, nem em loopback: api e crawler o alcançam
#     pela rede do compose, e administração se faz por `docker compose exec`;
#   - a api também não publica porta: só o Caddy fala com ela;
#   - nenhuma senha tem valor padrão — falta de variável derruba a subida, que
#     é o comportamento desejado em produção.
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: juris
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?defina POSTGRES_PASSWORD no .env}
      POSTGRES_DB: juris
    volumes:
      - juris_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U juris"]
      interval: 10s
      timeout: 5s
      retries: 10
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }

  api:
    build: .
    restart: unless-stopped
    command: uvicorn juris.api.main:app --host 0.0.0.0 --port 8000
    environment:
      JURIS_DATABASE_URL: postgresql+asyncpg://juris:${POSTGRES_PASSWORD}@db:5432/juris
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/health')\""]
      interval: 30s
      timeout: 5s
      retries: 3
    depends_on:
      db:
        condition: service_healthy
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }

  crawler:
    build: .
    restart: unless-stopped
    command: python -m juris.scheduler
    environment:
      JURIS_DATABASE_URL: postgresql+asyncpg://juris:${POSTGRES_PASSWORD}@db:5432/juris
      JURIS_TELEGRAM_BOT_TOKEN: ${JURIS_TELEGRAM_BOT_TOKEN}
      JURIS_TELEGRAM_CHAT_ID: ${JURIS_TELEGRAM_CHAT_ID}
    depends_on:
      db:
        condition: service_healthy
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      JURIS_DOMINIO: ${JURIS_DOMINIO:?defina JURIS_DOMINIO no .env}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - api
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }

volumes:
  juris_pgdata:
  caddy_data:
  caddy_config:
```

- [ ] **Step 5: Criar o exemplo de ambiente de produção**

Criar `.env.prod.example`:

```
# Copie para .env na VPS e preencha. NUNCA versione o .env preenchido.
# Gere a senha com: openssl rand -base64 32
POSTGRES_PASSWORD=
JURIS_DOMINIO=juris.exemplo.com.br

JURIS_TELEGRAM_BOT_TOKEN=
JURIS_TELEGRAM_CHAT_ID=

JURIS_BACKUP_S3_ENDPOINT=
JURIS_BACKUP_S3_BUCKET=
JURIS_BACKUP_S3_ACCESS_KEY=
JURIS_BACKUP_S3_SECRET_KEY=
JURIS_BACKUP_RETENCAO_DIARIOS=7
JURIS_BACKUP_RETENCAO_SEMANAIS=4
```

Em `.gitignore`, acrescentar após a linha `.env`:

```
.env.prod
backups/
```

- [ ] **Step 6: Verificar que o compose de produção é válido e recusa segredo ausente**

Run:
```bash
docker compose -f docker-compose.prod.yml config >/dev/null \
  && echo "ERRO: deveria ter recusado sem POSTGRES_PASSWORD" \
  || echo "OK: recusa subir sem segredo"
```
Expected: `OK: recusa subir sem segredo`

Depois, com as variáveis presentes:
```bash
POSTGRES_PASSWORD=teste JURIS_DOMINIO=exemplo.test \
  docker compose -f docker-compose.prod.yml config >/dev/null && echo "OK: compose válido"
```
Expected: `OK: compose válido`

- [ ] **Step 7: Verificar que a imagem sobe como não-root e o Playwright funciona**

Run:
```bash
docker compose build crawler
docker compose run --rm crawler id
docker compose run --rm crawler python -c \
  "from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close(); print('chromium ok')"
```
Expected: `uid=10001(juris)` e `chromium ok`.

Se o Chromium falhar, registrar no runbook (Task 12) o fallback documentado — voltar a `USER root` com `--no-sandbox` — e anotar o risco. Não silenciar a falha.

- [ ] **Step 8: Rodar a suíte**

Run: `uv run pytest -v`
Expected: todos passam.

- [ ] **Step 9: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml Caddyfile Dockerfile \
        .env.prod.example .gitignore
git commit -m "Compose de produção com Caddy, sem porta de banco e sem senha versionada"
```

---

### Task 11: Backup com restore testado

**Files:**
- Create: `scripts/backup.sh`
- Create: `scripts/restore-test.sh`
- Modify: `docker-compose.yml` (serviço `minio`, profile `backup-teste`)
- Modify: `.env.example`

**Interfaces:**
- Consumes: `docker-compose.prod.yml` (Task 10)
- Produces: `scripts/backup.sh` e `scripts/restore-test.sh`, referenciados pelo runbook (Task 12).

- [ ] **Step 1: Acrescentar o MinIO ao compose de desenvolvimento**

Em `docker-compose.yml`, acrescentar:

```yaml
  # Só para exercitar o caminho de upload do backup localmente. Sem isto, o
  # upload seria código que executa pela primeira vez em produção, de
  # madrugada, sem ninguém olhando.
  minio:
    profiles: ["backup-teste"]
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9001:9001"
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 3s
      retries: 10
```

- [ ] **Step 2: Escrever o script de backup**

Criar `scripts/backup.sh`:

```bash
#!/usr/bin/env bash
# Backup do banco: pg_dump comprimido, retenção local e upload S3-compatível.
#
# Uso:   scripts/backup.sh [arquivo-compose]
# Cron:  0 4 * * * cd /opt/juris && scripts/backup.sh docker-compose.prod.yml
set -euo pipefail

COMPOSE="${1:-docker-compose.yml}"
DESTINO="${JURIS_BACKUP_DIR:-backups}"
RETENCAO_DIARIOS="${JURIS_BACKUP_RETENCAO_DIARIOS:-7}"
RETENCAO_SEMANAIS="${JURIS_BACKUP_RETENCAO_SEMANAIS:-4}"

mkdir -p "$DESTINO"
carimbo="$(date +%Y%m%d-%H%M%S)"
arquivo="$DESTINO/juris-$carimbo.dump"

echo "Gerando $arquivo ..."
docker compose -f "$COMPOSE" exec -T db \
  pg_dump -U juris -d juris -Fc > "$arquivo"

tamanho="$(du -h "$arquivo" | cut -f1)"
echo "Dump gerado: $arquivo ($tamanho)"

# Um dump que não pode ser lido não é backup. pg_restore --list falha se o
# arquivo estiver truncado ou corrompido. Roda dentro do container para não
# depender de o host ter as ferramentas do Postgres instaladas.
docker compose -f "$COMPOSE" exec -T db pg_restore --list < "$arquivo" >/dev/null
echo "Dump íntegro."

if [[ -n "${JURIS_BACKUP_S3_BUCKET:-}" ]]; then
  echo "Enviando para ${JURIS_BACKUP_S3_BUCKET} ..."
  AWS_ACCESS_KEY_ID="$JURIS_BACKUP_S3_ACCESS_KEY" \
  AWS_SECRET_ACCESS_KEY="$JURIS_BACKUP_S3_SECRET_KEY" \
  aws --endpoint-url "$JURIS_BACKUP_S3_ENDPOINT" \
      s3 cp "$arquivo" "s3://$JURIS_BACKUP_S3_BUCKET/diarios/$(basename "$arquivo")"
  echo "Upload concluído."
else
  echo "JURIS_BACKUP_S3_BUCKET não definido; mantendo apenas cópia local."
fi

# Retenção local: mantém os N diários mais recentes e, aos domingos, promove
# uma cópia semanal.
if [[ "$(date +%u)" == "7" ]]; then
  cp "$arquivo" "$DESTINO/semanal-$carimbo.dump"
fi
ls -1t "$DESTINO"/juris-*.dump 2>/dev/null | tail -n +$((RETENCAO_DIARIOS + 1)) | xargs -r rm --
ls -1t "$DESTINO"/semanal-*.dump 2>/dev/null | tail -n +$((RETENCAO_SEMANAIS + 1)) | xargs -r rm --

echo "Backup concluído."
```

Run: `chmod +x scripts/backup.sh`

- [ ] **Step 3: Escrever o script de teste de restore**

Criar `scripts/restore-test.sh`:

```bash
#!/usr/bin/env bash
# Restaura o dump mais recente num container descartável e confere a contagem.
# Backup não testado não é backup.
#
# Uso: scripts/restore-test.sh [arquivo-compose]
set -euo pipefail

COMPOSE="${1:-docker-compose.yml}"
DESTINO="${JURIS_BACKUP_DIR:-backups}"
CONTAINER="juris-restore-teste"

dump="$(ls -1t "$DESTINO"/juris-*.dump 2>/dev/null | head -1 || true)"
[[ -n "$dump" ]] || { echo "Nenhum dump em $DESTINO"; exit 1; }
echo "Testando restore de $dump"

esperado="$(docker compose -f "$COMPOSE" exec -T db \
  psql -U juris -d juris -tAc 'select count(*) from decisoes')"
echo "Contagem na origem: $esperado"

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER=juris -e POSTGRES_PASSWORD=restore \
  -e POSTGRES_DB=juris postgres:16 >/dev/null

echo -n "Aguardando o Postgres descartável"
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U juris >/dev/null 2>&1; then break; fi
  echo -n "."; sleep 1
done
echo

docker exec -i "$CONTAINER" pg_restore -U juris -d juris --no-owner < "$dump"

obtido="$(docker exec "$CONTAINER" psql -U juris -d juris -tAc 'select count(*) from decisoes')"
echo "Contagem no restore: $obtido"

if [[ "$esperado" != "$obtido" ]]; then
  echo "FALHOU: origem $esperado, restore $obtido"
  exit 1
fi
echo "OK: restore íntegro, $obtido decisões."
```

Run: `chmod +x scripts/restore-test.sh`

- [ ] **Step 4: Documentar as variáveis de backup**

Em `.env.example`, acrescentar:

```
# Backup. Sem JURIS_BACKUP_S3_BUCKET o script mantém só a cópia local.
JURIS_BACKUP_DIR=backups
JURIS_BACKUP_S3_ENDPOINT=
JURIS_BACKUP_S3_BUCKET=
JURIS_BACKUP_S3_ACCESS_KEY=
JURIS_BACKUP_S3_SECRET_KEY=
JURIS_BACKUP_RETENCAO_DIARIOS=7
JURIS_BACKUP_RETENCAO_SEMANAIS=4
```

- [ ] **Step 5: Executar backup e restore localmente**

Run:
```bash
scripts/backup.sh
scripts/restore-test.sh
```
Expected: `OK: restore íntegro, 49036 decisões.`

- [ ] **Step 6: Exercitar o upload contra o MinIO**

Run:
```bash
docker compose --profile backup-teste up -d minio
docker compose --profile backup-teste exec -T minio \
  mc alias set local http://localhost:9000 minioadmin minioadmin
docker compose --profile backup-teste exec -T minio mc mb --ignore-existing local/juris-backup

JURIS_BACKUP_S3_ENDPOINT=http://localhost:9000 \
JURIS_BACKUP_S3_BUCKET=juris-backup \
JURIS_BACKUP_S3_ACCESS_KEY=minioadmin \
JURIS_BACKUP_S3_SECRET_KEY=minioadmin \
  scripts/backup.sh

docker compose --profile backup-teste exec -T minio mc ls local/juris-backup/diarios/
```
Expected: `Upload concluído.` e o dump listado no bucket.

Se o `aws` CLI não estiver instalado no host: `brew install awscli` (macOS) ou `apt install awscli` (Debian). Registrar a exigência no runbook.

- [ ] **Step 7: Derrubar o MinIO**

Run: `docker compose --profile backup-teste down minio`

- [ ] **Step 8: Commit**

```bash
git add scripts/backup.sh scripts/restore-test.sh docker-compose.yml .env.example
git commit -m "Backup com verificação de integridade, retenção e restore testado"
```

---

### Task 12: Runbook de deploy

**Files:**
- Create: `docs/runbook_deploy.md`
- Modify: `README.md` (link para o runbook e seção de operação)

**Interfaces:**
- Consumes: tudo produzido pelas Tasks 1 a 11.
- Produces: o documento que o operador segue para subir a VPS quando ela existir.

- [ ] **Step 1: Escrever o runbook**

Criar `docs/runbook_deploy.md` cobrindo, em ordem, cada seção abaixo. Todo comando precisa ser executável como está, sem lacuna:

1. **Pré-requisitos da VPS** — **Hostinger KVM 4** (4 vCPU, 16 GB RAM, 200 GB NVMe), **datacenter em São Paulo** (TJBA e TJMS recusaram conexão de fora do Brasil; ver §9 da spec), Docker e Docker Compose instalados, `awscli` para o backup. Registrar no runbook que o preço promocional (R$ 59,99/mês) exige pré-pagamento anual e a **renovação é R$ 149,99/mês** — para não virar surpresa no segundo ano. Precisar de mais folga é redimensionar para o KVM 8 (8 vCPU, 32 GB), não trocar de provedor.
2. **Endurecimento do host** — chave SSH e `PasswordAuthentication no`; UFW liberando apenas 22, 80 e 443; fail2ban.
3. **Repositório e ambiente** — `git clone`, `cp .env.prod.example .env`, geração da senha com `openssl rand -base64 32`, preenchimento do domínio e do bot do Telegram.
4. **Primeira subida** — `docker compose -f docker-compose.prod.yml up -d db`, depois `docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head`, depois os demais serviços. **A migração roda antes da api e do crawler subirem.**
5. **Restauração de um banco existente** — como levar o dump da máquina de desenvolvimento para a VPS e restaurá-lo, com a ressalva de que `alembic stamp 001` já foi aplicado na origem e o dump traz a tabela `alembic_version` junto.
6. **Criação da primeira chave** — `docker compose -f docker-compose.prod.yml exec api python -m juris.apikeys criar --nome squad --escopo publico`.
7. **Cron** — backup diário às 04:00 e watchdog de hora em hora, com as linhas de crontab prontas.
8. **Teste do alerta** — como forçar um alerta de ponta a ponta e confirmar que a mensagem chega ao Telegram, e como confirmar que a segunda tentativa dentro da janela é suprimida.
9. **Verificação pós-deploy** — `curl` no `/v1/health` anônimo (deve vir sem contagem), `curl` com chave (deve vir com), `docker compose ps` com todos os serviços saudáveis, e um `scripts/restore-test.sh`.
10. **Rollback** — `git checkout <tag anterior>`, `alembic downgrade -1`, `docker compose up -d --build`, e a ressalva de que a revisão 003 (prefixo de slug) tem `downgrade` funcional mas reescreve dados.
11. **Fallback do Chromium** — se o Step 7 da Task 10 tiver falhado, o procedimento documentado e o risco assumido.
12. **Rotina mensal** — testar o restore e conferir o crescimento do disco.

- [ ] **Step 2: Apontar o README para o runbook**

Em `README.md`, acrescentar ao fim da seção "Como rodar":

```markdown
Para subir em servidor, siga [docs/runbook_deploy.md](docs/runbook_deploy.md).
```

- [ ] **Step 3: Conferir o runbook contra os critérios de saída**

Ler `docs/superpowers/specs/2026-09-03-onda-0-endurecimento-design.md` §8 e confirmar que cada um dos 9 critérios tem um comando correspondente no runbook ou na suíte de testes. Anotar qualquer critério sem cobertura e resolvê-lo antes de fechar a task.

- [ ] **Step 4: Rodar a suíte completa uma última vez**

Run:
```bash
uv run pytest -v
uv run alembic upgrade head
uv run alembic current
```
Expected: todos os testes passam; `alembic current` mostra `006 (head)`.

- [ ] **Step 5: Commit**

```bash
git add docs/runbook_deploy.md README.md
git commit -m "Runbook de deploy da VPS"
```

- [ ] **Step 6: Abrir o pull request**

```bash
git push -u origin onda-0-endurecimento
```

Se o remoto ainda não existir no GitHub, o push falha — criar o repositório privado `pedrororatto/crawl-jurisprudencia` primeiro. O `gh` não está instalado nesta máquina; usar a interface web ou instalar com `brew install gh`.

---

## Verificação final

Depois da Task 12, percorrer os 9 critérios de saída da §8 da spec, um a um, e registrar o resultado. Um critério que não puder ser demonstrado com um comando é um critério não atendido.
