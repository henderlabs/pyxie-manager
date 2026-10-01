#!/usr/bin/env bash
# Install Docker Engine + the compose plugin on Ubuntu 24.04 from Docker's
# official apt repository, and open 80/443 if ufw is active.
#
# RUN AS ROOT BY A HUMAN:   sudo bash ops/docker/install-docker.sh [--add-user NAME]
#
# What it changes on the host (all idempotent):
#   1. adds /etc/apt/keyrings/docker.asc + /etc/apt/sources.list.d/docker.list
#   2. installs docker-ce, docker-ce-cli, containerd.io, docker-buildx-plugin,
#      docker-compose-plugin
#   3. writes /etc/docker/daemon.json (log rotation) ONLY if none exists
#   4. enables + starts docker.service
#   5. if ufw is active: allows 80/tcp and 443/tcp (+443/udp for HTTP/3)
#   6. with --add-user NAME: adds NAME to the `docker` group.
#      NOTE: docker-group membership is root-equivalent on this host.
#      Without it, run docker through sudo (password) instead.
#
# It does NOT start PyXie, touch the native services, or change sudoers.

set -euo pipefail

ADD_USER=""
if [[ "${1:-}" == "--add-user" ]]; then ADD_USER="${2:?--add-user needs a username}"; fi

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
. /etc/os-release
[[ "$ID" == "ubuntu" ]] || { echo "Ubuntu only (found: $ID)."; exit 1; }

echo "[1/6] Docker apt repository"
apt-get update -qq
apt-get install -y -qq ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable
EOF
apt-get update -qq

echo "[2/6] Installing packages"
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[3/6] Log rotation default"
if [[ ! -e /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" }
}
EOF
else
  echo "  /etc/docker/daemon.json exists -- left untouched"
fi

echo "[4/6] Enabling docker.service"
systemctl enable --now docker
systemctl restart docker

echo "[5/6] Firewall"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
else
  echo "  ufw not active -- nothing to do"
fi

echo "[6/6] Docker group"
if [[ -n "$ADD_USER" ]]; then
  usermod -aG docker "$ADD_USER"
  echo "  added $ADD_USER to docker (takes effect on next login)"
else
  echo "  no --add-user given -- skipped"
fi

echo
docker --version
docker compose version
echo "Done."
