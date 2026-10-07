"""Pure coverage for the updater's build-cache cleanup. Loads ops/docker/updater.py by path; no Docker needed."""

import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "ops" / "docker" / "updater.py"


def load():
    spec = importlib.util.spec_from_file_location("updater_under_test", PATH)
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    u.log = lambda *_a, **_k: None
    return u


def test_parse_reclaimed():
    u = load()
    assert u.parse_reclaimed("ID  RECLAIMABLE  SIZE\nabc  true  1GB\nTotal:  5.2GB\n") == "5.2GB"
    assert u.parse_reclaimed("Total:\t0B") == "0B"
    assert u.parse_reclaimed("nothing here") is None
    assert u.parse_reclaimed("") is None


def test_prune_caps_used_space_first_and_reports_freed():
    u = load()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return "Total:  4.1GB\n"

    u.run = fake_run
    msg = u.prune_build_cache()
    assert calls == [["docker", "builder", "prune", "-f", "--max-used-space", "3gb"]]
    assert "freed 4.1GB" in msg


def test_prune_falls_back_to_age_filter_on_older_docker():
    u = load()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "--max-used-space" in cmd:
            raise RuntimeError("unknown flag: --max-used-space")
        return "Total:  1GB\n"

    u.run = fake_run
    msg = u.prune_build_cache()
    assert len(calls) == 2 and "until=72h" in calls[1] and "freed 1GB" in msg


def test_prune_never_raises_even_if_everything_fails():
    u = load()

    def boom(cmd, **kw):
        raise RuntimeError("docker is not available")

    u.run = boom
    msg = u.prune_build_cache()
    assert "could not clear" in msg and "not a problem for the update" in msg
