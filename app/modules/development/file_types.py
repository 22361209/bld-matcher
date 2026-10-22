from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PreparedDevelopmentFile:
    revision_id: str
    original_name: str
    extension: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_path: str
    temporary_path: Path = field(repr=False)
    destination_path: Path = field(repr=False)
    created: bool = False
    created_identity: tuple[int, int] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class DevelopmentFilePayload:
    path: Path = field(repr=False)
    download_name: str
    content_type: str
    size_bytes: int
    sha256: str
    previewable: bool
