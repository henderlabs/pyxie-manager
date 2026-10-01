# Adding a PVE host/cluster to PyXie

Verified live against the CRE onboarding (2026-09-15/16, `pve-slc-m401`,
an 8-node/~130-VM cluster) -- every step below is what actually happened,
not a speculative writeup. Where something needed a follow-up fix, that's
noted with what changed.

> **Plan for step 7 up front.** PyXie can migrate, evacuate, and power-manage
> with just the API tokens in steps 1-6, but it **cannot apply package
> updates** without a small wrapper installed on *every* PVE node (step 7).
> That install is manual, per node, and part of initial setup -- not an
> optional extra if you intend to use Apply Updates or Full Maintenance.

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

## 7. Connect each host for patching **[manual, once per node -- required to apply updates]**

**Why this exists.** PVE's REST API can *list* pending updates and refresh the
package index, but it has no endpoint to *apply* them (PVE's own web UI does
that through a shell on the host). So PyXie applies updates through a small
wrapper installed on each node and reached over SSH. This is a completely
separate trust model from the PVE API tokens above: it has its own SSH
identity, and nothing in steps 1-6 grants it.

**What works without it:** discovery, migration, evacuation, enter/exit
maintenance mode, power actions, and *checking* for updates.
**What needs it:** Apply Updates, and the patch step of Full Maintenance.

**What is once, and what is per node**

| Once per PVE target (cluster) | Once per node (every node, including ones added later) |
|---|---|
| Generate the keypair in PyXie | Run the provisioning kit's `install.sh` on the node (root, on the host) |
| Download the provisioning kit | Probe, verify, and pin that node's SSH host key in PyXie |

PyXie never installs the wrapper for you: it runs as root on your hosts, so
it stays a deliberate manual step (or part of your own configuration
management). Budget for it: a 16-node cluster means 16 installs and 16 host
key pins.

**Steps**

1. **Generate the keypair.** Platform -> Credentials -> *Host maintenance --
   connect a host* -> **Generate Keypair** (admin only). The private key is
   stored encrypted and is never displayed again.
2. **Download the kit.** Click **Download Provisioning Kit**.
3. **Install on each node.** Copy the kit to the node, unpack it, and run
   `sudo ./install.sh` there. With many nodes, run it through whatever you
   already use for host configuration (Ansible, etc.) rather than by hand.
4. **Pin each node's host key.** Back in PyXie, in that node's row: probe the
   host key, then compare the fingerprint shown with the one on the node
   (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`). Only when they match,
   click **Confirms match -- Pin**. Do not skip the comparison: it is what
   stops PyXie trusting the wrong machine.
5. **Check it took.** The node's row should show the key pinned and the node
   reachable. A node showing "Key not pinned" or "not reachable" has not been
   connected yet (usually `install.sh` has not been run on it).

**Things to know**

- **Regenerating the keypair breaks every node already installed** -- each
  one holds the old public key, so the new kit must be installed on all of
  them again. Only regenerate on purpose.
- **Removing a node:** run `sudo ./uninstall.sh` on it (the page offers an
  uninstall script download), then click **Disconnect** in PyXie.
- **New nodes join later** (a cluster growing from 8 to 16, say): add
  "run `install.sh`, then pin the host key" to your node build checklist.
  PyXie lists the new node as "Key not pinned" until you do.
- Nodes you have not connected simply cannot be patched through PyXie; they
  still work for everything else.

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
