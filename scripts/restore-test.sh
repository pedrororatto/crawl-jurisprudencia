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
