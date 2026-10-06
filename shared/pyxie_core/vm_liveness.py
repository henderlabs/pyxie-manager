"""Is a running VM's QEMU actually answering?

PVE reports a VM as `running` from its process alone. When the QEMU control socket (QMP)
stops answering (VM 115 on m503, VM 1017 on m403) it still says `running`, but every
operation that talks to QEMU hangs: a live migration sits with an empty task log for about
10 minutes and then aborts. `status/current` is the cheapest call that needs QMP, so one
timed call tells us. Healthy VMs on CRE's cluster answer in about 0.06 s (measured 2026-10-06
across all 116 running VMs).

Pure assessment plus a small sequential scanner; the only thing it needs from the client is
`qemu_status_current(node, vmid, timeout=, retries=)`.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable

PROBE_TIMEOUT_SECONDS = 6.0
SLOW_SECONDS = 2.0
SCAN_BUDGET_SECONDS = 45.0

# QMP `query-status` values meaning "QEMU answered and the VM is in a normal or transitional state".
NORMAL_QMP = {
    "running", "paused", "prelaunch", "suspended", "inmigrate", "postmigrate",
    "finish-migrate", "save-vm", "restore-vm", "shutdown", "debug", "watchdog",
}
PROBLEM_QMP = {"internal-error", "io-error", "guest-panicked"}


def assess_liveness(status: dict | None, elapsed: float = 0.0, error: str | None = None) -> dict:
    """One status/current answer (or failure) -> {state, detail, elapsed}.

    state: ok | slow | unresponsive | problem | stopped | unknown
    unresponsive and problem are the two that block a migration.
    """
    out = {"elapsed": round(float(elapsed), 2)}
    if error is not None:
        if error.lower().lstrip().startswith("timeout"):
            return {**out, "state": "unresponsive",
                    "detail": f"PVE got no answer from this VM within {PROBE_TIMEOUT_SECONDS:.0f} s: its QEMU control socket is probably hung"}
        return {**out, "state": "unknown", "detail": f"Could not ask PVE: {error[:120]}"}
    if status is None:
        return {**out, "state": "unknown", "detail": "PVE returned no status"}
    st = status.get("status")
    if st != "running":
        return {**out, "state": "stopped", "detail": f"PVE status '{st}'"}
    qmp = status.get("qmpstatus")
    if qmp in (None, "", "unknown"):
        return {**out, "state": "unresponsive",
                "detail": "PVE says running but QEMU did not report its state: the QEMU control socket (QMP) is not answering"}
    if qmp in PROBLEM_QMP:
        return {**out, "state": "problem", "detail": f"QEMU reports '{qmp}'"}
    if qmp not in NORMAL_QMP:
        return {**out, "state": "problem", "detail": f"QEMU reports an unrecognised state '{qmp}'"}
    if elapsed > SLOW_SECONDS:
        return {**out, "state": "slow", "detail": f"Answered after {elapsed:.1f} s (healthy VMs answer in under 0.1 s)"}
    return {**out, "state": "ok", "detail": "QEMU answered normally"}


def probe_vm(client: Any, node_name: str, vmid: int, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict:
    """Ask PVE for one VM's live status and assess it. Never raises."""
    t0 = time.monotonic()
    error = None
    # One retry, on a timeout only: a heavily loaded host can be slow to answer the PVE API
    # without its VM being dead, while a genuinely hung QMP socket times out both times.
    for attempt in range(2):
        try:
            status = client.qemu_status_current(node_name, vmid, timeout=timeout, retries=0)
            return assess_liveness(status, time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001 -- a failed probe is a result, not an error
            error = str(e)
            if not error.lower().lstrip().startswith("timeout"):
                break
    return assess_liveness(None, time.monotonic() - t0, error=error)


def scan_vm_liveness(
    client: Any,
    guests: Iterable[tuple[str, int]],
    *,
    budget: float = SCAN_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[int, dict]:
    """Probe `(node_name, vmid)` pairs one after another; returns {vmid: assessment}.

    A VM with a hung socket costs up to PROBE_TIMEOUT_SECONDS, so a node with several would
    stall a preview: once the time budget is spent the rest are reported `unknown` (not
    checked) rather than holding things up.
    """
    start = clock()
    result: dict[int, dict] = {}
    for node_name, vmid in guests:
        if clock() - start > budget:
            result[vmid] = {"state": "unknown", "detail": "Not checked (scan time budget used up)", "elapsed": 0.0}
            continue
        result[vmid] = probe_vm(client, node_name, vmid)
    return result


def liveness_block_reason(vmid: Any, assessment: dict | None) -> str | None:
    """Why a running VM that is not answering must not be live-migrated, or None."""
    if not assessment or assessment.get("state") not in ("unresponsive", "problem"):
        return None
    return (
        f"VM does not respond normally ({assessment['detail']}). Moving it (or shutting it down) through PVE would hang for "
        f"about 10 minutes and then fail. Power-cycle it first (stop and start it from PVE; a graceful "
        f"shutdown will also hang), then preview again."
    )


# ---------------------------------------------------------------------------
# Background watch: turning a stream of probes into "this VM is persistently not responding"
# ---------------------------------------------------------------------------

BAD_STATES = ("unresponsive", "problem")
CONFIRM_AFTER = 2  # consecutive bad probes (each already retried once) before a finding is raised
FRESH_SECONDS = 180  # a result older than this no longer counts (VM stopped, worker down, ...)
NODE_WIDE_MIN_VMS = 4
NODE_WIDE_FRACTION = 0.6


def suppress_node_wide_failures(results: dict[int, dict], node_of: dict[int, str]) -> dict[int, dict]:
    """If most of a host's probed VMs fail in the same pass, the host's PVE API is struggling, not
    all those VMs at once: report those as `unknown` so no flood of 'not responding' findings.

    `results` is {vmid: assessment}; `node_of` is {vmid: node name}. Returns a new dict.
    """
    by_node: dict[str, list[int]] = {}
    for vmid in results:
        by_node.setdefault(node_of.get(vmid, ""), []).append(vmid)
    out = dict(results)
    for node, vmids in by_node.items():
        bad = [v for v in vmids if results[v].get("state") in BAD_STATES]
        if len(vmids) >= NODE_WIDE_MIN_VMS and len(bad) >= NODE_WIDE_FRACTION * len(vmids):
            for v in bad:
                out[v] = {
                    **results[v],
                    "state": "unknown",
                    "detail": f"{len(bad)} of {len(vmids)} VMs on {node} failed together: the host's PVE API is likely struggling, not these VMs",
                }
    return out


def next_liveness_row(prev: dict | None, assessment: dict, now: Any) -> dict:
    """Fold one probe into the stored row: {state, detail, elapsed, checked_at, bad_since, consecutive_bad}.

    A bad probe extends the streak; a definite answer (ok/slow/stopped) ends it; `unknown`
    (PVE could not be asked) leaves the streak as it was, so a blip does not clear a real problem.
    """
    prev_bad = int((prev or {}).get("consecutive_bad") or 0)
    prev_since = (prev or {}).get("bad_since")
    state = assessment.get("state")
    if state in BAD_STATES:
        bad, since = prev_bad + 1, prev_since or now
    elif state == "unknown":
        bad, since = prev_bad, prev_since
    else:
        bad, since = 0, None
    return {
        "state": state, "detail": assessment.get("detail"), "elapsed": assessment.get("elapsed"),
        "checked_at": now, "bad_since": since, "consecutive_bad": bad,
    }


def finding_worthy(row: dict, now: Any) -> bool:
    """True when a stored row justifies a 'VM is not responding' finding: a confirmed streak, still fresh."""
    checked = row.get("checked_at")
    if checked is None or int(row.get("consecutive_bad") or 0) < CONFIRM_AFTER:
        return False
    if checked.tzinfo is None and getattr(now, "tzinfo", None) is not None:
        checked = checked.replace(tzinfo=now.tzinfo)
    return (now - checked).total_seconds() <= FRESH_SECONDS
