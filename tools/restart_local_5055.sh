#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/tools/start_local_5055.applescript"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Missing launcher template: $TEMPLATE" >&2
  exit 1
fi

TMP_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/bld-start-local-5055.XXXXXX")"
trap 'rm -f "$TMP_SCRIPT"' EXIT

python3 - "$TEMPLATE" "$TMP_SCRIPT" "$PROJECT_DIR" <<'PY'
from pathlib import Path
import sys

template, output, project_dir = map(Path, sys.argv[1:4])
text = template.read_text(encoding="utf-8")
output.write_text(text.replace("__PROJECT_PATH__", str(project_dir)), encoding="utf-8")
PY

exec osascript "$TMP_SCRIPT"
