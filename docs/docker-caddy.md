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

## Notes

- The login cookie is `Secure` automatically when the request arrived over
  HTTPS (via Caddy's `X-Forwarded-Proto`), and stays non-`Secure` for plain
  HTTP deployments such as the lab.
- HSTS is intentionally off until a real certificate is in place.
- The API container runs migrations (`alembic upgrade head`) on every start.
