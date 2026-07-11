from __future__ import annotations

import hashlib
import mimetypes
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.database import connect
from app.platform.clock import now_text


ARTIFACT_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: str
    owner_id: str
    filename: str
    storage_path: Path
    content_type: str
    size_bytes: int
    sha256: str
    created_at: str
    expires_at: str

    def api_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "expires_at": self.expires_at,
            "download_url": f"/api/v1/artifacts/{self.id}",
        }


class ArtifactNotFoundError(LookupError):
    pass


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SQLiteArtifactStore:
    def __init__(
        self,
        database_path: Path,
        allowed_roots: tuple[Path, ...],
        *,
        default_ttl: timedelta = ARTIFACT_TTL,
    ) -> None:
        self.database_path = database_path
        self.allowed_roots = tuple(root.resolve() for root in allowed_roots)
        self.default_ttl = default_ttl

    def _checked_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError("Artifact source is not a file.")
        if not any(root == resolved or root in resolved.parents for root in self.allowed_roots):
            raise ValueError("Artifact source is outside an approved output directory.")
        return resolved

    def register(
        self,
        path: Path,
        *,
        owner_id: str,
        filename: str | None = None,
        content_type: str | None = None,
        ttl: timedelta | None = None,
    ) -> ArtifactRecord:
        resolved = self._checked_path(path)
        artifact_id = f"art_{secrets.token_urlsafe(18)}"
        created = datetime.now()
        expires = created + (ttl or self.default_ttl)
        safe_filename = Path(filename or resolved.name).name
        media_type = content_type or mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
        size = resolved.stat().st_size
        checksum = _sha256(resolved)
        with connect(self.database_path) as conn:
            conn.execute(
                """
                INSERT INTO api_artifacts
                  (id, owner_id, filename, storage_path, content_type, size_bytes, sha256,
                   created_at, expires_at, last_downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')
                """,
                (
                    artifact_id,
                    owner_id,
                    safe_filename,
                    str(resolved),
                    media_type,
                    size,
                    checksum,
                    _timestamp(created),
                    _timestamp(expires),
                ),
            )
            conn.commit()
        return ArtifactRecord(
            id=artifact_id,
            owner_id=owner_id,
            filename=safe_filename,
            storage_path=resolved,
            content_type=media_type,
            size_bytes=size,
            sha256=checksum,
            created_at=_timestamp(created),
            expires_at=_timestamp(expires),
        )

    def get(self, artifact_id: str, *, owner_id: str) -> ArtifactRecord:
        with connect(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT id, owner_id, filename, storage_path, content_type, size_bytes, sha256,
                       created_at, expires_at
                FROM api_artifacts
                WHERE id = ? AND owner_id = ? AND expires_at > ?
                """,
                (artifact_id, owner_id, now_text()),
            ).fetchone()
            if row is None:
                raise ArtifactNotFoundError("Artifact not found.")
            try:
                path = self._checked_path(Path(str(row["storage_path"])))
            except ValueError as exc:
                raise ArtifactNotFoundError("Artifact not found.") from exc
            conn.execute(
                "UPDATE api_artifacts SET last_downloaded_at = ? WHERE id = ?",
                (now_text(), artifact_id),
            )
            conn.commit()
        return ArtifactRecord(
            id=str(row["id"]),
            owner_id=str(row["owner_id"]),
            filename=str(row["filename"]),
            storage_path=path,
            content_type=str(row["content_type"] or "application/octet-stream"),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
        )

    def purge_expired(self, *, now: str | None = None) -> int:
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                "DELETE FROM api_artifacts WHERE expires_at <= ?",
                (now or now_text(),),
            )
            conn.commit()
            return int(cursor.rowcount)
