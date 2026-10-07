# Embedded VM console

PyXie can show a guest's screen (noVNC) inside the Console tab of a workload, and always offers
**Open in PVE**, which opens PVE's own console page in a new window (you sign in to PVE there).

## One-time setup (per PVE target; done by a PVE admin, not by PyXie)

Create a token with only console rights, on a PVE node:

```
pveum user token add pyxie-manager@pve console --privsep 1
pveum role add PyXieConsole --privs "VM.Console,VM.Audit"
pveum acl modify /vms --tokens 'pyxie-manager@pve!console' --roles PyXieConsole
```

Then in PyXie: **Credentials > add credential > slot `console`** (token user `pyxie-manager@pve`,
token id `console`, the secret), and **Settings > Allow the embedded VM console**.

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

## Notes

- Admin only; off by default (Settings switch). Switching it off closes open consoles within a minute.
- The PVE token's TLS is verified per the target's `tls_verify` setting (the lab has it off).
- The console credential cannot power off, migrate or reconfigure anything.
