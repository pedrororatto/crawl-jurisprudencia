#!/usr/bin/env bash
# Restaura o dump mais recente num container descartável e confere a contagem.
# Backup não testado não é backup.
#
# Uso: scripts/restore-test.sh [arquivo-compose]
set -euo pipefail

# Mesmo motivo do backup.sh: o .env é onde JURIS_BACKUP_DIR pode ser
# redefinido. Se só o backup.sh o lesse, ele gravaria num diretório e este
# script procuraria noutro — "Nenhum dump em backups", exit 1, com o backup
# perfeito no lugar certo.
# Aqui basta ignorar um .env ilegível: este script só lê dele o
# JURIS_BACKUP_DIR, e o backup.sh já falha com mensagem clara nesse caso.
if [[ -f .env ]] && bash -ec 'set -a; . ./.env' 2>/dev/null; then
  set -a; . ./.env; set +a
fi

COMPOSE="${1:-docker-compose.yml}"
DESTINO="${JURIS_BACKUP_DIR:-backups}"
CONTAINER="juris-restore-teste"

dump="$(ls -1t "$DESTINO"/juris-*.dump 2>/dev/null | head -1 || true)"
[[ -n "$dump" ]] || { echo "Nenhum dump em $DESTINO"; exit 1; }
echo "Testando restore de $dump"

# A referência vem do arquivo irmão "$dump.count", que o backup.sh escreveu
# em torno do dump — não do banco de produção agora. Entre o dump (até 24h
# atrás) e este teste, a rodada incremental diária já ingeriu linhas, e
# comparar contra o banco vivo produziria FALHOU todo mês mesmo com o
# restore íntegro. São duas linhas, mínimo e máximo, porque o snapshot do
# pg_dump cai em algum ponto entre as duas leituras.
minimo=""
maximo=""
if [[ -f "$dump.count" ]]; then
  # `|| true` nos dois `read`: sob `set -e`, um arquivo truncado faria o
  # segundo `read` derrubar o script ANTES do pg_restore, sem uma palavra de
  # diagnóstico — indistinguível de um backup corrompido. E a validação
  # numérica é obrigatória porque a comparação abaixo é aritmética: lixo em
  # "$minimo" viraria `unbound variable` no meio de uma avaliação, não uma
  # mensagem legível.
  { read -r minimo || true; read -r maximo || true; } < "$dump.count"
  if [[ "$minimo" =~ ^[0-9]+$ && "$maximo" =~ ^[0-9]+$ ]]; then
    echo "Decisões esperadas (de $dump.count): entre $minimo e $maximo"
  else
    echo "AVISO: $dump.count ilegível (esperadas duas linhas numéricas);" \
         "usando checagem de sanidade."
    minimo=""
    maximo=""
  fi
else
  # Dump antigo, gerado antes desta correção, sem arquivo irmão. Cai para uma
  # relação de sanidade em vez de uma igualdade que não há como satisfazer.
  echo "Sem $dump.count (dump anterior a esta correção); usando checagem de sanidade."
fi

# Achado da cadeia do .env: se o backup parar de rodar (um .env malformado
# derruba o backup.sh antes do pg_dump), este script continuaria dizendo "OK"
# testando alegremente um dump de semanas atrás. Um backup velho passa no
# teste de restore e ainda assim não protege nada.
idade_h=$(( ( $(date +%s) - $(date -r "$dump" +%s) ) / 3600 ))
if (( idade_h > 48 )); then
  echo "AVISO: o dump mais recente tem ${idade_h}h. O backup diário pode ter" \
       "parado de rodar — confira $DESTINO e logs/backup.log."
fi

contagem_viva="$(docker compose -f "$COMPOSE" exec -T db \
  psql -U juris -d juris -tAc 'select count(*) from decisoes')"
echo "Contagem na origem agora: $contagem_viva"

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

if [[ -n "$minimo" && -n "$maximo" ]]; then
  if [[ "$obtido" -lt "$minimo" || "$obtido" -gt "$maximo" ]]; then
    echo "FALHOU: restore com $obtido decisões, fora de [$minimo, $maximo]"
    exit 1
  fi
else
  # Sem contagem de referência: exige ao menos alguma coisa restaurada, e
  # nada além do que o banco vivo tem hoje (um dump não pode conter mais
  # linhas do que a origem já acumulou).
  if [[ "$obtido" -le 0 || "$obtido" -gt "$contagem_viva" ]]; then
    echo "FALHOU: restore com $obtido decisões, fora da faixa de sanidade (0, $contagem_viva]"
    exit 1
  fi
fi
echo "OK: restore íntegro, $obtido decisões."
