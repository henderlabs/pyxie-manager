# Demo stack and screenshots

A throwaway PyXie on **fictional data** (4 nodes, 18 guests, 3 affinity rules, 45 days of invented usage history),
used to regenerate the README and website screenshots each release. Public images must always be real captures
of the real app, never mockups.

```bash
ops/demo/demo.sh up       # build from this checkout, start the stack, seed the data (~3 min)
ops/demo/demo.sh shoot    # capture the pages in pages.json into ops/demo/out/
ops/demo/demo.sh down     # remove everything it created
```

Needs Docker, `openssl`, `python3`; `shoot` also needs Node 22+ and Chrome (set `CHROME=` if it is not at the
usual path). The app is served on `127.0.0.1:13000` (`DEMO_PORT` to change); to capture from another machine, tunnel
that port. The admin login is `admin@example.com` with a password generated into `ops/demo/.demo/` (git-ignored,
removed by `down`).

## What is in here

| File | Purpose |
| --- | --- |
| `demo.sh` | brings the stack up and down, runs the capture |
| `seed_demo.py` | the fictional dataset, run inside the api container; refuses to run if the database already has a PVE target |
| `fake_pve.py` | answers the few read-only PVE calls the planner makes (VM config, version), so Balance Load can produce a real plan on a cluster that does not exist |
| `shoot.mjs` | headless Chrome capture; a page can click a button first (`run`) or capture one region (`clip`) |
| `pages.json` | which pages to capture |

## Rules

- Run it on a machine that is **not** a PyXie production host. It starts no worker and PVE writes are off
  (`PVE_MUTATIONS_ENABLED=false`); it never reads a real `.env`.
- The usage numbers, findings and recommendations on the captured pages are computed by PyXie's own code from the
  seeded metric history; only the inputs are invented. Do not retouch the images.
- Everything in `seed_demo.py` is invented: no real hostnames, addresses, VM names or accounts. Keep it that way.
- After a release changes the UI, regenerate, review every image, then replace `docs/screenshots/` and the website images.
