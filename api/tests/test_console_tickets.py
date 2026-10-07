"""Pure coverage for the console ticket store, limits and origin check. A tiny fake stands in for Redis."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core import console_tickets as ct


class FakeRedis:
    def __init__(self):
        self.kv, self.z, self.ttl = {}, {}, {}

    def set(self, k, v, ex=None):
        self.kv[k] = v
        self.ttl[k] = ex

    def getdel(self, k):
        return self.kv.pop(k, None)

    def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]

    def expire(self, k, s):
        self.ttl[k] = s

    def zadd(self, k, m):
        self.z.setdefault(k, {}).update(m)

    def zrem(self, k, m):
        self.z.get(k, {}).pop(m, None)

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def zremrangebyscore(self, k, lo, hi):
        self.z[k] = {m: s for m, s in self.z.get(k, {}).items() if s > hi}


def test_ticket_is_single_use_and_hashed_in_store():
    r = FakeRedis()
    t = ct.new_ticket()
    ct.store_ticket(r, t, {"user_id": "u1", "port": 5900})
    assert all(t not in k for k in r.kv)
    assert list(r.ttl.values()) == [ct.TICKET_TTL_SECONDS]
    assert ct.consume_ticket(r, t) == {"user_id": "u1", "port": 5900}
    assert ct.consume_ticket(r, t) is None


def test_bad_tickets_are_rejected():
    r = FakeRedis()
    assert ct.consume_ticket(r, "") is None
    assert ct.consume_ticket(r, "x" * 500) is None
    assert ct.consume_ticket(r, "never-issued") is None


def test_tickets_are_unique():
    assert len({ct.new_ticket() for _ in range(50)}) == 50


def test_rate_limit():
    r = FakeRedis()
    assert all(ct.ticket_rate_ok(r, "u1", limit=3) for _ in range(3))
    assert not ct.ticket_rate_ok(r, "u1", limit=3)
    assert ct.ticket_rate_ok(r, "u2", limit=3)


def test_per_user_and_total_slots_and_release():
    r = FakeRedis()
    assert ct.acquire_slot(r, "u1", "c1", now=100, per_user=2, total=3)
    assert ct.acquire_slot(r, "u1", "c2", now=100, per_user=2, total=3)
    assert not ct.acquire_slot(r, "u1", "c3", now=100, per_user=2, total=3)
    assert ct.acquire_slot(r, "u2", "c4", now=100, per_user=2, total=3)
    assert not ct.acquire_slot(r, "u3", "c5", now=100, per_user=2, total=3)
    ct.release_slot(r, "u1", "c1")
    assert ct.acquire_slot(r, "u3", "c5", now=100, per_user=2, total=3)


def test_leaked_slots_expire():
    r = FakeRedis()
    assert ct.acquire_slot(r, "u1", "c1", now=100, per_user=1)
    assert not ct.acquire_slot(r, "u1", "c2", now=200, per_user=1)
    later = 100 + ct.MAX_SESSION_SECONDS + 301
    assert ct.acquire_slot(r, "u1", "c2", now=later, per_user=1)


def test_origin_check():
    assert ct.origin_allowed("https://st-pyxie.henderlabs.com", "st-pyxie.henderlabs.com")
    assert ct.origin_allowed("https://ST-Pyxie.henderlabs.com", "st-pyxie.henderlabs.com")
    assert not ct.origin_allowed("https://evil.example", "st-pyxie.henderlabs.com")
    assert not ct.origin_allowed("https://st-pyxie.henderlabs.com.evil.example", "st-pyxie.henderlabs.com")
    assert not ct.origin_allowed(None, "st-pyxie.henderlabs.com")
    assert not ct.origin_allowed("https://st-pyxie.henderlabs.com", None)
    assert not ct.origin_allowed("null", "st-pyxie.henderlabs.com")
    assert ct.origin_allowed("https://alias.example", "st-pyxie.henderlabs.com", extra_hosts=["alias.example"])


def test_kind_path():
    assert ct.pve_kind_path("vm") == "qemu"
    assert ct.pve_kind_path("lxc") == "lxc"
