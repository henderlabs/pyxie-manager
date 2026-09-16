#!/usr/bin/env bash
# PyXie Manager -- STEP 2 of 2: non-privileged app deploy.
#
# Run this AS the service account (not root, no sudo) from inside a
# freshly cloned repo, after 00-privileged.sh has already run once.
# Creates the Python venvs, installs web deps and builds it, generates
# .env (idempotent -- won't overwrite an existing one), runs migrations,
# and does the first service start.
#
# Usage (from the repo root): bash ops/install/01-app-deploy.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRETS_FILE="${HOME}/.pyxie-install-secrets"
cd "$APP_DIR"

echo "Deploying from ${APP_DIR}"

echo "== 1/6: api venv =="
python3.12 -m venv api/.venv
api/.venv/bin/pip install -q --upgrade pip
api/.venv/bin/pip install -q -r api/requirements.txt -r api/requirements-dev.txt

echo "== 2/6: worker venv =="
python3.12 -m venv worker/.venv
worker/.venv/bin/pip install -q --upgrade pip
worker/.venv/bin/pip install -q -r worker/requirements.txt

# api/pyxie_core and worker/pyxie_core are symlinks to ../shared/pyxie_core
# tracked directly in git -- `git clone` already created them, nothing to
# do here. (If you rsync'd the repo instead of cloning it, check these
# exist before continuing: `ls -la api/pyxie_core worker/pyxie_core`.)

echo "== 3/6: web deps + production build =="
(cd web && npm ci && npm run build)

echo "== 4/6: .env (skipped if one already exists) =="
if [ -f .env ]; then
  echo ".env already exists, leaving it untouched."
else
  if [ ! -f "$SECRETS_FILE" ] || ! grep -q '^POSTGRES_PASSWORD=' "$SECRETS_FILE"; then
    echo "ERROR: ${SECRETS_FILE} with POSTGRES_PASSWORD not found -- did 00-privileged.sh run" >&2
    echo "as this same user (or with a matching PYXIE_USER), and did it actually create the role" >&2
    echo "(vs. finding one that already existed)? See that script's step 4 output." >&2
    exit 1
  fi
  PGPASS="$(grep '^POSTGRES_PASSWORD=' "$SECRETS_FILE" | cut -d= -f2-)"
  FERNET_KEY="$(api/.venv/bin/python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  TZ_DETECTED="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"

  cat > .env << EOF
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
# pve_write_client.py's self-protection guard can refuse to ever
# shut down/force-stop the box PyXie is running on. Find it in the PVE
# web UI (or \`qm list\` on the hypervisor) once this VM exists there,
# then set it here and restart pyxie-api.
#PYXIE_SELF_VMID=
EOF
  chmod 600 .env
  echo ".env written. IMPORTANT: set PYXIE_SELF_VMID once you know this VM's real VMID (see the comment in .env), then restart pyxie-api."
fi

echo "== 5/6: database migrations =="
set -a; source .env; set +a
(cd api && .venv/bin/python3 -m alembic upgrade head)

echo "== 6/6: first service start =="
sudo systemctl restart pyxie-api
sleep 2
sudo systemctl restart pyxie-worker
sleep 2
sudo systemctl restart pyxie-web
sleep 2
sudo systemctl is-active pyxie-api pyxie-worker pyxie-web

echo
echo "Done. Visit http://<this-host>:3000/login to create the administrator account --"
echo "that first-run bootstrap is deliberately UI-only, not scriptable (see README.md)."
