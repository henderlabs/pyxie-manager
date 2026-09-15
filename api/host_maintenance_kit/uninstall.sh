#!/bin/bash
# uninstall.sh -- reverses install.sh for PyXie Manager's Stage W4
# host-maintenance wrapper (pyxie-maint). Run as root on the node you want
# to fully disconnect.
#
# Usage:
#   ./uninstall.sh
#
# Removes exactly what install.sh added on this node: the sudoers policy,
# the wrapper/dispatch scripts, and the pyxie-hostmaint service identity
# (including its home directory and authorized_keys). Safe to re-run --
# each step is skipped if already absent.
#
# What this does NOT do: touch PyXie's own database. Run this FIRST if
# you want a clean host, then use "Disconnect" in PyXie's UI to clear the
# pinned host key / connection status there too (or do it in either
# order -- the two are independent).

set -euo pipefail

SERVICE_USER="pyxie-hostmaint"

if [ "$(id -u)" -ne 0 ]; then
  echo "Must be run as root." >&2
  exit 1
fi

echo "==> [1/3] sudo policy"
if [ -f /etc/sudoers.d/pyxie-maint ]; then
  rm -f /etc/sudoers.d/pyxie-maint
  echo "    removed /etc/sudoers.d/pyxie-maint"
else
  echo "    already absent"
fi

echo "==> [2/3] wrapper scripts"
for f in /usr/local/sbin/pyxie-maint /usr/local/sbin/pyxie-maint-ssh-dispatch; do
  if [ -f "$f" ]; then
    rm -f "$f"
    echo "    removed $f"
  else
    echo "    $f already absent"
  fi
done

echo "==> [3/3] service identity '$SERVICE_USER'"
if id "$SERVICE_USER" >/dev/null 2>&1; then
  userdel -r "$SERVICE_USER" 2>/dev/null || userdel "$SERVICE_USER"
  echo "    removed user (and home directory, including authorized_keys)"
else
  echo "    already absent"
fi

echo ""
echo "OK: $(hostname) no longer has any PyXie Stage W4 access."
echo "If you haven't already, click Disconnect for this node in PyXie too."
