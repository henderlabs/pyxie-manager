#!/usr/bin/env bash
# Installs the daily PyXie Manager backup as a cron job for the current
# user. Idempotent -- safe to re-run.
set -euo pipefail

APP_DIR="/opt/henderlabs/apps/pyxie-manager"
LINE="15 3 * * * $APP_DIR/ops/backup.sh >> /opt/henderlabs/backups/pyxie-manager/backup.log 2>&1"

mkdir -p /opt/henderlabs/backups/pyxie-manager
chmod +x "$APP_DIR/ops/backup.sh" "$APP_DIR/ops/restore.sh"

( crontab -l 2>/dev/null | grep -vF "$APP_DIR/ops/backup.sh" || true; echo "$LINE" ) | crontab -
echo "installed cron: $LINE"
crontab -l | grep pyxie-manager
