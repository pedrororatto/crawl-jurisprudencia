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
destino_abs="$(cd "$DESTINO" && pwd)"
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
  # O `aws` CLI não é uma dependência do host: roda em container descartável
  # (amazon/aws-cli), com o diretório de backups montado e as credenciais
  # passadas só como variáveis de ambiente do processo — nunca gravadas em
  # arquivo. `host.docker.internal` permite alcançar um MinIO local publicado
  # no host (uso em desenvolvimento); em produção o endpoint é uma URL
  # pública e essa entrada extra de hosts é inofensiva.
  docker run --rm \
    --add-host host.docker.internal:host-gateway \
    -e AWS_ACCESS_KEY_ID="$JURIS_BACKUP_S3_ACCESS_KEY" \
    -e AWS_SECRET_ACCESS_KEY="$JURIS_BACKUP_S3_SECRET_KEY" \
    -v "$destino_abs:/backups:ro" \
    amazon/aws-cli --endpoint-url "$JURIS_BACKUP_S3_ENDPOINT" \
      s3 cp "/backups/$(basename "$arquivo")" "s3://$JURIS_BACKUP_S3_BUCKET/diarios/$(basename "$arquivo")"
  echo "Upload concluído."
else
  echo "JURIS_BACKUP_S3_BUCKET não definido; mantendo apenas cópia local."
fi

# Retenção local: mantém os N diários mais recentes e, aos domingos, promove
# uma cópia semanal.
if [[ "$(date +%u)" == "7" ]]; then
  cp "$arquivo" "$DESTINO/semanal-$carimbo.dump"
fi
# `|| true`: enquanto não existir nenhum dump diário/semanal além do que
# acabou de ser gerado, `ls` não acha nada e sai com erro — sem isto, o
# `set -e` derrubaria um backup que já tinha sido concluído com sucesso só
# porque não havia nada para limpar ainda.
ls -1t "$DESTINO"/juris-*.dump 2>/dev/null | tail -n +$((RETENCAO_DIARIOS + 1)) | xargs -r rm -- || true
ls -1t "$DESTINO"/semanal-*.dump 2>/dev/null | tail -n +$((RETENCAO_SEMANAIS + 1)) | xargs -r rm -- || true

echo "Backup concluído."
