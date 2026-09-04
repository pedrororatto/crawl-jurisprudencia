# Juris — API de Jurisprudência de Direito Digital

Robô que coleta acórdãos de **direito digital** do TJSP (e-SAJ/CJSG) usando
[Crawl4AI](https://github.com/unclecode/crawl4ai), grava em PostgreSQL com
busca full-text em português e expõe tudo numa API FastAPI. A coleta é
incremental: um job diário busca só o que foi publicado desde a última rodada.

Este projeto usa Crawl4AI (https://github.com/unclecode/crawl4ai) para extração de dados web.

## Arquitetura

```
config/temas.yaml ──> scheduler ──> collectors/tjsp_cjsg (crawl4ai/Playwright)
                          │                  │
                          │                  └── parse (BeautifulSoup) ──> pipeline/ingest (upsert)
                          └── watermark por tema (tabela execucoes)              │
                                                                             PostgreSQL (FTS pt)
                                                                                 │
                                                                          api/ (FastAPI /v1)
```

- **Temas**: [config/temas.yaml](config/temas.yaml) — termos curados de direito
  digital enviados à busca livre do CJSG. Edite sem mexer em código.
- **Dedup**: chave natural `(tribunal, cd_acordao)`; upsert mescla o array de
  temas quando a mesma decisão aparece em buscas diferentes.
- **Incremental**: tabela `execucoes` guarda a maior `data_publicacao` vista
  por tema; cada rodada busca de `watermark - 2 dias` até hoje.
- **Backfill histórico**: tabela `backfill_progresso` guarda o próximo mês a
  processar; o daemon executa um passo a cada 10 min (um mês, todos os temas,
  todas as páginas), retrocedendo do mês atual até `JURIS_BACKFILL_INICIO`.
  Se algum tema falhar, o ponteiro não avança e o mês é refeito no passo
  seguinte (a deduplicação torna a repetição inofensiva).
- **reCAPTCHA**: o e-SAJ valida o token do reCAPTCHA invisível no submit, por
  isso o coletor usa browser real (hook `after_goto` do crawl4ai) e espera o
  script carregar. Se aparecer um desafio interativo, a coleta para com
  `BloqueioDetectadoError` — o robô não burla captcha.

## Como rodar

```bash
uv sync
uv run playwright install chromium
docker compose up -d db
uv run alembic upgrade head

# coleta de teste (1 tema, 2 páginas)
uv run python -m juris.scheduler --once --tema digital-lgpd --max-paginas 2 --desde 2026-07-01

# rodada completa de todos os temas (respeita watermark)
uv run python -m juris.scheduler --once

# job por período: máximo de 1 mês, percorre TODAS as páginas retornadas
uv run python -m juris.scheduler --once --desde 2026-06-01 --ate 2026-06-30
uv run python -m juris.scheduler --once --tema digital-lgpd --desde 2026-06-01 --ate 2026-06-30

# um passo do backfill histórico (coleta UM mês inteiro e retrocede o ponteiro)
uv run python -m juris.scheduler --backfill-passo

# daemon: coleta incremental diária às 03:00 + backfill histórico automático,
# um mês a cada 10 minutos, do mês atual até JURIS_BACKFILL_INICIO (jan/2020)
uv run python -m juris.scheduler

# API (docs em http://localhost:8000/docs)
uv run uvicorn juris.api.main:app --port 8000
```

## Endpoints (v1)

| Endpoint | Descrição |
|---|---|
| `GET /v1/decisoes` | Lista (com a ementa completa de cada decisão) com busca full-text (`q`, sintaxe websearch) e filtros: `tribunal`, `relator`, `orgao_julgador`, `tema`, `data_julgamento_de/ate`, paginação `page`/`per_page` |
| `GET /v1/decisoes/{id}` | Decisão completa (inclui `cd_acordao` e ementa em markdown) |
| `GET /v1/temas` | Temas configurados e contagem de decisões |
| `GET /v1/health` | Status + timestamp da última coleta |

Sem autenticação no MVP; o hook `api_key_auth` em
[src/juris/api/routes.py](src/juris/api/routes.py) é o ponto de encaixe para
API keys quando a venda começar.

## Testes

```bash
uv run pytest
```

Os parsers são testados com HTML real do CJSG salvo em
[tests/fixtures/](tests/fixtures/).

## Roadmap

- Coletor TST (backend JSON de jurisprudencia-backend.tst.jus.br)
- Inteiro teor (PDF) dos acórdãos
- API keys, medição de uso e billing
- Busca semântica (pgvector) e classificação por subtema
