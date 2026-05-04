#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/tools/start_local_5055.applescript"
ICON="$PROJECT_DIR/tools/BLD.icns"
APP_PATH="/Applications/BLD.app"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Missing launcher template: $TEMPLATE" >&2
  exit 1
fi

if [[ ! -f "$ICON" ]]; then
  echo "Missing launcher icon: $ICON" >&2
  exit 1
fi

TMP_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/bld-launcher.XXXXXX.applescript")"
trap 'rm -f "$TMP_SCRIPT"' EXIT

python3 - "$TEMPLATE" "$TMP_SCRIPT" "$PROJECT_DIR" <<'PY'
from pathlib import Path
import sys

template, output, project_dir = map(Path, sys.argv[1:4])
text = template.read_text(encoding="utf-8")
text = text.replace("__PROJECT_PATH__", str(project_dir))
output.write_text(text, encoding="utf-8")
PY

osacompile -o "$APP_PATH" "$TMP_SCRIPT"

set_plist_string() {
  local key="$1"
  local value="$2"

  if /usr/libexec/PlistBuddy -c "Print :$key" "$APP_PATH/Contents/Info.plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :$key $value" "$APP_PATH/Contents/Info.plist" >/dev/null
  else
    /usr/libexec/PlistBuddy -c "Add :$key string $value" "$APP_PATH/Contents/Info.plist" >/dev/null
  fi
}

set_plist_string "CFBundleName" "BLD"
set_plist_string "CFBundleDisplayName" "BLD"
set_plist_string "CFBundleIdentifier" "com.linzhenyue.bld.local-launcher"
set_plist_string "CFBundleIconFile" "BLD"
set_plist_string "CFBundleIconName" "BLD"

cp "$ICON" "$APP_PATH/Contents/Resources/BLD.icns"
cp "$ICON" "$APP_PATH/Contents/Resources/applet.icns"
touch "$APP_PATH"

python3 - <<'PY'
from pathlib import Path
import plistlib

dock_plist = Path.home() / "Library/Preferences/com.apple.dock.plist"
app_url = "file:///Applications/BLD.app/"

if dock_plist.exists():
    with dock_plist.open("rb") as f:
        data = plistlib.load(f)
else:
    data = {}

apps = data.setdefault("persistent-apps", [])
apps[:] = [
    item for item in apps
    if item.get("tile-data", {}).get("file-data", {}).get("_CFURLString") != app_url
]
apps.append({
    "tile-data": {
        "file-data": {
            "_CFURLString": app_url,
            "_CFURLStringType": 15,
        },
        "file-label": "BLD",
        "file-type": 41,
    },
    "tile-type": "file-tile",
})

with dock_plist.open("wb") as f:
    plistlib.dump(data, f)
PY

qlmanage -r cache >/dev/null 2>&1 || true
killall Dock >/dev/null 2>&1 || true

echo "Installed $APP_PATH and added BLD to the Dock."
