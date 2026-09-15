#!/usr/bin/env bash
# PyXie Manager backup -- Stage W0 prerequisite before any PVE write capability.
#
# Deliberately writes OUTSIDE /opt/henderlabs/apps/pyxie-manager/ (the app's
# own source/config directory) so a future rsync mistake against the app
# dir (see README "Operational incident during this build" -- a --delete
# rsync once wiped .env) can never also destroy the backups meant to
# recover from exactly that kind of mistake.
#
# Run manually before every deploy/schema migration, and on a daily cron
# (see ops/install-cron.sh). Never prints secret values to stdout/stderr.

set -euo pipefail

APP_DIR="/opt/henderlabs/apps/pyxie-manager"
BACKUP_ROOT="/opt/henderlabs/backups/pyxie-manager"
KEEP=14

TS="$(date +%Y-%m-%d_%H%M%S)"
DEST="$BACKUP_ROOT/$TS"
mkdir -p "$DEST"

echo "[backup] $TS -> $DEST"

# 1) source + compose/config tree (NOT .env -- that's handled separately
#    below with tighter permissions, matching the review's requirement to
#    treat it as protected, not just another file in the tar).
tar --exclude='node_modules' --exclude='.next' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.env' \
    -C "$APP_DIR" -cf - . | zstd -q -19 -o "$DEST/source.tar.zst"

# 2) protected .env backup -- restrictive perms from the moment it's written,
#    never world/group readable even transiently.
umask 077
cp "$APP_DIR/.env" "$DEST/env.backup"
chmod 600 "$DEST/env.backup"

# 3) Postgres dump, straight out of the running container -- no need to
#    stop anything, pg_dump is a consistent point-in-time snapshot.
docker exec pyxie-manager-db pg_dump -U pyxie_manager pyxie_manager | gzip > "$DEST/pyxie.sql.gz"

# 4) manifest -- what's here, and checksums so restore can verify integrity
#    before trusting any of it.
{
    echo "backup_timestamp=$TS"
    echo "app_dir=$APP_DIR"
    echo "host=$(hostname)"
    echo "---"
    sha256sum "$DEST"/source.tar.zst "$DEST"/env.backup "$DEST"/pyxie.sql.gz
} > "$DEST/manifest.txt"

chmod -R go-rwx "$DEST"

# 5) retention -- keep the most recent $KEEP backups, prune the rest.
ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm -rf

echo "[backup] done: $DEST"
du -sh "$DEST"
