# Agent Rules

This file is the short, always-read rule set for this project. Read `PROJECT_CONSTITUTION.md` for non-negotiable engineering boundaries and `PROJECT_BRIEF.md` for current state. Use `项目交接说明.md` only as a searchable history archive.

## Interactive Password Prompts

When a command may require a password, passphrase, OTP, or hidden interactive input, do not run it in Codex's hidden shell.

Use a visible macOS Terminal window for:

- `sudo ...`
- SSH commands that may request a password or key passphrase
- NAS deployment commands that run Docker with unrestricted `sudo`
- any command likely to stop at `Password:` or similar

Exception: the NAS `deploy` user has a root-owned, limited passwordless sudo
wrapper for this project:

```bash
sudo -n /usr/local/sbin/rebuild-bld-matcher rebuild
sudo -n /usr/local/sbin/rebuild-bld-matcher status
```

Hidden shell sessions may use that wrapper because `sudo -n` fails immediately
instead of prompting for a password. Do not use hidden sessions for any other
NAS sudo command.

Preferred pattern:

```bash
osascript <<'APPLESCRIPT'
tell application "Terminal"
  activate
  do script "ssh -tt -i ~/.ssh/bld_matcher_deploy deploy@192.168.110.93 'cd /volume1/docker/bld-matcher && sudo /usr/local/bin/docker-compose up -d --build && sudo /usr/local/bin/docker-compose ps'"
end tell
APPLESCRIPT
```

Hidden shell sessions are fine for non-interactive checks such as `git status`, `git push`, `curl`, tests, and `ssh -o BatchMode=yes ...`.

## Long Operations

Before starting any operation expected to take more than 5 minutes, ask the user first. Briefly say:

- what will be done
- why it may take more than 5 minutes
- whether the project remains usable during the operation

## Data Safety

Never overwrite NAS runtime data with local data unless the user explicitly asks for that exact operation.

Do not overwrite or delete:

- `data/products.sqlite3`
- `data/catalog.xlsx`
- `data/stamping_materials.xlsx`
- `data/drawings/`
- `data/product_images/`
- `uploads/`
- `outputs/`
- `.env`

NAS product data and prices may be newer than local data. When syncing data, compare first and preserve NAS as the source of truth unless the user says otherwise.

## NAS Deployment

NAS updates must go through Git:

```text
local commit -> git push nas main -> NAS git fetch/reset -> docker-compose rebuild
```

Do not deploy by Finder, File Station, `scp`, `rsync`, zip upload, or manual overwrite of `/volume1/docker/bld-matcher`.

Standard commands:

```bash
git push nas main
ssh -i ~/.ssh/bld_matcher_deploy deploy@192.168.110.93
cd /volume1/docker/bld-matcher
git fetch origin main
git reset --hard origin/main
git status -sb
sudo -n /usr/local/sbin/rebuild-bld-matcher rebuild
sudo -n /usr/local/sbin/rebuild-bld-matcher status
```

Use visible Terminal for NAS `sudo` commands only when the limited wrapper is
missing or insufficient.

## Engineering Contract

- Keep the project a modular monolith. New routes must not access SQLite directly or import another route module.
- New full pages must follow `docs/ui/page-protocol.md`; do not add standalone document shells or inline scripts.
- AI and automation must use authenticated application APIs, never direct runtime database access.
- Architecture, framework, database, queue, external AI provider, or breaking API changes require an ADR under `docs/adr/`.
- Existing exceptions are frozen in `policy/legacy_allowlist.json`. Do not add new entries merely to make a check pass.
- Before commit or deployment, run `uv run python scripts/verify.py`. Required checks must pass.

Full rules live in `PROJECT_CONSTITUTION.md`. The machine-enforced subset lives in `scripts/check_project_contract.py`.

## Documentation

Keep `PROJECT_BRIEF.md` concise and current. Keep `项目交接说明.md` as detailed history; search it with `rg` or read small sections only.

### Mandatory change fragment

Every user-visible, data, permission, API, configuration, or operational change must add or update one `changes/*.json` fragment. This is a required completion condition, not an optional documentation follow-up.

- Follow `changes/README.md` and describe user-visible behavior plus data, API, and operational impact.
- The system updates page reads change fragments first and keeps `项目交接说明.md` only for historical compatibility.
- `scripts/check_project_contract.py` enforces the fragment requirement locally and in CI.

For important changes, update:

- `PROJECT_BRIEF.md` if current behavior, deployment, data ownership, or key workflow changed
- the relevant architecture, page, API, or ADR document when its contract changed
