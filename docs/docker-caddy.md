# Running PyXie in Docker behind Caddy (HTTPS)

The `compose.yaml` stack runs everything in containers and puts **Caddy** in
front on 80/443. Only Caddy publishes ports. Postgres, Redis and the API are
on a private Docker network and are not reachable from the LAN.

```
browser --HTTPS--> caddy :443 --> web (Next.js) :3000 --> api :8000 --> db / redis
                                                          worker -----> db / redis, PVE
```

The native systemd install (`ops/install/install.sh`) still works and is
unchanged. Pick one per host; don't run both against the same database.

## What you need first

- **One VM**, Ubuntu 24.04 LTS. Caddy, PostgreSQL and Redis run as containers in the stack; install only Docker Engine with
  the Compose plugin (`sudo bash ops/docker/install-docker.sh`), and put the login user in the `docker` group. `git`,
  `python3`, `cron` and `curl` (standard on Ubuntu) are used by the installer, the in-app updater and the backup.
- **Suggested size**: Small (up to 4 nodes / 50 guests) 2 vCPU, 4 GB, 40 GB; Medium (up to 16 nodes / 300 guests) 4 vCPU,
  8 GB, 80 GB; Large (more) 8 vCPU, 16 GB, 120 GB+. Disk is about 30 GB plus 0.1 GB per node or guest (400 days of
  metrics is 30 to 60 MB per object, plus backups; Docker build cache can reach 15 GB where images are built by hand
  repeatedly; the updater caps it at 3 GB after each update, and `docker builder prune` clears it any time). Measured on a 4-node, 24-guest lab: the whole stack used about 0.5 GB RAM and almost
  no CPU. On an 8-node, 130-guest production install: about 0.9 GB RAM, a 2.9 GB database, 22 GB of disk in use. The updater builds a new version beside
  the running one for about a minute, so keep at least 2 vCPU and 4 GB. Run check (Integrations, step 8) compares the VM
  with these numbers.
- **Network**: a DNS name for the VM; users reach 443 (80 redirects). VM to every Proxmox node on TCP 8006, and 22 if
  you patch hosts through PyXie. VM out to GitHub (HTTPS) and your mail relay. A node reaches the VM on 443 once, to get
  the host installer. NTP on the VM.
- **A certificate choice** (`PYXIE_TLS_MODE`, below). `internal` is quickest for a lab, but browsers and Proxmox nodes will
  not trust it by default (the host script has an option for that).
- **Access**: read access to the repository on GitHub, and someone with root on a Proxmox node to create the accounts and
  install the host wrapper (PyXie never does that for you).

## Configuration (`.env`)

The stack uses the same `.env` as the native install, plus:

| Variable | Meaning |
|---|---|
| `PYXIE_HOSTNAME` | FQDN Caddy serves, e.g. `cre-pyxie.slc.crengland.com` (required) |
| `PYXIE_TLS_MODE` | `internal` (default), `file`, or `acme` |
| `ACME_CA` | ACME directory URL, only for `acme` mode |

`DATABASE_URL` and `REDIS_URL` are overridden inside the containers (see
`compose.yaml`), so the localhost URLs a native install keeps in `.env` are
harmless. `POSTGRES_PASSWORD` must be URL-safe (letters, digits, `._~-`).

`PYXIE_CREDENTIAL_KEY` **must be carried over unchanged** from the existing
install. It encrypts stored PVE credentials; a different key makes them
unreadable.

## TLS modes

- **`internal`** — Caddy's own CA signs the cert. Browsers warn until you trust
  the root. Export it:
  `docker compose cp pyxie-manager-caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt`
  and import it into the OS/browser trust store of the machines that use PyXie.
- **`file`** — put `pyxie.crt` (full chain) and `pyxie.key` in `./certs/` (this
  directory is gitignored). Caddy does not renew these.
- **`acme`** — for an ACME-capable internal CA. Fill in
  `ops/caddy/tls/acme.caddy` (currently a placeholder) and set `ACME_CA`.
  Caddy then renews automatically.

Switching modes: edit `.env`, then `docker compose up -d pyxie-manager-caddy`.

## First-time setup on a host

1. Install Docker (a human, as root): `sudo bash ops/docker/install-docker.sh`
2. Add the Docker variables to `.env` (above). DNS for `PYXIE_HOSTNAME` must
   resolve to this host.
3. `docker compose build`
4. Copy the existing database in (see below) or start empty with
   `docker compose up -d` (migrations run when the API starts).

## Moving an existing native install

1. `ops/docker/restore-native-db.sh` — dumps the native DB (read-only) and
   restores it into the container DB. Re-runnable.
2. **Parallel test, no downtime:** start everything *except the worker*:
   `docker compose up -d pyxie-manager-db pyxie-manager-redis pyxie-manager-api pyxie-manager-web pyxie-manager-caddy`.
   The native app keeps serving on `:3000`. The worker is excluded on purpose:
   a second worker would poll the same clusters and could send duplicate alert
   emails.
3. **Cutover:** stop the native services (`pyxie-worker`, `pyxie-api`,
   `pyxie-web`: `systemctl disable --now`), re-run step 1 for a fresh copy,
   then `docker compose up -d`.
4. **Rollback:** `docker compose down`, `systemctl enable --now` the three
   native units. The native database was never modified.

## Day-to-day

```bash
docker compose ps
docker compose logs -f pyxie-manager-api
docker compose up -d --build          # deploy new code after `git pull`
ops/docker/backup.sh                  # pg_dump + .env copy to ~/pyxie-backups
```

Container logs rotate (10 MB x 5 per container).

## Scheduled backups with failure alerts

`ops/docker/backup-monitored.sh` wraps `backup.sh` for cron and runs on the
host, so it still works when the stack is down:

```cron
15 2 * * * cd <repo> && flock -n /tmp/pyxie-backup.lock ops/docker/backup-monitored.sh run   >> ~/pyxie-backups/backup.log 2>&1
30 8 * * * cd <repo> && ops/docker/backup-monitored.sh check >> ~/pyxie-backups/backup.log 2>&1
```

- `run` takes the backup, validates it (size and `pg_restore -l`), and emails on
  failure; a partial dump it created is removed so it cannot pass for a good one.
- `check` emails if the newest backup is older than 26 h (override with
  `BACKUP_MAX_AGE_HOURS`) or missing, which catches cron itself not firing.
- `test` sends a labeled test alert: `ops/docker/backup-monitored.sh test`.

Recipients and relay are managed in the app: every **enabled rule under
Settings > Notifications** (a backup alert is not a finding category, so rule
category/severity filters are not applied) and **Settings > Email (SMTP)**.
`BACKUP_ALERT_TO`, `BACKUP_ALERT_SMTP_HOST`, `BACKUP_ALERT_SMTP_PORT`,
`BACKUP_ALERT_SMTP_TLS` and `BACKUP_ALERT_FROM` in `.env` override them and are
the fallback if the database is unreachable. SMTP authentication is not
supported (internal relay assumed). A host that is completely down cannot alert
about itself; monitor it externally too.

## Notes

- The login cookie is `Secure` automatically when the request arrived over
  HTTPS (via Caddy's `X-Forwarded-Proto`), and stays non-`Secure` for plain
  HTTP deployments such as the lab.
- Client IPs: Caddy sets `X-Forwarded-For` (replacing any client-supplied value), the
  web tier passes the first address to the API, and the API honours it only from the
  private Docker network (`FORWARDED_ALLOW_IPS`). Sessions and audit events
  (`event_metadata.client_ip`, shown in Platform > Logging) record the real address.
- HSTS is intentionally off until a real certificate is in place.
- The API container runs migrations (`alembic upgrade head`) on every start.
