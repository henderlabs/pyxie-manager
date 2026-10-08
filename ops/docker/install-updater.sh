#!/usr/bin/env bash
# One-time (and idempotent) setup of the update notifier/updater on THIS host. Run it as the user that
# owns the checkout (the one whose crontab already runs the backups), BEFORE bringing up a version that
# mounts ./update into the api container, so the folder is yours and not root's.
#
#   bash ops/docker/install-updater.sh
#
# What it does: creates ./update (world-writable: the api container's user must be able to drop
# request.json there), adds two crontab lines (check for releases every 30 min; act on a request every
# minute), and runs the first check. It changes nothing else.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO="$(pwd)"
command -v python3 >/dev/null || { echo "python3 is required on the host"; exit 1; }
mkdir -p update
chmod 0777 update
CHECK="*/30 * * * * cd $REPO && /usr/bin/python3 ops/docker/updater.py check >> $REPO/update/cron.log 2>&1"
POLL="* * * * * cd $REPO && /usr/bin/python3 ops/docker/updater.py poll >> $REPO/update/cron.log 2>&1"
TMP="$(mktemp)"
crontab -l 2>/dev/null | grep -v 'ops/docker/updater.py' > "$TMP" || true
{ echo "# PyXie updater: look for new releases / act on an update request from Settings > Updates"; echo "$CHECK"; echo "$POLL"; } >> "$TMP"
crontab "$TMP"; rm -f "$TMP"
echo "crontab updated:"; crontab -l | grep -A2 'PyXie updater'
python3 ops/docker/updater.py check
echo "Done. Settings > Updates in the app now shows this server's release status."
