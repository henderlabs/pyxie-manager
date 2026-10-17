# Backup namespaces

**Storage > Backup namespaces** lists every datastore of every backup server with its namespaces.

A **namespace** is a folder inside a datastore: an NFS share or an S3 bucket on the backup server. The datastore's stored data is shared by all its namespaces, so a backup that exists in two namespaces is stored once. But each namespace has its **own list of backups**, its own jobs and its own pruning. That is how two policies keep different retention in the same datastore without a second NFS share or Wasabi bucket.

On disk (NFS) a namespace is a real subfolder, `<datastore>/ns/<name>`. On S3 it is a key prefix in the bucket. You do not create the folder yourself: the backup server creates it through its API, which PyXie calls after you confirm a preview.

## What you can do

- **New namespace**: a name of letters, digits and `. _ -`, up to 7 levels separated by `/` (the parent must exist). Creating one costs no space and deletes nothing.
- **Delete**: only an empty namespace that nothing uses (no backups, no child namespaces, no plan copy and no sync or prune job pointing at it). The button is greyed out otherwise, and says why.
- **Use for backups...**: adds the namespace to Proxmox as a backup storage, so a plan's *first backup* can go there. It needs the **backup token** (below).
- The **Used by** column shows the plan copies and the PBS jobs that point at each namespace.

A plan's **copies** can target a namespace without any extra setup: choose or create the namespace in the plan builder and PyXie creates it with the copy's jobs.

## The backup token

Copies are made by the backup server itself (a sync job), so they need no token beyond the admin token. A plan's first backup is written by **Proxmox**, which needs a storage that points at the namespace and a token that may write to it. Create it with the account script (Settings > Credentials > Proxmox Backup Servers > *Create the backup server accounts*: tick **Backup token**). The account `pyxie-pbs-backup@pbs` gets `DatastorePowerUser` on `/datastore`: it can write backups and prune the ones it owns, nothing else. Save its token under the server's credentials as `backup`, then use **Use for backups...**. The storage Proxmox already uses for the datastore keeps its own login.

## Restoring from a namespace

Open the guest on **Protection > Guests & restore**. Restore points in a namespace show as `datastore / namespace`. If Proxmox has no storage for that namespace the button says **Make restorable...**: it adds a read-only storage (restore token) for that datastore and namespace, and restoring then works like any other restore ([restore.md](restore.md)).

## Garbage collection

One schedule per datastore, shared by all its namespaces. Set it in the plan builder (First backup or a copy) or on the backup server's **Datastores** tab.
