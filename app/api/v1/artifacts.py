from __future__ import annotations

import base64
from datetime import timedelta

from flask import Blueprint, send_file

from app.config import DB_PATH, OUTPUT_DIR
from app.platform.api_auth import api_scope_required, current_api_principal
from app.platform.api_errors import ApiError, register_api_error_handlers
from app.platform.artifacts import ArtifactNotFoundError, SQLiteArtifactStore
from app.platform.runtime_factory import get_runtime_settings
from app.platform.openapi import OpenApiOperation, register_openapi_operation


artifact_v1_api = Blueprint("artifact_v1_api", __name__)
register_api_error_handlers(artifact_v1_api)


def artifact_store() -> SQLiteArtifactStore:
    return SQLiteArtifactStore(
        DB_PATH,
        (OUTPUT_DIR,),
        default_ttl=timedelta(hours=get_runtime_settings().artifact_retention_hours),
    )


@artifact_v1_api.get("/api/v1/artifacts/<string:artifact_id>")
@api_scope_required("artifacts:read")
def download_artifact_v1(artifact_id: str):
    principal = current_api_principal()
    if principal is None:
        raise RuntimeError("Artifact route requires an API principal.")
    try:
        artifact = artifact_store().get(artifact_id, owner_id=principal.subject)
    except ArtifactNotFoundError as exc:
        raise ApiError("artifact.not_found", "结果文件不存在、已过期或不属于当前调用方。", 404) from exc
    response = send_file(
        artifact.storage_path,
        mimetype=artifact.content_type,
        as_attachment=True,
        download_name=artifact.filename,
    )
    digest = base64.b64encode(bytes.fromhex(artifact.sha256)).decode("ascii")
    response.headers["Digest"] = f"sha-256={digest}"
    response.headers["Cache-Control"] = "private, no-store"
    return response


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/artifacts/{artifact_id}",
        method="GET",
        operation_id="downloadArtifact",
        summary="Download an artifact owned by the API principal",
        scopes=("artifacts:read",),
        response_model=None,
        response_media_type="application/octet-stream",
        response_schema={"type": "string", "format": "binary"},
        path_parameters=(("artifact_id", "string"),),
        response_headers=(
            ("Content-Disposition", "string"),
            ("Digest", "string"),
            ("Cache-Control", "string"),
        ),
    )
)


def register(app) -> None:
    app.register_blueprint(artifact_v1_api)
