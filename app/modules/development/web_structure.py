from app.security import actor_name, can, permission_required
from flask import abort, request

from .factory import get_development_service
from .web_support import BRANCH_FIELDS, COMPONENT_FIELDS, PROJECT_FIELDS, detail_url, form_data, task_data, version, write_action


def register(app) -> None:
    @app.post("/development/<project_id>/update")
    @permission_required("edit_development")
    def development_update(project_id):
        return write_action(project_id, lambda: get_development_service().update_project(
            project_id, form_data(PROJECT_FIELDS), expected_version=version(), actor=actor_name()))

    @app.post("/development/<project_id>/branches/add")
    @permission_required("edit_development")
    def development_add_branch(project_id):
        return write_action(project_id, lambda: get_development_service().add_branch(
            project_id, form_data(BRANCH_FIELDS), expected_version=version(), actor=actor_name()))

    @app.post("/development/<project_id>/branches/<branch_id>/update")
    @permission_required("edit_development")
    def development_update_branch(project_id, branch_id):
        data = form_data(BRANCH_FIELDS)
        data["production_ready"] = request.form.get("production_ready") == "1"
        return write_action(project_id, lambda: get_development_service().update_branch(
            project_id, branch_id, data, expected_version=version(), actor=actor_name()))

    @app.post("/development/<project_id>/branches/<branch_id>/copy")
    @permission_required("edit_development")
    def development_copy_branch(project_id, branch_id):
        if request.form.get("copy_drawings") == "1" and not can("view_development_files"):
            abort(403)
        modes = {key.removeprefix("mode_"): value for key, value in request.form.items() if key.startswith("mode_")}
        data = form_data(BRANCH_FIELDS)
        component_codes = {}
        for key, value in request.form.items():
            for prefix, field in (("code_", "code"), ("borrowed_code_", "borrowed_code")):
                if key.startswith(prefix):
                    component_codes.setdefault(key.removeprefix(prefix), {})[field] = value.strip()
        data["component_codes"] = component_codes
        return write_action(project_id, lambda: get_development_service().copy_branch(
            project_id, branch_id, data, modes=modes,
            copy_drawings=request.form.get("copy_drawings") == "1", expected_version=version(), actor=actor_name()),
            success="另一侧结构与核对编码已一起保存；独立任务从未开始登记，共用任务同步更新。",
            destination=lambda result: detail_url(project_id, view="components", branch=result["copied_branch_id"], component=""))

    @app.post("/development/<project_id>/branches/<branch_id>/confirm")
    @permission_required("edit_development")
    def development_confirm_branch(project_id, branch_id):
        return write_action(project_id, lambda: get_development_service().confirm_branch(
            project_id, branch_id, expected_version=version(), actor=actor_name()), success="负责人已确认成品完成。")

    @app.post("/development/<project_id>/components/add")
    @permission_required("edit_development")
    def development_add_component(project_id):
        data = form_data(COMPONENT_FIELDS)
        data["task"] = task_data(prefix="task_")
        data["task"]["name"] = data["task"]["name"] or data["name"]
        return write_action(project_id, lambda: get_development_service().add_component(
            project_id, request.form.get("branch", ""), data, expected_version=version(), actor=actor_name()),
            success="组件已登记。")

    @app.post("/development/<project_id>/components/<component_id>/update")
    @permission_required("edit_development")
    def development_update_component(project_id, component_id):
        return write_action(project_id, lambda: get_development_service().update_component(
            project_id, component_id, form_data(COMPONENT_FIELDS), expected_version=version(), actor=actor_name()))

    @app.post("/development/<project_id>/archive")
    @permission_required("edit_development")
    def development_archive(project_id):
        return write_action(project_id, lambda: get_development_service().archive_project(
            project_id, expected_version=version(), actor=actor_name()), success="开发单已归档，引用与历史继续保留。")
