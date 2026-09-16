# Adding a PVE host/cluster to PyXie

Verified live against the CRE onboarding (2026-09-15/16, `pve-slc-m401`,
an 8-node/~130-VM cluster) -- every step below is what actually happened,
not a speculative writeup. Where something needed a follow-up fix, that's
noted with what changed.

## Prerequisites

- A PVE cluster (or standalone host) reachable on port 8006 from the
  PyXie VM.
- Admin access to that PVE cluster's own web UI (Datacenter ->
  Permissions), to create the service accounts below. PyXie cannot
  create these for you -- it only ever authenticates as a token you
  hand it, never provisions PVE-side identities itself.
- A Site already added in PyXie (Platform -> Integrations -> Step 1).

## 1. Create dedicated PVE service accounts **[manual, in PVE's own UI]**

Never use `root@pam`. Create one user per credential purpose you plan to
configure -- PyXie's own onboarding form warns against `root@pam`
directly.

**Datacenter -> Permissions -> Users -> Add**, once per purpose:

| User | Realm | Purpose |
|---|---|---|
| `pyxie-ro` | Proxmox VE authentication server (`pve`) | inventory (read-only) |
| `pyxie-maint` | `pve` | maintenance (write-capable) |

Only create `pyxie-maint` when you're actually going to configure that
credential -- an unused user with no role grant has zero privilege, so
there's no harm creating it ahead of time, but there's also no need to
rush it.

## 2. Create the maintenance role, if you haven't already **[manual]**

`inventory` uses PVE's own built-in **`PVEAuditor`** role -- nothing to
create. `maintenance` needs a custom role that doesn't exist by default:

**Datacenter -> Permissions -> Roles -> Add**, name `PyXieMaintenanceW1`,
privileges (check all 11):

```
Datastore.Allocate, Datastore.AllocateSpace, Sys.Audit, Sys.Modify,
Sys.PowerMgmt, VM.Audit, VM.Config.CPU, VM.Config.Disk,
VM.Config.Memory, VM.Migrate, VM.PowerMgmt
```

This is deliberately narrower than PVE's built-in `PVEAdmin` role --
scoped to exactly what PyXie's write-capable code paths actually call,
not a general "can do most things" grant.

## 3. Create an API token per user **[manual]**

**Datacenter -> Permissions -> API Tokens -> Add**, once per user:

- User: the account from step 1
- Token ID: the credential purpose name (`inventory` or `maintenance`)
  -- matches PyXie's own form placeholder, not required but keeps things
  readable
- Privilege Separation: checked
- Expire: never (PyXie has no token-rotation flow yet)
- Copy the secret immediately -- PVE shows it exactly once

## 4. Grant the role **[manual]**

**Datacenter -> Permissions -> Add**, path `/`, once for the **user** and
once for the **token** (Privilege Separation means the token needs its
own grant, separate from the user's):

- `pyxie-ro@pve` and `pyxie-ro@pve!inventory` -> `PVEAuditor`
- `pyxie-maint@pve` and `pyxie-maint@pve!maintenance` -> `PyXieMaintenanceW1`

**Known, accepted risk:** the `maintenance` role is scoped at `/` (root),
not per-resource -- see README.md's Safety Contract section for why.

## 5. Add the PVE Target in PyXie

Platform -> Integrations -> Step 2 -> **+ Add PVE Target**:

- **Name**: your own label for this connection -- not required to match
  the PVE cluster's real name, which PyXie auto-detects on first sync
  and displays everywhere else in the app.
- **Hostname / IP**: any *one* node in the cluster. PVE exposes
  cluster-wide state from any single node's API; PyXie discovers every
  other node from there. It does not need to be able to reach every
  node's IP directly.
- **Verify TLS certificate**: leave checked only if this node has a
  real (non-self-signed) certificate. A stock PVE install uses a
  self-signed cert, which correctly fails verification -- uncheck this
  box, or fix it after the fact via the target's **Edit** link on the
  Integrations page (no need to delete and recreate).
- **Token user / Token ID / Token secret**: the `inventory` token from
  step 3.

Click **Test Connection**. This runs the same discovery pass as a
periodic sync, so it can take anywhere from a few seconds to over a
minute depending on cluster size -- large clusters (dozens of nodes,
100+ VMs) genuinely take longer, that's not a hang.

## 6. Add the maintenance credential (optional, when you're ready for write features)

Platform -> Credentials -> **+ Add Credential Purpose** on the target ->
`maintenance`, using the `pyxie-maint` token from step 3. Click **Test
Connection** on that specific credential row afterward -- the
target-level Test Connection only ever validates the `inventory` slot,
so this is the only way to confirm `maintenance` actually works.

Note: `maintenance` unlocks *reading* things `inventory` can't (PVE
requires `Sys.Modify`, not just `Sys.Audit`, to even list pending
package updates -- a real PVE API quirk), but PyXie's own
`PVE_MUTATIONS_ENABLED`-equivalent switch (Platform -> Settings ->
"Allow PyXie to write to Proxmox VE") is a separate, independent gate.
Both the credential *and* that switch have to allow it before any
actual write reaches PVE, no matter what gets approved in between.

## Known gotchas, found live

- **Self-signed cert TLS failure** looks like a connection failure with
  a certificate error in the target's status. Fix: uncheck "Verify TLS
  certificate" via Edit (see step 5).
- **A single very busy node can slow down or fail discovery.** One
  node's own guest-list response can legitimately take 10+ seconds on a
  cluster where that node carries far more VMs than the rest -- PyXie's
  PVE client timeout accounts for this, and if one node's fetch still
  fails, the rest of the cluster's data is unaffected (surfaced as a
  "Warning" status with the specific node/reason named, not a silent
  gap or a total failure).
- **Two syncs can't race each other.** Manually running discovery (via
  a script, or clicking Test Connection/Sync Now) while the periodic
  background sync is also running for the same target used to be able
  to collide; the second one now correctly waits its turn instead of
  writing conflicting data.
