# Patching a node through PyXie

The procedure used to patch the CRE cluster one node at a time (m503 and
m401 first, 2026-10-02). Every step is previewed, then approved by a person;
nothing here runs unattended. Read
[`adding-a-host.md`](adding-a-host.md) first: the node must already have the
patching wrapper installed and its host key pinned.

## Before you start

1. **One node at a time.** Check that the remaining nodes can absorb the
   node's running RAM with room to spare (the preview shows where each VM
   would land and each destination's score).
2. **Nothing else in flight.** The Tasks panel should show nothing running
   or awaiting approval.
3. **Look for VMs that will not migrate.** A VM whose QEMU control socket
   is dead (it reports `running` but does not answer) hangs a live
   migration. Check the guest agent / status of each VM first; plan to skip
   or stop such VMs by hand and handle them separately.
4. **Know your keep-apart VMs.** Affinity rules (Affinity Rules page) are
   enforced when plans are built (v0.18.3+). Databases should have a rule;
   see below.
5. **Check the PVE-writes switch** (Settings) is on.

## The stages (each is its own approval)

1. **Enter Maintenance Mode.** Select exactly one node (zoom in and check
   the ticked box -- a banner can shift the page and move buttons), open
   the preview, review where every VM goes and which stopped ones stay,
   then *Approve & Execute*. VMs move one at a time, smallest first,
   roughly 45-60 s each for ordinary VMs; the run stops at the first
   failure. The node is only marked as in maintenance once PVE confirms no
   guests are running on it.
2. **Apply Updates.** Checks, then applies automatically if the check is
   clean (full `dist-upgrade` through the wrapper, about 6 minutes for ~270
   packages). A new kernel installs but only takes effect at the reboot.
3. **Reboot Node.** Preview, then approve. Expect the node offline for
   about 3 minutes. Watch cluster quorum (it should stay quorate with the
   other nodes online), that the node returns on the new kernel, that all
   storages are active, and that the PVE services are running.
4. **Exit Maintenance Mode.** Preview and approve. (The worker picks the
   operation up after a short delay.)
5. **Return load** (optional). Balance Load proposes moves cluster-wide and
   favours the largest VMs; review it and do not approve moves you would
   not make by hand. Bulk Migrate on the Maintenance page lets you tick
   specific VMs and pick a fixed destination.

A *Full Maintenance* run chains all of these; the staged form above is
easier to stop and was used on live workloads.

## Things to know

- **A failed operation cannot be resumed.** Start a new one (for example,
  re-run Enter Maintenance Mode: already-moved VMs are simply no longer on
  the node).
- **Keep databases apart.** Create a keep-apart rule per pair (workload
  pair) or tag the VMs and use one tag-group rule. Hard block forbids
  sharing a host; soft only applies a scoring penalty and will not stop a
  plan when one host is far emptier. Rules restrict moves PyXie plans; they
  do not move VMs that already share a host, and they are not checked
  against moves made directly in the PVE console.
- **Ballooning changes need a power-cycle** through PVE to take effect (not
  a restart from inside the guest); see README, "Memory readings and
  ballooning".
- **A mixed-version cluster is normal mid-rollout.** The nodes stay in the
  same cluster while you patch them one by one.

## What to watch while it runs

The Tasks panel shows each step with its start time and who started it.
For a deeper check: the operation's stage (`evacuating`, `verifying`,
`completed`), the PVE task log on the node (`qmigrate` tasks), cluster
quorum, and the worker log for errors.
