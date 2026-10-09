"""Step-by-step setup guide: which steps of a new installation are done, from plain data.

Pure functions (no database, no network) so the logic is testable. The API gathers the inputs (see
routers/setup.py); the Integrations page renders the result as a numbered guide.

States: done, next (the one to do now), todo (not done, not first), waiting (needs an earlier step), optional.
"""

from . import host_kit

REQUIRED = ("sites", "accounts", "cluster")


def _node_ready(n: dict, expected: str) -> tuple[bool, str]:
    if not n.get("pinned"):
        return False, "host key not pinned"
    ver = n.get("wrapper_version")
    if not ver:
        return False, "wrapper not seen yet"
    if host_kit.is_outdated(ver, expected):
        return False, f"wrapper {ver}, update available ({expected})"
    return True, f"pinned, wrapper {ver}"


def build_steps(inp: dict) -> list[dict]:
    expected = inp.get("expected_wrapper") or "0"
    targets = inp.get("targets", [])
    nodes = inp.get("nodes", [])
    st = inp.get("settings", {})
    steps: list[dict] = []

    def add(key, title, state, summary, **extra):
        steps.append({"key": key, "number": len(steps) + 1, "title": title, "state": state, "summary": summary, **extra})

    # 1. site
    n_sites = inp.get("sites", 0)
    add("sites", "Add a site", "done" if n_sites else "todo",
        f"{n_sites} site{'s' if n_sites != 1 else ''}." if n_sites else "A site is a location or grouping, such as Lab or Head office.",
        action={"label": "Open sites", "anchor": "sites"})

    # 2. proxmox accounts (we can only see their effect: an inventory credential exists)
    have_inv = any(t.get("inventory_status") for t in targets)
    add("accounts", "Create the Proxmox accounts", "done" if have_inv else "todo",
        "Read-only account and token created." if have_inv else
        "PyXie never creates accounts itself. In the Script builder (Proxmox accounts) tick what you need, copy the script to any Proxmox node, run it as root, "
        "and keep the token secrets it prints: you then add them to PyXie yourself (a new account does not appear in PyXie on its own).",
        action={"label": "Open the Script builder", "anchor": "builder-accounts"})

    # 3. cluster
    if not targets:
        add("cluster", "Connect your cluster", "waiting" if not have_inv else "todo",
            "Enter the hostname of any one node and the read-only token from step 2, then Test connection.",
            action={"label": "Add a PVE target", "anchor": "targets"})
    else:
        bad = [t for t in targets if t.get("inventory_status") != "valid"]
        empty = [t for t in targets if not t.get("nodes")]
        if bad:
            add("cluster", "Connect your cluster", "todo",
                f"{bad[0]['name']}: credential is {bad[0].get('inventory_status') or 'missing'}. Run Test connection on the target.",
                action={"label": "Open PVE targets", "anchor": "targets"})
        elif empty:
            add("cluster", "Connect your cluster", "todo", f"{empty[0]['name']}: connected, but no nodes discovered yet. Run Sync now.",
                action={"label": "Open PVE targets", "anchor": "targets"})
        else:
            total = sum(t.get("nodes", 0) for t in targets)
            add("cluster", "Connect your cluster", "done", f"{len(targets)} target{'s' if len(targets) != 1 else ''}, {total} nodes discovered.",
                action={"label": "Open PVE targets", "anchor": "targets"})
    cluster_ok = steps[-1]["state"] == "done"

    # 4. admin (maintenance) credential
    maint = [t.get("maintenance_status") for t in targets if t.get("maintenance_status")]
    if maint and all(s == "valid" for s in maint):
        add("admin", "Add the admin credential (for changes)", "done", "Maintenance (Admin) token saved and tested.", action={"label": "Open credentials", "href": "/platform/credentials"})
    elif maint:
        add("admin", "Add the admin credential (for changes)", "todo", "Saved but not tested yet. Open Credentials and click Test connection on it.", action={"label": "Open credentials", "href": "/platform/credentials"})
    else:
        add("admin", "Add the admin credential (for changes)", "waiting" if not cluster_ok else "optional",
            "Needed for migrations, power actions and reboots. Add the Maintenance (Admin) token on the target, then Test connection.",
            action={"label": "Open credentials", "href": "/platform/credentials"})

    # 5. hosts: three sub-steps, in the order they must be done
    cred_targets = {h.get("target_id") for h in inp.get("hostmaint", []) if h.get("has_cred")}
    missing_key = [t for t in targets if t["id"] not in cred_targets]
    items = []
    ready = 0
    wrapper_ok = 0
    pinned = 0
    for n in nodes:
        ok, why = _node_ready(n, expected)
        ready += ok
        pinned += bool(n.get("pinned"))
        wrapper_ok += bool(n.get("wrapper_version")) and not host_kit.is_outdated(n.get("wrapper_version"), expected)
        items.append({"name": n["name"], "ok": ok, "detail": why})
    n_nodes = len(nodes)
    key_done = bool(targets) and not missing_key
    wrap_done = bool(n_nodes) and wrapper_ok == n_nodes
    pin_done = bool(n_nodes) and pinned == n_nodes
    substeps = [
        {"key": "keypair", "label": "Generate the host key pair (once per cluster)",
         "state": "done" if key_done else ("waiting" if not cluster_ok else "todo"),
         "detail": "Done." if key_done else "PyXie creates an SSH key pair; the host script carries only its public half.",
         "action": None if key_done or not cluster_ok else {"kind": "generate_keypair", "target_id": missing_key[0]["id"] if missing_key else None, "label": "Generate the key pair"}},
        {"key": "script", "label": "Run the host script on every node",
         "state": "done" if wrap_done else ("waiting" if not key_done else "todo"),
         "detail": f"{wrapper_ok} of {n_nodes} nodes have the current wrapper." if key_done else "Needs the key pair first.",
         "action": None if wrap_done or not key_done else {"kind": "anchor", "anchor": "builder-host", "label": "Open the Host wrapper builder"}},
        {"key": "pin", "label": "Pin each node's SSH host key (compare the fingerprint)",
         "state": "done" if pin_done else ("waiting" if not key_done else "todo"),
         "detail": f"{pinned} of {n_nodes} nodes pinned." if key_done else "Needs the key pair first.",
         "action": None if pin_done or not key_done else {"kind": "href", "href": "/platform/credentials", "label": "Pin host keys (Credentials page)"}},
    ]
    if not cluster_ok:
        add("hosts", "Connect each host for patching", "waiting", "Needs the cluster connected first.", items=items, substeps=substeps)
    elif key_done and wrap_done and pin_done:
        add("hosts", "Connect each host for patching", "done", f"All {n_nodes} nodes ready.", items=items, substeps=substeps)
    elif not key_done:
        add("hosts", "Connect each host for patching", "todo",
            "Three parts, in order: a) generate the host key pair, b) run the host script on every node, c) pin each node's SSH host key.",
            items=items, substeps=substeps)
    else:
        add("hosts", "Connect each host for patching", "todo", f"{ready} of {n_nodes} nodes ready. Finish the parts below, in order.",
            items=items, substeps=substeps)

    # 6. features
    writes, console = bool(st.get("pve_mutations_enabled")), bool(st.get("console_enabled"))
    have_console_cred = any(t.get("console_credential") for t in targets)
    maint_ok = bool(maint) and all(m == "valid" for m in maint)
    if console and not (have_console_cred or maint_ok):
        console_txt = "on, but there is no credential that can open a console yet (add a console token, or a tested admin token whose role includes VM.Console)"
    elif console:
        console_txt = "on"
    elif have_console_cred or maint_ok:
        console_txt = "off (a credential is ready: switch it on in Settings to use the VM console)"
    else:
        console_txt = "off, and no console credential yet (in the Proxmox accounts builder either add VM.Console to the admin role or create a separate console token, then add it on Credentials)"
    feat = [f"Writes to Proxmox: {'on' if writes else 'off'}", f"Embedded console: {console_txt}"]
    add("features", "Turn features on", "done" if writes else "optional",
        "Both are off until you switch them on in Settings. " + "; ".join(feat) + ".", action={"label": "Open settings", "href": "/platform/settings"})

    # 7. notifications
    mail = bool(st.get("smtp_enabled")) and bool(st.get("notification_recipient_set"))
    add("notifications", "Notifications", "done" if mail else "optional",
        f"Email on, {st.get('notification_rules', 0)} rule(s)." if mail else "Set an email relay and who gets alerts, so problems reach you.",
        action={"label": "Open email settings", "href": "/platform/settings/email"})

    # 8. check
    add("check", "Check everything", "optional", "Runs every check and lists what is missing, in plain words.", action={"label": "Run check", "anchor": "check"})

    # mark the first required (then first recommended) step that is not done as "next"
    order = [s for s in steps if s["state"] in ("todo",) and s["key"] in REQUIRED] or [s for s in steps if s["state"] == "todo"]
    if order:
        order[0]["state"] = "next"
    return steps


def progress(steps: list[dict]) -> dict:
    countable = [s for s in steps if s["key"] != "check"]
    return {"done": sum(1 for s in countable if s["state"] == "done"), "total": len(countable)}


def server_checks(*, cpus: int, mem_total_mb: int, mem_avail_mb: int, disk_total_gb: float, disk_free_gb: float, db_mb: float, objects: int) -> list[dict]:
    """How this server compares with the suggested sizing (see Prerequisites.tsx). Plain data in, check rows out."""
    g = "This server"
    out = []

    def row(label, status, detail, fix=""):
        out.append({"group": g, "label": label, "status": status, "detail": detail, "fix": fix})

    row("CPU", "ok" if cpus >= 2 else "warn", f"{cpus} vCPU." + ("" if cpus >= 2 else " Updates build a new version beside the running one and will be slow."),
        "" if cpus >= 2 else "Give the VM at least 2 vCPU.")
    gb = mem_total_mb / 1024
    row("Memory", "ok" if mem_total_mb >= 3500 else "warn", f"{gb:.1f} GB total, {mem_avail_mb / 1024:.1f} GB available.",
        "" if mem_total_mb >= 3500 else "Give the VM at least 4 GB; updates build images and need the headroom.")
    pct_free = 100 * disk_free_gb / disk_total_gb if disk_total_gb else 0
    need = 30 + 0.1 * objects
    if disk_free_gb < 5 or pct_free < 5:
        st, fix = "fail", "Free space or grow the disk now: updates and backups will start failing."
    elif disk_free_gb < 10 or pct_free < 15:
        st, fix = "warn", "Getting low. Clear Docker build cache (docker builder prune) or grow the disk."
    else:
        st, fix = "ok", ""
    row("Disk", st, f"{disk_free_gb:.0f} GB free of {disk_total_gb:.0f} GB ({pct_free:.0f}% free). Suggested for {objects} nodes and guests: about {need:.0f} GB in total.", fix)
    row("Database", "ok", f"{db_mb:.0f} MB.")
    return out
