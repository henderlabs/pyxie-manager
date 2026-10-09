"""Pure coverage for the update feature: the host updater's decision logic and the app's helpers.
No Docker, no git, no network, no database."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "shared"))

from pyxie_core.updates import block_reasons, read_history, rollback_target, tail_lines, validate_apply, write_request

spec = importlib.util.spec_from_file_location("pyxie_updater", HERE.parents[2] / "ops" / "docker" / "updater.py")
upd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upd)

TAGS = ["v0.1.0", "v0.9.0", "v0.10.0", "v0.24.0", "v0.25.0", "v0.25.1", "v1.0.0", "nightly", "v0.26", "vX.Y.Z", "v0.24.0-rc1"]


def test_select_releases_orders_numerically_and_ignores_junk():
    assert upd.select_releases(TAGS, "0.24.0") == ["0.25.0", "0.25.1", "1.0.0"]
    assert upd.select_releases(TAGS, "0.9.0") == ["0.10.0", "0.24.0", "0.25.0", "0.25.1", "1.0.0"]  # 0.10 sorts after 0.9, not before
    assert upd.select_releases(TAGS, "1.0.0") == []


def test_updater_refuses_bad_or_old_versions():
    for bad in (None, "", "latest", "0.25", "0.25.0; rm -rf /", "../0.25.0"):
        try:
            upd.validate_version(bad, "0.24.0", TAGS)
            assert False, f"accepted {bad!r}"
        except upd.StepFailed:
            pass
    assert upd.validate_version("v0.25.0", "0.24.0", TAGS) == "0.25.0"
    for v in ("0.24.0", "0.9.0"):
        try:
            upd.validate_version(v, "0.24.0", TAGS)
            assert False
        except upd.StepFailed as e:
            assert "not newer" in str(e)
    try:
        upd.validate_version("0.99.0", "0.24.0", TAGS)
        assert False
    except upd.StepFailed as e:
        assert "not a release tag" in str(e)


def test_instance_name_matches_the_web_app():
    assert upd.instance_name("cre-pyxie.slc.crengland.com") == "CRE-PyXie"
    assert upd.instance_name("st-pyxie.henderlabs.com") == "ST-PyXie"
    assert upd.instance_name("pyxie.example.com") == "PyXie" and upd.instance_name("10.1.6.199") == "PyXie" and upd.instance_name(None) == "PyXie"
    assert upd.instance_name("ops-console.example.com") == "PyXie (ops-console)"


def test_parse_env_handles_quotes_comments_and_never_needs_values_printed():
    e = upd.parse_env('# c\nA=1\nB="two words"\nC=\'x\'\n\nD=a=b\nBAD LINE\n')
    assert e == {"A": "1", "B": "two words", "C": "x", "D": "a=b"}


def test_in_flight_query_excludes_finished_and_not_yet_started_states():
    sql = upd.in_flight_sql()
    for s in ("completed", "failed", "blocked", "cancelled", "awaiting_approval", "pending", "dry_run"):
        assert f"'{s}'" in sql
    assert sql.startswith("select count(*) from operations where status not in")


def test_steps_have_unique_keys():
    for steps in (upd.UPDATE_STEPS, upd.ROLLBACK_STEPS):
        keys = [k for k, _ in steps]
        assert len(keys) == len(set(keys))


def test_app_only_offers_versions_the_last_check_found():
    status = {"releases": [{"version": "0.25.0"}, {"version": "0.25.1"}]}
    assert validate_apply("v0.25.1", status, "0.24.0") == "0.25.1"
    for v, why in (("0.30.0", "not an available"), ("0.24.0", "not an available"), ("abc", "not a release"), (None, "not a release")):
        try:
            validate_apply(v, status, "0.24.0")
            assert False
        except ValueError as e:
            assert why in str(e)
    try:
        validate_apply("0.25.0", status, "0.25.1")
        assert False
    except ValueError as e:
        assert "not newer" in str(e)


def test_block_reasons():
    ok = dict(installed=True, state={"state": "idle"}, in_flight=0, request_waiting=False)
    assert block_reasons(**ok) == []
    assert any("not installed" in r for r in block_reasons(**{**ok, "installed": False}))
    assert any("already in progress" in r for r in block_reasons(**{**ok, "state": {"state": "running"}}))
    assert any("3 operation" in r for r in block_reasons(**{**ok, "in_flight": 3}))
    assert any("waiting" in r for r in block_reasons(**{**ok, "request_waiting": True}))
    assert len(block_reasons(installed=False, state={"state": "running"}, in_flight=2, request_waiting=True)) == 4


def test_rollback_offered_only_for_the_update_that_is_still_running():
    h = [{"action": "update", "result": "success", "to_version": "0.25.0", "from_version": "0.24.0", "rolled_back": False},
         {"action": "update", "result": "success", "to_version": "0.24.0", "from_version": "0.23.0", "rolled_back": False}]
    assert rollback_target(h, "0.25.0")["from_version"] == "0.24.0"
    assert rollback_target(h, "0.26.0") is None                       # something newer is running now
    assert rollback_target([{**h[0], "rolled_back": True}], "0.25.0") is None
    assert rollback_target([{"action": "update", "result": "failed", "to_version": "0.25.0"}], "0.25.0") is None
    assert rollback_target([], "0.25.0") is None


def test_request_file_is_written_atomically_and_never_overwritten():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        write_request(p, {"action": "update", "version": "0.25.0"})
        assert json.loads((p / "request.json").read_text())["version"] == "0.25.0"
        assert not (p / "request.json.tmp").exists()
        try:
            write_request(p, {"action": "check"})
            assert False
        except FileExistsError:
            pass


def test_history_and_log_readers_tolerate_missing_and_damaged_files():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        assert read_history(p / "nope.jsonl") == [] and tail_lines(p / "nope.log") == []
        (p / "h.jsonl").write_text('{"a":1}\nnot json\n{"a":2}\n')
        assert [h["a"] for h in read_history(p / "h.jsonl")] == [2, 1]  # newest first, bad line skipped
        (p / "l.log").write_text("\n".join(str(i) for i in range(200)))
        assert tail_lines(p / "l.log", 3) == ["197", "198", "199"]


def test_console_route_probe_logic():
    from pyxie_core import edge_probe

    assert edge_probe.console_route_ok(403)  # made-up ticket refused by the API: the route reaches it
    assert not edge_probe.console_route_ok(502)  # fell through to the web layer: stale Caddy config
    assert not edge_probe.console_route_ok(None)
    assert edge_probe.probe_console_route("127.0.0.1", "x.example", port=1, timeout=0.5) is None  # nothing listening


def test_caddy_recreate_decision(monkeypatch):
    monkeypatch.setattr(upd, "git", lambda *a, **k: "ops/caddy/Caddyfile\n")
    assert "changed" in upd.caddy_needs_recreate("abc123")
    monkeypatch.setattr(upd, "git", lambda *a, **k: "")
    monkeypatch.setattr(upd, "compose", lambda *a, **k: "")  # no caddy container (native install): nothing to recreate
    assert upd.caddy_needs_recreate("abc123") is None
