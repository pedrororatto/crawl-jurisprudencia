# Runbook de deploy — Juris em produção

Este documento é o guia operacional para subir o Juris numa VPS. Ele assume
que quem o segue **não conhece o código** deste repositório, mas sabe operar
Linux, SSH e Docker. Todo comando abaixo é para ser copiado e colado como
está — os únicos pontos que exigem preenchimento manual estão marcados com
`<placeholder>`.

Convenção usada no documento: comandos prefixados com `vps$` rodam **na
VPS**, via SSH; comandos prefixados com `dev$` rodam na **máquina de
desenvolvimento** (onde este repositório já está clonado e o Postgres de
desenvolvimento roda em Docker).

---

## 1. Pré-requisitos da VPS

- **Hostinger, plano KVM 4** (4 vCPU, 16 GB RAM, 200 GB NVMe).
- **Datacenter em São Paulo**, obrigatório — não é uma preferência de
  latência. Na sondagem que motivou este projeto (§9 da spec de onda 0),
  **TJBA e TJMS recusaram conexão vinda de fora do Brasil**, apesar de DNS
  válido. Um datacenter fora do país derruba dois dos tribunais-alvo antes
  mesmo do coletor genérico existir.
- **Preço**: R$ 59,99/mês no plano promocional, condicionado a
  **pré-pagamento anual**. A **renovação (segundo ano em diante) é
  R$ 149,99/mês** — registre isso agora para não ser surpresa na renovação.
  Se a carga crescer e KVM 4 não bastar (ver §10 da spec, "o risco de volume
  ao abrir uma área nova"), o caminho é **redimensionar para KVM 8** (8
  vCPU, 32 GB) dentro do mesmo provedor Hostinger — não trocar de provedor,
  porque o requisito de datacenter brasileiro se manteria.
- Sistema operacional: este runbook assume **Ubuntu 22.04 ou 24.04 LTS**
  (imagem padrão da Hostinger para KVM).
- Acesso root inicial via senha ou chave fornecida pela Hostinger no
  provisionamento.
- Um domínio (ou subdomínio) apontando o registro `A` para o IP da VPS —
  necessário para o Caddy emitir o certificado TLS automaticamente.
- Um bot do Telegram já criado (fale com `@BotFather`) e o `chat_id` de
  destino dos alertas — necessários na seção 3.

Não é preciso instalar `awscli` na VPS: o script de backup roda o `aws` CLI
dentro de um container descartável (`amazon/aws-cli`), não como binário do
host. O único software que este runbook instala na VPS é `git`, `docker`,
`ufw` e `fail2ban` (seção 2) — o Docker Compose vem embutido no Docker via
plugin (`docker compose`, sem hífen), instalado junto.

---

## 2. Endurecimento do host

Faça isto **antes** de clonar o repositório. Todos os comandos desta seção
rodam como `root` (ou com `sudo`), via SSH, na VPS recém-provisionada.

### 2.1. Chave SSH e desativação de senha

Na sua máquina local, se ainda não tiver uma chave dedicada:

```bash
dev$ ssh-keygen -t ed25519 -C "juris-vps" -f ~/.ssh/juris_vps
dev$ ssh-copy-id -i ~/.ssh/juris_vps.pub root@<ip-da-vps>
```

Confirme que consegue logar com a chave **antes** de desativar a senha:

```bash
dev$ ssh -i ~/.ssh/juris_vps root@<ip-da-vps>
```

Na VPS, edite `/etc/ssh/sshd_config` e garanta estas linhas (descomentadas,
sem outras entradas conflitantes):

```bash
vps$ sudo sed -i \
  -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
  -e 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' \
  /etc/ssh/sshd_config
vps$ sudo systemctl restart ssh
```

A partir daqui, só chave SSH autentica — login por senha para qualquer
usuário está desligado.

### 2.2. Firewall (UFW)

Libere só o necessário: SSH, HTTP e HTTPS. O Postgres (5432) e a API direta
(8000) **não** entram na lista — o `docker-compose.prod.yml` não publica
essas portas no host, então não há nada para liberar nelas.

```bash
vps$ sudo apt update && sudo apt install -y ufw
vps$ sudo ufw allow 22/tcp
vps$ sudo ufw allow 80/tcp
vps$ sudo ufw allow 443/tcp
vps$ sudo ufw --force enable
vps$ sudo ufw status verbose
```

### 2.3. fail2ban

Protege o SSH contra tentativas de força bruta (mitiga o que o UFW sozinho
não filtra: várias tentativas de login vindas do mesmo IP na porta que ficou
aberta).

```bash
vps$ sudo apt install -y fail2ban
vps$ sudo systemctl enable --now fail2ban
vps$ sudo fail2ban-client status sshd
```

---

## 3. Repositório e ambiente

### 3.1. Dependências do sistema

```bash
vps$ sudo apt install -y git
vps$ curl -fsSL https://get.docker.com | sudo sh
```

O script oficial do Docker instala o daemon, o CLI e o plugin `docker
compose` juntos. Confirme:

```bash
vps$ docker --version
vps$ docker compose version
```

### 3.2. Clonar o repositório

O repositório é privado. Antes de clonar, adicione a chave pública da VPS
(gere uma dedicada, não reaproveite a do passo 2.1) como **Deploy Key**
(somente leitura) em GitHub → Settings → Deploy keys do repositório
`pedrororatto/crawl-jurisprudencia`.

```bash
vps$ ssh-keygen -t ed25519 -C "deploy-key-vps" -f ~/.ssh/id_ed25519 -N ""
vps$ cat ~/.ssh/id_ed25519.pub
# cole a saída acima em GitHub → Settings → Deploy keys (read-only)
vps$ sudo mkdir -p /opt/juris
vps$ sudo chown "$(whoami)":"$(whoami)" /opt/juris
vps$ git clone git@github.com:pedrororatto/crawl-jurisprudencia.git /opt/juris
vps$ cd /opt/juris
vps$ mkdir -p logs backups
```

### 3.3. Arquivo de ambiente

```bash
vps$ cp .env.prod.example .env
vps$ openssl rand -base64 32
```

Cole a saída do `openssl rand` em `POSTGRES_PASSWORD=` dentro de `.env`.
Edite `.env` (`nano .env` ou `vim .env`) e preencha também:

- `JURIS_DOMINIO` — o domínio que aponta para esta VPS (ex.:
  `juris.exemplo.com.br`).
- `JURIS_TELEGRAM_BOT_TOKEN` e `JURIS_TELEGRAM_CHAT_ID` — do bot criado no
  pré-requisito (seção 1).
- Opcionalmente, `JURIS_BACKUP_S3_ENDPOINT`, `JURIS_BACKUP_S3_BUCKET`,
  `JURIS_BACKUP_S3_ACCESS_KEY`, `JURIS_BACKUP_S3_SECRET_KEY` — só se você já
  tiver um bucket S3-compatível para backup externo (ver seção 7). Sem
  essas variáveis, o backup local ainda funciona; só o upload externo fica
  desligado.

`.env` nunca é versionado (está no `.gitignore`). Confirme:

```bash
vps$ git status --short
# .env não deve aparecer na saída
```

---

## 4. Primeira subida — banco vazio

Use esta seção quando a VPS **começa do zero, sem dados anteriores**. Se
você está migrando um banco já existente da máquina de desenvolvimento, pule
para a seção 5 — ela substitui o passo de migração abaixo, não o soma a ele.

**A ordem importa.** Nem `api` nem `crawler` esperam a migração — os dois
compose files só esperam o `db` ficar saudável (`depends_on: condition:
service_healthy`). Se você subir tudo de uma vez com `docker compose up -d`,
`api` e `crawler` vão subir contra um schema que ainda não existe. A
sequência correta é: banco, depois migração, só então o resto.

```bash
vps$ cd /opt/juris
vps$ docker compose -f docker-compose.prod.yml up -d db
```

Espere o banco ficar saudável:

```bash
vps$ until docker compose -f docker-compose.prod.yml exec db pg_isready -U juris; do sleep 2; done
```

Agora, e só agora, rode a migração:

```bash
vps$ docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```

Confirme que chegou em `006 (head)`:

```bash
vps$ docker compose -f docker-compose.prod.yml run --rm api alembic current
```

Só então suba o resto:

```bash
vps$ docker compose -f docker-compose.prod.yml up -d
vps$ docker compose -f docker-compose.prod.yml ps
```

---

## 5. Restauração de um banco existente

Use esta seção **em vez da** migração da seção 4, quando você quer levar
para a VPS os dados já coletados na máquina de desenvolvimento (ou de outra
instalação), em vez de começar de um banco vazio.

### 5.1. O que o dump carrega, e por que isso muda o passo de migração

Na Task 1 desta onda de trabalho, a tabela `alembic_version` do banco de
desenvolvimento recebeu `alembic stamp 001` (o schema já existia antes de o
histórico de migrations existir; o `stamp` só registrou onde ele estava,
sem tentar recriar nada). Todas as migrations seguintes (002 a 006) foram
aplicadas normalmente por cima disso.

Um `pg_dump` da máquina de desenvolvimento inclui a tabela
`alembic_version` junto com os dados — ou seja, **o dump já chega com o
histórico de migrations completo**, apontando para a revisão em que o banco
de origem estava (hoje, `006`). Isso tem duas consequências práticas:

- **Não rode `alembic stamp` nenhum na VPS.** O `stamp 001` já está dentro
  do dump que você vai restaurar; repeti-lo (ou rodar um `stamp` diferente)
  reescreveria um histórico que já é o correto e poderia descolar
  `alembic_version` do schema real.
- **Rode `alembic upgrade head` mesmo assim, depois do restore.** Não para
  "aplicar do zero" — é para pegar qualquer migration escrita *depois* do
  dump ter sido tirado. Se o dump já está em `head`, o comando não faz nada
  (idempotente); se não está, ele completa a diferença.

### 5.2. Gerar o dump na máquina de desenvolvimento

```bash
dev$ cd /caminho/para/crawl-jurisprudência
dev$ ./scripts/backup.sh
```

Isso grava um arquivo em `backups/juris-<timestamp>.dump` e já confere a
integridade com `pg_restore --list`. Anote a contagem atual antes de
transferir, para conferir depois:

```bash
dev$ docker compose exec -T db psql -U juris -d juris -tAc 'select count(*) from decisoes'
```

### 5.3. Transferir para a VPS

```bash
dev$ scp backups/juris-<timestamp>.dump <usuario>@<ip-da-vps>:/opt/juris/backups/
```

### 5.4. Restaurar

Suba só o banco (schema ainda vazio):

```bash
vps$ cd /opt/juris
vps$ docker compose -f docker-compose.prod.yml up -d db
vps$ until docker compose -f docker-compose.prod.yml exec db pg_isready -U juris; do sleep 2; done
```

Restaure o dump:

```bash
vps$ docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U juris -d juris --no-owner < backups/juris-<timestamp>.dump
```

Complete a migração (ver 5.1 — não é opcional, mesmo restaurando de um
dump recente):

```bash
vps$ docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
vps$ docker compose -f docker-compose.prod.yml run --rm api alembic current
# esperado: 006 (head)
```

### 5.5. Verificação de integridade pós-restore

Este bloco cobre, de um só golpe, os critérios de saída 2 e 9 da spec desta
onda de trabalho: preservação da contagem, preenchimento de
`content_hash`/`url_origem`, e prefixo `digital-` em todos os slugs.

```bash
vps$ docker compose -f docker-compose.prod.yml exec -T db psql -U juris -d juris <<'SQL'
select count(*) as total_decisoes from decisoes;
select count(*) as sem_content_hash from decisoes where content_hash is null;
select count(*) as tjsp_sem_url_origem from decisoes where tribunal = 'TJSP' and url_origem is null;
select count(*) as slug_sem_prefixo
  from decisoes d, unnest(d.temas) t
 where t not like 'digital-%';
SQL
```

Esperado: `total_decisoes` igual ao valor anotado no passo 5.2 (na data em
que a spec desta onda foi escrita, 49.036); as outras três contagens em
zero. Qualquer uma delas maior que zero é motivo para não seguir adiante —
pare e investigue antes de subir `api`/`crawler`.

### 5.6. Subir o resto

```bash
vps$ docker compose -f docker-compose.prod.yml up -d
vps$ docker compose -f docker-compose.prod.yml ps
```

---

## 6. Criação da primeira chave de API

Com a stack no ar (seção 4 ou 5 concluída):

```bash
vps$ docker compose -f docker-compose.prod.yml exec api \
  python -m juris.apikeys criar --nome squad --escopo publico
```

A chave é impressa **uma única vez**, no formato `juris_<token>`. Copie-a
para um gerenciador de senhas imediatamente — o banco guarda só o hash
SHA-256, não há como recuperá-la depois. Escopos disponíveis: `publico`
(o padrão) e `interno` (alcança tudo que `publico` alcança, e mais).

Para listar chaves ativas/revogadas mais tarde:

```bash
vps$ docker compose -f docker-compose.prod.yml exec api python -m juris.apikeys listar
```

---

## 7. Cron: backup diário e watchdog de hora em hora

### 7.1. Descobrir o caminho do `docker`

O cron roda com um `PATH` mínimo (tipicamente só `/usr/bin:/bin`), que pode
não incluir onde o `docker` foi instalado. Descubra o caminho real **na
própria VPS**, depois de instalar o Docker (seção 3.1):

```bash
vps$ command -v docker
```

Com a instalação via `get.docker.com` num Ubuntu padrão, isso normalmente
devolve `/usr/bin/docker`. Use exatamente o que o comando acima devolveu —
não copie o caminho deste documento sem conferir.

### 7.2. Editar o crontab

```bash
vps$ crontab -e
```

Adicione uma linha `PATH=` no topo do arquivo (ajuste ao que `command -v
docker` devolveu) e as duas tarefas agendadas:

```cron
PATH=/usr/bin:/bin:/usr/local/bin

# Backup diário às 04:00
0 4 * * * cd /opt/juris && ./scripts/backup.sh docker-compose.prod.yml >> /opt/juris/logs/backup.log 2>&1

# Watchdog de hora em hora
0 * * * * cd /opt/juris && docker compose -f docker-compose.prod.yml run --rm crawler python -m juris.watchdog >> /opt/juris/logs/watchdog.log 2>&1
```

Confirme que os jobs estão registrados:

```bash
vps$ crontab -l
```

### 7.3. Antes de confiar no cron: exercite o upload uma vez, à mão

Se você preencheu as variáveis `JURIS_BACKUP_S3_*` no `.env` (seção 3.3),
rode o backup manualmente uma vez, fora do cron, para confirmar que o
upload chega ao bucket real antes de deixar isso rodando sozinho às 4h da
manhã:

```bash
vps$ cd /opt/juris
vps$ set -a && source .env && set +a
vps$ ./scripts/backup.sh docker-compose.prod.yml
```

Confira no console do seu provedor S3 (ou via `mc ls`, se ele for
compatível) que o arquivo `diarios/juris-<timestamp>.dump` chegou. Isso já
foi exercitado de ponta a ponta contra um MinIO local na Task 11 (critério
de saída 4 da spec); este passo repete o mesmo caminho de código, agora
contra o provedor de verdade.

**Ressalva conhecida:** `scripts/backup.sh` roda `amazon/aws-cli` sem
travar a versão (`docker run --rm amazon/aws-cli ...`, sem tag). Isso
significa que a imagem que o cron das 4h puxar numa madrugada pode não ser
byte-a-byte a mesma testada aqui. Não há sinal automático se uma versão
nova mudar o comportamento do `s3 cp` — o único jeito de perceber é o
`restore-test.sh` mensal (seção 12) ou uma checagem manual do bucket.

---

## 8. Teste do alerta

O gatilho mais crítico é o de bloqueio anti-robô (chave
`bloqueio:<tribunal>:<tema>`), porque coleta parada e silenciosa é o pior
modo de falha deste sistema. Teste o caminho completo — envio real ao
Telegram e supressão por cooldown — sem esperar um bloqueio de verdade
acontecer:

```bash
vps$ docker compose -f docker-compose.prod.yml run --rm crawler python -c "
import asyncio
from juris.alertas import alertar
from juris.db.engine import SessionLocal

async def main():
    async with SessionLocal() as session:
        enviado = await alertar(session, 'teste-runbook', 'Teste do runbook de deploy — pode ignorar.')
        print('primeira chamada, enviado =', enviado)

asyncio.run(main())
"
```

Confirme que a mensagem chegou no chat do Telegram configurado em
`JURIS_TELEGRAM_CHAT_ID`. Em seguida, rode o **mesmo comando de novo**,
dentro da janela de cooldown (6 horas por padrão,
`JURIS_ALERTA_COOLDOWN_HORAS`):

```bash
vps$ docker compose -f docker-compose.prod.yml run --rm crawler python -c "
import asyncio
from juris.alertas import alertar
from juris.db.engine import SessionLocal

async def main():
    async with SessionLocal() as session:
        enviado = await alertar(session, 'teste-runbook', 'Segunda tentativa — deve ser suprimida.')
        print('segunda chamada, enviado =', enviado)

asyncio.run(main())
"
```

Esperado: `primeira chamada, enviado = True` e uma mensagem no Telegram; na
segunda chamada, `enviado = False` e **nenhuma** mensagem nova — a chave
`teste-runbook` está em cooldown. Depois do teste, limpe o registro para não
confundir uma auditoria futura da tabela:

```bash
vps$ docker compose -f docker-compose.prod.yml exec -T db \
  psql -U juris -d juris -c "delete from alertas_enviados where chave = 'teste-runbook'"
```

Os gatilhos reais que rodam em produção (em `juris.scheduler.avaliar_gatilhos`
e `juris.watchdog`) são:

1. `bloqueio:<tribunal>:<tema>` — anti-robô detectado, dispara na hora.
2. `falhas-seguidas:<tribunal>:<tema>` — três rodadas seguidas falhando no
   mesmo tema.
3. `rodada-vazia:<tribunal>` — nenhum tema da rodada devolveu resultado
   algum.
4. `coleta-parada` (watchdog, processo separado) — nenhuma rodada bem
   sucedida em `JURIS_WATCHDOG_LIMITE_HORAS` (36h por padrão).

**Ressalva sobre o gatilho 3.** A chave de cooldown de `rodada-vazia` é
compartilhada entre rodada incremental e passo de backfill — não há uma
chave por tipo de rodada. Hoje isso não importa: o backfill já alcançou
janeiro de 2020 e está completo, então não há mais passos de backfill
rodando. Se `JURIS_BACKFILL_INICIO` for movido para uma data mais antiga no
futuro (reabrindo backfill), um mês legitimamente sem resultados durante o
backfill pode consumir a janela de 6h e mascarar uma rodada incremental
vazia de verdade que aconteça logo depois. Vale revisitar se isso
acontecer.

---

## 9. Verificação pós-deploy

### 9.1. `/v1/health` sem chave — não deve revelar acervo

```bash
vps$ curl -s https://<seu-dominio>/v1/health
```

Esperado: `{"status":"ok"}` — sem `total_decisoes` nem `ultima_coleta`.

### 9.2. `/v1/health` com chave — deve revelar acervo

```bash
vps$ curl -s https://<seu-dominio>/v1/health -H "Authorization: Bearer <chave-criada-na-secao-6>"
```

Esperado: `{"status":"ok","total_decisoes":<N>,"ultima_coleta":<timestamp-ou-null>}`.

### 9.3. Endpoint autenticado sem chave — deve recusar

```bash
vps$ curl -s -o /dev/null -w '%{http_code}\n' https://<seu-dominio>/v1/decisoes
```

Esperado: `401`.

### 9.4. Todos os serviços no ar

```bash
vps$ docker compose -f docker-compose.prod.yml ps
```

Esperado: `db` e `api` com status `healthy` (eles têm healthcheck
definido); `crawler` e `caddy` com status `Up` (não têm healthcheck
próprio, então não mostram `healthy` — isso é esperado, não é falha).

### 9.5. Restore testado

```bash
vps$ cd /opt/juris
vps$ ./scripts/restore-test.sh docker-compose.prod.yml
```

Esperado: `OK: restore íntegro, <N> decisões.` — o script sobe um Postgres
descartável, restaura o dump mais recente de `backups/` nele e compara a
contagem com a do banco em produção.

### 9.6. Rodada curta de fumaça

Só depois de todos os checks acima passarem, exercite uma coleta real e
pequena para confirmar que o coletor fala com o tribunal de ponta a ponta:

```bash
vps$ docker compose -f docker-compose.prod.yml run --rm crawler \
  python -m juris.scheduler --once --tema digital-lgpd --max-paginas 2
```

Esperado: um log de algumas dezenas de linhas (não um traceback), terminando
sem `BloqueioDetectadoError`.

---

## 10. Rollback

Nomeie cada deploy com uma tag antes de subir, para ter sempre um alvo
previsível de rollback:

```bash
dev$ git tag deploy-$(date +%Y-%m-%d)
dev$ git push --tags
```

Se um deploy precisar ser desfeito:

```bash
vps$ cd /opt/juris
vps$ git fetch --tags
vps$ git checkout <tag-anterior>
vps$ docker compose -f docker-compose.prod.yml run --rm api alembic downgrade -1
vps$ docker compose -f docker-compose.prod.yml up -d --build
```

**Ressalva sobre a migration 003 (prefixo `digital-` nos slugs).** O
`downgrade` dela é funcional — remove o prefixo `digital-` de volta — mas
**reescreve dados** (a coluna `temas` de toda `decisoes` e `tema_slug` de
toda `execucoes`). Se o rollback cruzar essa revisão (de `006` para
qualquer coisa abaixo de `003`), o `downgrade -1` sozinho não basta: rode
`alembic downgrade <revisao-alvo>` explicitamente e confira o resultado com
a mesma consulta da seção 5.5 antes de considerar o rollback concluído.
Prefira, sempre que possível, resolver o problema com um `git checkout`
para uma tag anterior **sem** downgrade de banco (só o código muda) — é o
caminho mais seguro quando o schema mais novo é compatível com o código
mais antigo.

---

## 11. Fallback do Chromium — registro histórico

Esta seção existe para registrar uma decisão já resolvida, não um risco em
aberto.

Na Task 10 desta onda de trabalho, a primeira tentativa de rodar o
container como usuário sem privilégio (`uid=10001`, usuário `juris`) falhou:
o Chromium do Playwright não subia. A causa não foi o sandbox do Chromium —
foi que `playwright install` roda como `root` durante o build da imagem, e
`/root` é `700` (inacessível ao usuário `juris` em runtime). A correção foi
definir `ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` **antes** do `RUN uv
run playwright install --with-deps chromium` no `Dockerfile`, tirando o
cache dos navegadores de dentro de `/root`, e `chmod -R a+rX` no diretório
resultante.

**Estado atual: o container roda como `uid=10001` com o sandbox do Chromium
ligado, verificado.** Não há `--no-sandbox` em vigor em lugar nenhum do
código (`BrowserConfig(headless=True, verbose=False)` em
`src/juris/collectors/tjsp_cjsg.py`, sem `extra_args`). Não há fallback
ativo nem pendente — isto não precisa de ação do operador.

Se uma regressão futura (troca de imagem base, atualização do Playwright)
reintroduzir o mesmo sintoma, o fallback documentado — e que **enfraquece**
a defesa contra HTML hostil, por isso é última instância — seria adicionar
`extra_args=["--no-sandbox"]` ao `BrowserConfig` citado acima e reconstruir
a imagem. Isso é uma mudança de código, não um passo de runbook, e deve ser
tratado como uma decisão consciente, revisada, não como um ajuste de
produção.

---

## 12. Rotina mensal

- **Testar o restore de verdade**, não só confiar que o backup rodou:

  ```bash
  vps$ cd /opt/juris
  vps$ ./scripts/restore-test.sh docker-compose.prod.yml
  ```

  Backup que nunca foi restaurado não é backup, é uma esperança.

- **Conferir o crescimento do disco**:

  ```bash
  vps$ df -h /
  vps$ du -sh /opt/juris/backups
  ```

  A VPS tem 200 GB de NVMe; a base de dados hoje (dezenas de milhares de
  registros de uma área estreita) é pequena perto disso, mas backups
  acumulam e uma área nova pode mudar a escala rapidamente (ver §10 da
  spec — "o risco de volume ao abrir uma área nova"). Se `du -sh
  backups` estiver crescendo mês a mês além do esperado pela retenção
  configurada (`JURIS_BACKUP_RETENCAO_DIARIOS` + `_SEMANAIS`), é sinal de
  que a limpeza não está funcionando — ver o próximo item.

- **Olhar a limpeza de retenção com desconfiança.** As duas linhas de
  `scripts/backup.sh` que apagam dumps antigos terminam em `|| true`:

  ```bash
  ls -1t "$DESTINO"/juris-*.dump 2>/dev/null | tail -n +$((RETENCAO_DIARIOS + 1)) | xargs -r rm -- || true
  ```

  Esse `|| true` existe para não derrubar um backup que já tinha sido
  concluído com sucesso só porque ainda não havia nada para limpar (o caso
  normal na primeira semana). O efeito colateral é que ele também engole em
  silêncio um `rm` que falhasse de verdade (permissão, disco cheio,
  arquivo em uso) — sem gerar nenhum sinal no log. Não há alarme automático
  para isso; a checagem é olhar `ls -la backups/` de vez em quando e
  confirmar que a contagem de arquivos bate com a retenção configurada.

---

## Referências

- [`docker-compose.prod.yml`](../docker-compose.prod.yml) — stack de produção.
- [`.env.prod.example`](../.env.prod.example) — variáveis a preencher no `.env` da VPS.
- [`scripts/backup.sh`](../scripts/backup.sh) — backup com verificação de integridade e retenção.
- [`scripts/restore-test.sh`](../scripts/restore-test.sh) — restore testado em container descartável.
- [`Caddyfile`](../Caddyfile) — TLS automático e cabeçalhos de segurança.
- [`docs/superpowers/specs/2026-09-03-onda-0-endurecimento-design.md`](superpowers/specs/2026-09-03-onda-0-endurecimento-design.md) — spec desta onda de trabalho, §8 (critérios de saída) e §9 (evidência que motivou as decisões acima).
