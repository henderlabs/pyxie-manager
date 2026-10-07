#!/usr/bin/env python3
"""PyXie host-side updater. Runs on the HOST (like backup-monitored.sh), never inside a container,
so the web app never gets any control over Docker.

    python3 updater.py check              look for a newer release, write update/status.json, announce it once
    python3 updater.py poll               (cron, every minute) carry out update/request.json if the app wrote one
    python3 updater.py update 0.25.0 [--dry-run]   update now from the command line (--dry-run = safety checks only)
    python3 updater.py rollback           restore the version before the last update
    python3 updater.py status             print what the app would show
    python3 updater.py test-mail          send a test announcement e-mail

How the app talks to it: the API container may write ONE file, update/request.json
({"action": "check"|"update"|"rollback", "version": "0.25.0", "requested_by": "..."}), and read the
other files in update/ (status.json, state.json, update.log, history.jsonl). Everything in the request is
re-validated here: the version must be a vX.Y.Z tag that exists in origin, is newer than what is running,
and fast-forwards the checked-out branch. Nothing else in a request is acted on.

An update: refuse if any operation is running -> database backup (verified) -> fetch -> fast-forward the
code -> build the new images while the old ones keep running -> restart -> wait until the new version
reports healthy. If it does not come up, the previous version is put back automatically (and the
database is restored from the backup if the new version had applied migrations).

Standard library only. Never prints .env.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

REPO = Path(os.environ.get("PYXIE_REPO") or Path(__file__).resolve().parents[2])  # PYXIE_REPO: for testing against another checkout
UPDATE_DIR = Path(os.environ.get("PYXIE_UPDATE_DIR") or (REPO / "update"))
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
SVC = ("pyxie-manager-api", "pyxie-manager-worker", "pyxie-manager-web")
DB = "pyxie-manager-db"
API = "pyxie-manager-api"
HEALTH_TIMEOUT = 240
TERMINAL = ("completed", "failed", "blocked", "cancelled")
PRE_EXECUTION = ("awaiting_approval", "pending", "dry_run")

UPDATE_STEPS = [
    ("safety", "Checked it is safe to update"),
    ("backup", "Database backup saved and verified"),
    ("fetch", "Downloaded the new version"),
    ("code", "Switched to the new code"),
    ("build", "Built the new version (the old one keeps running)"),
    ("restart", "Restarted the services"),
    ("health", "Checked the new version is healthy"),
]
ROLLBACK_STEPS = [
    ("safety", "Checked it is safe to restore"),
    ("stop", "Stopped the services"),
    ("restore", "Restored the database from the backup"),
    ("code", "Switched back to the previous code"),
    ("build", "Built the previous version"),
    ("restart", "Restarted the services"),
    ("health", "Checked the previous version is healthy"),
]


class StepFailed(Exception):
    pass


# ----------------------------------------------------------------------------- pure helpers

def semver(text: str) -> tuple[int, int, int]:
    a, b, c = text.lstrip("v").split(".")
    return int(a), int(b), int(c)


def select_releases(tags: list[str], current: str) -> list[str]:
    """Versions (no leading v) of vX.Y.Z tags newer than `current`, oldest first."""
    found = {m.group(1) for t in tags if (m := TAG.match(t.strip()))}
    cur = semver(current)
    return sorted((v for v in found if semver(v) > cur), key=semver)


def validate_version(version: str | None, current: str, tags: list[str]) -> str:
    """The requested version, or StepFailed saying why not."""
    v = (version or "").strip().lstrip("v")
    if not SEMVER.match(v):
        raise StepFailed(f"'{version}' is not a release version (expected something like 0.25.0)")
    if f"v{v}" not in {t.strip() for t in tags}:
        raise StepFailed(f"v{v} is not a release tag in this repository")
    if semver(v) <= semver(current):
        raise StepFailed(f"v{v} is not newer than the running version v{current}")
    return v


def instance_name(host: str | None) -> str:
    """cre-pyxie.example.com -> CRE-PyXie (same rule as the web app's tab title)."""
    h = (host or "").split(",")[0].strip().lower()
    h = re.sub(r":\d+$", "", h)
    if not h or h == "localhost" or ":" in h or re.fullmatch(r"[\d.]+", h):
        return "PyXie"
    label = h.split(".")[0]
    if "pyxie" not in label:
        return f"PyXie ({label})"
    return "-".join("PyXie" if p == "pyxie" else p.upper() for p in label.split("-") if p)


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def in_flight_sql() -> str:
    terminal = ",".join(f"'{s}'" for s in TERMINAL + PRE_EXECUTION)
    return f"select count(*) from operations where status not in ({terminal})"


# ----------------------------------------------------------------------------- environment, files, logging

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env() -> dict[str, str]:
    p = REPO / ".env"
    return parse_env(p.read_text()) if p.exists() else {}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data) -> None:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    p = UPDATE_DIR / "update.log"
    with p.open("a") as f:
        f.write(line + "\n")
    try:
        os.chmod(p, 0o644)
        if p.stat().st_size > 1_000_000:  # keep the log small: drop the older half
            lines = p.read_text().splitlines()
            p.write_text("\n".join(lines[len(lines) // 2:]) + "\n")
    except OSError:
        pass


def run(cmd: list[str], *, timeout: int = 300, input: str | None = None, check: bool = True, stdin_file: Path | None = None, stdout_file: Path | None = None) -> str:
    stdin = open(stdin_file, "rb") if stdin_file else None
    stdout = open(stdout_file, "wb") if stdout_file else subprocess.PIPE
    try:
        r = subprocess.run(cmd, cwd=REPO, input=(input.encode() if (input is not None and not stdin) else None), stdin=stdin,
                           stdout=stdout, stderr=subprocess.PIPE, timeout=timeout)
    finally:
        if stdin:
            stdin.close()
        if stdout_file:
            stdout.close()
    err = (r.stderr or b"").decode(errors="replace")
    if check and r.returncode != 0:
        raise StepFailed(f"{' '.join(cmd[:4])} failed ({r.returncode}): {err.strip()[-400:]}")
    return "" if stdout_file else (r.stdout or b"").decode(errors="replace")


def compose(*args: str, **kw) -> str:
    return run(["docker", "compose", *args], **kw)


def psql(sql: str, **variables: str) -> str:
    e = env()
    cmd = ["exec", "-T", DB, "psql", "-U", e.get("POSTGRES_USER", ""), "-d", e.get("POSTGRES_DB", ""), "-Atq", "-F", "|", "-v", "ON_ERROR_STOP=1"]
    for k, v in variables.items():
        cmd += ["-v", f"{k}={v}"]
    return compose(*cmd, input=sql, timeout=60).strip()


def current_version() -> str:
    return (REPO / "VERSION").read_text().strip()


def git(*args: str, **kw) -> str:
    return run(["git", *args], **kw).strip()


def all_tags() -> list[str]:
    return git("tag", "-l", "v*").splitlines()


def fast_forwards_to(version: str) -> bool:
    """True when the checked-out commit is an ancestor of vX.Y.Z, i.e. updating is a plain fast-forward."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", f"v{version}"], cwd=REPO, capture_output=True).returncode == 0


# ----------------------------------------------------------------------------- notifications (in-app + e-mail)

def notify(severity: str, title: str, message: str, *, email: bool = True, subject_tag: str = "update") -> None:
    try:
        psql(
            "insert into notifications (id, severity, title, message, status, source, created_at) "
            "values (gen_random_uuid(), :'sev', :'title', :'msg', 'unread', 'updates', now())",
            sev=severity, title=title, msg=message,
        )
    except Exception as e:  # noqa: BLE001 -- a failed notification must never fail an update
        log(f"[notify] could not record the in-app notification: {e}")
    if email:
        send_mail(f"[PyXie {subject_tag}] {title}", message)


def send_mail(subject: str, body: str) -> bool:
    """Same recipients and relay as the backup alerts: every ENABLED notification rule's recipients,
    through the SMTP relay configured under Settings > Email."""
    e = env()
    try:
        row = psql("select smtp_host, smtp_port, smtp_use_tls::int, coalesce(smtp_from_address,'') from app_settings "
                   "where smtp_enabled and coalesce(smtp_host,'')<>'' limit 1")
        to = psql("select string_agg(distinct r, ',') from notification_rules n, jsonb_array_elements_text(n.recipients) r where n.enabled")
    except Exception as ex:  # noqa: BLE001
        log(f"[mail] could not read mail settings: {ex}")
        return False
    if not row or not to:
        log("[mail] no recipient or relay configured (Settings > Notifications / Settings > Email); not sent: " + subject)
        return False
    host, port, tls, frm = (row.split("|") + ["", "", "", ""])[:4]
    frm = e.get("BACKUP_ALERT_FROM") or frm or f"pyxie-update@{os.uname().nodename}"
    rcpt = [a.strip() for a in to.split(",") if a.strip()]
    msg = MIMEText(body)
    msg["Subject"], msg["From"], msg["To"] = subject, frm, ", ".join(rcpt)
    try:
        with smtplib.SMTP(host, int(port or 25), timeout=15) as s:
            if tls == "1":
                s.starttls()
            s.sendmail(frm, rcpt, msg.as_string())
    except Exception as ex:  # noqa: BLE001
        log(f"[mail] send failed: {ex}")
        return False
    log(f"[mail] sent: {subject}")
    return True


def settings_url() -> str:
    host = env().get("PYXIE_HOSTNAME", "").strip()
    return f"https://{host}/platform/settings/updates" if host else "Settings > Updates"


# ----------------------------------------------------------------------------- check

def do_check(announce: bool = True) -> dict:
    err = None
    try:
        git("fetch", "--tags", "--force", "-q", "origin", timeout=120)
    except Exception as e:  # noqa: BLE001
        err = str(e)
        log(f"[check] fetch failed: {err}")
    cur = current_version()
    tags = all_tags()
    newer = select_releases(tags, cur)
    # only announce releases that fast-forward this checkout (not stray tags on other branches)
    newer = [v for v in newer if fast_forwards_to(v)]
    releases = []
    for v in newer[-15:]:
        notes = git("tag", "-l", "--format=%(contents)", f"v{v}", check=False)
        when = git("tag", "-l", "--format=%(creatordate:short)", f"v{v}", check=False)
        releases.append({"version": v, "date": when, "notes": notes})
    status = {
        "checked_at": now(), "current": cur, "latest": newer[-1] if newer else cur, "update_available": bool(newer),
        "releases": releases, "error": err, "instance": instance_name(env().get("PYXIE_HOSTNAME")),
    }
    write_json(UPDATE_DIR / "status.json", status)
    if announce and newer and not err:
        announced = read_json(UPDATE_DIR / "announced.json", {})
        if announced.get("version") != status["latest"]:
            body = (f"PyXie v{status['latest']} is available for {status['instance']} (running v{cur}).\n\n"
                    + "\n\n".join(f"v{r['version']}" + (f" ({r['date']})" if r["date"] else "") + "\n" + (r["notes"] or "(no release notes)") for r in releases)
                    + f"\n\nReview and update: {settings_url()}\n(Settings > Updates; an administrator has to click Update. Nothing updates by itself.)")
            notify("informational", f"PyXie v{status['latest']} is available for {status['instance']}", body)
            write_json(UPDATE_DIR / "announced.json", {"version": status["latest"], "at": now()})
    log(f"[check] running v{cur}, latest v{status['latest']}" + (" (update available)" if status["update_available"] else ""))
    return status


# ----------------------------------------------------------------------------- state

class State:
    def __init__(self, action: str, steps: list[tuple[str, str]], **fields):
        self.d = {
            "state": "running", "action": action, "started_at": now(), "finished_at": None, "message": "",
            "steps": [{"key": k, "label": label, "status": "pending"} for k, label in steps], **fields,
        }
        self.save()

    def save(self) -> None:
        write_json(UPDATE_DIR / "state.json", self.d)

    def step(self, key: str, status: str) -> None:
        for s in self.d["steps"]:
            if s["key"] == key:
                s["status"] = status
        self.save()

    def finish(self, state: str, message: str) -> None:
        self.d.update(state=state, message=message, finished_at=now())
        for s in self.d["steps"]:
            if s["status"] == "running":
                s["status"] = "failed" if state != "success" else "done"
        self.save()


def maybe_fail(key: str) -> None:
    """Test hook for rehearsing the rollback path (see docs/updates.md); a normal run never sets it."""
    if os.environ.get("PYXIE_UPDATER_TEST_FAIL") == key:
        raise StepFailed(f"test failure injected after '{key}'")


def history() -> list[dict]:
    p = UPDATE_DIR / "history.jsonl"
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    return out


def save_history(entries: list[dict]) -> None:
    p = UPDATE_DIR / "history.jsonl"
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in entries[-50:]))
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


# ----------------------------------------------------------------------------- shared steps

def in_flight() -> int:
    return int(psql(in_flight_sql()) or 0)


def alembic_head() -> str:
    return psql("select version_num from alembic_version")


def wait_healthy(version: str) -> None:
    deadline = time.time() + HEALTH_TIMEOUT
    last_log = 0.0
    why = "not started"
    while time.time() < deadline:
        try:
            rows = []
            for line in compose("ps", "--format", "json", timeout=30).splitlines():
                line = line.strip()
                if line:
                    j = json.loads(line)
                    rows.extend(j if isinstance(j, list) else [j])
            by = {r.get("Service") or r.get("Name"): r for r in rows}
            ok = True
            for svc in (API, "pyxie-manager-web"):
                r = by.get(svc) or {}
                if r.get("State") != "running" or r.get("Health") not in ("healthy", ""):
                    ok = False
                    why = f"{svc} is {r.get('State')}/{r.get('Health')}"
            if (by.get("pyxie-manager-worker") or {}).get("State") != "running":
                ok = False
                why = "worker is not running"
            if ok:
                h = json.loads(compose("exec", "-T", API, "python", "-c",
                                       "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/health',timeout=5).read().decode())", timeout=30))
                if h.get("version") == version:
                    return
                why = f"the app reports v{h.get('version')}, expected v{version}"
        except Exception as e:  # noqa: BLE001
            why = str(e)[:160]
        if time.time() - last_log > 20:
            log(f"[health] waiting: {why}")
            last_log = time.time()
        time.sleep(4)
    raise StepFailed(f"the new version did not become healthy within {HEALTH_TIMEOUT}s ({why})")


def backup_db(tag: str) -> Path:
    e = env()
    d = Path(os.environ.get("BACKUP_DIR") or e.get("BACKUP_DIR") or (Path.home() / "pyxie-backups"))
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    path = d / f"pyxie_manager_pre_{tag}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.dump"
    compose("exec", "-T", DB, "pg_dump", "-U", e.get("POSTGRES_USER", ""), "-d", e.get("POSTGRES_DB", ""), "-Fc", stdout_file=path, timeout=900)
    os.chmod(path, 0o600)
    toc = compose("exec", "-T", DB, "pg_restore", "-l", stdin_file=path, timeout=120)
    if path.stat().st_size < 100_000 or len(toc.splitlines()) < 20:
        path.unlink(missing_ok=True)
        raise StepFailed("the database backup looks incomplete, so nothing was changed")
    return path


def restore_db(dump: Path) -> None:
    e = env()
    compose("exec", "-T", DB, "psql", "-U", e.get("POSTGRES_USER", ""), "-d", e.get("POSTGRES_DB", ""), "-v", "ON_ERROR_STOP=1",
            "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;", timeout=120)
    compose("exec", "-T", DB, "pg_restore", "-U", e.get("POSTGRES_USER", ""), "-d", e.get("POSTGRES_DB", ""), "--no-owner", "--no-privileges",
            stdin_file=dump, timeout=900, check=True)


def rebuild_and_start(state: State, version: str, *, inject: bool = True) -> None:
    state.step("build", "running")
    log("[build] building images (the running version stays up)")
    compose("build", *SVC, timeout=1800)
    if inject:
        maybe_fail("build")
    state.step("build", "done")
    state.step("restart", "running")
    log("[restart] docker compose up -d")
    compose("up", "-d", timeout=900)
    if inject:
        maybe_fail("restart")
    state.step("restart", "done")
    state.step("health", "running")
    wait_healthy(version)
    if inject:
        maybe_fail("health")
    state.step("health", "done")


def restore_previous(state: State, *, prev_sha: str, prev_version: str, dump: Path | None, migrated: bool) -> None:
    """Put the previous code back (and the database, if the new version changed its schema)."""
    state.step("stop", "running")
    compose("stop", *SVC, timeout=300)
    state.step("stop", "done")
    state.step("restore", "running")
    if (migrated or os.environ.get("PYXIE_UPDATER_TEST_FORCE_DB_RESTORE")) and dump and dump.exists():
        log(f"[restore] the new version changed the database schema: restoring {dump.name}")
        restore_db(dump)
        state.step("restore", "done")
    else:
        state.step("restore", "skipped")
    state.step("code", "running")
    git("reset", "--hard", prev_sha)
    state.step("code", "done")
    rebuild_and_start(state, prev_version, inject=False)


# ----------------------------------------------------------------------------- update

def safety_checks(version: str | None, *, rollback: bool = False) -> str | None:
    cur = current_version()
    if not rollback:
        git("fetch", "--tags", "--force", "-q", "origin", timeout=120)
        v = validate_version(version, cur, all_tags())
        if not fast_forwards_to(v):
            raise StepFailed(f"v{v} does not fast-forward this checkout (it has diverged from the release history)")
    else:
        v = None
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise StepFailed("this checkout is not on the main branch")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise StepFailed("this checkout has uncommitted local changes; refusing to touch it")
    n = in_flight()
    if n:
        raise StepFailed(f"{n} operation(s) are running or approved; wait for them to finish")
    return v


def do_update(version: str | None, requested_by: str = "cron", dry_run: bool = False) -> bool:
    cur = current_version()
    if dry_run:
        v = safety_checks(version)
        log(f"[dry-run] v{cur} -> v{v}: all safety checks pass")
        return True
    state = State("update", UPDATE_STEPS, **{"from": cur, "to": (version or "").lstrip("v"), "requested_by": requested_by})
    log(f"=== update v{cur} -> v{(version or '').lstrip('v')} requested by {requested_by} ===")
    dump: Path | None = None
    prev_sha = ""
    before = ""
    changed_code = False
    try:
        state.step("safety", "running")
        v = safety_checks(version)
        state.d["to"] = v
        state.step("safety", "done")
        maybe_fail("safety")

        state.step("backup", "running")
        dump = backup_db(f"v{v}")
        state.d["backup"] = str(dump)
        log(f"[backup] {dump} ({dump.stat().st_size // 1024} KB)")
        state.step("backup", "done")
        maybe_fail("backup")
        before = alembic_head()

        state.step("fetch", "running")
        prev_sha = git("rev-parse", "HEAD")
        git("fetch", "--tags", "--force", "-q", "origin", timeout=120)
        state.step("fetch", "done")

        state.step("code", "running")
        git("merge", "--ff-only", f"v{v}")
        changed_code = True
        log(f"[code] now at {git('rev-parse', '--short', 'HEAD')} (v{v})")
        state.step("code", "done")
        maybe_fail("code")

        rebuild_and_start(state, v)
        after = alembic_head()
        migrated = after != before
        entry = {"id": now(), "action": "update", "from_version": cur, "from_sha": prev_sha, "to_version": v, "to_sha": git("rev-parse", "HEAD"),
                 "backup": str(dump), "migrated": migrated, "alembic_before": before, "alembic_after": after,
                 "started_at": state.d["started_at"], "finished_at": now(), "result": "success", "requested_by": requested_by, "rolled_back": False}
        save_history(history() + [entry])
        state.finish("success", f"Updated to v{v}." + (" The database schema changed." if migrated else ""))
        log(f"=== updated to v{v} ===")
        do_check(announce=False)
        notify("informational", f"PyXie updated to v{v} on {instance_name(env().get('PYXIE_HOSTNAME'))}",
               f"PyXie was updated from v{cur} to v{v}, requested by {requested_by}.\nDatabase backup taken first: {dump}\n{settings_url()}",
               subject_tag="update")
        return True
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        log(f"[FAILED] {msg}")
        for s in state.d["steps"]:
            if s["status"] == "running":
                s["status"] = "failed"
        state.save()
        if not changed_code:
            state.finish("failed", f"{msg} Nothing was changed.")
            notify("warning", "PyXie update did not start", f"The update to v{version} was stopped before anything changed: {msg}", subject_tag="update FAILED")
            return False
        # The code was switched: put the previous version back.
        log("[rollback] putting the previous version back")
        state.d["steps"] = [{"key": "failed", "label": "The update did not succeed", "status": "failed"}] + [
            {"key": k, "label": label, "status": "pending"} for k, label in ROLLBACK_STEPS[1:]
        ]
        state.save()
        try:
            migrated = alembic_head() != before
        except Exception:  # noqa: BLE001
            migrated = True  # cannot tell: assume it changed and restore the backup
        try:
            restore_previous(state, prev_sha=prev_sha, prev_version=cur, dump=dump, migrated=migrated)
            state.finish("rolled_back", f"{msg} The previous version v{cur} was restored automatically.")
            log(f"=== rolled back to v{cur} ===")
            notify("warning", f"PyXie update to v{version} failed and was rolled back",
                   f"{msg}\n\nPyXie v{cur} was restored automatically. Database backup: {dump}\nDetails: {settings_url()}", subject_tag="update FAILED")
        except Exception as e2:  # noqa: BLE001
            state.finish("failed", f"{msg} The automatic rollback ALSO failed: {e2}. Backup: {dump}. Check update/update.log on the server.")
            log(f"[rollback FAILED] {e2}")
            notify("critical", f"PyXie update failed and the rollback failed ({instance_name(env().get('PYXIE_HOSTNAME'))})",
                   f"{msg}\nRollback error: {e2}\nDatabase backup: {dump}\nThe server needs attention: see update/update.log.", subject_tag="update FAILED")
        return False


def do_rollback(requested_by: str = "cron") -> bool:
    cur = current_version()
    hist = history()
    last = next((h for h in reversed(hist) if h.get("action") == "update" and h.get("result") == "success"), None)
    state = State("rollback", ROLLBACK_STEPS, **{"from": cur, "to": (last or {}).get("from_version", ""), "requested_by": requested_by})
    log(f"=== restore previous version requested by {requested_by} ===")
    try:
        state.step("safety", "running")
        if not last or last.get("rolled_back") or last.get("to_version") != cur:
            raise StepFailed("there is no update to undo (the running version is not the result of the last update)")
        safety_checks(None, rollback=True)
        dump = Path(last["backup"])
        if last.get("migrated") and not dump.exists():
            raise StepFailed(f"the backup {dump} is gone, so the database cannot be restored")
        state.step("safety", "done")
        restore_previous(state, prev_sha=last["from_sha"], prev_version=last["from_version"], dump=dump, migrated=bool(last.get("migrated")))
        for h in hist:
            if h.get("id") == last.get("id"):
                h["rolled_back"] = True
        save_history(hist + [{"id": now(), "action": "rollback", "from_version": cur, "to_version": last["from_version"], "finished_at": now(),
                              "result": "success", "requested_by": requested_by}])
        state.finish("success", f"Restored v{last['from_version']}." + (" The database was restored from the backup taken before the update." if last.get("migrated") else ""))
        log(f"=== restored v{last['from_version']} ===")
        do_check(announce=False)
        notify("informational", f"PyXie restored to v{last['from_version']}", f"The update to v{cur} was undone, requested by {requested_by}.", subject_tag="update")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"[FAILED] {e}")
        state.finish("failed", f"{e}")
        notify("warning", "PyXie restore failed", str(e), subject_tag="update FAILED")
        return False


# ----------------------------------------------------------------------------- poll / entry point

def poll() -> None:
    req_path = UPDATE_DIR / "request.json"
    state = read_json(UPDATE_DIR / "state.json", {})
    if state.get("state") == "running":
        # We hold the lock, so whoever started this run is gone (power cut, killed): do not leave it "running".
        log("[poll] a previous run was interrupted")
        state.update(state="failed", message="The updater was interrupted part-way. Check update/update.log on the server; "
                                           "the pre-update database backup is named in the log.", finished_at=now())
        write_json(UPDATE_DIR / "state.json", state)
    if not req_path.exists():
        return
    work = UPDATE_DIR / "request.processing"
    os.replace(req_path, work)
    try:
        req = json.loads(work.read_text())
    except Exception:  # noqa: BLE001
        log("[poll] unreadable request ignored")
        work.unlink(missing_ok=True)
        return
    try:
        action = req.get("action")
        who = str(req.get("requested_by") or "unknown")[:80]
        if action == "check":
            do_check(announce=False)
        elif action == "update":
            do_update(req.get("version"), who)
        elif action == "rollback":
            do_rollback(who)
        else:
            log(f"[poll] unknown action ignored: {action!r}")
    finally:
        work.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(UPDATE_DIR, 0o777)  # the API container (a different uid) drops request.json here
    except OSError:
        pass
    mode = argv[1] if len(argv) > 1 else "poll"
    lock = open(UPDATE_DIR / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another run is in progress
    if mode == "poll":
        poll()
    elif mode == "check":
        do_check()
    elif mode == "update":
        ok = do_update(argv[2] if len(argv) > 2 else None, os.environ.get("USER", "cli"), dry_run="--dry-run" in argv)
        return 0 if ok else 1
    elif mode == "rollback":
        return 0 if do_rollback(os.environ.get("USER", "cli")) else 1
    elif mode == "status":
        print(json.dumps({"status": read_json(UPDATE_DIR / "status.json", {}), "state": read_json(UPDATE_DIR / "state.json", {}), "history": history()[-3:]}, indent=1))
    elif mode == "test-mail":
        return 0 if send_mail(f"[PyXie update] TEST ({instance_name(env().get('PYXIE_HOSTNAME'))})", "If you can read this, update announcements can reach you.") else 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
