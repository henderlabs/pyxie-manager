"""Pure coverage for the live host-log follower and streamer. No SSH, no database."""

import base64
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.operation_log import HostLogFollower, HostLogStreamer, MAX_LOG_CHARS


def poll(content: bytes, window: int = 32768) -> dict:
    start = max(0, len(content) - window)
    return {"size": len(content), "start": start, "data_b64": base64.b64encode(content[start:]).decode()}


OLD = b"=== pyxie-maint apply started 2026-10-06T10:00:00Z-11 ===\nold line\n=== pyxie-maint apply finished exit=0 ===\n"
NEW_HEAD = b"=== pyxie-maint apply started 2026-10-07T02:00:00Z-99 ===\n"


def test_nothing_until_this_run_starts_then_only_this_runs_output():
    f = HostLogFollower(poll(OLD))
    assert f.feed(poll(OLD)) == ""  # old run still in the file
    cur = NEW_HEAD
    out = f.feed(poll(cur))
    assert "started 2026-10-07" in out and "old line" not in out
    cur += b"Setting up a ...\n"
    assert f.feed(poll(cur)) == "Setting up a ...\n"
    assert f.feed(poll(cur)) == ""  # no change, no repeat
    cur += b"Setting up b ...\n=== pyxie-maint apply finished exit=0 ===\n"
    out = f.feed(poll(cur))
    assert out.startswith("Setting up b") and f.finished


def test_no_log_file_before_first_ever_run():
    f = HostLogFollower({"size": 0, "start": 0, "data_b64": ""})
    assert f.feed({"size": 0, "start": 0, "data_b64": ""}) == ""
    assert "started" in f.feed(poll(NEW_HEAD))


def test_old_wrapper_format_log_without_markers_is_ignored_while_unchanged():
    legacy = b"x" * 40000  # an old-format log bigger than the window, no markers
    f = HostLogFollower(poll(legacy))
    assert f.feed(poll(legacy)) == ""


def test_run_that_outgrows_the_window_between_polls_reports_a_gap():
    f = HostLogFollower(poll(OLD))
    big = NEW_HEAD + b"line\n" * 20000  # 100 KB written before the first poll
    out = f.feed(poll(big))
    assert out.startswith("[earlier output not shown]") and len(out) > 1000
    more = big + b"tail\n"
    assert f.feed(poll(more)) == "tail\n"


def test_gap_between_polls_is_noted():
    f = HostLogFollower(None)
    cur = NEW_HEAD
    assert "started" in f.feed(poll(cur))
    cur += b"z" * 50000  # more than the window arrived between two polls
    out = f.feed(poll(cur))
    assert out.startswith("[some output was skipped]")


def test_ansi_and_control_bytes_are_stripped():
    f = HostLogFollower(None)
    cur = NEW_HEAD + b"\x1b[31mred\x1b[0m ok\x07\n"
    assert "\x1b" not in f.feed(poll(cur))


def test_truncation_by_a_newer_run_restarts():
    f = HostLogFollower(None)
    long_run = NEW_HEAD + b"a\n" * 50
    f.feed(poll(long_run))
    second = b"=== pyxie-maint apply started 2026-10-07T03:00:00Z-5 ===\nfresh\n"
    out = f.feed(poll(second))
    assert "fresh" in out and "a\n" not in out


class FakeClient:
    """Serves a growing log; the streamer's second SSH session."""
    content = NEW_HEAD
    calls = 0

    def __init__(self, creds):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def log(self):
        FakeClient.calls += 1
        if FakeClient.calls == 2:
            FakeClient.content += b"mid run\n"
        if FakeClient.calls == 3:
            FakeClient.content += b"=== pyxie-maint apply finished exit=0 ===\n"
        return poll(FakeClient.content)


def test_streamer_appends_chunks_and_drains_on_stop():
    FakeClient.content, FakeClient.calls = NEW_HEAD, 0
    got = []
    done = threading.Event()

    def append(op_id, text, source):
        got.append((source, text))
        if "finished" in text:
            done.set()
        return True

    s = HostLogStreamer("op1", FakeClient, None, poll(OLD), interval=0.01, append=append).start()
    assert done.wait(5)
    s.stop()
    joined = "".join(t for _, t in got)
    assert "mid run" in joined and "finished exit=0" in joined
    assert all(src == "host" for src, _ in got)


class FlakyClient(FakeClient):
    def log(self):
        raise RuntimeError("ssh dropped")


def test_streamer_survives_ssh_failure_and_says_so_once():
    got = []

    def append(op_id, text, source):
        got.append((source, text))
        return True

    s = HostLogStreamer("op1", FlakyClient, None, None, interval=0.01, append=append).start()
    threading.Event().wait(0.2)
    s.stop()
    paused = [t for src, t in got if "paused" in t]
    assert len(paused) == 1


def test_cap_constant_is_one_megabyte():
    assert MAX_LOG_CHARS == 1_000_000
