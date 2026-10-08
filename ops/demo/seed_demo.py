"""Fictional demo dataset for PyXie Manager screenshots.

Run INSIDE the api container of the THROWAWAY demo stack (ops/demo/demo.sh does this):
    docker exec -i pyxie-demo-api python - < seed_demo.py
Everything here is invented: no real hostnames, IPs, VM names or accounts. Refuses to run
against a database that already holds a PVE target, so it cannot touch a real deployment.
"""
import math
import os
import random
from datetime import datetime, timedelta, timezone

from pyxie_core.db import SessionLocal
from pyxie_core.models import (
    AppSettings, Cluster, MetricPoint, Node, Organization, PlacementAffinityRule,
    Provider, ProviderCategory, PveCredential, PveTarget, Site, Storage, Workload,
)

GIB = 1024 ** 3
TIB = 1024 ** 4
now = datetime.now(timezone.utc)
rnd = random.Random(20261008)

db = SessionLocal()
if db.query(PveTarget).count() > 0:
    raise SystemExit("refusing to seed: this database already has a PVE target (not a fresh demo stack)")
org = db.query(Organization).first()
site = db.query(Site).filter(Site.organization_id == org.id).first()
org.name = "Example Org"
site.name = "Example Site"

settings = db.query(AppSettings).filter(AppSettings.id == 1).one()
settings.pve_mutations_enabled = False

# --- provider + target + cluster -------------------------------------------------
cats = [c.id for c in db.query(ProviderCategory).all()]
cat = next((c for c in cats if "compute" in c or "virtual" in c or c == "pve"), cats[0])
prov = Provider(category_id=cat, provider_type="proxmox_ve", name="Proxmox VE", instance_name="demo",
                enabled=True, contract_version=1, connection_health="healthy",
                implementation_status="implemented", live_validation_status="validated")
db.add(prov)
db.flush()
target = PveTarget(site_id=site.id, provider_id=prov.id, name="example-cluster",
                   hostname=os.environ.get("DEMO_PVE_HOST", "pyxie-demo-pve"), api_port=8006, tls_verify=False)
db.add(target)
db.flush()
# An inventory credential so the planner can build a client; it talks to ops/demo/fake_pve.py, not a real PVE.
from pyxie_core.crypto import encrypt_secret
db.add(PveCredential(pve_target_id=target.id, slot_name="inventory", token_user="demo@pve", token_id="inventory",
                     encrypted_secret=encrypt_secret("demo-not-a-real-secret"), status="valid", created_by="demo"))
db.flush()
cluster = Cluster(site_id=site.id, pve_target_id=target.id, name="example-cluster", quorate=True,
                  pve_version="8.4.1", node_count=4, first_seen=now - timedelta(days=90), last_seen=now)
db.add(cluster)
db.flush()

# --- nodes: deliberately uneven so the balance gauge has something to say --------
NODE_SPECS = [
    # name, cpu%, mem%, pending updates
    ("node-a", 31, 82, 2),
    ("node-b", 12, 41, 0),
    ("node-c", 36, 66, 3),
    ("node-d", 9, 35, 0),
]
nodes = {}
for name, cpu, mem, upd in NODE_SPECS:
    n = Node(cluster_id=cluster.id, site_id=site.id, name=name, status="online",
             management_ip=None, cpu_usage_pct=cpu, mem_usage_pct=mem, mem_total_bytes=128 * GIB,
             uptime_seconds=rnd.randint(20, 60) * 86400, pve_version="8.4.1", kernel_version="6.8.12-4-pve",
             pending_updates=upd, maintenance_mode=False, first_seen=now - timedelta(days=90), last_seen=now)
    db.add(n)
    nodes[name] = n
db.flush()

# --- workloads: name, node, type, vcpu, ram GiB, cpu mean%, mem mean%, os, tags, status
WL = [
    ("app-01",     "node-a", "vm",  8, 16, 62, 93, "l26",       ["app"],  "running"),
    ("app-02",     "node-c", "vm",  4,  8, 28, 55, "l26",       ["app"],  "running"),
    ("db-01",      "node-a", "vm",  8, 32, 34, 71, "l26",       ["db"],   "running"),
    ("db-02",      "node-c", "vm",  8, 32, 31, 69, "l26",       ["db"],   "running"),
    ("cache-01",   "node-a", "vm",  2,  8, 12, 58, "l26",       ["app"],  "running"),
    ("web-01",     "node-b", "vm",  2,  4, 14, 47, "l26",       ["web"],  "running"),
    ("web-02",     "node-d", "vm",  2,  4, 11, 44, "l26",       ["web"],  "running"),
    ("file-01",    "node-b", "vm",  4, 16,  4, 28, "l26",       [],       "running"),
    ("mail-01",    "node-c", "vm",  2,  4,  5, 55, "l26",       [],       "running"),
    ("dns-01",     "node-d", "vm",  2,  2,  1, 25, "l26",       [],       "running"),
    ("dc-01",      "node-a", "vm",  2,  8,  7, 52, "win2022",   ["dc"],   "running"),
    ("dc-02",      "node-c", "vm",  2,  8,  8, 49, "win2022",   ["dc"],   "running"),
    ("win-app-01", "node-b", "vm",  4,  8,  6, 35, "win2022",   [],       "running"),
    ("monitor-01", "node-d", "vm",  2,  4, 18, 61, "l26",       [],       "stopped"),
    ("proxy-01",   "node-a", "lxc", 2,  2,  6, 30, None,        [],       "running"),
    ("git-01",     "node-b", "lxc", 2,  4,  9, 42, None,        [],       "running"),
    ("wiki-01",    "node-d", "lxc", 1,  2,  3, 38, None,        [],       "running"),
    ("vpn-01",     "node-c", "lxc", 1,  1,  2, 22, None,        [],       "running"),
]
wls = {}
vmid = 100
for name, node, typ, vcpu, ram, cpu, mem, os_type, tags, status in WL:
    vmid += 1
    w = Workload(node_id=nodes[node].id, cluster_id=cluster.id, site_id=site.id, vmid=vmid, name=name,
                 type=typ, status=status, cpu_cores=vcpu, memory_bytes=ram * GIB, mem_guest_stats=True,
                 mem_host_bytes=ram * GIB, mem_used_bytes=int(ram * GIB * mem / 100), os_type=os_type,
                 tags=tags or None, first_seen=now - timedelta(days=90), last_seen=now)
    db.add(w)
    wls[name] = (w, cpu, mem, status)
db.flush()

# --- storage: ~48% used overall ---------------------------------------------------
STORAGE = [
    ("nas-nfs",     None,     "nfs", "cluster-shared", 8 * TIB, 0.52),
    ("local-lvm-a", "node-a", "lvm", "node-local",     2 * TIB, 0.44),
    ("local-lvm-b", "node-b", "lvm", "node-local",     2 * TIB, 0.41),
    ("local-lvm-c", "node-c", "lvm", "node-local",     2 * TIB, 0.50),
    ("local-lvm-d", "node-d", "lvm", "node-local",     2 * TIB, 0.38),
]
for name, node, typ, scope, cap, used in STORAGE:
    db.add(Storage(site_id=site.id, cluster_id=cluster.id, node_id=nodes[node].id if node else None,
                   name=name, type=typ, scope=scope, capacity_bytes=int(cap), used_bytes=int(cap * used),
                   status="available", first_seen=now - timedelta(days=90), last_seen=now))

# --- affinity rules ---------------------------------------------------------------
def pair(a, b):
    return [str(wls[a][0].id), str(wls[b][0].id)]

db.add(PlacementAffinityRule(rule_type="keep_apart", scope_type="workload_pair", workload_ids=pair("db-01", "db-02"),
                             strict=True, description="Databases stay on different nodes", created_by="demo"))
db.add(PlacementAffinityRule(rule_type="keep_together", scope_type="workload_pair", workload_ids=pair("app-01", "cache-01"),
                             strict=True, description="App and its cache share a node", created_by="demo"))
db.add(PlacementAffinityRule(rule_type="keep_apart", scope_type="workload_pair", workload_ids=pair("dc-01", "dc-02"),
                             strict=True, description="Domain controllers stay on different nodes", created_by="demo"))

# --- 45 days of hourly usage history ----------------------------------------------
def series(mean, hours, phase):
    out = []
    for h in range(hours):
        t = now - timedelta(hours=hours - h)
        daily = 1 + 0.35 * math.sin((t.hour + phase) / 24 * 2 * math.pi)
        weekday = 0.85 if t.weekday() >= 5 else 1.0
        v = mean * daily * weekday + rnd.gauss(0, mean * 0.08)
        out.append((t, max(0.2, min(99.0, v))))
    return out

HOURS = 45 * 24
rows = []
for name, (w, cpu, mem, status) in wls.items():
    if status != "running":
        continue
    phase = rnd.randint(0, 23)
    for t, v in series(cpu, HOURS, phase):
        rows.append(MetricPoint(object_type="workload", object_id=w.id, metric="cpu_pct", sampled_at=t, value=round(v, 2), source="demo"))
    for t, v in series(mem, HOURS, phase + 3):
        # memory moves much less than cpu
        v = mem + (v - mem) * 0.18
        rows.append(MetricPoint(object_type="workload", object_id=w.id, metric="mem_pct", sampled_at=t,
                                value=round(max(1.0, min(99.0, v)), 2), source="demo"))
for name, cpu, mem, _ in NODE_SPECS:
    n = nodes[name]
    for t, v in series(cpu, HOURS, 2):
        rows.append(MetricPoint(object_type="node", object_id=n.id, metric="cpu_pct", sampled_at=t, value=round(v, 2), source="demo"))
    for t, v in series(mem, HOURS, 5):
        v = mem + (v - mem) * 0.2
        rows.append(MetricPoint(object_type="node", object_id=n.id, metric="mem_pct", sampled_at=t,
                                value=round(max(1.0, min(99.0, v)), 2), source="demo"))
db.bulk_save_objects(rows)
db.commit()
# Let the app's own code compute rightsizing, recommendations and findings from the seeded history.
from pyxie_core.findings import evaluate_findings
from pyxie_core.recommendations import generate_recommendations
from pyxie_core.rightsizing import refresh_rightsizing_cache
refresh_rightsizing_cache(db)
generate_recommendations(db)
evaluate_findings(db)
print("seeded: %d nodes, %d workloads, %d storage, 3 rules, %d metric points" % (len(nodes), len(wls), len(STORAGE), len(rows)))
