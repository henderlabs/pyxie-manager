"""Pure coverage for the single-file host installer builder, link store and version comparison."""

import base64
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core import host_kit as hk

KIT = Path(__file__).resolve().parents[1] / "host_maintenance_kit"
PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestkeytestkeytestkeytestkeytestkeytest pyxie-manager-hostmaint"


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def get(self, k):
        return self.kv.get(k)

    def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]


def test_wrapper_version_comes_from_the_wrapper_script():
    assert hk.wrapper_version(KIT) == "1.1.0"


def test_version_comparison():
    assert hk.is_outdated("1.0.0", "1.1.0")
    assert not hk.is_outdated("1.1.0", "1.1.0")
    assert not hk.is_outdated("1.2.0", "1.1.0")
    assert hk.is_outdated("1.9.0", "1.10.0")  # numeric, not lexical
    assert not hk.is_outdated(None, "1.1.0")  # unknown is not "outdated"
    assert not hk.is_outdated("garbage", "1.1.0")


def test_installer_is_deterministic_and_carries_the_public_key_only():
    a = hk.build_installer(KIT, PUB, "Lab", "0.27.1")
    b = hk.build_installer(KIT, PUB, "Lab", "0.27.1")
    assert a == b and hk.sha256_hex(a) == hk.sha256_hex(b)
    assert hk.build_installer(KIT, PUB, "Lab", "0.27.2") != a
    assert b"PRIVATE KEY" not in a and a.startswith(b"#!/bin/bash")


def _extract(script: bytes) -> tarfile.TarFile:
    payload = script.split(b"__PYXIE_KIT_PAYLOAD__\n", 1)[1]
    return tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload)), mode="r:gz")


def test_payload_has_every_kit_file_and_the_key():
    tar = _extract(hk.build_installer(KIT, PUB, "Lab", "0.27.1"))
    names = sorted(m.name for m in tar.getmembers())
    assert names == sorted(f"pyxie-hostmaint-kit/{n}" for n in (*hk.KIT_FILES, hk.KEY_NAME))
    assert tar.extractfile(f"pyxie-hostmaint-kit/{hk.KEY_NAME}").read().decode().strip() == PUB
    assert b"pyxie-maint log" in tar.extractfile("pyxie-hostmaint-kit/pyxie-maint.sudoers").read()


def test_installer_script_runs_its_info_modes_and_rejects_bad_args():
    script = hk.build_installer(KIT, PUB, "Lab", "0.27.1")
    with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as f:
        f.write(script)
    out = subprocess.run(["bash", f.name, "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and "0.27.1" in out.stdout and "1.1.0" in out.stdout
    bad = subprocess.run(["bash", f.name, "--nope"], capture_output=True, text=True)
    assert bad.returncode == 2
    assert subprocess.run(["bash", "-n", f.name]).returncode == 0


def test_link_works_until_cap_and_unknown_tokens_fail():
    r = FakeRedis()
    t = hk.new_link_token()
    hk.store_link(r, t, "target-1")
    assert hk.resolve_link(r, t) == "target-1"
    assert hk.resolve_link(r, "nope") is None
    assert hk.resolve_link(r, "") is None
    assert hk.resolve_link(r, "x" * 200) is None
    for _ in range(hk.LINK_MAX_DOWNLOADS - 1):
        assert hk.resolve_link(r, t) == "target-1"
    assert hk.resolve_link(r, t) is None  # over the cap


def test_no_secret_material_in_link_store_keys():
    r = FakeRedis()
    t = hk.new_link_token()
    hk.store_link(r, t, "target-1")
    assert all(t not in k for k in r.kv)
