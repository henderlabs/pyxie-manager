# Setup guide (Integrations page)

Platform > Integrations opens with "Set up PyXie, step by step": eight numbered steps with a status from your real data
(Done, Do this next, To do, Waiting, Optional), a progress bar, and a button for each step that jumps to where you do it.

| # | Step | Counts as done when |
|---|---|---|
| 1 | Add a site | at least one site exists |
| 2 | Create the Proxmox accounts | a read-only (inventory) credential exists |
| 3 | Connect your cluster | every target's inventory credential is valid and nodes are discovered |
| 4 | Add the admin credential | the Maintenance (Admin) credential is saved and tested |
| 5 | Connect each host for patching | host key pair generated, and every node has its SSH host key pinned and a current wrapper |
| 6 | Turn features on | the "write to Proxmox" switch is on (console is shown alongside) |
| 7 | Notifications | email relay on and a recipient set |
| 8 | Check everything | (button) |

Step 2 is done by running the script from the script builder on a Proxmox node; PyXie never creates accounts itself.
The builder works before any cluster is connected (the host-wrapper part needs a connected target).

"Run check" (step 8) does live, read-only checks and lists problems in plain words: each token works, the console
permission (when the console is on), how many nodes can act as the cluster entry point if one is down (ingress failover),
each host's SSH wrapper (connected, version), the write and console switches, and email alerts.

API: `GET /api/setup/status` (database only, any signed-in user) and `POST /api/setup/check` (admin).

## Quick start (Platform > Quick start)

A short page for new installations: the two Proxmox tokens side by side (read-only inventory and Maintenance (Admin)),
what each is used for and cannot do, exactly how to create each in Proxmox (the script builder, or the web-interface
clicks) and where each goes in PyXie, what each privilege of the `PyXieAdmin` role is for, and the safety gates. The
Dashboard shows a "New here?" banner until a site, the accounts and a connected cluster exist.
