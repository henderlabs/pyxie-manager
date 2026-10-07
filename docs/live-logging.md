# Live logging for host updates and reboots

The Maintenance page tails an operation's output while it runs and keeps it afterwards (Log panel on the
operation card). Two kinds of lines share one stream, stored in `operation_log` (capped at 1 MB per operation):

- **Workflow lines** (any host, no setup): "Approved. Re-checking...", "Starting apt dist-upgrade", "Waiting for pve103 to come
  back (45s)", "Verification passed: quorate, uptime...". Both host updates and host reboots write them.
- **Host output** (host updates only): the host's own `apt-get dist-upgrade` output, followed live. This needs
  `pyxie-maint` **1.1.0** on the host, which adds a seventh, argument-less, read-only command `log`.

## Upgrading the wrapper on a host (run as root, once per PVE node)

PyXie never installs this itself. From a checkout of this repo that is at v0.27.0 or later:

```
cd api/host_maintenance_kit
./install.sh /path/to/pyxie-hostmaint-key.pub     # the same public key as the original install
```

The installer is idempotent: it replaces `pyxie-maint`, `pyxie-maint-ssh-dispatch` and the sudoers entry in place (sudoers is
validated with `visudo -cf` before use) and does not create a second user or key line. Check with:

```
sudo -u pyxie-hostmaint sudo /usr/local/sbin/pyxie-maint version     # wrapper_version 1.1.0, "log" in capabilities
```

Hosts still on 1.0.0 keep working: updates apply exactly as before, the log panel shows the workflow lines plus
"Live host output is not available...", and the end-of-run output appears when apt finishes.

## How the live host output works

- `pyxie-maint apply` writes a unique start marker, then apt's output, then a finish line to `/var/log/pyxie-maint-apply.log`.
- While `apply` runs on one SSH session, a second PyXie SSH session calls `pyxie-maint log` every 2 s. It returns the last 32 KB
  with absolute byte offsets (base64); PyXie keeps its own offset, so nothing is stored on the host and nothing is accepted
  as an argument.
- The start marker is how PyXie tells this run from an older one still in the file. If more than 32 KB arrives between two polls
  the panel notes "[some output was skipped]"; the full log tail is still stored with the operation when apply ends.
- If the second SSH session drops, the panel says "(live output paused...)" once and retries; the update itself is unaffected.
