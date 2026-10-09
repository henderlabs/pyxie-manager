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

Get it from **Platform > Setup guide > "Script builder: Host wrapper"**. The card generates a short script that downloads the
installer from a 30-minute link (`/host-kit/<token>`, no login needed so a node can `curl` it) and checks the SHA-256 PyXie
shows before running it. The link carries no secret; integrity comes from the checksum, not the network. Links are capped
at 50 downloads. The card above it, "Script builder: Proxmox accounts", generates the Proxmox-account script (user, roles, tokens, optional console access). **You run the script yourself, as root, on each node: PyXie never installs it.** If your nodes do not trust PyXie's certificate (always true with the default internal CA, `PYXIE_TLS_MODE=internal`), the builder ticks "This node does not trust PyXie's certificate" for you, which adds `curl -k`; the checksum still protects the download. Without it the download fails with `curl: (60) SSL certificate problem`.

What the wrapper can do is fixed: seven commands behind a forced-command SSH key and an exact sudoers allowlist (tested:
`bash -i`, `; id`, `$(id)` and extra arguments are all refused).

PyXie records each node's wrapper version when it checks it (host page, Credentials page, a host update dry-run) and raises
a **warning** "Host wrapper on X is outdated" (category *Host wrapper*, e-mailable through Settings > Notifications, and shown as a banner on the Dashboard) when it is older than the wrapper this PyXie ships, and an info finding "Host wrapper not seen on X yet" for nodes of a cluster whose host key pair exists. Running the script on the node and checking it clears both.

On a node that is already set up, running the script again upgrades it, and the script's last lines say there is nothing more to do in PyXie (the host key stays pinned). Only a first install asks you to pin the host key.

**Impact:** once the wrapper is installed, applying updates from PyXie can reboot that node (kernel updates need it). Use maintenance mode so its VMs move off first. Installing the wrapper does not reboot anything.
