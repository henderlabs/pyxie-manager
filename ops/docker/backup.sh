#!/usr/bin/env bash
# Backup for the containerized stack: a pg_dump of the container database plus
# a private copy of .env (it holds PYXIE_CREDENTIAL_KEY -- without it, stored
# PVE credentials in a restored database are unreadable). Writes outside the
# repo checkout, keeps the newest $KEEP of each.
#
#   ops/docker/backup.sh

set -euo pipefail
cd "$(dirname "$0")/../.."

set -a; . ./.env; set +a
: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"

BACKUP_DIR="${BACKUP_DIR:-$HOME/pyxie-backups}"
KEEP="${KEEP:-14}"
TS="$(date +%Y%m%d_%H%M%S)"
umask 077
mkdir -p "$BACKUP_DIR"

docker compose exec -T pyxie-manager-db pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$BACKUP_DIR/pyxie_manager_docker_$TS.dump"
cp .env "$BACKUP_DIR/env_docker_$TS"

ls -1t "$BACKUP_DIR"/pyxie_manager_docker_*.dump 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm --
ls -1t "$BACKUP_DIR"/env_docker_* 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm --
echo "[backup] $BACKUP_DIR/pyxie_manager_docker_$TS.dump ($(du -h "$BACKUP_DIR/pyxie_manager_docker_$TS.dump" | cut -f1))"
