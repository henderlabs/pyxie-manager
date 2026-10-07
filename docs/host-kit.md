# Host kit: one installer per host

`pyxie-host-kit.sh` is a single self-extracting bash script that installs or upgrades PyXie's host-maintenance wrapper
(`pyxie-maint`, its SSH dispatch script and the sudoers entry) on a Proxmox node. It carries the scripts and this PyXie's
PUBLIC host key only; no secrets.

```
sudo bash pyxie-host-kit.sh              install or upgrade this node (idempotent)
     bash pyxie-host-kit.sh --check      installed vs available wrapper version (exit 0 current, 3 outdated, 4 not installed)
     bash pyxie-host-kit.sh --version
sudo bash pyxie-host-kit.sh --uninstall  remove the identity, wrapper and sudoers entry
```

Get it from **Platform > Integrations > Step 3 "Prepare a host"**. The page generates a short script that downloads the
installer from a 30-minute link (`/host-kit/<token>`, no login needed so a node can `curl` it) and checks the SHA-256 PyXie
shows before running it. The link carries no secret; integrity comes from the checksum, not the network. Links are capped
at 50 downloads. The same page can generate the Proxmox-account script (user, roles, tokens, optional console access).

What the wrapper can do is fixed: seven commands behind a forced-command SSH key and an exact sudoers allowlist (tested:
`bash -i`, `; id`, `$(id)` and extra arguments are all refused).

PyXie records each node's wrapper version when it checks it (host page, Credentials page, a host update dry-run) and raises
an info finding "Host wrapper on X is outdated" when it is older than the wrapper this PyXie ships.
