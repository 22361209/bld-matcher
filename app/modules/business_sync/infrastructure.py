from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path
from typing import IO

from ._comparison import media_summary, normalized_incoming
from ._database_apply import apply_package, resolve_quote_customers
from ._media_transaction import (
    atomic_copy,
    atomic_copy_stream,
    copy_requested_media,
    restore_media,
)
from ._package_archive import add_media_directory, export_package, read_package
from ._preview import preview_package
from ._schema import DATASETS, PACKAGE_SUFFIX


__all__ = ("DATASETS", "PACKAGE_SUFFIX", "BusinessSyncRepository")


class BusinessSyncRepository:
    """Compatibility facade for the decomposed business-sync infrastructure."""

    def __init__(
        self,
        database_path: Path,
        *,
        drawing_dir: Path | None = None,
        image_dir: Path | None = None,
        material_drawing_dir: Path | None = None,
    ) -> None:
        self.database_path = database_path
        self.media_dirs = {
            "drawings": drawing_dir,
            "product_images": image_dir,
            "material_drawings": material_drawing_dir,
        }

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
        return export_package(
            self.database_path,
            output_path=output_path,
            selected=selected,
            actor=actor,
            include_drawings=include_drawings,
            include_images=include_images,
            include_material_drawings=include_material_drawings,
            add_media_directory_fn=self._add_media_directory,
            read_package_fn=self.read,
        )

    def _add_media_directory(self, archive: tarfile.TarFile, key: str) -> int:
        return add_media_directory(archive, key, self.media_dirs)

    @staticmethod
    def read(package_path: Path) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
        return read_package(package_path)

    def preview(self, package_path: Path) -> dict[str, object]:
        return preview_package(
            self.database_path,
            package_path,
            read_package_fn=self.read,
            normalized_incoming_fn=self._normalized_incoming,
            media_summary_fn=self._media_summary,
        )

    @staticmethod
    def _normalized_incoming(key: str, incoming: object) -> dict[str, object]:
        return normalized_incoming(key, incoming)

    @staticmethod
    def _media_summary(manifest: dict[str, object]) -> dict[str, object]:
        return media_summary(manifest)

    @staticmethod
    def _resolve_quote_customers(
        connection: sqlite3.Connection,
        payload: dict[str, list[dict[str, object]]],
        mappings: dict[str, str | None],
    ) -> None:
        resolve_quote_customers(connection, payload, mappings)

    def apply(
        self,
        package_path: Path,
        *,
        backup_path: Path,
        actor: str,
        expected_token: str,
        selected_conflicts: dict[str, set[str]],
        customer_mappings: dict[str, str | None] | None = None,
        include_drawings: bool = False,
        include_images: bool = False,
        include_material_drawings: bool = False,
        deactivate_local_only: bool = False,
    ) -> dict[str, dict[str, int]]:
        return apply_package(
            self.database_path,
            package_path,
            backup_path=backup_path,
            actor=actor,
            expected_token=expected_token,
            selected_conflicts=selected_conflicts,
            customer_mappings=customer_mappings or {},
            include_drawings=include_drawings,
            include_images=include_images,
            include_material_drawings=include_material_drawings,
            deactivate_local_only=deactivate_local_only,
            read_package_fn=self.read,
            normalized_incoming_fn=self._normalized_incoming,
            media_copy_fn=self._copy_requested_media,
            media_restore_fn=self._restore_media,
            resolve_quote_customers_fn=self._resolve_quote_customers,
        )

    def _copy_requested_media(
        self,
        package_path: Path,
        manifest: dict[str, object],
        requests: dict[str, bool],
        backup_root: Path,
        changes: list[tuple[Path, Path | None]],
    ) -> None:
        copy_requested_media(
            package_path,
            manifest,
            requests,
            backup_root,
            changes,
            media_dirs=self.media_dirs,
            media_summary=self._media_summary,
            atomic_copy_fn=self._atomic_copy,
            atomic_copy_stream_fn=self._atomic_copy_stream,
        )

    @staticmethod
    def _atomic_copy(source: Path, target: Path) -> None:
        atomic_copy(source, target)

    @staticmethod
    def _atomic_copy_stream(source: IO[bytes], target: Path) -> None:
        atomic_copy_stream(source, target)

    def _restore_media(self, changes: list[tuple[Path, Path | None]]) -> None:
        restore_media(changes, atomic_copy_fn=self._atomic_copy)
