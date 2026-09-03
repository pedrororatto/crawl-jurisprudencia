# Onda 0 — Endurecimento para operação 24/7

**Data:** 2026-09-03
**Status:** aprovado para planejamento
**Projeto:** `crawl-jurisprudência` (Juris)

## 1. Contexto

O Juris coleta acórdãos de direito digital do TJSP (e-SAJ/CJSG) e os expõe numa
API FastAPI. Hoje são 49.036 decisões, backfill de 2020 até a data corrente
concluído, 506 MB em disco (77 MB de tabela, 111 MB de índices, ~318 MB de
`raw_html` em TOAST).

O objetivo de médio prazo é mover o coletor para uma VPS onde ele rode sozinho,
ampliar as fontes (mais tribunais, legislação, doutrina) e servir a Fase 4 —
Pesquisa Jurídica Verificável — do projeto `reis-esteves-squad`.

Esta onda não amplia fonte nenhuma. Ela torna seguro deixar o que já existe
rodando sem supervisão. Sete lacunas motivam isso: ausência de controle de
versão, schema criado por `create_all` sem migrations, ausência de referência
verificável nas decisões, log sem rotação, ausência de métrica e alerta, API sem
autenticação, e ausência de backup.

## 2. Escopo

### Entra

1. Controle de versão (git + remoto GitHub privado).
2. Alembic substituindo `create_all`, com o banco atual preservado.
3. Duas colunas novas em `decisoes` (`url_origem`, `content_hash`) e os campos
   derivados que completam o contrato da Fase 4.
4. Log em stdout com rotação pelo driver do Docker, e redução do ruído do
   crawl4ai.
5. Tabela `metricas_coleta` e alerta por Telegram com quatro gatilhos e cooldown.
6. Watchdog externo (dead man's switch) rodando por cron.
7. Autenticação por API key com escopo e contagem de uso.
8. `docker-compose.prod.yml` com Caddy, sem porta de banco publicada, containers
   como usuário não-root, senha de banco gerada e fora do repositório.
9. Backup `pg_dump` com retenção, upload S3-compatível e teste de restore.
10. `docs/runbook_deploy.md`.
11. Prefixo de área nos slugs de tema (§3.9), preparando o sistema para outras
    áreas do direito.

### Não entra

- Fila de jobs em Postgres e workers concorrentes — **onda 1**. O formato certo
  da tabela `jobs` depende de saber qual intervalo cada tribunal tolera e se o
  bloqueio é por IP ou por sessão. Construir antes é decidir com menos
  informação do que teremos de graça em duas semanas.
- Coletor genérico `EsajCjsgCollector(base_url, tribunal)` — **onda 1**. Está
  bloqueado por evidência: a maioria dos hosts e-SAJ não responde da máquina de
  desenvolvimento (§9), então um coletor multi-tribunal escrito agora não teria
  como ser validado contra tribunal novo nenhum.
- pgvector, chunks, embeddings, busca híbrida — **onda 4**. É o que o Squad
  Digital e qualquer outro sistema consumidor vão precisar de fato; a onda 0
  apenas garante que a base sobre a qual isso será construído não se perca nem
  pare em silêncio.
- Campo `area` no YAML e coluna `areas` em `decisoes` — **quando a segunda área
  existir** (§3.9). Adiar é coerente com o motivo de fazer a onda 0: o Alembic
  existe justamente para que mudanças de schema fiquem baratas depois.
- Deploy real na VPS. Ela ainda não existe. A onda entrega artefatos e runbook,
  validados localmente.
- `git push`. O repositório remoto pode ainda não existir; publicar é ação
  externa e fica para o operador.

## 3. Decisões de design

### 3.1 Versionamento

`git init` na branch `main`, remoto `git@github.com:pedrororatto/crawl-jurisprudencia.git`.
O `.gitignore` preexistente já cobre `.env`, `run/`, `logs/`, `.venv/` e caches.

O commit inicial (`aabf389`) registra o estado anterior à onda como ponto de
restauração. Trabalho subsequente em branch.

### 3.2 Migrations

`alembic init -t async`, com `env.py` lendo `settings.database_url`.

- **Revisão `001`** reproduz o schema atual exatamente como `models.py` o define:
  tabelas `decisoes`, `execucoes`, `backfill_progresso`, a extensão `pg_trgm` e o
  índice `ix_decisoes_ementa_trgm` — os dois últimos hoje residem em
  `db/init.py`.
- **No banco existente:** `alembic stamp 001`, que registra a posição sem
  executar DDL, preservando os 49.036 registros.
- **Revisões `002` e `003`** trazem, respectivamente, as colunas novas de
  `decisoes` e as tabelas de métrica, alerta e API key (§4).
- **Bancos novos:** `alembic upgrade head` chega ao mesmo schema.
- `db/init.py` é removido. O comentário nele que explica por que índices novos
  precisavam ser criados à mão deixa de ser verdadeiro e sai junto.

`CREATE EXTENSION` exige privilégio elevado. No container de desenvolvimento o
usuário `juris` é o superusuário do Postgres e isso passa; o runbook registra a
exigência para o ambiente de produção.

### 3.3 Contrato de fontes

Regra que decide o que vira coluna: **armazenar o que é fato sobre o evento de
coleta; derivar o que é fato sobre o sistema de origem.**

| Campo | Onde vive | Justificativa |
|---|---|---|
| `url_origem` | coluna em `decisoes` | Proveniência. O squad copia a URL para dentro do `LegalSource` dele; corrigir uma renderização aqui não alcançaria a cópia lá. |
| `content_hash` | coluna em `decisoes` | Fato sobre o conteúdo servido. Hash de `ementa_texto`, não de `raw_html` — mudança cosmética de markup não deve invalidar a fonte. |
| `referencia_citavel` | derivado, schema Pydantic | Formatação. Mudar o padrão de citação depois não deve reescrever 49 mil linhas. |
| `url_requer_login` | derivado, mapa `{"TJSP": True}` | Política de acesso do tribunal, idêntica em todo registro. Tribunal fora do mapa devolve `null`, nunca um palpite. Na onda 1 vira campo de `tribunais.yaml`. |
| `retrieved_at` | derivado de `updated_at` | O upsert reescreve `updated_at` toda vez que revemos o conteúdo na origem — é exatamente a semântica desejada. Coluna nova seria duplicata. |

#### `content_hash` é calculado na aplicação, não pelo banco

A intenção inicial era uma coluna gerada, seguindo o padrão que `busca` já usa
em `models.py`. O Postgres recusa:

```
ERROR: generation expression is not immutable
```

`convert_to()` é `STABLE`, não `IMMUTABLE`, porque depende da codificação do
servidor. O contorno habitual — embrulhar numa função declarada `IMMUTABLE` à
força — mente para o planejador sobre um valor que fica armazenado, e foi
descartado.

Decisão: o hash é calculado em Python no `pipeline/ingest.py`, com teste
unitário. O backfill das linhas existentes usa um `UPDATE` comum, onde a
imutabilidade é irrelevante. Verificou-se que os dois caminhos produzem o mesmo
dígito.

Formato: `sha256:<hex>`, autodescritivo. Entrada é `ementa_texto` ou string
vazia quando nulo.

#### Formato da `referencia_citavel`

```
{tribunal}, {classe} {numero_processo}, Rel. {relator}, {orgao_julgador}, j. {DD/MM/YYYY}
```

Exemplo real da base:

```
TJSP, Apelação Cível 1135221-60.2022.8.26.0100, Rel. Caio Marcelo Mendes de
Oliveira, 32ª Câmara de Direito Privado, j. 08/10/2024
```

Componentes ausentes são omitidos junto com sua pontuação. Nos 49.036 registros
atuais, `classe`, `relator`, `orgao_julgador`, `data_julgamento` e
`ementa_texto` não têm nulo nenhum, mas a formatação não pode presumir isso.

O campo `relator` armazena o nome puro, sem tratamento. A formatação **não**
acrescenta "Des." nem "Desa.": o dado não traz o título, e inferi-lo erraria o
gênero de parte dos relatores. Não inventar o que a fonte não diz é a mesma
regra que governa o `excerpt`.

### 3.4 Observabilidade

**Log.** `logging` passa a escrever em stdout; a rotação fica com o driver
`json-file` do Docker (`max-size: 10m`, `max-file: 5`). Não se usa
`RotatingFileHandler` — em container isso duplica um mecanismo que a plataforma
já oferece.

O diretório `logs/`, hoje com um arquivo de 13 MB produzido por redirecionamento
de shell, deixa de ser o destino de log. Permanece no `.gitignore`.

As linhas `[FETCH] ↓`, `[SCRAPE] ◆` e `[COMPLETE] ●` emitidas pelo crawl4ai são
três por página coletada e representam a maior parte dos 13 MB do log atual, sem
valor operacional. São silenciadas.

**Métricas.** `execucoes` já responde "está rodando?" via `ultima_execucao` e
`ultima_qtd`. Não responde à degradação silenciosa: o coletor roda, não levanta
erro, e devolve zero porque o layout do e-SAJ mudou. Detectar isso exige
histórico, comparando o resultado de hoje com a mediana recente.

Tabela `metricas_coleta`, append-only, uma linha por tema por rodada.

### 3.5 Alerta

Canal: bot do Telegram por HTTP. Sem dependência nova, sem SMTP, notificação no
celular.

Gatilhos:

1. `BloqueioDetectadoError` — imediato, sempre.
2. Três **rodadas** consecutivas com falha no mesmo tema (não três falhas dentro
   de uma rodada: há uma tentativa por tema por rodada).
3. Rodada inteira em que **nenhum tema retornou resultado algum** — suspeita de
   parser quebrado.
4. Nenhuma rodada bem-sucedida em 36 horas.

O gatilho 3 mede resultados retornados, **não decisões novas**. A distinção é o
que separa um alerta útil de um que grita todo dia até ser ignorado: numa rodada
incremental saudável é normal que `novas` seja zero, porque a janela de busca
(`watermark - 2 dias` até hoje) sempre cobre decisões já conhecidas, que entram
como `atualizadas`. O que nunca é normal é a busca voltar vazia em todos os
temas — isso é o formato exato de um parser quebrado ou de um layout alterado.

**O gatilho 4 não pode viver dentro do coletor.** Se o processo morreu, ele não
avisa que morreu. É um dead man's switch e roda fora: `python -m juris.watchdog`
em cron horário, consultando apenas o banco. Coletor e watchdog compartilham o
Postgres, então o watchdog não precisa saber nada sobre o processo vigiado.

**Cooldown.** Sem ele o alerta se autodestrói: um tribunal bloqueado geraria
centenas de mensagens até o operador silenciar o bot — e o canal morre
exatamente quando importa. Tabela `alertas_enviados(chave, enviado_em)`; a mesma
chave não se repete dentro de 6 horas (configurável).

**Falha de entrega não derruba a coleta.** Se a API do Telegram estiver fora, o
envio é registrado em log e a rodada segue. O alerta é best-effort; o watchdog é
a rede de segurança.

O token do bot nunca vai para log.

### 3.6 Autenticação

Tabela `api_keys` com escopo (`publico` | `interno`) e tabela `api_key_uso`
particionada por dia.

- Formato da chave: `juris_<32 caracteres urlsafe>`, exibida uma única vez na
  criação.
- O banco guarda somente `sha256` da chave.
- Comparação com `secrets.compare_digest`, não `==`.
- Header `Authorization: Bearer <chave>`, substituindo o `X-API-Key` do stub
  atual — é o que o contrato da Fase 4 já previa, e nenhum consumidor existe
  ainda.
- Resposta 401 genérica: não distinguir "sem chave" de "chave inválida", para
  não facilitar enumeração.
- A contagem diária em `api_key_uso` se reinicia sozinha pela chave primária
  `(api_key_id, dia)` — sem cron de reset — e já serve de base para cobrança.

CLI: `python -m juris.apikeys criar|listar|revogar`.

**Sobre o escopo nesta onda.** Nenhum endpoint da onda 0 exige `interno` — o
acervo doutrinário só existe na onda 5. O mecanismo de verificação é implementado
e testado mesmo assim (a dependência aceita o escopo exigido como parâmetro,
com `interno` implicando `publico`), porque a alternativa seria uma coluna morta
no banco. O teste usa uma rota registrada apenas no ambiente de teste; nenhum
endpoint de produção declara `interno` antes da onda 5.

**`/v1/health` deixa de vazar o acervo.** Hoje devolve `total_decisoes` e
`ultima_coleta` sem autenticação; num IP público isso entrega tamanho da base e
ritmo de coleta a qualquer varredura. Passa a devolver `{"status": "ok"}` para
chamador anônimo, mantendo utilidade para verificação de disponibilidade. A
versão detalhada, com frescor por tribunal, exige chave.

### 3.7 Deploy

`docker-compose.prod.yml` separado, seguindo a convenção de `infra/` que o
`reis-esteves-squad` já usa. Serviços: `db`, `api`, `crawler`, `caddy`.

**A senha do banco sai do repositório.** O `docker-compose.yml` atual traz
`POSTGRES_PASSWORD: juris` e a URL `postgresql+asyncpg://juris:juris@db:5432/juris`
escritas no arquivo. Em rede local isso é indiferente; num host público é a
primeira coisa que uma varredura tenta. A senha passa a ser gerada e lida de
`.env` (já ignorado pelo git).

**Postgres não publica porta em produção**, nem em `127.0.0.1`. `api` e
`crawler` o alcançam pela rede do compose; administração por
`docker compose exec`. Porta nenhuma exposta é estritamente melhor que porta em
loopback. O bind em `127.0.0.1` do compose de desenvolvimento permanece, com o
comentário que o justifica.

**`api` também não publica porta.** Só o Caddy fala com ela, e só o Caddy expõe
80/443, com TLS automático. O `--host 0.0.0.0` do uvicorn fica confinado à rede
interna do compose.

**Containers como usuário não-root.** Isso interage com uma peculiaridade do
Chromium, que recusa rodar como root sem `--no-sandbox`. Rodar como usuário
comum permite manter o sandbox ligado — a defesa contra HTML hostil vindo de
site de terceiro, que é precisamente o que um crawler consome.

Todos os serviços com `restart: unless-stopped` e healthcheck, não só o banco.

### 3.8 Backup

`pg_dump -Fc` diário, retenção de 7 diários e 4 semanais, upload para endpoint
S3-compatível.

O teste faz parte da entrega: um script restaura o dump num container descartável
e roda `select count(*) from decisoes`. O caminho de upload é exercitado contra
um **MinIO local**, num profile de desenvolvimento do compose — do contrário é
código que executa pela primeira vez em produção, de madrugada, sem ninguém
olhando.

### 3.9 Preparação para outras áreas do direito

O sistema já é agnóstico de domínio. Uma verificação em `src/` encontra a
palavra "digital" em exatamente duas linhas, ambas no título e na descrição do
FastAPI em `api/main.py`. O coletor não sabe o que é direito digital: recebe uma
string de busca e a envia ao e-SAJ. Todo o domínio mora em `config/temas.yaml`,
e o `Tema` já carrega configuração por tema (`campo: ementa` vs `inteiro_teor`).

Acrescentar direito de família ou do consumidor é, portanto, acrescentar
entradas num arquivo YAML — **exceto por um ponto que fica gravado nos dados.**

**Os slugs são globais e vivem no array `temas` de cada decisão.** Hoje são
`lgpd`, `marco-civil`, `perfil-falso`. Quando uma segunda área entrar,
`dano-moral` e `prescricao` vão querer existir em duas áreas ao mesmo tempo, e a
colisão estará espalhada por centenas de milhares de linhas.

Decisão: **prefixar o slug pela área** — `digital-lgpd`, `digital-marco-civil`.
Resolve a colisão por convenção de nomenclatura, sem coluna nova e sem parsing
de separador. Os 10 slugs atuais são renomeados agora, com 49.036 linhas a
atualizar e um contrato de API que nenhum consumidor usa ainda. É o momento mais
barato em que essa mudança pode ser feita.

O campo `area` explícito e a coluna `areas` em `decisoes` — necessários para que
o Squad Digital filtre e não receba acórdão de direito de família — ficam para
quando a segunda área existir de fato.

## 4. Modelo de dados

### Alterações em `decisoes` (revisão 002)

```sql
ALTER TABLE decisoes ADD COLUMN url_origem   TEXT;
ALTER TABLE decisoes ADD COLUMN content_hash TEXT;

UPDATE decisoes
   SET url_origem = 'https://esaj.tjsp.jus.br/cjsg/getArquivo.do?cdAcordao='
                    || cd_acordao || '&cdForo=0'
 WHERE tribunal = 'TJSP' AND url_origem IS NULL;

UPDATE decisoes
   SET content_hash = 'sha256:'
                      || encode(sha256(convert_to(coalesce(ementa_texto,''),'UTF8')),'hex')
 WHERE content_hash IS NULL;
```

Sem índice em `content_hash`: o acesso é por `id`, não por hash. Deduplicação
entre tribunais não é caso de uso desta onda.

### Tabelas novas (revisão 003)

```sql
CREATE TABLE metricas_coleta (
  id          BIGSERIAL PRIMARY KEY,
  rodada_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
  tribunal    VARCHAR(10) NOT NULL,
  tema_slug   VARCHAR(60) NOT NULL,
  novas       INTEGER NOT NULL DEFAULT 0,
  atualizadas INTEGER NOT NULL DEFAULT 0,
  paginas     INTEGER NOT NULL DEFAULT 0,
  falhou      BOOLEAN NOT NULL DEFAULT false,
  erro        TEXT,
  duracao_s   NUMERIC(10,2)
);
CREATE INDEX ix_metricas_rodada
    ON metricas_coleta (tribunal, tema_slug, rodada_em DESC);

CREATE TABLE alertas_enviados (
  chave      TEXT PRIMARY KEY,
  enviado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
  id            SERIAL PRIMARY KEY,
  nome          VARCHAR(100) NOT NULL UNIQUE,
  key_hash      CHAR(64) NOT NULL UNIQUE,
  escopo        VARCHAR(20) NOT NULL DEFAULT 'publico',
  ativa         BOOLEAN NOT NULL DEFAULT true,
  limite_diario INTEGER,
  criada_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
  ultimo_uso_em TIMESTAMPTZ
);

CREATE TABLE api_key_uso (
  api_key_id  INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
  dia         DATE NOT NULL,
  requisicoes INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (api_key_id, dia)
);
```

### Prefixo de área nos slugs (revisão 004)

```sql
-- O guard NOT LIKE torna a migração inofensiva se reexecutada.
UPDATE decisoes
   SET temas = ARRAY(
         SELECT DISTINCT CASE WHEN t LIKE 'digital-%' THEN t ELSE 'digital-' || t END
           FROM unnest(temas) AS t ORDER BY 1);

UPDATE execucoes
   SET tema_slug = 'digital-' || tema_slug
 WHERE tema_slug NOT LIKE 'digital-%';
```

`metricas_coleta` é criada vazia na revisão 003 e não precisa de renomeação.
`config/temas.yaml` tem os 10 slugs renomeados no mesmo commit. O `downgrade`
reverte removendo o prefixo.

## 5. Interfaces

### Variáveis de ambiente (prefixo `JURIS_`)

| Variável | Uso |
|---|---|
| `DATABASE_URL` | já existe |
| `BACKFILL_INICIO`, `WATERMARK_MARGEM_DIAS`, `MAX_PAGINAS_POR_RODADA`, `REQ_INTERVALO_SEGUNDOS` | já existem |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | alerta; segredo |
| `ALERTA_COOLDOWN_HORAS` | padrão 6 |
| `WATCHDOG_LIMITE_HORAS` | padrão 36 |
| `BACKUP_S3_ENDPOINT`, `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` | backup; segredo |
| `BACKUP_RETENCAO_DIARIOS`, `BACKUP_RETENCAO_SEMANAIS` | padrão 7 e 4 |

Ausência de `TELEGRAM_BOT_TOKEN` desliga o alerta com aviso em log, sem quebrar
a coleta — o desenvolvimento local não deve exigir um bot.

### Endpoints

| Endpoint | Mudança |
|---|---|
| `GET /v1/decisoes` | exige `Bearer` |
| `GET /v1/decisoes/{id}` | exige `Bearer`; resposta ganha `url_origem`, `url_requer_login`, `referencia_citavel`, `content_hash`, `retrieved_at` |
| `GET /v1/temas` | exige `Bearer` |
| `GET /v1/health` | anônimo devolve `{"status":"ok"}`; com `Bearer` devolve total, última coleta e frescor por tribunal |

### CLI

```
python -m juris.apikeys criar --nome squad --escopo publico [--limite-diario N]
python -m juris.apikeys listar
python -m juris.apikeys revogar --nome squad
python -m juris.watchdog
scripts/backup.sh
scripts/restore-test.sh
```

## 6. Tratamento de erros

| Situação | Comportamento |
|---|---|
| Telegram indisponível | log de aviso, rodada segue; watchdog é a rede de segurança |
| `TELEGRAM_BOT_TOKEN` ausente | alerta desligado com aviso; sem exceção |
| Migração falha | Postgres tem DDL transacional; a revisão reverte inteira |
| Chave ausente ou inválida | 401 com mensagem genérica idêntica nos dois casos |
| Limite diário excedido | 429 |
| `BloqueioDetectadoError` | alerta, tema marcado como falho em `metricas_coleta`, rodada segue para o próximo tema (comportamento atual preservado) |
| Restore de teste diverge na contagem | script sai com código diferente de zero |

## 7. Testes

O plano de implementação é estruturado em TDD: teste que falha, código que passa,
refatoração.

Cobertura existente: `test_tjsp_parser.py` e `test_normalize.py`, 89 linhas.

Acréscimos:

- Hash em Python confere com o `UPDATE` de backfill, inclusive para
  `ementa_texto` nula.
- Construção de `url_origem` a partir de `cd_acordao`.
- Formatação de `referencia_citavel`, incluindo omissão graciosa de componente
  ausente e ausência de tratamento inventado no nome do relator.
- API key: válida, inválida, inativa, limite diário excedido, e escopo
  insuficiente (contra rota registrada apenas no ambiente de teste).
- `/v1/health` anônimo não revela contagem.
- Supressão do segundo alerta com a mesma chave dentro da janela.
- Gatilho 3 **não** dispara numa rodada em que os temas retornaram resultados
  mas nenhuma decisão era nova — o falso positivo que mataria o canal.
- Watchdog dispara com `ultima_execucao` envelhecido artificialmente.
- Renomeação de slug preserva a contagem por tema e é idempotente se
  reexecutada (nada vira `digital-digital-lgpd`).
- **`alembic upgrade head` em banco vazio seguido de `autogenerate` produzindo
  diff vazio.** Este é o teste mais valioso da onda: é o que impede `models.py`
  e as migrations divergirem, o modo de falha clássico de quem adota Alembic num
  projeto que já usava `create_all`.

## 8. Critérios de saída

| # | Critério |
|---|---|
| 1 | `upgrade head` em banco vazio → `autogenerate` com diff vazio |
| 2 | `stamp 001 && upgrade head` no banco atual preserva os 49.036 registros, preenche `content_hash` em 100% deles e `url_origem` em 100% dos registros TJSP |
| 3 | Restore do dump em container limpo devolve a mesma contagem |
| 4 | Upload de backup exercitado de ponta a ponta contra MinIO |
| 5 | Sem chave → 401; com chave → 200; `/v1/health` anônimo não revela acervo |
| 6 | Bloqueio injetado dispara Telegram uma vez; repetição em 6 h é suprimida |
| 7 | Watchdog dispara com watermark envelhecido |
| 8 | `pytest` verde e log de uma rodada curta na casa de dezenas de linhas |
| 9 | Após a revisão 004, a soma das contagens por tema é idêntica à anterior e todo slug começa com `digital-` |

## 9. Evidência que motivou decisões

Medições feitas em 2026-09-03, na máquina de desenvolvimento.

**Inteiro teor do e-SAJ exige login.** `getArquivo.do?cdAcordao=20819001&cdForo=0`
responde 302 para `jsp/verificarLoginArquivo.jsp`. Não há URL pública do PDF;
`url_origem` é gravada com `url_requer_login` derivado como verdadeiro, e a
`referencia_citavel` textual é a prova de origem que funciona sem sessão.

**Coluna gerada para `content_hash` é impossível.** `convert_to()` é `STABLE`;
o Postgres 16.14 rejeita a expressão de geração.

**Hosts e-SAJ respondem de forma desigual.** Sondagem de
`/cjsg/consultaCompleta.do` em 14 hosts: 200 em TJSP, TJAC, TJAL (`www2.tjal`),
TJAM (`consultasaj.tjam`) e TJCE; 403 em TJRN (bloqueio por user-agent, provável
que passe com browser); conexão recusada em TJBA e TJMS apesar de DNS válido; e
seis hosts sem DNS no padrão `esaj.<tj>.jus.br`. É o que sustenta adiar o
coletor genérico e recomendar VPS no Brasil.

**Integridade dos dados atuais.** Nos 49.036 registros, `classe`, `relator`,
`orgao_julgador`, `data_julgamento` e `ementa_texto` não têm nulos.

## 10. Riscos

| Risco | Mitigação |
|---|---|
| `stamp 001` aplicado a um banco cujo schema difere de `models.py` corromperia o histórico de migrations | O critério de saída 1 valida a equivalência antes; o `stamp` é feito depois, sobre backup |
| Rodar containers como não-root pode quebrar o Playwright | Validado localmente antes de entrar no runbook; se quebrar, o fallback documentado é `--no-sandbox` com o risco registrado |
| Métricas construídas para um scheduler serial serão parcialmente reescritas na onda 1 | Aceito conscientemente: são poucas horas de retrabalho, contra o custo de projetar a fila sem informação |
| Alerta por Telegram depende de serviço de terceiro | Watchdog independente; falha de entrega não derruba coleta |
| **Uma área nova do direito pode, sozinha, ser maior que todo o acervo atual** | Ver abaixo |

### O risco de volume ao abrir uma área nova

Este é o risco que a modularidade fácil esconde, e ele não é proporcional ao
número de temas.

O tempo de coleta escala bem. A rodada incremental completa medida no log levou
146 segundos para 10 temas — cerca de 15 s por tema. Com os workers paralelos da
onda 1 (um por tribunal), quatro áreas de ~45 temas dão cerca de 45 minutos de
relógio por dia por tribunal. O backfill histórico de uma área nova custa da
ordem de 18 horas por tribunal, uma única vez. Ambos são confortáveis.

O que **não** escala por analogia é o tamanho das áreas. Direito digital é uma
área estreita: 49.036 acórdãos em seis anos de TJSP de segundo grau. Direito do
consumidor, civil ou trabalhista no mesmo tribunal são uma ou duas ordens de
grandeza maiores — o TJSP julga milhões de processos. Uma única área larga pode
exceder sozinha tudo o que existe hoje na base, e isso muda armazenamento, custo
de embedding, tempo de construção do índice HNSW e janela de backfill de forma
não linear com o número de temas.

**Mitigação, e é a mesma disciplina imposta à coleta por assunto CNJ na onda 2:
antes de comprometer prazo com uma área nova, coletar um mês de amostra e medir
o volume.** Se um mês de uma área devolver dezenas de milhares de acórdãos, ela
entra com recorte adicional, ou não entra.

## 11. Referências

- Plano de escala completo (ondas 0 a 6): artefato publicado em 2026-09-03.
- Contrato `LegalSource`: `reis-esteves-squad/CLAUDE.md` §8.
- Fase 4 do roadmap: `reis-esteves-squad/docs/roadmap_mvp_squad_digital.md`.
