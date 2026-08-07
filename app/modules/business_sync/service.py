from __future__ import annotations

from pathlib import Path

from .infrastructure import BusinessSyncRepository


class BusinessSyncService:
    def __init__(self, repository: BusinessSyncRepository) -> None:
        self.repository = repository

    def export(
        self,
        *,
        output_path: Path,
        selected: tuple[str, ...],
        actor: str,
        include_drawings: bool = False,
        include_images: bool = False,
        include_material_drawings: bool = False,
    ) -> Path:
        return self.repository.export(
            output_path=output_path,
            selected=selected,
            include_drawings=include_drawings,
            include_images=include_images,
            include_material_drawings=include_material_drawings,
            actor=actor,
        )

    def preview(self, package_path: Path) -> dict[str, object]:
        return self.repository.preview(package_path)

    def apply(
        self,
        package_path: Path,
        *,
        backup_path: Path,
        actor: str,
        expected_token: str,
        selected_conflicts: dict[str, set[str]] | None = None,
        customer_mappings: dict[str, str | None] | None = None,
        include_drawings: bool = False,
        include_images: bool = False,
        include_material_drawings: bool = False,
        deactivate_local_only: bool = False,
    ) -> dict[str, dict[str, int]]:
        return self.repository.apply(
            package_path,
            backup_path=backup_path,
            actor=actor,
            expected_token=expected_token,
            selected_conflicts=selected_conflicts or {},
            customer_mappings=customer_mappings,
            include_drawings=include_drawings,
            include_images=include_images,
            include_material_drawings=include_material_drawings,
            deactivate_local_only=deactivate_local_only,
        )
