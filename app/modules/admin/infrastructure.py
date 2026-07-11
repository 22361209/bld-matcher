from __future__ import annotations

import json
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
        self.updates_doc_path = base_dir / "项目交接说明.md"
        self.fragments_dir = base_dir / "changes"

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
            updates.append(
                {
                    "date": str(payload.get("date") or ""),
                    "version": str(payload.get("version") or ""),
                    "title": str(payload.get("title") or "重要变更"),
                    "entries": [str(entry) for entry in entries],
                }
            )
        return updates

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
