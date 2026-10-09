"""A node's pinned default storage is where guests arriving from LOCAL storage land; guests already on shared storage stay put."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core import placement


class FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *_a):
        return self

    def filter(self, *_a):
        return self

    def all(self):
        return self.rows


def storages():
    shared = NS(id="s1", name="nas", scope="cluster-shared", node_id=None, capacity_bytes=10, used_bytes=1)
    pin = NS(id="l1", name="intel-ssd-103", scope="node-local", node_id="n103", capacity_bytes=100, used_bytes=1)
    other = NS(id="l2", name="local-lvm", scope="node-local", node_id="n103", capacity_bytes=50, used_bytes=0)
    return [shared, pin, other]


def test_local_guest_goes_to_the_pinned_local_storage(monkeypatch):
    monkeypatch.setattr(placement, "get_node_default_storage_id", lambda db, nid: "l2")
    rec = placement.recommend_storage_for_candidate(FakeDb(storages()), "n103", False)
    assert rec["name"] == "local-lvm" and "pinned" in rec["reason"]  # the pin beats "most free space"


def test_shared_guest_stays_on_shared_even_with_a_local_pin(monkeypatch):
    monkeypatch.setattr(placement, "get_node_default_storage_id", lambda db, nid: "l1")
    assert placement.recommend_storage_for_candidate(FakeDb(storages()), "n103", True) is None


def test_explicit_local_preference_still_overrides(monkeypatch):
    monkeypatch.setattr(placement, "get_node_default_storage_id", lambda db, nid: "l1")
    rec = placement.recommend_storage_for_candidate(FakeDb(storages()), "n103", True, storage_preference="local")
    assert rec["name"] == "intel-ssd-103"
