"""Pure coverage for the VM liveness assessment and scanner. Fake clients only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.vm_liveness import (
    assess_liveness, liveness_block_reason, probe_vm, scan_vm_liveness,
)

OK = {"status": "running", "qmpstatus": "running", "uptime": 5}


def test_assess_ok_stopped_and_paused():
    assert assess_liveness(OK, 0.06)["state"] == "ok"
    assert assess_liveness({"status": "stopped", "qmpstatus": "stopped"}, 0.05)["state"] == "stopped"
    assert assess_liveness({"status": "running", "qmpstatus": "paused"}, 0.05)["state"] == "ok"


def test_assess_missing_or_unknown_qmpstatus_is_unresponsive():
    for st in ({"status": "running", "uptime": 9}, {"status": "running", "qmpstatus": "unknown"}, {"status": "running", "qmpstatus": ""}):
        a = assess_liveness(st, 0.2)
        assert a["state"] == "unresponsive" and "QMP" in a["detail"]


def test_assess_qemu_fault_states_are_problems():
    for q in ("internal-error", "io-error", "guest-panicked", "something-new"):
        assert assess_liveness({"status": "running", "qmpstatus": q}, 0.1)["state"] == "problem"


def test_assess_slow_and_errors():
    assert assess_liveness(OK, 3.5)["state"] == "slow"
    assert assess_liveness(None, 6.1, error="timeout: read timed out")["state"] == "unresponsive"
    assert assess_liveness(None, 0.1, error="Connection refused")["state"] == "unknown"
    assert assess_liveness(None, 0.0)["state"] == "unknown"


def test_block_reason_only_for_unresponsive_and_problem():
    assert liveness_block_reason(1, assess_liveness(OK, 0.05)) is None
    assert liveness_block_reason(1, assess_liveness(OK, 4.0)) is None  # slow alone does not block
    assert liveness_block_reason(1, assess_liveness(None, 1, error="Connection refused")) is None
    assert liveness_block_reason(1, None) is None
    r = liveness_block_reason(115, assess_liveness({"status": "running"}, 0.1))
    assert r and "Power-cycle" in r and "10 minutes" in r


class FakeClient:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def qemu_status_current(self, node, vmid, *, timeout=None, retries=1):
        self.calls.append((node, vmid, timeout, retries))
        a = self.answers[vmid]
        if isinstance(a, Exception):
            raise a
        if isinstance(a, list):  # successive answers
            a = a.pop(0)
            if isinstance(a, Exception):
                raise a
        return a


def test_probe_retries_a_timeout_once_then_gives_up():
    c = FakeClient({1: [Exception("timeout: slow"), OK]})
    assert probe_vm(c, "n1", 1)["state"] == "ok" and len(c.calls) == 2  # loaded host recovered on retry
    dead = FakeClient({2: Exception("timeout: dead")})
    assert probe_vm(dead, "n1", 2)["state"] == "unresponsive" and len(dead.calls) == 2
    refused = FakeClient({3: Exception("Connection refused")})
    assert probe_vm(refused, "n1", 3)["state"] == "unknown" and len(refused.calls) == 1  # not retried


def test_probe_passes_timeout_and_no_client_retries():
    c = FakeClient({1: OK})
    probe_vm(c, "n1", 1, timeout=4.0)
    assert c.calls == [("n1", 1, 4.0, 0)]


def test_scan_reports_each_vm_and_honours_the_time_budget():
    c = FakeClient({1: OK, 2: {"status": "running"}, 3: OK})
    r = scan_vm_liveness(c, [("n1", 1), ("n1", 2), ("n2", 3)])
    assert r[1]["state"] == "ok" and r[2]["state"] == "unresponsive" and r[3]["state"] == "ok"
    ticks = iter([0, 1, 100, 100, 100])  # clock: start, first VM in budget, then past it
    c2 = FakeClient({1: OK, 2: OK, 3: OK})
    r2 = scan_vm_liveness(c2, [("n", 1), ("n", 2), ("n", 3)], budget=45.0, clock=lambda: next(ticks))
    assert r2[1]["state"] == "ok" and r2[2]["state"] == "unknown" and r2[3]["state"] == "unknown"
    assert len(c2.calls) == 1  # the unchecked ones were never probed
