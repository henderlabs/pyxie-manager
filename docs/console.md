# Embedded VM console

PyXie can show a guest's screen (noVNC) inside the Console tab of a workload, and always offers
**Open in PVE**, which opens PVE's own console page in a new window (you sign in to PVE there).

## One-time setup (per PVE target; done by a PVE admin, not by PyXie)

Two options. PyXie uses the `console` credential if one is saved and otherwise falls back to the
`maintenance` credential. The easiest way is the **Script builder: Proxmox accounts** card on
**Platform > Setup guide**: it writes the script for either option (nothing is ticked at first).

| Builder choice ("Embedded VM console") | What the script does | PyXie credential to add |
|---|---|---|
| Not needed | nothing | none; the console stays unusable |
| **Add VM.Console to the admin role** | adds `VM.Console` to the `PyXieAdmin` role that the admin token (default `pyxie-admin@pve!maintenance`) holds | none beyond the `maintenance` credential: it is used for the console too |
| **Separate console token** | creates `pyxie-console@pve` (default name), token id `console`, built-in role `PVEVMConsole` at `/vms` | Credentials > + Add Credential Purpose > `console` (token user as created, token id `console`) |

The separate token has the smaller blast radius if it leaks: it cannot power off, migrate or reconfigure anything.

Either way, then switch on **Settings > Allow the embedded VM console**. The Setup guide's "Turn features on" step says
whether the console is off, and whether a credential that can open a console exists yet.

**By hand instead** (same result): for the admin-role option, `pveum role modify PyXieAdmin --privs VM.Console --append 1`
(the grant is on the token because of privilege separation: `pveum acl list` shows it). For a dedicated token:

```
pveum user token add pyxie-console@pve console --privsep 1
pveum acl modify /vms --tokens 'pyxie-console@pve!console' --roles PVEVMConsole
```

The account names are only labels; an install made earlier may use others (for example `pyxie-manager@pve`).

**If the console fails with 502 / "malformed HTTP status code"** in the Caddy log, the web proxy is running an old
configuration without the `/console-ws` route (see "Caddy keeps an old config" in docker-caddy.md). Health shows
"Caddy config is out of date" and the updater fixes it automatically from v0.36.6.

## How it works

1. An admin clicks Connect. `POST /api/workloads/{id}/console` asks PVE for a `vncproxy` session
   using the `console` token. PVE's ticket stays in Redis behind a one-time PyXie ticket
   (30 s, single use, bound to the user and guest).
2. The browser opens `wss://<host>/console-ws/<ticket>`. Caddy routes `/console-ws/*` straight to
   the API container (the Next.js layer cannot proxy websockets). The API consumes the ticket,
   checks the Origin header equals the page host, re-validates the session, and pipes frames to
   PVE's `vncwebsocket`.
3. Every 60 s the API re-checks the session, admin rights, the Settings switch, guest state and the
   credential; any failure closes the console. Hard cap 4 h. The 15 min idle limit is enforced in
   the browser (VNC traffic never goes quiet, so the server cannot tell idle from active).
4. Limits: 2 consoles per user, 10 total, 10 tickets per minute per user.
5. Audit events: `console.requested`, `console.opened`, `console.closed` (duration, byte counts,
   reason), `console.denied`, `settings.console_enabled_changed`. No screen or keystroke capture.

## Copy and paste

The **Clipboard** button opens a panel. **Type it in** sends your text to the guest as keystrokes
(up to 4000 characters) and works on any guest. **Send to guest clipboard** uses VNC cut text; the
guest then pastes with Ctrl+V, but only if it runs a clipboard agent (spice-vdagent on Linux). Text
copied inside the guest appears in the panel (same agent requirement) and in your browser
clipboard when the browser allows it. Clipboard text is not stored or logged.

## Notes

- Admin only; off by default (Settings switch). Switching it off closes open consoles within a minute.
- The PVE token's TLS is verified per the target's `tls_verify` setting (the lab has it off).
- The console credential cannot power off, migrate or reconfigure anything.
