"""Live log lines for an operation (host package updates, host reboots).

Two kinds of text land in `operation_log`, in order, and the Maintenance page tails them:
  - source "stage": short lines the workflow writes itself ("Applying updates", "Waiting for the host...")
  - source "host":  chunks of the host's own apt output, followed live from the pyxie-maint `log` command

The follower below is a pure state machine (testable without SSH or a database): it turns
successive `log` poll results into the new text since the last poll, using the unique
"=== pyxie-maint apply started <id> ===" marker line the wrapper writes first, so an older run
left in the file is never mistaken for this one.
"""

import base64
import logging
import re
import threading
from typing import Callable, Optional

from .host_maintenance_client import sanitize_captured_output

log = logging.getLogger(__name__)

MAX_LOG_CHARS = 1_000_000
_MARKER_RE = re.compile(rb"=== pyxie-maint apply started (\S+) ===\n")
FINISH_PREFIX = "=== pyxie-maint apply finished"


def _markers(data: bytes) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1).decode("ascii", "replace")) for m in _MARKER_RE.finditer(data)]


class HostLogFollower:
    """Feed it each `log` poll result; it returns the new, sanitized text (or "")."""

    def __init__(self, baseline: Optional[dict] = None):
        data = base64.b64decode((baseline or {}).get("data_b64", "") or b"")
        marks = _markers(data)
        self.baseline_marker = marks[-1][1] if marks else None
        self.baseline_size = int((baseline or {}).get("size", 0) or 0)
        self.offset: Optional[int] = None  # absolute raw byte offset consumed so far
        self.finished = False

    def feed(self, poll: dict) -> str:
        size = int(poll.get("size", 0) or 0)
        start = int(poll.get("start", 0) or 0)
        data = base64.b64decode(poll.get("data_b64", "") or b"")
        if size == 0 or not data:
            return ""
        note = ""
        if self.offset is None:
            new = [(i, mid) for i, mid in _markers(data) if mid != self.baseline_marker]
            if new:
                self.offset = start + new[-1][0]
            elif start > 0 and size != self.baseline_size and not _markers(data):
                # The log outgrew the 32 KB window before we saw its first line.
                self.offset = start
                note = "[earlier output not shown]\n"
            else:
                return ""  # this run has not started writing yet
        if size < self.offset:  # file was truncated again: a new run
            self.offset = None
            return self.feed(poll)
        begin = self.offset
        if begin < start:
            note = note or "[some output was skipped]\n"
            begin = start
        chunk = data[begin - start:]
        self.offset = start + len(data)
        text = sanitize_captured_output(chunk.decode("utf-8", "replace"), max_chars=1_000_000)
        if FINISH_PREFIX in text:
            self.finished = True
        return note + text


def append_log(operation_id, text: str, source: str = "host", *, session_factory=None) -> bool:
    """Append one chunk in its own short transaction (never touches the caller's session).
    Returns False once the per-operation cap is reached."""
    if not text:
        return True
    from sqlalchemy import func

    from .db import SessionLocal
    from .models import OperationLog

    db = (session_factory or SessionLocal)()
    try:
        used = db.query(func.coalesce(func.sum(func.length(OperationLog.text)), 0)).filter(
            OperationLog.operation_id == operation_id).scalar() or 0
        if used >= MAX_LOG_CHARS:
            return False
        if used + len(text) > MAX_LOG_CHARS:
            text = text[: MAX_LOG_CHARS - used] + "\n[log limit reached; further output not stored]\n"
        db.add(OperationLog(operation_id=operation_id, source=source, text=text))
        db.commit()
        return True
    except Exception:  # noqa: BLE001 -- logging must never break an operation
        log.exception("could not append operation log")
        db.rollback()
        return True
    finally:
        db.close()


def log_stage(operation_id, message: str) -> None:
    """One human-readable line from the workflow itself."""
    append_log(operation_id, message.rstrip("\n") + "\n", source="stage")


class HostLogStreamer:
    """Follows the host's apply log from a second SSH session while apply() runs in the first."""

    def __init__(self, operation_id, client_factory: Callable, creds, baseline: Optional[dict], *,
                 interval: float = 2.0, append=append_log):
        self.operation_id = operation_id
        self._client_factory = client_factory
        self._creds = creds
        self._follower = HostLogFollower(baseline)
        self._interval = interval
        self._append = append
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"oplog-{operation_id}", daemon=True)
        self._warned = False

    def start(self) -> "HostLogStreamer":
        self._thread.start()
        return self

    def stop(self) -> None:
        """Ask the thread to drain once more and finish; waits briefly."""
        self._stop.set()
        self._thread.join(timeout=20)

    def _run(self) -> None:
        client = None
        try:
            while True:
                stopping = self._stop.is_set()
                try:
                    if client is None:
                        client = self._client_factory(self._creds)
                        client.__enter__()
                    text = self._follower.feed(client.log())
                    if text:
                        self._append(self.operation_id, text, "host")
                    self._warned = False
                except Exception as exc:  # noqa: BLE001
                    if client is not None:
                        try:
                            client.__exit__(None, None, None)
                        except Exception:  # noqa: BLE001
                            pass
                        client = None
                    if not self._warned:
                        self._warned = True
                        self._append(self.operation_id, f"(live output paused: {str(exc)[:120]}; retrying)\n", "stage")
                if stopping:
                    return
                self._stop.wait(self._interval)
        finally:
            if client is not None:
                try:
                    client.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
