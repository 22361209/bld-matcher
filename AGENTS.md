# Agent Rules

This file is the short, always-read rule set for this project. For project shape and current state, read `PROJECT_BRIEF.md`. Use `项目交接说明.md` only as a searchable history archive.

## Interactive Password Prompts

When a command may require a password, passphrase, OTP, or hidden interactive input, do not run it in Codex's hidden shell.

Use a visible macOS Terminal window for:

- `sudo ...`
- SSH commands that may request a password or key passphrase
- NAS deployment commands that run Docker with `sudo`
- any command likely to stop at `Password:` or similar

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
sudo /usr/local/bin/docker-compose up -d --build
sudo /usr/local/bin/docker-compose ps
```

Use visible Terminal for the NAS `sudo` commands.

## Documentation

Keep `PROJECT_BRIEF.md` concise and current. Keep `项目交接说明.md` as detailed history; search it with `rg` or read small sections only.

### Mandatory update log

Every commit that changes tracked project files must add or update an entry under `项目交接说明.md` -> `## 当前最近重要变更`. This is a required completion condition, not an optional documentation follow-up.

- Update the log in the same commit as the code, UI, database, configuration, deployment, or documentation change.
- Use `### YYYY-MM-DD · commit · title`; use `本次提交` while preparing a new commit.
- Describe the user-visible behavior and any operational, permission, data, or compatibility impact.
- Before committing, confirm the staged diff includes `项目交接说明.md`. Do not commit or deploy when it is missing.
- This rule also applies to small fixes and follow-up changes so the web “系统更新” page never silently falls behind.

For important changes, update:

- `PROJECT_BRIEF.md` if current behavior, deployment, data ownership, or key workflow changed
- `项目交接说明.md` if the system updates page or detailed historical changelog should show the change
