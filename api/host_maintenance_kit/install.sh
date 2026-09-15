#!/bin/bash
# install.sh -- idempotent per-node installer for PyXie Manager's Stage W4
# host-maintenance wrapper (pyxie-maint). Run as root on EACH PVE node.
#
# Usage:
#   ./install.sh [path-to-public-key]
#
# If no path is given, looks for pyxie-hostmaint-key.pub next to this
# script (the shared keypair generated once per cluster/org -- see
# DEPLOY.md). Safe to re-run at any time: re-running with an updated copy
# of this directory upgrades the wrapper/dispatch scripts and sudoers
# policy in place, and re-authorizing the same or a new public key never
# creates a duplicate user, sudoers entry, or authorized_keys line.
#
# What this does NOT do: install PyXie itself, register anything with
# PyXie's own database, or touch any node other than the one it's run on.
# After running this on a node, still: (1) pin that node's SSH host key in
# PyXie (probe + confirm the fingerprint out-of-band, then pin), and
# (2) register the shared private key as PyXie's host-maintenance
# credential for the cluster, once -- see DEPLOY.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBKEY_PATH="${1:-$SCRIPT_DIR/pyxie-hostmaint-key.pub}"
SERVICE_USER="pyxie-hostmaint"
SERVICE_HOME="/home/$SERVICE_USER"

if [ "$(id -u)" -ne 0 ]; then
  echo "Must be run as root." >&2
  exit 1
fi

if [ ! -f "$PUBKEY_PATH" ]; then
  echo "Public key not found: $PUBKEY_PATH" >&2
  echo "Usage: $0 [path-to-public-key]" >&2
  exit 1
fi

for f in pyxie-maint pyxie-maint-ssh-dispatch pyxie-maint.sudoers; do
  if [ ! -f "$SCRIPT_DIR/$f" ]; then
    echo "Missing required file next to this script: $f" >&2
    exit 1
  fi
done

echo "==> [1/6] sudo"
if command -v sudo >/dev/null 2>&1; then
  echo "    already installed"
else
  echo "    not present -- installing (this is normal on a minimal PVE host)"
  apt-get update -qq
  apt-get install -y sudo
fi

echo "==> [2/6] service identity '$SERVICE_USER'"
if id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "    already exists"
else
  useradd -r -m -d "$SERVICE_HOME" -s /bin/bash "$SERVICE_USER"
  echo "    created"
fi
passwd -l "$SERVICE_USER" >/dev/null
echo "    password locked -- key-only auth, no fallback login"

echo "==> [3/6] wrapper scripts"
install -o root -g root -m 0700 "$SCRIPT_DIR/pyxie-maint" /usr/local/sbin/pyxie-maint
install -o root -g root -m 0755 "$SCRIPT_DIR/pyxie-maint-ssh-dispatch" /usr/local/sbin/pyxie-maint-ssh-dispatch
echo "    /usr/local/sbin/pyxie-maint (0700, root-owned)"
echo "    /usr/local/sbin/pyxie-maint-ssh-dispatch (0755, root-owned)"

echo "==> [4/6] SSH key authorization"
mkdir -p "$SERVICE_HOME/.ssh"
chmod 700 "$SERVICE_HOME/.ssh"
{
  printf 'command="/usr/local/sbin/pyxie-maint-ssh-dispatch",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty '
  cat "$PUBKEY_PATH"
} > "$SERVICE_HOME/.ssh/authorized_keys"
chmod 600 "$SERVICE_HOME/.ssh/authorized_keys"
chown -R "$SERVICE_USER:$SERVICE_USER" "$SERVICE_HOME/.ssh"
echo "    authorized_keys written -- forced command, forwarding/PTY disabled"

echo "==> [5/6] sudo policy"
install -o root -g root -m 0440 "$SCRIPT_DIR/pyxie-maint.sudoers" /etc/sudoers.d/pyxie-maint
if ! visudo -cf /etc/sudoers.d/pyxie-maint; then
  echo "!! sudoers file failed validation -- removing it, nothing is authorized until this is fixed" >&2
  rm -f /etc/sudoers.d/pyxie-maint
  exit 1
fi
echo "    installed and syntax-validated"

echo "==> [6/6] verification"
if sudo -u "$SERVICE_USER" sudo /usr/local/sbin/pyxie-maint version; then
  echo ""
  echo "OK: $(hostname) is provisioned for PyXie Stage W4."
  echo "Next: pin this node's SSH host key in PyXie, then register the"
  echo "private key as the host-maintenance credential (once per cluster)."
else
  echo "!! Verification call failed -- see output above." >&2
  exit 1
fi
