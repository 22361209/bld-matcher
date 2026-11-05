"""Choices and initial values supplied to the cost sheet editor."""

from .quote_roles import QUOTE_ROLES
from .material_attributes import suggested_material_attribute


_TEMPLATE_LINES = {
    "arm": (
        ("主板", "焊接组件"), ("冲压工费", "焊接组件"), ("焊接管", "焊接组件"), ("焊接工费", "焊接组件"),
        ("电泳", "表面处理"), ("衬套", "衬套"), ("安装工费", "安装"), ("包装", "包装"), ("运费", "运费"),
    ),
    "ball": (("球销", "球头"), ("球座", "球头"), ("防尘罩", "球头"), ("封板", "球头"), ("安装工费", "安装")),
}


def editor_choices(references: dict) -> dict:
    choices = {}
    for group in ("cartons", "rates"):
        entries = references.get(group)
        choices[group] = []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identity = {"name": "纸箱", "category": "外包装"} if group == "cartons" else entry
            choices[group].append({**entry, "material_attribute": suggested_material_attribute(identity)})
    templates = {}
    for kind, lines in _TEMPLATE_LINES.items():
        items = [{"name": name, "category": category} for name, category in lines]
        for item in items:
            item["material_attribute"] = suggested_material_attribute(item)
        templates[kind] = items
    return {**choices, "templates": templates, "quote_roles": QUOTE_ROLES}
