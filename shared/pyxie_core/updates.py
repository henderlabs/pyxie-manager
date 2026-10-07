"""What the app may know and ask about software updates. Pure helpers: no database, no network.

The host-side updater (ops/docker/updater.py) does the work; the app only reads the files it
leaves in the update folder and writes one request file. See docs/updates.md.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
TERMINAL_STATUSES = ("completed", "failed", "blocked", "cancelled")
PRE_EXECUTION_STATUSES = ("awaiting_approval", "pending", "dry_run")


def semver(text: str) -> tuple[int, int, int]:
    a, b, c = text.lstrip("v").split(".")
    return int(a), int(b), int(c)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 -- a missing or half-written file is just "no data yet"
        return default


def read_history(path: Path, limit: int = 10) -> list[dict]:
    """Newest first."""
    out: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        return []
    return list(reversed(out))[:limit]


def tail_lines(path: Path, n: int = 80) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []


def block_reasons(*, installed: bool, state: dict, in_flight: int, request_waiting: bool) -> list[str]:
    """Why an update (or restore) cannot start right now; empty means it can."""
    reasons = []
    if not installed:
        reasons.append("The updater is not installed on this server (run `bash ops/docker/install-updater.sh` on it).")
    if state.get("state") == "running":
        reasons.append("An update is already in progress.")
    if request_waiting:
        reasons.append("A request is already waiting for the updater.")
    if in_flight:
        reasons.append(f"{in_flight} operation(s) are running or approved: wait for them to finish.")
    return reasons


def validate_apply(version: str | None, status: dict, current: str) -> str:
    """The version the user may ask for: it must be one the last check found, and newer than current."""
    v = (version or "").strip().lstrip("v")
    if not SEMVER.match(v):
        raise ValueError("That is not a release version.")
    offered = {r.get("version") for r in status.get("releases") or []}
    if v not in offered:
        raise ValueError(f"v{v} is not an available release (run a check first).")
    if semver(v) <= semver(current):
        raise ValueError(f"v{v} is not newer than the running v{current}.")
    return v


def rollback_target(history: list[dict], current: str) -> dict | None:
    """The last successful update, if it is still what is running and was not undone yet."""
    last = next((h for h in history if h.get("action") == "update" and h.get("result") == "success"), None)
    if last and not last.get("rolled_back") and last.get("to_version") == current:
        return last
    return None


def write_request(update_dir: Path, payload: dict) -> None:
    """Drop the request for the host updater. Refuses to overwrite one it has not picked up yet."""
    target = update_dir / "request.json"
    if target.exists():
        raise FileExistsError("A request is already waiting.")
    tmp = update_dir / "request.json.tmp"
    tmp.write_text(json.dumps(payload))
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
