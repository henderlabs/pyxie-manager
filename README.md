# PyXie Manager

Browser-based Proxmox VE (PVE) operations console: inventory, historical
metrics, findings/recommendations, capacity/rightsizing, backup/protection
visibility, and a real write-capable maintenance/migration/placement
engine, gated by an explicit Safety Contract on every mutating action.

**This is not a read-only product.** VM live migration was the first
write capability added; the app now includes a full staged
maintenance/migration/placement engine covering guest lifecycle, resize,
node reboot, host package updates, node/full maintenance runs, cluster
rebalancing, per-workload NIC VLAN reassignment, and PBS backup-job
membership -- see the Safety Contract section below for exactly how every
one of those is gated.

## Installation

See [`docs/INSTALL.md`](docs/INSTALL.md) for a from-scratch install on a
fresh Ubuntu 24.04 VM, and [`docs/adding-a-host.md`](docs/adding-a-host.md)
for onboarding a PVE cluster once the app is running.

## Screenshots

**Dashboard** -- cluster resource usage, environment health, and what needs attention right now:

![Dashboard](docs/screenshots/dashboard.png)

**Rightsizing** -- per-workload observation status with evidence-backed sizing suggestions:

![Rightsizing](docs/screenshots/rightsizing.png)

**Maintenance** -- node evacuation, guest lifecycle, host updates, and full maintenance runs, every action previewed before approval:

![Maintenance](docs/screenshots/maintenance.png)

## Versioning

The root `VERSION` file (plain text, e.g. `0.3.0`) is the single source of
truth -- not an environment variable, which would let a running deployment's
version silently drift from what's actually committed. `api/app/config.py`
reads it directly at startup (`Settings.APP_VERSION`, surfaced via
`GET /api/health` and the FastAPI app's own `version` field); `web/src/app/layout.tsx`
reads the same file server-side on every request and threads it down to the
sidebar footer -- both always reflect exactly what's on disk, no rebuild
required to pick up a bump. Follows semver (pre-1.0: breaking changes can
still happen between minor bumps). Every version bump gets a matching
annotated git tag (`vX.Y.Z`) pushed to GitHub, so `git tag`/the repo's Tags
page is the authoritative version history across every deployment running
this codebase -- multiple independent deployments can be on different
versions at different times, and the tag is how you know which is which.

## Configuration

Every environment-specific value (database connection, Redis connection,
PVE/PBS targets and credentials, the Fernet key used to encrypt stored
credentials, the global write kill switch, org/site identity) lives in a
single `.env` file, never committed. See `.env.example` for the full list
of variables and what each one does. A deployment's `.env` is the only
thing that differs between environments -- the application code is
identical everywhere it runs.

`PYXIE_CREDENTIAL_KEY` (the Fernet key encrypting stored PVE/PBS token
secrets) has no recovery path if lost -- every stored credential becomes
undecryptable. Back it up alongside any database dump, outside of git.

## Safety Contract for every PVE write

`shared/pyxie_core/pve_write_client.py` is documented as, and actually is,
the ONLY module in this codebase allowed to issue a write request against
PVE -- `pve_client.py` stays GET-only by design. Every write goes through
two independent guardrails checked on every call, not just at startup:

1. **"Allow PyXie to write to Proxmox VE" must be turned on**, under
   Platform -> Settings. A global kill switch -- backed by
   `app_settings.pve_mutations_enabled`, not an env var (it used to be
   `PVE_MUTATIONS_ENABLED` in `.env`; moved to a live Settings toggle so
   it doesn't need a redeploy to change), and re-checked fresh on every
   single write call, not cached from operation-creation time.
2. **The credential used must come from the `maintenance` slot**, never
   `inventory` -- the two are loaded through separate code paths on
   purpose, so they can never accidentally cross.

Every write-capable operation type follows the identical staged sequence:
**dry-run (preview, no writes) -> awaiting_approval -> approved ->
revalidate against LIVE PVE state (never the DB cache) -> acquire a
resource lock -> execute -> monitor the real PVE task (a UPID is a
submission receipt, never treated as success on its own) -> independently
read state back -> verify -> audit.** Migration execution specifically
re-runs the exact same hard-block checks the preview showed (maintenance
mode, trust tier, PyXie affinity, PVE HA/CRS rules, CPU compatibility, PCI
passthrough, storage locality, headroom) via one shared function used by
both stages, so the two cannot drift apart.

**Self-protection:** the VM PyXie Manager itself runs on (configured via
`PYXIE_SELF_VMID`) is hard-blocked from `shutdown`/`force_stop`
unconditionally, no override exists -- this exists because a maintenance
plan spanning every guest on a host will otherwise happily include the
host running PyXie itself. Migrating or rebooting that VM is deliberately
still allowed: a node reboot's own PVE task already guarantees the guest
comes back up on its own, and migration is the actual safe way to move it
off a node that needs maintenance.

**Host-level network config is deliberately read-only.** The Network page
shows bridge/VLAN topology from PVE, and lets you reassign which VLAN a
workload's NIC is on (same Safety Contract as every other write above) --
but there is no write path for host bridge/VLAN-aware configuration
itself. That's a pending-changes/reload model where a bad apply can sever
a node's own network connectivity, a meaningfully different risk class
from every other write this app makes.

**Known, accepted risk (documented, not yet tightened):** the
`maintenance` PVE role is scoped at `/` (root), not per-resource. A bug in
PyXie could in principle touch any VM/storage/backup job visible to that
account, not only its intended target. Tightening this would mean real
ACL engineering that needs updating by hand every time PyXie's managed
resource set changes (new VM, new storage, new backup job) -- judged a
worse maintenance burden than the current root scope for a single-operator
deployment. Revisit for a deployment with more than one operator.

**Known, accepted risk (documented, not yet built):** capability grants
exist as descriptive metadata in the database but are not enforced as a
request-time authorization check anywhere in this codebase -- login is
the real gate. Consistent with how every other capability in this app
already works, not a special-cased gap; would need a deliberate decision
to change for all of them, not just one.

## Layered checks, HA/CRS is best-effort

PVE's own HA groups + HA rules are read and factored into migration/
maintenance eligibility (`shared/pyxie_core/maintenance.py::check_crs_affinity`),
but this is best-effort, not full parity with PVE's own HA manager:
node-affinity rules are fully parsed (including PVE 9's `node:priority`
syntax) and enforced as hard blocks; resource affinity/anti-affinity
(co-location between different workloads) is only flagged as present,
never fully resolved -- verify those manually for any HA-enrolled
workload before migrating it. The Workloads page's HA column tooltip
says the same thing.

## Stack

- **`web`** — Next.js 14 (App Router, TypeScript, Tailwind). Server
  components fetch the API directly; a handful of Next.js Route Handlers
  (`web/src/app/api/...`) proxy client-side mutations to the FastAPI
  backend.
- **`api`** — FastAPI + SQLAlchemy + Alembic, served by uvicorn.
- **`worker`** — Python, RQ-based. A background thread enqueues
  `worker.jobs.run_all` (discovery -> protection sync -> findings ->
  recommendations) on a schedule from Settings; the `operations` queue is
  where every dry-run's approved execution actually runs, one job per
  Operation, resumable across a worker restart via
  `resume_inflight_operations()` at startup.
- **PostgreSQL 16**, **Redis 7** (RQ queue backend).

Shared code (models, PVE read/write clients, discovery, placement, every
workflow module) lives in `shared/pyxie_core/` and is imported by both
`api` and `worker` via a symlink at `api/pyxie_core` / `worker/pyxie_core`
pointing at `../shared/pyxie_core`.

## Testing

`api/tests/` (pytest, `pip install -r api/requirements-dev.txt` to get
pytest itself if it's not already in the venv). Every test is fully
isolated -- no real database or PVE connection, even for reads; see
`api/tests/conftest.py` and `api/tests/fakes.py` for why that matters --
this suite is built to make it structurally impossible for a "pure logic
check" test to accidentally issue a real PVE write, not just unlikely.

```bash
cd api && source .venv/bin/activate && python3 -m pytest tests/ -v
```

## Authentication

Local auth, no external identity provider. Bootstrap: the first visit to
`/login` detects zero `users` rows and shows an admin-account creation
form instead (`GET /api/auth/bootstrap-status`, `POST /api/auth/bootstrap`
-- refuses once any user exists). Passwords hashed with bcrypt (cost 12).
Sessions are opaque random tokens in a `sessions` table, 24h fixed TTL,
checked on every protected request via a FastAPI dependency
(`get_current_user`). Admin/Viewer roles: every mutating endpoint is
gated by `require_admin`; a Viewer's write UI is hidden client-side as
polish, but the real boundary is the backend 403. The browser's session
cookie is HttpOnly, forwarded by `web`'s server-side code as
`Authorization: Bearer <token>` -- the API is never reachable from the
browser directly. Login/logout/failed-login are all audited.

## Historical metrics

`shared/pyxie_core/metrics.py` pulls PVE's own RRD data rather than
sampling continuously -- the *first* collection for an object backfills
`month`/`year` timeframes into `metric_points`, then every subsequent poll
tops up `hour`. Retention is a policy (`metrics.retention_days`, default
400) enforced by a periodic prune job.

`observation_stats()` computes avg/P95/max/sample_count/earliest/latest,
optionally windowed; `confidence_for_observation_days()` implements
cold-start bands (insufficient_data <7d, preliminary <30d, moderate <60d,
high 60d+).

## Findings, recommendations, capacity, rightsizing

- **Findings** (`shared/pyxie_core/findings.py`): an observed condition,
  reconciled every pass by a stable `dedupe_key` -- seen again bumps
  `last_observed`, not seen this pass auto-resolves it. Nothing is ever
  hard-deleted.
- **Recommendations** (`shared/pyxie_core/recommendations.py`):
  rightsizing, capacity-pressure, and pending-update suggestions, each
  with evidence/benefit/impact/confidence. Lifecycle is partly user-owned
  -- regenerating never reopens something a user dismissed.
- **Rightsizing** (`shared/pyxie_core/rightsizing.py`): conservative by
  design -- only ever suggests a reduction, never below 30 days of
  observed history for `moderate`/`high` confidence.
- **Capacity** (`shared/pyxie_core/capacity.py`): allocated vs. observed
  per node/cluster, busiest node, most-constrained resource.

## Protection subsystem

Generic contract (`protection_targets`/`protection_credentials`) +
normalized `protection_results` (tri-state `protected`: `true`/`false`/
`unknown`; `unknown` is never treated as safe). PBS is the fully
live-tested adapter today; Veeam/Commvault are structural-only, explicitly
marked `live_validation_status=not_tested` so structural support is never
mistaken for proven interoperability.

If a protection provider sync fails, every existing `ProtectionResult` row
for that provider is degraded to `confidence="stale"` (with the failure
recorded on the row itself) rather than silently keeping whatever
confidence its last successful sync produced -- a provider being
unreachable for an unknown length of time should be visibly untrustworthy,
not indistinguishable from a fresh, verified read.

## Maintenance, migration, and placement -- real write paths, staged

`shared/pyxie_core/maintenance.py` + the `*_workflow.py` modules implement
node evacuation, guest lifecycle, VM migration (live and offline
transport), resize, host package updates, node/full maintenance runs,
cluster rebalancing, per-workload NIC VLAN changes, and PBS backup-job
membership -- all through the Safety Contract described above. Quorum
math reads PVE's own live `/cluster/status` at call time (not a snapshot
of PyXie's own possibly-stale Node table), re-checked both at dry-run and
immediately before actually removing a node from service.

Resource locks (`shared/pyxie_core/locks.py`) prevent two PyXie operations
from fighting over the same node/workload/cluster; a partial unique index
(`uq_resource_locks_active_resource`) makes that structurally impossible
at the database level, not just application-level best-effort. Lock TTLs
are set to match each operation type's own real maximum runtime (its RQ
job timeout) plus a margin, not a flat default -- a migration/evacuation/
maintenance run needs to hold its lock for its actual multi-hour duration.

## Database schema summary

- **Core hierarchy:** `organizations` -> `sites` -> `clusters` -> `nodes`
  -> `workloads`; `storage` with an explicit `scope`.
- **Provider framework:** `provider_categories`/`capabilities` as plain
  data rows; `providers` (instance registry) + `provider_capabilities` +
  `capability_grants` (scoped, currently metadata-only -- see above).
- **PVE targets/credentials:** `pve_targets` + `pve_credentials` (slots:
  `inventory`/`maintenance`/`administrative`, Fernet-encrypted secrets).
- **Operations engine:** `operation_types` (seeded, stage list per type) +
  `operations` (one row per in-flight or historical write action, full
  state machine) + `resource_locks` + `audit_events` (full envelope).
- **Placement:** `placement_affinity_rules` (PyXie-level keep-together/
  keep-apart, independent of PVE's own HA affinity), node performance/
  trust tiers (stored as `policies` rows), `workloads.preferred_node_id`
  (soft placement preference).
- **Metrics/Findings/Recommendations/Policies/Safety rules:** as before.
- **Protection:** `protection_targets`/`protection_credentials`/
  `protection_results`.
- **Maintenance planning:** `maintenance_plans` + `maintenance_plan_workloads`.
- **Host maintenance:** `host_maintenance_credentials` -- a completely
  separate SSH-based trust model from PVE API credentials, used only for
  package updates (PVE's own API has no endpoint to apply them, only list).
- **Auth:** `users`, `sessions`.
- **Internal jobs:** `internal_job_runs`.

Alembic migrations: `api/migrations/versions/`.

## Known limitations / open items

- PVE resource pools are not tracked at all -- a PBS backup job scoped to
  a pool is detected and explicitly refused (rather than guessing at its
  membership), not supported.
- Protection status matching is VMID-only, not namespaced by cluster/site
  -- fine for a single-cluster deployment, would need real scoping before
  a second cluster with potentially-colliding VMIDs is added.
- Host update verification confirms the specific approved packages
  actually disappear from the live upgradable list after applying (not
  just that the wrapper self-reported success), but does not yet
  cross-check reboot-required semantics against which packages were
  applied.
- Search/filter state is not yet persisted in the URL for every table.
- The Workloads table is wide and several inline selects lack visible
  labels beyond their column header -- usable once you know the page, a
  genuinely new operator would benefit from a further pass here.
