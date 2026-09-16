# Installing PyXie Manager

Native install, no Docker, one Linux VM. This covers a fresh deployment end
to end. Steps marked **[scripted]** are handled by `ops/install/`; steps
marked **[manual]** genuinely can't be scripted (they depend on your
network, or are an interactive/credential decision only a human should
make) and are the ones most worth double-checking if something doesn't
come up right.

## Prerequisites

- A Linux VM: Ubuntu 24.04 LTS, tested. 4+ vCPU / 8GB+ RAM / 50GB+ disk is
  comfortable for a small deployment; scale up for a larger PVE
  cluster/workload count -- PyXie's own resource use scales with the
  number of nodes+workloads it's watching, not cluster complexity. See
  the sizing table in this repo's commit history (or ask -- there's no
  fixed formula yet, this is based on real production data from two
  deployments so far).
- Network reachability to your PVE cluster's API (port 8006) from this VM.
- Outbound HTTPS (443) to GitHub, at minimum. Outbound SSH (port 22, or
  even SSH tunneled over 443) may be blocked by your network's security
  policy -- some corporate SASE/proxy setups intercept the SSH protocol
  specifically, regardless of which port it's riding on, even though a
  raw TCP connect to that port looks like it succeeds. If `git clone` over
  SSH hangs with no output at all (not even a host-key prompt), that's
  almost certainly what's happening -- switch to the HTTPS path below
  rather than debugging the SSH path further.

## 1. Get GitHub access to this repo **[manual -- network-dependent]**

This repo is private. Pick ONE of these, based on what your network
actually allows (test both if you're not sure):

**Option A -- SSH deploy key** (works if outbound SSH isn't intercepted):
1. `ssh-keygen -t ed25519 -f ~/.ssh/pyxie_deploy_key -N ""` on the VM.
2. Add the `.pub` as a **read-only** Deploy Key on the GitHub repo
   (Settings -> Deploy keys -> Add deploy key). Read-only is enough unless
   you'll be committing FROM this box too (see below).
3. Add an SSH config entry pointing `github.com` at that identity file.

**Option B -- HTTPS + fine-grained PAT** (works when SSH is blocked, which
is common on corporate networks with deep packet inspection):
1. On github.com: Settings -> Developer settings -> Fine-grained tokens ->
   Generate new token.
2. Repository access: "Only select repositories" -> this one repo, only.
3. Permissions -> Repository permissions -> **Contents**: `Read-only` if
   this box only ever deploys, or `Read and write` if you'll also be
   developing/committing from it. Metadata: Read-only is auto-required,
   leave it.
4. Store it in a git credential helper on the VM, NOT embedded in the
   remote URL where it'd sit in plaintext `.git/config`:
   ```bash
   git config --global credential.helper store
   echo "https://<your-github-username>:<TOKEN>@github.com" > ~/.git-credentials
   chmod 600 ~/.git-credentials
   ```
5. Set a commit identity too, if this box will commit:
   `git config --global user.name` / `user.email`.

## 2. Clone the repo **[manual, one command]**

```bash
git clone https://github.com/henderlabs/pyxie-manager.git ~/pyxie-manager
```
(or the `git@github.com:...` form if you used Option A.)

## 3. Privileged install **[scripted]**

Review `ops/install/00-privileged.sh` first -- it installs OS packages
(Node 20 via NodeSource, since Ubuntu 24.04's own repo only has 18.x;
PostgreSQL 16 and Redis are already current in the default repos), creates
the `pyxie_manager` Postgres role + database with a freshly generated
password, writes the three systemd unit files, and adds a narrow NOPASSWD
sudo rule scoped to just `restart`/`status`/`is-active` on those three
units (so routine deploys and restarts don't need a password every time,
without granting broad sudo).

```bash
sudo bash ~/pyxie-manager/ops/install/00-privileged.sh
```

Defaults to the invoking `sudo` user's own account as the service account
and app directory (`~<user>/pyxie-manager`). Override with
`PYXIE_USER=someuser PYXIE_HOME=/home/someuser sudo -E bash ...` if you
want a different service account than whoever ran `git clone`.

## 4. App deploy **[scripted]**

Run as the **same non-root user** from step 3, from inside the cloned repo:

```bash
cd ~/pyxie-manager && bash ops/install/01-app-deploy.sh
```

Creates the Python venvs for `api`/`worker`, installs `web`'s dependencies
and builds it, generates `.env` (reads the Postgres password step 3 wrote
to `~/.pyxie-install-secrets`, generates a fresh `PYXIE_CREDENTIAL_KEY`,
auto-detects the VM's timezone, defaults `PVE_MUTATIONS_ENABLED=false`),
runs every database migration, and starts all three services for the
first time.

**Idempotent for re-runs**: if `.env` already exists it's left untouched,
so re-running this script after a `git pull` (to pick up a new commit) is
safe and just re-syncs dependencies/migrations/the running services.

## 5. Set `PYXIE_SELF_VMID` **[manual -- can't be known until the VM exists in PVE]**

`.env` is written with `PYXIE_SELF_VMID` commented out. This is the VMID
this VM itself has inside the PVE cluster it will manage -- it's what lets
`pve_write_client.py`'s self-protection guard refuse to ever shut down or
force-stop the box PyXie is running on, even as part of an otherwise
legitimate maintenance plan covering every guest on a host. Find the VMID
in the PVE web UI (or `qm list` on the hypervisor), uncomment and set it
in `.env`, then `sudo systemctl restart pyxie-api`.

**Don't skip this** if PyXie will ever run a maintenance/evacuation plan
against the same cluster it's hosted on -- this is the actual fix for a
real near-incident (see README.md's Safety Contract section).

## 6. Bootstrap the administrator account **[manual, deliberately -- see below]**

Visit `http://<this-vm>:3000/login`. A fresh install with zero users shows
"Create the administrator account" instead of a login form. This is
intentionally never scripted or API-automated by anyone other than the
person who'll actually hold that login -- create it yourself, in your own
browser, with your own password.

## 7. Add your PVE cluster

See [`docs/adding-a-host.md`](adding-a-host.md) -- the UI-driven walkthrough
for onboarding a site, PVE target, and credentials, and running the first
discovery.

## Troubleshooting notes from real installs

- **`alembic upgrade head` fails with a `ForeignKeyViolation` on
  `capabilities.category_id`**: this was a real bug in migration `0023`
  (fixed as of the commit that added this doc) -- it assumed
  `provider_categories` was already seeded by the app's own startup code,
  which is only true if the app has run at least once before this
  migration executes. A genuinely fresh `alembic upgrade head` (this
  script's own step 5) hits that ordering problem head-on. If you're on a
  version of this repo from before the fix, either update, or manually
  seed just the `protection` category before re-running (see the fixed
  migration file for the exact idempotent INSERT).
- **`git clone`/`git pull` over SSH hangs with no output**: see the
  Prerequisites note above -- switch to the HTTPS+PAT path.
- **`sudo: a password is required` on a `systemctl restart` you expected
  to be passwordless**: the NOPASSWD rule only matches the *exact* command
  line from step 3 (single unit, no extra flags) -- `sudo systemctl status
  pyxie-api --no-pager` won't match even though `sudo systemctl status
  pyxie-api` does.
