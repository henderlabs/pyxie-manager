# Updates (Settings > Updates)

PyXie tells administrators when a newer release exists and can install it from **Settings > Updates**.
Nothing ever updates by itself: an administrator has to click **Update**.

## How it works

The web app and API never get any control over Docker. A small script on the **host**,
`ops/docker/updater.py`, does the work and is run from the same crontab that runs the backups:

| Cron | What it does |
|---|---|
| daily at 06:17: `updater.py check` | `git fetch --tags`; writes `update/status.json`; the first time it sees a newer release it records an in-app notification and e-mails the recipients of the enabled rules under Settings > Notifications (same relay as the backup alerts) |
| every minute: `updater.py poll` | if the app dropped `update/request.json`, carries it out |

"Check now" on Settings > Updates still works at any time: it drops a `check` request that the every-minute `poll` carries out. Hosts installed before v0.32.1 check every 30 minutes until `ops/docker/install-updater.sh` is run again (it replaces the old crontab lines and changes nothing else).

The API container mounts `./update` at `/update`. It reads `status.json`, `state.json`, `update.log`,
`history.jsonl`, and may write exactly one file, `request.json`
(`{"action": "check" | "update" | "rollback", "version": "0.25.0", "requested_by": "..."}`).
The updater **re-validates everything**: the version must be a `vX.Y.Z` tag that exists in origin, be newer
than the running version, and fast-forward the checked-out `main`. Nothing else in a request is acted on.

## An update, step by step

1. **Safety checks.** Refuses if any operation is running or approved, if the checkout has local changes or is not on `main`.
2. **Database backup**, validated with `pg_restore -l` (kept in `~/pyxie-backups`, named `pyxie_manager_pre_vX.Y.Z_*.dump`).
3. Fetch, then **fast-forward** the code to the tag.
4. **Build** the new images while the old containers keep running (a build failure changes nothing that is running).
5. `docker compose up -d`; the API applies any new migrations on start.
   If anything under `ops/caddy` changed in the update, or the Caddy container can no longer see its config, Caddy is
   **recreated** too (a few seconds of HTTPS downtime; certificates are kept). See "Caddy and updates" below.
6. **Health check**: api and web healthy and the app reports the new version, within 4 minutes. Then a **console route
   check**: a websocket for a made-up console ticket is sent through Caddy and must be refused by the API (403). A 502
   means Caddy is on an old config; the update still succeeds but you get a warning notification and the log says so.

If steps 3 to 6 fail, the previous version is restored automatically: code reset to the commit it was at, and
**the database restored from the backup if the new version had changed its schema** (migrations cannot be
safely run backwards). Changes made in the few minutes in between are lost in that case; the log says so.

**Restore previous version** (Settings > Updates) does the same on request for the last update, while it is
still the running version.

## Caddy and updates

Caddy mounts the `ops/caddy` **directory**. An update that replaces that directory leaves the running container holding
the old, deleted copy: it sees an empty `/etc/caddy` and keeps serving the configuration it loaded before the update.
Everything looks fine until a new route is needed -- on a server updated this way the embedded console failed with
`502` / `malformed HTTP status code "Server"` in the Caddy log because the `/console-ws` route did not exist in the
running config. Since v0.36.6 the updater recreates Caddy in that situation, and **Health** shows a warning
*"Caddy config is out of date: the console route is missing"* if it ever happens anyway (for example after a manual
`git pull`). Fix by hand with `docker compose up -d --force-recreate pyxie-manager-caddy`.

## Install on a server (once per host, as the user that owns the checkout)

```
bash ops/docker/install-updater.sh
```

Creates `./update` (world-writable, because the API container runs as a different user), adds the two cron
lines, and runs the first check. Run it **before** bringing up a version that mounts `./update`, otherwise
Docker creates the folder owned by root.

## Command line

```
python3 ops/docker/updater.py status
python3 ops/docker/updater.py update 0.25.0 --dry-run     # safety checks only
python3 ops/docker/updater.py update 0.25.0
python3 ops/docker/updater.py rollback
python3 ops/docker/updater.py test-mail
```

## Rehearsing failure (test only)

`PYXIE_UPDATER_TEST_FAIL=health|restart|build|code|backup|safety` makes an update fail right after that step,
which exercises the automatic rollback. `PYXIE_UPDATER_TEST_FORCE_DB_RESTORE=1` forces the database restore
during a rollback. Neither is set in cron, so normal runs are unaffected.
