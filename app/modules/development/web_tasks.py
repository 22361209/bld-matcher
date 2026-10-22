from flask import request

from app.security import actor_name, permission_required

from .factory import get_development_service
from .web_support import OPERATION_FIELDS, form_data, task_data, version, write_action


def register(app) -> None:
    @app.post("/development/<project_id>/tasks/<task_id>/update")
    @permission_required("edit_development")
    def development_update_task(project_id, task_id):
        return write_action(project_id, lambda: get_development_service().update_task(
            project_id, task_id, task_data(), expected_version=version(), actor=actor_name()),
            success="开发任务已保存，引用它的共用组件同步更新。")

    @app.post("/development/<project_id>/operations/add")
    @permission_required("edit_development")
    def development_add_operation(project_id):
        return write_action(project_id, lambda: get_development_service().add_operation(
            project_id, request.form.get("task_id", ""), form_data(OPERATION_FIELDS), expected_version=version(), actor=actor_name()))

    @app.post("/development/<project_id>/operations/<operation_id>/update")
    @permission_required("edit_development")
    def development_update_operation(project_id, operation_id):
        return write_action(project_id, lambda: get_development_service().update_operation(
            project_id, operation_id, form_data(OPERATION_FIELDS), expected_version=version(), actor=actor_name()))

    @app.post("/development/<project_id>/suppliers/save")
    @permission_required("edit_development")
    def development_save_supplier(project_id):
        return write_action(project_id, lambda: get_development_service().save_supplier(
            form_data(("name", "contact", "phone", "notes")), supplier_id=request.form.get("supplier_id") or None,
            actor=actor_name()), success="供应商已保存，可在任务中选用。")
