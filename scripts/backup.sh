#!/usr/bin/env bash
# Backup do banco: pg_dump comprimido, retenção local e upload S3-compatível.
#
# Uso:   scripts/backup.sh [arquivo-compose]
# Cron:  0 4 * * * cd /opt/juris && scripts/backup.sh docker-compose.prod.yml
set -euo pipefail

# O cron não carrega .env. Sem isto o upload some em silêncio e o backup
# passa a existir só na máquina que ele deveria proteger.
#
# O ensaio em `bash -ec` antes do `source` de verdade existe porque o
# `source` acontece antes do pg_dump: um .env que o bash não consiga ler
# (valor com espaço, aspas, `$` — o parser do Compose aceita coisas que o
# shell não) derrubaria o script inteiro com um "command not found" e rc=127,
# sem gerar dump nenhum e sem dizer o que aconteceu. Melhor nomear a falha.
if [[ -f .env ]]; then
  bash -ec 'set -a; . ./.env' || {
    echo "ERRO: o bash não consegue ler o .env (veja a mensagem acima)." >&2
    echo "      Valores com espaço, aspas, '\$' ou '#' precisam de aspas" >&2
    echo "      simples. Nenhum backup foi gerado." >&2
    exit 1
  }
  set -a; . ./.env; set +a
fi

COMPOSE="${1:-docker-compose.yml}"
DESTINO="${JURIS_BACKUP_DIR:-backups}"
RETENCAO_DIARIOS="${JURIS_BACKUP_RETENCAO_DIARIOS:-7}"
RETENCAO_SEMANAIS="${JURIS_BACKUP_RETENCAO_SEMANAIS:-4}"

mkdir -p "$DESTINO"
destino_abs="$(cd "$DESTINO" && pwd)"
carimbo="$(date +%Y%m%d-%H%M%S)"
arquivo="$DESTINO/juris-$carimbo.dump"

conta_decisoes() {
  docker compose -f "$COMPOSE" exec -T db \
    psql -U juris -d juris -tAc 'select count(*) from decisoes'
}

# Contagem ANTES e DEPOIS do dump. O snapshot do pg_dump é tomado no início
# da execução dele, e o backfill commita a cada 10 minutos: uma única leitura
# depois do dump poderia contar linhas que o dump não tem, e o restore-test
# acusaria FALHOU com o backup íntegro — exatamente o falso alarme que o
# arquivo irmão existe para eliminar. O que o dump contém está, com certeza,
# dentro do intervalo [antes, depois].
antes="$(conta_decisoes)"

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

# Arquivo irmão com o intervalo, duas linhas: mínimo e máximo. É contra ele —
# não contra o banco de produção "agora", que já ingeriu linhas novas até a
# hora do restore-test — que scripts/restore-test.sh compara.
depois="$(conta_decisoes)"
printf '%s\n%s\n' "$antes" "$depois" > "$arquivo.count"
echo "Decisões no dump: entre $antes e $depois"

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
  echo "AVISO: JURIS_BACKUP_S3_BUCKET não definido; mantendo apenas cópia local."
fi

# Retenção local: mantém os N diários mais recentes e, aos domingos, promove
# uma cópia semanal.
if [[ "$(date +%u)" == "7" ]]; then
  cp "$arquivo" "$DESTINO/semanal-$carimbo.dump"
  # A cópia semanal leva o arquivo irmão junto: sem ele, restaurar um dump
  # semanal daqui a um mês não teria contra o que ser conferido.
  cp "$arquivo.count" "$DESTINO/semanal-$carimbo.dump.count"
fi
# `|| true`: enquanto não existir nenhum dump diário/semanal além do que
# acabou de ser gerado, `ls` não acha nada e sai com erro — sem isto, o
# `set -e` derrubaria um backup que já tinha sido concluído com sucesso só
# porque não havia nada para limpar ainda.
ls -1t "$DESTINO"/juris-*.dump 2>/dev/null | tail -n +$((RETENCAO_DIARIOS + 1)) | xargs -r rm -- || true
ls -1t "$DESTINO"/semanal-*.dump 2>/dev/null | tail -n +$((RETENCAO_SEMANAIS + 1)) | xargs -r rm -- || true

# Arquivo irmão órfão (dump que a retenção acima já apagou) não serve mais
# para nada e só acumula. Sem `|| true`, ao contrário das duas linhas de
# retenção acima: aqui um `rm` só falha por problema real (permissão, FS
# somente-leitura), nunca por "não havia nada para apagar" — o `[[ -e ]]`
# já cobre esse caso. Sair diferente de zero é a resposta certa, mesmo com o
# dump e o upload já concluídos: é sinal de que o disco não está saudável.
for arquivo_count in "$DESTINO"/juris-*.dump.count "$DESTINO"/semanal-*.dump.count; do
  [[ -e "$arquivo_count" ]] || continue
  [[ -e "${arquivo_count%.count}" ]] || rm -- "$arquivo_count"
done

echo "Backup concluído."
