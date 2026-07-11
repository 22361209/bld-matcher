from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from app.locks import ImportLockError

from .sync_domain import ProductSyncResult
from .sync_infrastructure import MediaChange, ProductMediaSynchronizer, ProductPackageStore
from .sync_repository import SQLiteProductSyncRepository


logger = logging.getLogger(__name__)
LockFactory = Callable[[str, str], AbstractContextManager]


class ProductSyncApplyError(RuntimeError):
    def __init__(self, *, media_restored: bool) -> None:
        super().__init__("Product data package apply failed.")
        self.media_restored = media_restored


class ProductSyncService:
    def __init__(
        self,
        repository: SQLiteProductSyncRepository,
        packages: ProductPackageStore,
        media: ProductMediaSynchronizer,
        lock_factory: LockFactory,
        *,
        database_name: str,
    ) -> None:
        self.repository = repository
        self.packages = packages
        self.media = media
        self.lock_factory = lock_factory
        self.database_name = database_name

    def export(
        self,
        *,
        output_dir: Path,
        file_label: str,
        include_drawings: bool,
        include_images: bool,
        actor: str,
    ) -> Path:
        output_path = output_dir / f"product-data-{file_label}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.tar.gz"
        path = self.packages.export(
            output_path,
            include_drawings=include_drawings,
            include_images=include_images,
        )
        try:
            self.repository.audit(
                "导出产品数据包",
                path.name,
                f"包含图纸：{'是' if include_drawings else '否'}；包含图片：{'是' if include_images else '否'}",
                actor=actor,
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def preview(
        self,
        package_path: Path,
        *,
        package_name: str,
        include_drawings: bool,
        include_images: bool,
    ) -> dict[str, object]:
        with self.packages.prepare(package_path) as prepared:
            return {
                "package_path": str(package_path),
                "package_name": package_name,
                "manifest": prepared.manifest,
                "diff": self.repository.diff(prepared.product_database),
                "include_drawings": include_drawings,
                "include_images": include_images,
                "media_counts": {
                    "drawings": self.packages.media_count(prepared, "drawings"),
                    "product_images": self.packages.media_count(prepared, "product_images"),
                },
            }

    def apply(
        self,
        package_path: Path,
        *,
        backup_dir: Path,
        include_drawings: bool,
        include_images: bool,
        deactivate_local_only: bool,
        actor: str,
    ) -> tuple[ProductSyncResult, bool]:
        media_changes: list[MediaChange] = []
        database_applied = False
        rolled_back_media = False
        try:
            with self.lock_factory(actor, "产品数据包导入"):
                backup_dir.mkdir(parents=True, exist_ok=True)
                self.repository.backup(backup_dir / self.database_name)
                with self.packages.prepare(package_path) as prepared:
                    copied_drawings = (
                        self.media.copy(prepared, key="drawings", backup_dir=backup_dir, changes=media_changes)
                        if include_drawings
                        else 0
                    )
                    copied_images = (
                        self.media.copy(prepared, key="product_images", backup_dir=backup_dir, changes=media_changes)
                        if include_images
                        else 0
                    )
                    result = self.repository.apply(
                        prepared.product_database,
                        deactivate_local_only=deactivate_local_only,
                        actor=actor,
                    )
                    database_applied = True
                    result = replace(result, copied_drawings=copied_drawings, copied_images=copied_images)
                    try:
                        self.repository.audit(
                            "应用产品数据包媒体",
                            package_path.name,
                            f"复制图纸 {copied_drawings} 个；复制图片 {copied_images} 个；已创建一致性备份",
                            actor=actor,
                        )
                    except Exception:
                        logger.exception("Product data was applied but the media audit could not be recorded")
                    return result, rolled_back_media
        except ImportLockError:
            raise
        except Exception as exc:
            if media_changes and not database_applied:
                self.media.restore(media_changes)
                rolled_back_media = True
            raise ProductSyncApplyError(media_restored=rolled_back_media) from exc
