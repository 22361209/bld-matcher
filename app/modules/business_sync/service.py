from __future__ import annotations

from pathlib import Path

from .infrastructure import BusinessSyncRepository


class BusinessSyncService:
    def __init__(self, repository: BusinessSyncRepository) -> None:
        self.repository = repository

    def export(self, *, output_path: Path, selected: tuple[str, ...], actor: str) -> Path:
        return self.repository.export(output_path=output_path, selected=selected, actor=actor)

    def preview(self, package_path: Path) -> dict[str, object]:
        return self.repository.preview(package_path)

    def apply(self, package_path: Path, *, backup_path: Path, actor: str, expected_token: str) -> dict[str, dict[str, int]]:
        return self.repository.apply(package_path, backup_path=backup_path, actor=actor, expected_token=expected_token)
