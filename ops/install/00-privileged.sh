#!/usr/bin/env bash
# PyXie Manager -- STEP 1 of 2: privileged (root) install steps.
#
# Installs OS packages (Node 20, PostgreSQL 16, Redis, build tools),
# creates the pyxie_manager Postgres role/database, writes the three
# systemd unit files, and adds a narrow NOPASSWD sudo rule scoped to
# restart/status on just those three units (so routine deploys don't
# need a password every time -- see README.md "Operator access").
#
# Review before running. Idempotent where practical -- safe to re-run.
#
# Usage: sudo PYXIE_USER=phccode PYXIE_HOME=/home/phccode bash 00-privileged.sh
# (PYXIE_USER/PYXIE_HOME default to the invoking sudo user's own account.)
set -euo pipefail

PYXIE_USER="${PYXIE_USER:-${SUDO_USER:?Run with sudo, or set PYXIE_USER explicitly}}"
PYXIE_HOME="${PYXIE_HOME:-$(getent passwd "$PYXIE_USER" | cut -d: -f6)}"
APP_DIR="${PYXIE_HOME}/pyxie-manager"
SECRETS_FILE="${PYXIE_HOME}/.pyxie-install-secrets"

echo "Installing for user=${PYXIE_USER} home=${PYXIE_HOME} app_dir=${APP_DIR}"

echo "== 1/7: NodeSource 20.x repo (Ubuntu 24.04's own repo only has 18.x, PyXie needs 20) =="
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -

echo "== 2/7: apt packages =="
apt-get update
apt-get install -y \
  nodejs \
  postgresql-16 \
  redis-server \
  build-essential \
  libpq-dev \
  python3.12-venv \
  git

echo "== 3/7: enable + start postgres/redis =="
systemctl enable --now postgresql redis-server

echo "== 4/7: create the pyxie_manager Postgres role + database (idempotent) =="
touch "$SECRETS_FILE"
chown "${PYXIE_USER}:${PYXIE_USER}" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='pyxie_manager'" | grep -q 1; then
  PGPASS="$(openssl rand -hex 24)"
  sudo -u postgres psql -c "CREATE USER pyxie_manager WITH PASSWORD '${PGPASS}';"
  sudo -u postgres psql -c "CREATE DATABASE pyxie_manager OWNER pyxie_manager;"
  echo "POSTGRES_PASSWORD=${PGPASS}" >> "$SECRETS_FILE"
  echo "Generated a new Postgres password, written to ${SECRETS_FILE} (600, owned by ${PYXIE_USER})."
else
  echo "pyxie_manager role already exists -- skipping creation. If ${SECRETS_FILE} doesn't already have"
  echo "POSTGRES_PASSWORD set from a prior run, you'll need to reset the role's password or find the"
  echo "original value before running 01-app-deploy.sh."
fi

echo "== 5/7: systemd unit files =="
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
# Enabled now (so they survive a reboot without a second privileged step
# later), but not started -- there's no app code/venvs/.env yet. They'll
# fail-and-retry harmlessly (Restart=on-failure) if something starts them
# early; 01-app-deploy.sh does the real first start once everything exists.
systemctl enable pyxie-api pyxie-worker pyxie-web

echo "== 6/7: narrow NOPASSWD sudo rule for service restart/status =="
cat > /etc/sudoers.d/pyxie-service-restart << EOF
${PYXIE_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart pyxie-api, /usr/bin/systemctl restart pyxie-worker, /usr/bin/systemctl restart pyxie-web, /usr/bin/systemctl status pyxie-api, /usr/bin/systemctl status pyxie-worker, /usr/bin/systemctl status pyxie-web, /usr/bin/systemctl is-active pyxie-api, /usr/bin/systemctl is-active pyxie-worker, /usr/bin/systemctl is-active pyxie-web
EOF
chmod 440 /etc/sudoers.d/pyxie-service-restart
visudo -c -f /etc/sudoers.d/pyxie-service-restart

echo "== 7/7: sanity check =="
systemctl is-active postgresql redis-server
node --version
python3.12 --version

echo
echo "Privileged setup done. As ${PYXIE_USER}, next: clone the repo into ${APP_DIR}"
echo "and run ops/install/01-app-deploy.sh from inside it."
