#!/usr/bin/env bash
# Copy the NATIVE (host Postgres) database into the containerized one.
# Safe to re-run: it dumps the native DB (read-only) and restores into the
# container with --clean, replacing whatever the container DB held.
#
#   ops/docker/restore-native-db.sh
#
# Run from the repo checkout that holds the stack's .env. Needs docker access.
# Does not stop, start or modify the native services or the native database.
# Does NOT start api/worker/web/caddy -- see docs/docker-caddy.md.

set -euo pipefail
cd "$(dirname "$0")/../.."

[[ -f .env ]] || { echo ".env not found in $(pwd)"; exit 1; }
set -a; . ./.env; set +a
: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" "${POSTGRES_PASSWORD:?}"

# The native DATABASE_URL (localhost) is what we dump FROM.
NATIVE_URL="${DATABASE_URL/postgresql+psycopg2/postgresql}"
case "$NATIVE_URL" in *pyxie-manager-db*) echo "DATABASE_URL points at the container, not the native DB; aborting."; exit 1;; esac

BACKUP_DIR="${BACKUP_DIR:-$HOME/pyxie-backups}"
mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
DUMP="$BACKUP_DIR/pyxie_manager_native_to_docker_$(date +%Y%m%d_%H%M%S).dump"

echo "[1/4] Dumping native database -> $DUMP"
pg_dump -Fc -d "$NATIVE_URL" -f "$DUMP"
ls -lh "$DUMP"

echo "[2/4] Starting container db"
docker compose up -d pyxie-manager-db
for i in $(seq 1 30); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' pyxie-manager-db)" == "healthy" ]] && break
  sleep 2
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' pyxie-manager-db)" == "healthy" ]] || { echo "container db not healthy"; exit 1; }

echo "[3/4] Restoring into container db (replaces its contents)"
docker compose exec -T pyxie-manager-db \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges < "$DUMP"

echo "[4/4] Sanity check"
docker compose exec -T pyxie-manager-db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select 'alembic_version', version_num from alembic_version union all select 'workloads', count(*)::text from workloads union all select 'users', count(*)::text from users"
echo "Done. Dump kept at $DUMP"
