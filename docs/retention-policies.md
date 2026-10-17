# The backup plan

A **backup plan** says, in one place, how a guest is protected: where it is backed up and when, how long that is kept, which copies are made elsewhere and how long each is kept, and the **maintenance** that keeps it healthy (pruning, freeing space, verifying). Open **Protection**.

There is **one default plan** that every guest follows. Anything different is an **exception**: a second plan that you pick for a guest (or a tag) on the guest's row.

Example, the lab's *Standard* plan:

1. Back up every night at 02:00 to the NAS: keep 14 daily, 4 weekly.
2. Copy monthly to Wasabi: keep 6 monthly.
3. Maintenance: prune daily, free the space (garbage collection) Saturday 18:15, verify Sunday 06:00.

## How Proxmox Backup Server keeps backups

- Every backup is a **complete snapshot** that shares its unchanged data with the others (deduplication). There is no full or incremental to choose and no synthetic full to build. "Keep 14 daily and 4 weekly" keeps the newest backup of each of the last 14 days and of each of the last 4 weeks (the two overlap a little, so the total is a bit under 18), and pruning never breaks a restore point because nothing depends on another backup.
- **Pruning** deletes backups outside the keep rules. It frees no space by itself.
- **Garbage collection (GC)** then removes data that no backup uses any more. It is **one schedule per datastore**, shared by every plan that writes there. On S3 (Wasabi) unused data is billed until GC runs.
- **Verify** re-reads backups and checks them against their checksums.

Veeam / Commvault words: the first backup is the backup job (primary copy); a **copy** is a backup copy job or auxiliary copy (a PBS sync job); pruning is retention or data aging; GC is the space-reclaim step.

## The plan form

A small **diagram** at the top of the form redraws as you change anything: the guest, each place a backup lands (disk or S3, offsite, namespace) with how often and what is kept there, the maintenance switched on, and a live 3-2-1 check (copies, kinds of storage, offsite).

- **1. Back up**: when, to which Proxmox storage, and how many daily, weekly, monthly and yearly backups to keep (*more* adds "newest N" and hourly). The Proxmox job prunes after each run, per guest.
- **2. Copy** (add as many as you like): when, to which backup server and datastore, and what to keep there. *More options for this copy*: a **namespace** (a folder inside the datastore with its own retention, see [backup-namespaces.md](backup-namespaces.md)), copy only the newest N, copy only verified backups, and remove a copy when the original is pruned (leave it off to keep copies longer than the originals).
- **Maintenance**: three switches, each with a schedule: **Prune**, **Free the space (GC)**, **Verify** (and after how many days a backup is checked again). They apply to every place the plan writes to. Off means PyXie leaves that setting alone.
- *More options*: make this the default plan, apply it to every guest with a tag, backup mode and a note on each backup.

**Preview** lists everything that will change: the Proxmox job, namespaces, copy jobs, prune jobs, verify jobs, garbage collection, and any other job that must skip these guests so nothing is done twice. Nothing changes until you confirm; PyXie then reads everything back from Proxmox and the backup server.

## What the maintenance switches do

- **Prune** keeps each place pruned to the plan: the Proxmox job's retention for the first backup; for the **default** plan also the datastore's root prune job (PyXie updates your existing job, for example `prune-archive`, and removes the keep rules the plan does not have, so "the rest is pruned"; with no such job it creates one); for a copy in a namespace its own prune job limited to that namespace. A datastore-wide prune job is limited to the datastore root (`max-depth 0`) so it never prunes namespaces.
- **GC** sets the datastore's garbage-collection schedule.
- **Verify** uses a verify job on the datastore (or namespace). An existing job for the same place is respected; otherwise PyXie creates `px-v-...`.
- A plan that is **not** the default never changes the datastore-wide prune job. A warning appears when the datastore's own prune job keeps less than the plan asks for.

## Per guest

Under **Guests**, open a guest to see its plan (the plan that applies and why: the default, a tag, or chosen for this guest), with a drop-down to change it: *Follow the default / tag*, *No backup*, or any plan. Below are all of its restore points, including those in namespaces.

A plan can only be deleted when nothing uses it: it must not be the default, not applied to a tag and not chosen for any guest. Move those guests first.

## What PyXie writes

- **Proxmox**: one backup job per plan, `pyxie-<plan>`. The default plan's job is an all-guests job with an exclude list, so a new guest is covered at once. Other plans' jobs list their guests.
- **Backup server**: for each copy a sync job `px-<plan>-<id>-s` that copies exactly the plan's guests (a group filter), a prune job `px-...-p` for a namespace copy, and verify jobs `px-v-...`. Their comment starts with "PyXie policy:". PyXie changes only jobs it created, plus these changes to jobs you own, always in the preview: it updates the datastore-root prune job for the default plan, limits other datastore-wide prune jobs to the root, and tells an older sync job to skip guests a plan now copies. The tick box *Take guests out of other backup and sync jobs...* turns the last one off.
- A copy needs a **remote that points at the backup server itself** (PBS: Configuration > Remotes, host 127.0.0.1, for example `localhost`). If the server has several, PyXie prefers the loopback one. The admin token must be saved (Settings > Credentials).

## 3-2-1

Counted like the scorecard: the live guest is copy 1, then the first backup and every copy. A plan meets 3-2-1 with at least 3 copies, 2 kinds of storage (live, disk, S3) and one offsite copy (an S3 datastore, or one named offsite). The coloured numbers at the top of Protection count guests; each plan shows whether it meets it.

## Safety

Every change is an operation: preview, your approval, a re-check that nothing changed in between, a lock, the writes, a read-back and an audit record. If the plan differs between the preview and the approval (someone edited a job meanwhile) it is blocked and you preview again. Deleting a plan deletes its jobs but never a backup.
