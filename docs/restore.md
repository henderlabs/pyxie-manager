# Restoring a guest from a backup

On **Protection**, every guest row opens to show **all of its restore points**: each backup on every datastore PyXie can see, newest first, with its datastore (on site or offsite), size, verification state and notes. Press **Restore…** on a point to restore from it.

PyXie restores through Proxmox VE (the same call as `qmrestore` and `pct restore`), so it works for VMs and containers. Nothing changes until you confirm the preview.

## Choices

**Restore as a new guest** (the default, and the safest way to check that a backup really restores):
- **Name** (default `<name>-restored`), **ID** (default the next free one), **host** (online ones), **disks go to** (a storage with room, or the storages named in the backup) and **pool**.
- **Connect the network devices**: off by default. The new guest gets new hardware (MAC) addresses and its network devices are restored **disconnected** (link down), so it cannot appear on the network beside the original with the same address. Tick the box to restore them connected, or connect them later in the guest's hardware settings.
- **Start it after the restore**: off by default.
- The new guest gets the tag `restored` and a note naming the backup, and is undone with the guarded **Delete**.

**Overwrite the existing guest**: replaces the guest's disks and snapshots with the backup. This **cannot be undone**. It needs the guest **stopped**, not HA-managed, not locked and not the VM PyXie itself runs on, and you must type its exact name. The network option works the same way.

## What the preview checks

The backup still exists and has **not failed verification** on the backup server (a backup that is simply not verified restores with a warning); its configuration is read so the preview can show the disks and network devices; the new ID is free and the name is valid; the host is online (and not in maintenance for a new guest); the target storage can hold the disks (with a 10% margin); the pool exists; the maintenance token has the privileges. After you confirm, it checks the same things again, takes a lock, runs the Proxmox restore task (progress is in the Tasks panel; a large guest takes a while and survives a PyXie restart), sets the name, network state, tag and note, optionally starts the guest, and reads the result back from Proxmox (name, number of disks and network devices, link state, tag, status).

## Good to know

- A disk marked "no backup" (`backup=0`) is not in the backup, so it is not restored; Proxmox turns that line into a note on the restored guest. PyXie does not count such a disk when it checks the result.
- After an **overwrite**, Proxmox 9 may keep the replaced disks as `unused` disks of the guest, still using space. The result of the operation lists them; remove them in the guest's hardware settings.
- A restored guest does not start by itself when its host boots (the "start at boot" setting is cleared on a new guest).
- Containers use the same restore (`pct restore`). Proxmox restores a container onto one storage, so all its disks go to the storage of its root disk in the backup (or the storage you choose).
- Each restore point also has **Verify** (the backup server re-checks that backup now) and **Protect** (pruning skips a protected backup).

## Offsite copies

Proxmox can only restore from a datastore it has as a storage. A copy that exists only on another datastore (for example the offsite one) is listed with a **Make restorable…** button: it adds that datastore to Proxmox as a read-only backup storage through the optional `restore` token of the backup server (see [adding-a-pbs.md](adding-a-pbs.md)), after a preview. If no restore token is saved the preview says so. Restoring from an S3-backed datastore reads the data back from the bucket.

## Not covered

Restoring single files out of a backup stays in Proxmox or the backup server. Backups of a guest that no longer exists are not listed (restore points are per existing guest).

## Restoring from a namespace

Restore points that live in a namespace of a datastore show as `datastore / namespace` (see [backup-namespaces.md](backup-namespaces.md)). If Proxmox has no storage for that datastore and namespace, **Make restorable...** adds a read-only one (restore token), exactly like for an offsite datastore. A test restore of a container from a Wasabi namespace was verified on the lab: restored, config read back from Proxmox, then deleted.
