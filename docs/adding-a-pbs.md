# Adding and managing a Proxmox Backup Server

PyXie treats a Proxmox Backup Server (PBS) like a Proxmox host: it reads its status, shows and edits its sync, prune and verify jobs, and installs its package updates. Open **Protection > Backup servers > Manage**.

Setup has three independent parts, each with its own account so backups are protected by separation of duties: `pyxie-pbs-ro@pbs` (read only, distinct from the Proxmox read-only account), `pyxie-pbs-admin@pbs` (job edits only, distinct from the Proxmox `pyxie-admin`), and the `pyxie-hostmaint` SSH user (patching only). Disable or delete one without affecting the others. Step 1 is enough to watch a server; each later step adds a capability. PyXie never creates users, tokens or keys on the PBS: you run the commands, so you stay in control of what it may do.

## Before you start

- The backup server must be able to run `apt-get update`. A server **without a subscription** must not use the enterprise repository (it answers 401 and every apt command fails, including the host kit and patching). Disable it and use the no-subscription one:

  ```
  echo 'Enabled: no' >> /etc/apt/sources.list.d/pbs-enterprise.sources
  apt-get update
  ```

  (The no-subscription source is a line `deb http://download.proxmox.com/debian/pbs trixie pbs-no-subscription` in `/etc/apt/sources.list.d/pbs-no-subscription.list`.)
- A minimal PBS has no `sudo`. The host kit installs it (step 3), which needs the apt above to work. Run the kit as root with `bash`, not `sudo bash`.

## Step 1 and 2: the accounts (Setup guide, "Connect a backup server")

Same as for Proxmox VE: tick the tokens you want in **Settings > Credentials > Proxmox Backup Servers > Create the backup server accounts** (or the server's **Access** tab, step 0), press *Generate the account script*, and paste it into a root shell on the PBS. It is safe to paste and to re-run, and it saves the token secrets to `/root/pyxie-pbs-tokens.txt` (root only).

| Account (default name) | Token | Grants (to the user **and** its token) |
|---|---|---|
| `pyxie-pbs-ro@pbs` | `inventory` | `Audit` on `/`, `RemoteAudit` on `/remote` |
| `pyxie-pbs-admin@pbs` | `administrative` | `Audit` on `/`, `DatastoreAdmin` on `/datastore`, `RemoteSyncOperator` on `/remote`, and `Admin` on `/system` (only for Reboot Node and Full Maintenance) |
| `pyxie-pbs-restore@pbs` (optional) | `restore` | `DatastoreReader` on `/datastore` (only to add a datastore to Proxmox so you can restore from it) |
| `pyxie-pbs-backup@pbs` (optional) | `backup` | `DatastorePowerUser` on `/datastore` (only to add a namespace to Proxmox as a place a policy's first backup can go; it writes backups and prunes the ones it owns) |

The `Admin` grant on `/system` is the only way to give PBS's `Sys.PowerManagement` privilege (PBS has no custom roles); it is broader than PyXie needs, so skip it if you do not want PyXie to reboot the server (Test Connection then says so, and Reboot Node and Full Maintenance are refused). A token can never do more than its user, so both must be granted. (Granting only the token leaves it with no permissions and every check returns 403.) The `Audit` role does not include `Remote.Audit`: without `RemoteAudit` on `/remote` PBS hides sync jobs and remotes, and PyXie then shows no sync job for a job that exists.

Then add the server on the Protection or Setup page with the read-only token, and enter the admin token under **Settings > Credentials > Proxmox Backup Servers**. Press **Test Connection** on each token: it lists what the token can and cannot see.

With only the read-only token PyXie shows: version, uptime, load, memory and system disk, datastore usage with the estimated full date, running and failed tasks (24 hours), pending package updates, and all sync, prune and verify jobs with their last and next run. It raises findings (category "Backup server") when the server is unreachable, a datastore is 85% full or more, tasks failed, or updates are waiting.

The admin token lets PyXie create, change, delete and run sync, prune and verify jobs (**Jobs** tab) and manage each datastore (**Datastores** tab): set the garbage collection schedule, run garbage collection now, and verify every backup now. On Protection, a guest's restore points also have **Verify** (re-check one backup against its checksums) and **Protect** (a protected backup is skipped by pruning). Every change follows the Safety Contract: a preview with the exact settings that change, your confirmation, a re-check that the job is unchanged, the write, a read-back from PBS and an audit record. Deleting a job never deletes backups. The token cannot install packages or touch the operating system. The same Platform > Settings write switch that guards Proxmox changes guards these too.

## Copies, namespaces and the backup plan

With the admin token PyXie can also create **namespaces**, and the sync, prune and verify jobs a backup plan's copies need ([retention-policies.md](retention-policies.md), [backup-namespaces.md](backup-namespaces.md)). A copy is a sync job that pulls from the same server, so the server needs a **remote that points at itself** (PBS: Configuration > Remotes, host 127.0.0.1, for example named `localhost`, with a token that can read the datastores). The admin token creates the namespace and the jobs; it does not need a new grant.

## Step 3: SSH access (patching)

Neither the PVE nor the PBS API can install updates, only list them, so patching uses the same restricted wrapper (`pyxie-maint`) and pinned SSH host key PyXie uses for Proxmox hosts: fixed commands, no shell.

1. **Settings > Credentials > the server > Install or remove the host wrapper** (or the server's Access tab): choose *Install or upgrade the host wrapper* and *Generate the host script* (it generates the SSH key if needed). Paste the script into a root shell on the PBS. It downloads the kit, refuses to run if the checksum differs, installs `sudo` if missing, and installs the `pyxie-hostmaint` user, the wrapper and a sudoers entry.
2. **Settings > Credentials > Proxmox Backup Servers > Connect a Host:** *Read Host Key*, compare the fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the PBS, *Pin it*, then *Check Connection* (read-only: it asks the wrapper for its version and status). The state becomes READY.

Patching and rebooting are on the **Maintenance** page: the backup server is a row in the Nodes card (with a PBS chip, its update count and a *Reboot Required* badge), and the four actions work on it:

- **Check for Updates** lists what is waiting (refreshes apt through the wrapper, installs nothing).
- **Apply Updates** previews and installs: preview, your confirmation, a re-check, the upgrade, then a check against the live package list. 
- **Reboot Node** restarts the server through PBS's own API (needs the `Admin` grant above): preview, approval, re-check, reboot, then it waits for the server to answer with a smaller uptime and verifies the newest kernel is running and the datastores are listed.
- **Full Maintenance** is update (skipped if nothing is waiting), reboot and verify under one approval; each step keeps its own checks and a blocked or failed step stops the run.

All of them refuse unless the server is idle: no PBS task (backup, sync, prune, verify, garbage collection) running, and no backup running on a Proxmox host that may be writing to it, or when PyXie cannot read what is running. Backups that start while the server is rebooting fail and run at their next schedule, so pick a quiet moment.

## Offsite freshness

The Protection scorecard uses the schedule of the job that feeds an offsite datastore (its sync job, or its prune job when no sync job is visible) to decide how old that copy may be: 35 days for monthly, 9 for weekly, 48 hours for daily.


## Restoring from a datastore Proxmox does not have

Proxmox can only restore from storages it has. A backup that exists only on another datastore of the backup server (typically the offsite one) is listed on Protection as a restore point with **Make restorable…**. This adds that datastore to Proxmox as a read-only backup storage, using the optional `restore` token (`DatastoreReader`, never the admin token). The preview shows what is added; Proxmox keeps the restore token's secret in its cluster storage configuration; remove the storage later in Storage setup, nothing on the backup server changes. Then restore from it like any other restore point.
