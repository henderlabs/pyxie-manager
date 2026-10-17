# Protection: the 3-2-1 scorecard

**Protection** answers one question per guest: does it have **3 copies** of its data, on **2 kinds of storage**, with **1 copy offsite**?

Proxmox VE and Proxmox Backup Server (PBS) run the backups. PyXie reads what they did and scores it. Nothing on this page changes a schedule, a retention rule or a datastore.

## The pages

- **Protection** is one page: the totals (guests that meet 3-2-1, partly protected, not protected), the **backup plan** in plain words (the default plan and any exceptions, each with Edit; see [retention-policies.md](retention-policies.md)) and the **guests**: search by name, ID, result, job or what is missing, and open a guest to see its plan (and pick a different one), every restore point on site and offsite, and to restore from one ([restore.md](restore.md)).
- **Protection > Backup servers** (details): the backup servers, then one box per datastore with labelled rows as pills: *Used by plans* (click a plan name to open its settings), *Pruning* (the keep rules, schedule and last run of each prune job, editable), *Freeing space* (garbage collection schedule, last run, edit and run now), *Verifying*, and *Copies arriving here*. A last box lists the Proxmox backup jobs with the plan that manages each and the retention each prunes to. A plan sets all of this for you; use this page to see or adjust what exists.
- **Storage > Backup namespaces**: folders inside a datastore that keep different retention apart ([backup-namespaces.md](backup-namespaces.md)).

## What the guest table says is missing

Each line names the place and the limit, for example *2-NAS_ST-Archive: newest backup is 3d old, should be under 2 days*, *3-OFFSITE_Wasabi (offsite): newest backup is 40d old, should be under 35 days* or *no backup yet*. A late **offsite** copy is judged against its sync job's schedule (35 days for monthly), a local one against 48 hours.

## How a copy is scored

- A copy that lives in a **namespace** still counts as a copy on its datastore.
- A datastore is **offsite** when it is S3-backed or its name contains "offsite".
- A backup counts when it is under 48 hours old. An offsite copy counts while it is within what its feeding job's schedule allows: 35 days for a monthly job, 9 days for a weekly one, 48 hours for a daily one. PyXie takes the schedule from the datastore's sync job, or its prune job if no sync job is visible.
- 3-2-1 is met when there are at least 3 copies (the live guest plus two fresh backups), at least 2 kinds of storage (for example disk and S3), and a fresh offsite copy.

A Proxmox Backup Server is managed from this page too (status, jobs, patching, access): see [adding-a-pbs.md](adding-a-pbs.md).

## What PyXie needs

Read access to PBS through the "inventory" token on the PBS target (Audit on the datastores and the prune, verify and sync jobs; without Remote.Audit PBS hides sync jobs that pull from a remote, and the page then says no sync job is visible), and the Proxmox privileges listed in [adding-a-host.md](adding-a-host.md).

## Earlier versions

v0.37.0 had a backup policy builder, v0.37.1 removed its UI, and it returns here as **Retention policies**, rebuilt around copies, namespaces and one default policy. Jobs named `pyxie-...` belong to policies; do not edit them by hand (a policy's preview shows what would be put back).
