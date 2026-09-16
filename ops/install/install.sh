#!/usr/bin/env bash
# PyXie Manager -- full install, one run: OS packages, app deploy, first
# service start. Clone the repo first, then run this once as root:
#
#   git clone https://github.com/henderlabs/pyxie-manager.git ~/pyxie-manager
#   cd ~/pyxie-manager && sudo bash ops/install/install.sh
#
# Deploys as whichever user ran `sudo` (override with PYXIE_USER=... if
# you're bootstrapping for a different account). Idempotent -- safe to
# re-run after a `git pull` to pick up a new release; won't touch an
# existing .env or recreate an already-present Postgres role.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

PYXIE_USER="${PYXIE_USER:-${SUDO_USER:?Run via sudo, not directly as root, so we know who to deploy as -- or set PYXIE_USER explicitly}}"
PYXIE_HOME="$(getent passwd "$PYXIE_USER" | cut -d: -f6)"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRETS_FILE="${PYXIE_HOME}/.pyxie-install-secrets"

echo "Installing PyXie: user=${PYXIE_USER} app_dir=${APP_DIR}"

echo "== 1/9: NodeSource 20.x repo (Ubuntu 24.04's own repo only has 18.x, PyXie needs 20) =="
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -

echo "== 2/9: apt packages =="
apt-get update
apt-get install -y nodejs postgresql-16 redis-server build-essential libpq-dev python3.12-venv git

echo "== 3/9: enable + start postgres/redis =="
systemctl enable --now postgresql redis-server

echo "== 4/9: create the pyxie_manager Postgres role + database (idempotent) =="
touch "$SECRETS_FILE"
chown "${PYXIE_USER}:${PYXIE_USER}" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='pyxie_manager'" | grep -q 1; then
  PGPASS="$(openssl rand -hex 24)"
  sudo -u postgres psql -c "CREATE USER pyxie_manager WITH PASSWORD '${PGPASS}';"
  sudo -u postgres psql -c "CREATE DATABASE pyxie_manager OWNER pyxie_manager;"
  echo "POSTGRES_PASSWORD=${PGPASS}" >> "$SECRETS_FILE"
  echo "Generated a new Postgres password."
else
  echo "pyxie_manager role already exists -- skipping creation."
fi

echo "== 5/9: systemd unit files =="
cat > /etc/systemd/system/pyxie-api.service << EOF
[Unit]
Description=PyXie Manager API
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=${PYXIE_USER}
WorkingDirectory=${APP_DIR}/api
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/api/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/pyxie-worker.service << EOF
[Unit]
Description=PyXie Manager Worker
After=network.target postgresql.service redis-server.service pyxie-api.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=${PYXIE_USER}
WorkingDirectory=${APP_DIR}/worker
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/worker/.venv/bin/python -m worker.main
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/pyxie-web.service << EOF
[Unit]
Description=PyXie Manager Web
After=network.target pyxie-api.service
Wants=pyxie-api.service

[Service]
Type=simple
User=${PYXIE_USER}
WorkingDirectory=${APP_DIR}/web
Environment=NODE_ENV=production
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/web/node_modules/.bin/next start -p 3000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pyxie-api pyxie-worker pyxie-web

echo "== 6/9: narrow NOPASSWD sudo rule (for routine restarts/status AFTER this install, not used by this script itself) =="
cat > /etc/sudoers.d/pyxie-service-restart << EOF
${PYXIE_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart pyxie-api, /usr/bin/systemctl restart pyxie-worker, /usr/bin/systemctl restart pyxie-web, /usr/bin/systemctl status pyxie-api, /usr/bin/systemctl status pyxie-worker, /usr/bin/systemctl status pyxie-web, /usr/bin/systemctl is-active pyxie-api, /usr/bin/systemctl is-active pyxie-worker, /usr/bin/systemctl is-active pyxie-web
EOF
chmod 440 /etc/sudoers.d/pyxie-service-restart
visudo -c -f /etc/sudoers.d/pyxie-service-restart

echo "== 7/9: python venvs (as ${PYXIE_USER}, not root -- avoids root-owned files in their home dir) =="
runuser -u "$PYXIE_USER" -- bash -c "
  set -e
  cd '$APP_DIR'
  python3.12 -m venv api/.venv
  api/.venv/bin/pip install -q --upgrade pip
  api/.venv/bin/pip install -q -r api/requirements.txt -r api/requirements-dev.txt
  python3.12 -m venv worker/.venv
  worker/.venv/bin/pip install -q --upgrade pip
  worker/.venv/bin/pip install -q -r worker/requirements.txt
"

echo "== 8/9: web deps + production build (as ${PYXIE_USER}) =="
runuser -u "$PYXIE_USER" -- bash -c "cd '$APP_DIR/web' && npm ci && npm run build"

echo "== 9/9: .env (skipped if one exists) + migrations + first service start =="
if [ -f "$APP_DIR/.env" ]; then
  echo ".env already exists, leaving it untouched."
else
  PGPASS="$(grep '^POSTGRES_PASSWORD=' "$SECRETS_FILE" | cut -d= -f2-)"
  FERNET_KEY="$(runuser -u "$PYXIE_USER" -- "$APP_DIR/api/.venv/bin/python3" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  TZ_DETECTED="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"
  cat > "$APP_DIR/.env" << EOF
POSTGRES_DB=pyxie_manager
POSTGRES_USER=pyxie_manager
POSTGRES_PASSWORD=${PGPASS}

DATABASE_URL=postgresql://pyxie_manager:${PGPASS}@127.0.0.1:5432/pyxie_manager
REDIS_URL=redis://127.0.0.1:6379/0

PYXIE_CREDENTIAL_KEY=${FERNET_KEY}

# Keep false until you've deliberately decided to allow PyXie to submit
# writes to PVE -- see README.md "Safety Contract for every PVE write".
PVE_MUTATIONS_ENABLED=false

TZ=${TZ_DETECTED}
API_INTERNAL_URL=http://127.0.0.1:8000

# REQUIRED before any maintenance/migration write path is used for real:
# the VMID this VM itself has in the PVE cluster it manages, so
# pve_write_client.py's self-protection guard can refuse to ever shut
# down/force-stop the box PyXie is running on. Find it in the PVE web UI
# (or \`qm list\` on the hypervisor) once this VM exists there, then set
# it here and restart pyxie-api.
#PYXIE_SELF_VMID=
EOF
  chown "${PYXIE_USER}:${PYXIE_USER}" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo ".env written. IMPORTANT: set PYXIE_SELF_VMID once you know this VM's real VMID (see the comment in .env), then restart pyxie-api."
fi

runuser -u "$PYXIE_USER" -- bash -c "cd '$APP_DIR/api' && set -a; source ../.env; set +a; .venv/bin/python3 -m alembic upgrade head"

# Already root here -- no sudo needed for these, which sidesteps a real
# flakiness seen calling `sudo systemctl restart ...` from deep inside a
# long-running non-interactive script during actual testing (root cause
# not fully pinned down; running as root throughout removes the need to
# reproduce or diagnose it further).
systemctl restart pyxie-api
sleep 2
systemctl restart pyxie-worker
sleep 2
systemctl restart pyxie-web
sleep 2
systemctl is-active pyxie-api pyxie-worker pyxie-web

echo
echo "Done. Visit http://<this-host>:3000/login to create the administrator account --"
echo "that first-run bootstrap is deliberately UI-only, not scriptable (see README.md)."
