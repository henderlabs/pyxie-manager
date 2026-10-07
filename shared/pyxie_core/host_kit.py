"""The single-file host installer: everything a PVE node needs for PyXie's host-maintenance wrapper.

`build_installer` returns one self-extracting bash script (scripts + this target's PUBLIC key embedded as a
base64 tarball). Running it as root installs or upgrades the wrapper, dispatch script and sudoers entry; it is
idempotent, so upgrading a host is just running a newer installer. The output is deterministic for a given kit,
key and version, so the SHA-256 PyXie shows in its UI matches the file the host downloads.
"""

import base64
import gzip
import hashlib
import io
import re
import secrets
import tarfile
from pathlib import Path

KIT_FILES = ("install.sh", "uninstall.sh", "pyxie-maint", "pyxie-maint-ssh-dispatch", "pyxie-maint.sudoers")
KEY_NAME = "pyxie-hostmaint-key.pub"
LINK_TTL_SECONDS = 30 * 60
LINK_MAX_DOWNLOADS = 50
_WRAPPER_VERSION_RE = re.compile(r'^WRAPPER_VERSION="([^"]+)"', re.M)


def wrapper_version(kit_dir) -> str:
    """The wrapper version this kit installs (single source of truth: the pyxie-maint script itself)."""
    m = _WRAPPER_VERSION_RE.search((Path(kit_dir) / "pyxie-maint").read_text())
    if not m:
        raise ValueError("WRAPPER_VERSION not found in pyxie-maint")
    return m.group(1)


def version_tuple(v) -> tuple:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        return ()


def is_outdated(installed, expected) -> bool:
    a, b = version_tuple(installed), version_tuple(expected)
    return bool(a) and bool(b) and a < b


def _payload(kit_dir, pubkey_line: str) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        def add(name: str, data: bytes, mode: int) -> None:
            info = tarfile.TarInfo(name=f"pyxie-hostmaint-kit/{name}")
            info.size, info.mode, info.mtime = len(data), mode, 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
        for fname in sorted(KIT_FILES):
            add(fname, (Path(kit_dir) / fname).read_bytes(), 0o755)
        add(KEY_NAME, (pubkey_line.strip() + "\n").encode(), 0o644)
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as g:
        g.write(raw.getvalue())
    return gz.getvalue()


_HEADER = r'''#!/bin/bash
# PyXie host kit for "@@NAME@@": installs or upgrades the PyXie host-maintenance wrapper on THIS node.
#   kit @@KIT@@  |  wrapper @@WRAPPER@@
#
#   sudo bash pyxie-host-kit.sh            install or upgrade this node (idempotent)
#   bash     pyxie-host-kit.sh --check     show installed vs this kit's wrapper version, change nothing
#   sudo bash pyxie-host-kit.sh --uninstall  remove the PyXie host identity, wrapper and sudoers entry from this node
#   bash     pyxie-host-kit.sh --version
#
# Contains: the pyxie-maint wrapper, its SSH dispatch script, the sudoers entry and the installer, plus the
# PUBLIC half of this PyXie's host-maintenance key. No secrets. Verify the SHA-256 shown in PyXie before running.
set -euo pipefail
KIT_VERSION="@@KIT@@"
WRAPPER_VERSION="@@WRAPPER@@"
INSTALLED=/usr/local/sbin/pyxie-maint
MODE=install

installed_version() {
  if [ -r "$INSTALLED" ]; then grep -m1 '^WRAPPER_VERSION=' "$INSTALLED" | cut -d'"' -f2; fi
}

case "${1:-}" in
  --version) echo "pyxie-host-kit $KIT_VERSION (wrapper $WRAPPER_VERSION)"; exit 0 ;;
  --check)
    cur="$(installed_version || true)"
    if [ -z "$cur" ]; then
      echo "not installed (or run as root to read it). This kit installs wrapper $WRAPPER_VERSION."; exit 4
    elif [ "$cur" = "$WRAPPER_VERSION" ]; then
      echo "wrapper $cur is current."; exit 0
    else
      echo "wrapper $cur installed; this kit has $WRAPPER_VERSION. Run: sudo bash $0"; exit 3
    fi ;;
  "") ;;
  --uninstall) MODE=uninstall ;;
  *) echo "usage: sudo bash $0 [--check|--version|--uninstall]" >&2; exit 2 ;;
esac

if [ "$(id -u)" -ne 0 ]; then echo "Run as root: sudo bash $0" >&2; exit 1; fi
for c in tar base64 python3 gzip; do
  command -v "$c" >/dev/null 2>&1 || { echo "Missing required command: $c" >&2; exit 1; }
done
if [ ! -f "$0" ]; then echo "Save this script to a file and run it with bash (do not pipe it)." >&2; exit 1; fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ "$MODE" = "uninstall" ]; then
  sed -n '1,/^__PYXIE_KIT_PAYLOAD__$/!p' "$0" | base64 -d | tar xzf - -C "$TMP"
  bash "$TMP/pyxie-hostmaint-kit/uninstall.sh"
  exit 0
fi
before="$(installed_version || true)"
echo "PyXie host kit $KIT_VERSION: wrapper ${before:-none} -> $WRAPPER_VERSION on $(hostname)"
sed -n '1,/^__PYXIE_KIT_PAYLOAD__$/!p' "$0" | base64 -d | tar xzf - -C "$TMP"
bash "$TMP/pyxie-hostmaint-kit/install.sh" "$TMP/pyxie-hostmaint-kit/@@KEYNAME@@"
echo ""
echo "Installed wrapper version: $(installed_version)"
exit 0
__PYXIE_KIT_PAYLOAD__
'''


def build_installer(kit_dir, pubkey_line: str, target_name: str, kit_version: str) -> bytes:
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", target_name)[:60]
    header = (_HEADER.replace("@@NAME@@", safe_name).replace("@@KIT@@", kit_version)
              .replace("@@WRAPPER@@", wrapper_version(kit_dir)).replace("@@KEYNAME@@", KEY_NAME))
    body = base64.encodebytes(_payload(kit_dir, pubkey_line))  # 76-col lines, deterministic
    return header.encode() + body


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_link_token() -> str:
    return secrets.token_urlsafe(24)


def _key(token: str) -> str:
    return "pyxie:hostkit:link:" + hashlib.sha256(token.encode()).hexdigest()


def store_link(r, token: str, target_id: str) -> None:
    r.set(_key(token), target_id, ex=LINK_TTL_SECONDS)
    r.set(_key(token) + ":n", 0, ex=LINK_TTL_SECONDS)


def resolve_link(r, token: str):
    """target_id for a live link, else None. Counts the download; refuses after the cap."""
    if not token or len(token) > 80:
        return None
    target_id = r.get(_key(token))
    if not target_id:
        return None
    if r.incr(_key(token) + ":n") > LINK_MAX_DOWNLOADS:
        return None
    return target_id.decode() if isinstance(target_id, bytes) else target_id
