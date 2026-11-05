#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONTAINER_NAME="bld-matcher-local"
ACTION="${1:-status}"
MANAGED_CONTAINER_EXISTS=false
MANAGED_CONTAINER_RUNNING=false

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    '用法：bash tools/docker_local.sh {start|stop|status|logs|quick-update|update}' \
    'quick-update：验证后让正在运行的 5055 加载代码快照，需先用完整更新启用。' \
    'start：启动已经部署的本机容器；不构建镜像。' \
    'update：完整检查、构建、停止本机服务、备份、迁移并启动新版。' \
    'Docker Desktop 中也可直接启动、停止或重启 bld-matcher-local。'
}

case "$ACTION" in
  start|stop|status|logs|quick-update|update) ;;
  help|-h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[[ "$#" -le 1 ]] || die '每次只接受一个操作。'

# CLI context takes precedence over ambient DOCKER_HOST/DOCKER_CONTEXT settings.
# Reject a changed desktop-linux context before any daemon operation.
docker_local() {
  docker --context desktop-linux "$@"
}

local_env_digest() {
  shasum -a 256 < "$PROJECT_DIR/.env" | cut -d ' ' -f 1
}

compose_local() {
  docker_local compose --project-name bld-local \
    --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/compose.local.yml" "$@"
}

command -v docker >/dev/null || die '未找到 Docker，请先安装并打开 Docker Desktop。'
endpoint="$(docker_local context inspect desktop-linux --format '{{.Endpoints.docker.Host}}')"
case "$endpoint" in
  unix://*) ;;
  *) die 'desktop-linux 没有指向本机 Unix socket，拒绝操作。请检查 Docker Desktop context。' ;;
esac
[[ "$(docker_local info --format '{{.OSType}}')" == linux ]] || \
  die '本机 Linux Docker 引擎不可用，请先打开 Docker Desktop。'

if metadata="$(docker_local inspect --type container \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{index .Config.Labels "com.docker.compose.project"}}|{{.State.Running}}' \
  "$CONTAINER_NAME" 2>/dev/null)"; then
  case "$metadata" in
    "$PROJECT_DIR|bld-local|true")
      MANAGED_CONTAINER_EXISTS=true
      MANAGED_CONTAINER_RUNNING=true
      ;;
    "$PROJECT_DIR|bld-local|false") MANAGED_CONTAINER_EXISTS=true ;;
    *) die "$CONTAINER_NAME 不属于当前工作区，拒绝操作。" ;;
  esac
fi

check_port() {
  command -v lsof >/dev/null || die '未找到 lsof，无法确认 5055 端口归属。'
  if lsof -nP -iTCP:5055 -sTCP:LISTEN >/dev/null 2>&1 && \
    [[ "$MANAGED_CONTAINER_RUNNING" != true ]]; then
    die '5055 端口已被其他服务占用。请先正常停止该服务；本入口不会终止其他进程。'
  fi
}

probe() {
  compose_local exec -T bld-matcher python -m scripts.runtime_probe \
    --base-url http://127.0.0.1:8000
  printf '%s\n' '本机服务已就绪：http://127.0.0.1:5055/'
}

cd "$PROJECT_DIR"
case "$ACTION" in
  start)
    [[ "$MANAGED_CONTAINER_EXISTS" == true ]] || \
      die '本机容器尚未部署，请先运行 bash tools/docker_local.sh update。'
    check_port
    compose_local start --wait --wait-timeout 180 bld-matcher
    probe
    ;;
  stop)
    compose_local stop bld-matcher
    ;;
  status)
    compose_local ps -a
    ;;
  logs)
    compose_local logs --tail=100 --follow bld-matcher
    ;;
  quick-update)
    [[ "$MANAGED_CONTAINER_RUNNING" == true ]] || die '请先启动已部署的本机容器。'
    compose_local exec -T bld-matcher test -f /app/.quick-baseline.json || \
      die '此镜像尚未启用快速更新，请先运行 bash tools/docker_local.sh update。'
    command -v uv >/dev/null || die '未找到 uv，无法运行项目检查。'
    release_temp="$(mktemp -d)"
    trap 'rm -rf "$release_temp"' EXIT
    # Hashes only: the build label covers additions, removals, and edits to .env.
    deployed_env="$(docker_local inspect --format '{{index .Config.Labels "bld.local.env-sha256"}}' "$CONTAINER_NAME")"
    [[ "$deployed_env" == "$(local_env_digest)" ]] || die '本机 .env 与部署时不同，请使用完整更新。'
    head_before="$(git rev-parse HEAD)"
    uv run python tools/quick_release.py pack --source "$PROJECT_DIR" --output "$release_temp/source.tar"
    uv run python scripts/verify.py
    uv run python tools/quick_release.py pack --source "$PROJECT_DIR" --output "$release_temp/after.tar"
    [[ "$(git rev-parse HEAD)" == "$head_before" ]] && cmp -s "$release_temp/source.tar" "$release_temp/after.tar" || \
      die '验证期间代码发生变化，请重新运行快速更新。'
    hash="$(shasum -a 256 < "$release_temp/source.tar")"
    revision="$head_before-${hash:0:12}"
    compose_local exec -T bld-matcher python /app/tools/quick_runtime.py apply "$revision" < "$release_temp/source.tar"
    ;;
  update)
    check_port
    command -v uv >/dev/null || die '未找到 uv，无法执行项目完整检查和运行数据备份。'
    printf '%s\n' '执行完整项目检查；通过后构建新镜像。'
    uv run python scripts/verify.py
    BLD_LOCAL_ENV_DIGEST="$(local_env_digest)"
    export BLD_LOCAL_ENV_DIGEST
    compose_local build bld-matcher
    printf '%s\n' '停止本机服务并备份运行数据；备份或迁移失败时保持停止。'
    compose_local stop bld-matcher
    uv run python tools/backup_local_runtime.py
    compose_local run --rm --no-deps --name bld-matcher-local-image-migrate \
      bld-matcher sh -c 'python -m scripts.init_database && python tools/migrate_product_images.py --apply'
    compose_local up -d --no-build --force-recreate --wait --wait-timeout 180 bld-matcher
    probe
    ;;
esac
