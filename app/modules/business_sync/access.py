from pathlib import Path

from app.security import can

from .infrastructure import DATASETS, BusinessSyncRepository


def development_access(*, write: bool = False) -> bool:
    return can("view_development") and can("view_development_files") and (not write or can("edit_development"))


def costing_access(*, write: bool = False) -> bool:
    return can("view_costs") and (not write or can("manage_costs"))


def permitted_datasets(*, write: bool = False) -> dict:
    checks = {"development": development_access, "costing": costing_access}
    return {key: value for key, value in DATASETS.items() if key not in checks or checks[key](write=write)}


def can_access_package(path: Path, *, write: bool = False) -> bool:
    return can("sync_product_data") and set(BusinessSyncRepository.package_datasets(path)).issubset(permitted_datasets(write=write))
