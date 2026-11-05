# Agent Rules

This file is the short, always-read rule set for this project. Read `PROJECT_CONSTITUTION.md` for non-negotiable engineering boundaries and `PROJECT_BRIEF.md` for current state. Use `项目交接说明.md` only as a searchable history archive.

## Interactive Password Prompts

When a command may require a password, passphrase, OTP, or hidden interactive input, do not run it in Codex's hidden shell.

Use a terminal visible to the user (Terminal on macOS) for:

- `sudo ...`
- SSH commands that may request a password or key passphrase
- NAS deployment commands that run Docker with unrestricted `sudo`
- any command likely to stop at `Password:` or similar

Exception: the NAS `deploy` user may use the project's root-owned, limited
passwordless commands after the NAS administrator has installed them:

```bash
sudo -n /usr/local/sbin/rebuild-bld-matcher rebuild
sudo -n /usr/local/sbin/rebuild-bld-matcher status
```

For quick updates the client invokes `sudo -n /usr/local/sbin/quick-bld-matcher`
with no arguments and supplies the full Git commit SHA on stdin. See
[`NAS_DEPLOY.md`](NAS_DEPLOY.md) for installation and permissions. Hidden shell
sessions may use only these limited commands. If a wrapper is missing or sudo
rejects it, report the error and use the documented setup in a visible terminal.

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

## 更新入口（所有 AI 客户端）

从当前仓库根目录操作。先确认用户要更新的是 Mac mini 本机 5055 还是公司群晖。拉取代码或刷新浏览器后，服务仍运行上次发布的版本。

| 目标与改动 | 操作 |
| --- | --- |
| Mac mini 本机，模板、静态文件或普通 Python 业务代码 | 检查当前工作树和变更片段后运行 `bash tools/docker_local.sh quick-update`，将这批改动作为一个版本发布。详见[本机手册](docs/operations/local-docker.md)。 |
| 群晖，同类改动 | 先在本机验证，再按 [NAS 部署手册](NAS_DEPLOY.md) 从有权限的电脑运行 `uv run python tools/nas_quick_update.py`。该命令会推送干净 `main` 上的已验证提交并发布；群晖只访问自己的 Git 仓库。 |
| 首次启用、依赖或镜像配置变化、数据库或图片迁移、快速入口提示需完整更新 | 本机使用 `bash tools/docker_local.sh update`；群晖按 [NAS 部署手册](NAS_DEPLOY.md) 走 Git 和受限 `rebuild` 流程。遵守长操作规则。 |

发布后核对入口返回的版本和业务探针，再报告成功。对未安装快速入口的目标先完成首次部署。不得通过复制源码、挂载整个工作区或传输运行数据绕过检查。

为快速更新如实填写 `changes/*.json`：纯代码改动可用 `impact.data: "none"`、`impact.api: "compatible"`、`impact.operations: "quick-update"`。涉及依赖、配置或迁移时按实际影响填写，走完整更新。

## Local 5055 Restart

The Mac mini local service is managed by Docker Desktop using the explicit
`compose.local.yml` configuration. Use the `bld-local` project / `bld-matcher-local`
container in Docker Desktop, or `bash tools/docker_local.sh start|stop|status|logs`.
The full `update` builds the image, stops the local service, backs up local
runtime data, applies migrations, and waits for readiness.

Keep `docker-compose.yml` as the NAS configuration. Local Docker commands must
use the `desktop-linux` context and `compose.local.yml`; never point them at NAS.

For an explicitly requested native fallback, first stop the local Docker
container, then use `bash tools/restart_local_5055.sh` or `/Applications/BLD.app`.
Do not run both on port 5055. Do not use an ad-hoc `Terminal do script` command
to launch `app.py`: it bypasses managed-window cleanup.

## Data Safety

Never overwrite NAS runtime data with local data unless the user explicitly asks for that exact operation.

Do not overwrite or delete:

- `data/products.sqlite3`
- `data/catalog.xlsx`
- `data/stamping_materials.xlsx`
- `data/drawings/`
- `data/customer_files/`
- `data/product_images/`
- `uploads/`
- `outputs/`
- `.env`

NAS product data and prices may be newer than local data. When syncing data, compare first and preserve NAS as the source of truth unless the user says otherwise.

## NAS Deployment

NAS updates must go through Git:

```text
verified client commit -> git push nas main -> NAS local Git repository -> quick update or full rebuild
```

Do not deploy by Finder, File Station, `scp`, `rsync`, zip upload, or manual overwrite of `/volume1/docker/bld-matcher`.

Full rebuild commands (perform the preflight in `NAS_DEPLOY.md` first):

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
