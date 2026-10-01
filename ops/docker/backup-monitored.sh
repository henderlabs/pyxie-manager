#!/usr/bin/env bash
# Backup with failure alerting, for cron. Runs on the HOST (not in a container)
# so it still works when the stack is broken -- which is when you need it.
#
#   ops/docker/backup-monitored.sh run     take a backup, validate it, alert on failure
#   ops/docker/backup-monitored.sh check   alert if the newest backup is stale/missing
#
# Typical crontab (the check catches cron itself not firing):
#   15 2 * * * cd <repo> && flock -n /tmp/pyxie-backup.lock ops/docker/backup-monitored.sh run   >> ~/pyxie-backups/backup.log 2>&1
#   30 8 * * * cd <repo> && ops/docker/backup-monitored.sh check >> ~/pyxie-backups/backup.log 2>&1
#
# Who gets alerted, and through what relay, is managed in the app: recipients
# are the union of every ENABLED rule under Settings > Notifications, and the
# relay is Settings > Email (SMTP). (A backup alert is not one of the app's
# finding categories, so a rule's category/severity filters are not applied.)
# Optional .env values override the app's, and are the fallback when the
# database is unreachable (the stack being down is exactly when this matters):
#   BACKUP_ALERT_TO         recipient(s), comma-separated
#   BACKUP_ALERT_SMTP_HOST  SMTP relay host
#   BACKUP_ALERT_SMTP_PORT  default 25
#   BACKUP_ALERT_SMTP_TLS   1 = STARTTLS (default 0)
#   BACKUP_ALERT_FROM       default pyxie-backup@<hostname>
#   BACKUP_MAX_AGE_HOURS    staleness threshold for `check` (default 26)
# No SMTP authentication is supported (internal relay assumed).
# A failed alert send is logged and the script exits non-zero.

set -uo pipefail
cd "$(dirname "$0")/../.."
MODE="${1:-run}"

set -a; . ./.env; set +a
BACKUP_DIR="${BACKUP_DIR:-$HOME/pyxie-backups}"
MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
HOST="$(hostname)"

psql_app() {
  docker compose exec -T pyxie-manager-db psql -U "${POSTGRES_USER:-}" -d "${POSTGRES_DB:-}" -Atq -F '|' -c "$1" 2>/dev/null
}

# Fill any alert setting not given in .env from the app's own settings.
resolve_settings() {
  local row
  if [[ -z "${BACKUP_ALERT_SMTP_HOST:-}" ]]; then
    row="$(psql_app "select smtp_host, smtp_port, smtp_use_tls::int, coalesce(smtp_from_address,'') from app_settings where smtp_enabled and coalesce(smtp_host,'')<>'' limit 1")" || row=""
    if [[ -n "$row" ]]; then
      IFS='|' read -r BACKUP_ALERT_SMTP_HOST BACKUP_ALERT_SMTP_PORT BACKUP_ALERT_SMTP_TLS BACKUP_ALERT_FROM_DB <<<"$row"
      BACKUP_ALERT_FROM="${BACKUP_ALERT_FROM:-$BACKUP_ALERT_FROM_DB}"
    fi
  fi
  if [[ -z "${BACKUP_ALERT_TO:-}" ]]; then
    BACKUP_ALERT_TO="$(psql_app "select string_agg(distinct r, ',') from notification_rules n, jsonb_array_elements_text(n.recipients) r where n.enabled" || true)"
  fi
  export BACKUP_ALERT_TO BACKUP_ALERT_SMTP_HOST BACKUP_ALERT_SMTP_PORT BACKUP_ALERT_SMTP_TLS BACKUP_ALERT_FROM
}

send_alert() {  # $1 subject, $2 body
  resolve_settings
  if [[ -z "${BACKUP_ALERT_TO:-}" || -z "${BACKUP_ALERT_SMTP_HOST:-}" ]]; then
    echo "[alert] no recipient/relay: enable a rule with recipients under Settings > Notifications and SMTP under Settings > Email, or set BACKUP_ALERT_TO / BACKUP_ALERT_SMTP_HOST in .env; cannot send: $1" >&2
    return 1
  fi
  SUBJECT="[PyXie backup] $1 ($HOST)" BODY="$2" python3 - <<'PY' || { echo "[alert] SEND FAILED: $1" >&2; return 1; }
import os, smtplib
from email.mime.text import MIMEText
e = os.environ
frm = e.get("BACKUP_ALERT_FROM") or "pyxie-backup@" + os.uname().nodename
msg = MIMEText(e["BODY"])
to = [a.strip() for a in e["BACKUP_ALERT_TO"].split(",") if a.strip()]
msg["Subject"], msg["From"], msg["To"] = e["SUBJECT"], frm, ", ".join(to)
with smtplib.SMTP(e["BACKUP_ALERT_SMTP_HOST"], int(e.get("BACKUP_ALERT_SMTP_PORT") or 25), timeout=15) as s:
    if e.get("BACKUP_ALERT_SMTP_TLS") == "1":
        s.starttls()
    s.sendmail(frm, to, msg.as_string())
PY
  echo "[alert] sent: $1"
}

newest_dump() { ls -1t "$BACKUP_DIR"/pyxie_manager_docker_*.dump 2>/dev/null | head -1; }

case "$MODE" in
  run)
    OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT
    before="$(newest_dump)"
    if ! ops/docker/backup.sh >"$OUT" 2>&1; then
      cat "$OUT"
      send_alert "backup FAILED" "backup.sh exited non-zero at $(date).

Last output:
$(tail -n 20 "$OUT")"
      # A failed pg_dump can leave a partial file; remove it (only if THIS run
      # created it) so it can't pass for a good backup in the staleness check.
      f="$(newest_dump)"
      [[ -n "$f" && "$f" != "$before" ]] && rm -f -- "$f"
      exit 1
    fi
    cat "$OUT"
    f="$(newest_dump)"
    if [[ -z "$f" ]] || [[ "$(stat -c %s "$f")" -lt 1048576 ]] \
       || ! docker compose exec -T pyxie-manager-db pg_restore -l < "$f" >/dev/null 2>&1; then
      send_alert "backup INVALID" "backup.sh succeeded but the newest dump (${f:-none}) is missing, under 1 MB, or unreadable by pg_restore at $(date)."
      [[ -n "$f" && "$f" != "$before" ]] && rm -f -- "$f"
      exit 1
    fi
    echo "[monitor] validated $f"
    ;;
  check)
    f="$(newest_dump)"
    if [[ -z "$f" ]]; then
      send_alert "NO backups found" "No pyxie_manager_docker_*.dump in $BACKUP_DIR at $(date)."; exit 1
    fi
    age_h=$(( ( $(date +%s) - $(stat -c %Y "$f") ) / 3600 ))
    if (( age_h >= MAX_AGE_HOURS )); then
      send_alert "backup STALE (${age_h}h old)" "Newest backup is $f, ${age_h} hours old (threshold ${MAX_AGE_HOURS}h) at $(date). Cron may not be running, or the host/Docker was down overnight."
      exit 1
    fi
    echo "[monitor] ok: newest backup ${age_h}h old"
    ;;
  test)
    send_alert "TEST alert" "This is a test of the PyXie backup failure alert, sent at $(date). If you can read this, alerts work."
    ;;
  *) echo "usage: $0 run|check|test"; exit 2;;
esac
