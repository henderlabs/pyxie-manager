#!/usr/bin/env bash
# Throwaway PyXie demo stack on FICTIONAL data, for regenerating README / website screenshots.
#   ops/demo/demo.sh up       build images from this checkout, start db+redis+fake PVE+api+web, seed the demo data
#   ops/demo/demo.sh shoot    capture pages.json into ops/demo/out/ (needs Node 22+ and Chrome on this machine)
#   ops/demo/demo.sh down     remove every container, the network and the generated secrets
# Run it on a machine that is NOT a PyXie production host. It never reads or writes a real .env and has no worker,
# so nothing here talks to a real cluster. See README.md in this folder.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
STATE="$HERE/.demo"
NET=pyxie-demo
PORT="${DEMO_PORT:-13000}"
ADMIN=admin@example.com

up() {
  [ -e "$STATE/env" ] && { echo "demo stack state exists; run '$0 down' first" >&2; exit 1; }
  mkdir -p "$STATE"; umask 077
  local pw key
  pw=$(openssl rand -hex 12); key=$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
  openssl rand -hex 12 > "$STATE/password"
  cat > "$STATE/env" <<E
POSTGRES_DB=pyxie
POSTGRES_USER=pyxie
POSTGRES_PASSWORD=$pw
DATABASE_URL=postgresql+psycopg2://pyxie:$pw@pyxie-demo-db:5432/pyxie
REDIS_URL=redis://pyxie-demo-redis:6379/0
PYXIE_CREDENTIAL_KEY=$key
PVE_MUTATIONS_ENABLED=false
TZ=UTC
PYXIE_SELF_VMID=0
FORWARDED_ALLOW_IPS=*
E
  docker build -q -t pyxie-demo-api -f "$ROOT/api/Dockerfile" "$ROOT" >/dev/null
  docker build -q -t pyxie-demo-web -f "$ROOT/web/Dockerfile" "$ROOT" >/dev/null
  docker network create $NET >/dev/null
  docker run -d --name pyxie-demo-db --network $NET -e POSTGRES_DB=pyxie -e POSTGRES_USER=pyxie -e POSTGRES_PASSWORD="$pw" postgres:16-alpine >/dev/null
  docker run -d --name pyxie-demo-redis --network $NET redis:7-alpine >/dev/null
  sleep 6
  docker run -d --name pyxie-demo-api --network $NET --env-file "$STATE/env" pyxie-demo-api >/dev/null
  docker run -d --name pyxie-demo-pve --network $NET --entrypoint python -v "$HERE/fake_pve.py:/f.py:ro" pyxie-demo-api /f.py 8006 >/dev/null
  for _ in $(seq 1 40); do docker exec pyxie-demo-api python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')" 2>/dev/null && break; sleep 3; done
  docker exec -e PW="$(cat "$STATE/password")" -e AD=$ADMIN pyxie-demo-api python -c "
import json,os,urllib.request
r=urllib.request.Request('http://localhost:8000/api/auth/bootstrap',data=json.dumps({'email':os.environ['AD'],'password':os.environ['PW'],'display_name':'Demo Admin'}).encode(),headers={'Content-Type':'application/json'})
urllib.request.urlopen(r).read()"
  docker exec -i pyxie-demo-api python - < "$HERE/seed_demo.py"
  docker run -d --name pyxie-demo-web --network $NET -p "127.0.0.1:$PORT:3000" -e API_INTERNAL_URL=http://pyxie-demo-api:8000 \
    -e NODE_ENV=production -e HOSTNAME=0.0.0.0 pyxie-demo-web >/dev/null
  echo "demo stack up on http://127.0.0.1:$PORT (login $ADMIN, password in $STATE/password)"
}

shoot() {
  [ -e "$STATE/password" ] || { echo "no demo stack; run '$0 up' first" >&2; exit 1; }
  PORT=$PORT DEMO_PASSWORD="$(cat "$STATE/password")" W="${W:-1920}" H="${H:-1080}" DSF="${DSF:-1.5}" \
    node "$HERE/shoot.mjs" "$HERE/out" "$(cat "$HERE/pages.json")"
}

down() {
  docker rm -f pyxie-demo-web pyxie-demo-pve pyxie-demo-api pyxie-demo-redis pyxie-demo-db >/dev/null 2>&1 || true
  docker network rm $NET >/dev/null 2>&1 || true
  docker rmi pyxie-demo-api pyxie-demo-web >/dev/null 2>&1 || true
  rm -rf "$STATE"
  echo "demo stack removed"
}

case "${1:-}" in up) up ;; shoot) shoot ;; down) down ;; *) sed -n 2,7p "$0"; exit 1 ;; esac
