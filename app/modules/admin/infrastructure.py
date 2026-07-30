from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _parse_update_heading(heading: str) -> dict[str, str]:
    parts = [part.strip() for part in heading.split("·")]
    if len(parts) >= 3:
        return {"date": parts[0], "version": parts[1], "title": " · ".join(parts[2:])}
    if len(parts) == 2:
        return {"date": parts[0], "version": parts[1], "title": "重要变更"}
    return {"date": heading.strip(), "version": "", "title": "重要变更"}


class FileSystemUpdateReader:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.updates_doc_path = base_dir / "项目交接说明.md"
        self.fragments_dir = base_dir / "changes"
        self.deployment_version_path = base_dir / ".deployment-version"
        self._current_version_cache: str | None = None

    @property
    def source_name(self) -> str:
        return f"changes/*.json + {self.updates_doc_path.name}"

    def read(self) -> list[dict[str, object]]:
        updates = [*self._read_fragments(), *self._read_archive()]
        unique: list[dict[str, object]] = []
        seen: set[tuple[object, object]] = set()
        for item in updates:
            key = (item["date"], item["title"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _read_fragments(self) -> list[dict[str, object]]:
        updates = []
        current_version = self._current_version()
        if not self.fragments_dir.is_dir():
            return updates
        for path in sorted(self.fragments_dir.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries = payload.get("entries")
            if not isinstance(entries, list) or not entries:
                continue
            version = str(payload.get("version") or "")
            updates.append(
                {
                    "date": str(payload.get("date") or ""),
                    "version": current_version if version == "unreleased" else version,
                    "title": str(payload.get("title") or "重要变更"),
                    "entries": [str(entry) for entry in entries],
                }
            )
        return updates

    def _current_version(self) -> str:
        if self._current_version_cache is None:
            self._current_version_cache = self._resolve_current_version()
        return self._current_version_cache

    def _resolve_current_version(self) -> str:
        configured_version = os.environ.get("BLD_APP_VERSION", "").strip()
        if configured_version:
            return f"当前版本 {configured_version}"

        try:
            deployed_version = self.deployment_version_path.read_text(encoding="utf-8").strip()
        except OSError:
            deployed_version = ""
        if deployed_version and deployed_version != "unknown":
            return f"当前版本 {deployed_version}"

        try:
            result = subprocess.run(
                ["git", "-C", str(self.base_dir), "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return "待发布"
        revision = result.stdout.strip()
        return f"当前版本 {revision}" if revision else "待发布"

    def _read_archive(self) -> list[dict[str, object]]:
        if not self.updates_doc_path.exists():
            return []
        updates: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        in_section = False
        for raw_line in self.updates_doc_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line == "## 当前最近重要变更":
                in_section = True
                continue
            if not in_section:
                continue
            if line.startswith("## "):
                break
            if line.startswith("### "):
                current = {**_parse_update_heading(line.removeprefix("### ").strip()), "entries": []}
                updates.append(current)
                continue
            if line.startswith("- ") and current is not None:
                entries = current["entries"]
                if isinstance(entries, list):
                    entries.append(line.removeprefix("- ").strip())
        return [item for item in updates if item["entries"]]
