from flask import abort, request, send_file

from app.security import actor_name, can, permission_required

from .document_factory import get_development_document_service
from .web_support import form_data, version, write_action


def _target():
    target = request.form.get("target", "")
    if target:
        parts = target.split("|", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""
    return request.form.get("target_type", ""), request.form.get("target_id", "")


def _upload_data():
    data = form_data(("document_id", "kind", "number", "title", "label", "change_note", "state"))
    data["target_type"], data["target_id"] = _target()
    return data


def _send_revision(project_id, revision_id, *, preview=False):
    try:
        payload = get_development_document_service().file_payload(project_id, revision_id)
    except ValueError:
        abort(404)
    if preview and not payload.previewable:
        abort(415)
    response = send_file(payload.path, mimetype=payload.content_type,
                         as_attachment=not preview, download_name=payload.download_name,
                         conditional=True, etag=payload.sha256, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if preview:
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
    return response


def register(app) -> None:
    @app.post("/development/<project_id>/documents/upload")
    @permission_required("edit_development")
    @permission_required("view_development_files")
    def development_upload_document(project_id):
        return write_action(project_id, lambda: get_development_document_service().upload(
            project_id,
            _upload_data(),
            request.files.get("file"), actor=actor_name(), expected_version=version()),
            success="文件修订已上传。已有采用版本保持不变，请按需手动升级。")

    @app.post("/development/<project_id>/documents/<revision_id>/adopt")
    @permission_required("edit_development")
    @permission_required("view_development_files")
    def development_adopt_document(project_id, revision_id):
        return write_action(project_id, lambda: get_development_document_service().adopt(
            project_id, revision_id, target_type=_target()[0],
            target_id=_target()[1], actor=actor_name(), expected_version=version()),
            success="已明确采用此修订。")

    @app.post("/development/<project_id>/documents/<revision_id>/state")
    @permission_required("edit_development")
    @permission_required("view_development_files")
    def development_document_state(project_id, revision_id):
        return write_action(project_id, lambda: get_development_document_service().set_state(
            project_id, revision_id, request.form.get("state", ""), change_note=request.form.get("change_note", ""),
            actor=actor_name(), expected_version=version()), success="已记录新的状态修订，历史与现有采用版本保留。")

    @app.post("/development/<project_id>/documents/<document_id>/archive")
    @permission_required("edit_development")
    @permission_required("view_development_files")
    def development_archive_document(project_id, document_id):
        return write_action(project_id, lambda: get_development_document_service().archive(
            project_id, document_id, actor=actor_name(), expected_version=version()),
            success="文档已归档，已采用的历史修订保留。")

    @app.post("/development/<project_id>/documents/snapshot")
    @permission_required("edit_development")
    @permission_required("view_development_files")
    def development_snapshot_document(project_id):
        permission = {"product": "view_product_drawings", "material": "view_material_drawings"}.get(request.form.get("source_kind", ""))
        if not permission or not can(permission):
            abort(403)
        return write_action(project_id, lambda: get_development_document_service().snapshot_legacy(
            project_id, _upload_data(), source_kind=request.form.get("source_kind", ""),
            relative_name=request.form.get("relative_name", ""), actor=actor_name(), expected_version=version()),
            success="旧图纸已保存为不可变快照，原目录文件以后更新不会替换本次修订。")

    @app.get("/development/<project_id>/documents/<revision_id>/download")
    @permission_required("view_development_files")
    def development_download_document(project_id, revision_id):
        return _send_revision(project_id, revision_id)

    @app.get("/development/<project_id>/documents/<revision_id>/preview")
    @permission_required("view_development_files")
    def development_preview_document(project_id, revision_id):
        return _send_revision(project_id, revision_id, preview=True)
