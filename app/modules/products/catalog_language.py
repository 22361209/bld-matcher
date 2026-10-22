from __future__ import annotations

from typing import Mapping


# Reviewed against the 22 distinct existing item names. Ambiguous names remain empty.
ITEM_TRANSLATIONS = {
    "Front Left Lower Ball Joint": "前左下球头",
    "Front Left Lower Control Arm": "前左下控制臂",
    "Front Left Upper Control Arm": "前左上控制臂",
    "Front Lower Control Arm": "前下控制臂",
    "Front Right Lower Ball Joint": "前右下球头",
    "Front Right Lower Control Arm": "前右下控制臂",
    "Front Right Upper Control Arm": "前右上控制臂",
    "Front Upper Control Arm": "前上控制臂",
    "Rear Arm": "后悬架臂",
    "Rear Left Arm": "后左悬架臂",
    "Rear Left Lower Control Arm": "后左下控制臂",
    "Rear Left Trailing Arm": "后左纵臂",
    "Rear Left Upper Control Arm": "后左上控制臂",
    "Rear Lower Control Arm": "后下控制臂",
    "Rear Right Arm": "后右悬架臂",
    "Rear Right Lower Control Arm": "后右下控制臂",
    "Rear Right Trailing Arm": "后右纵臂",
    "Rear Right Upper Control Arm": "后右上控制臂",
    "Tie Rod End": "转向拉杆端",
}
PENDING_ITEM_TRANSLATIONS = (
    "Central Link",
    "Front Lower Rear Left with Ball Joint",
    "Front Lower Rear Right with Ball Joint",
)
CATALOG_IMPORT_HEADERS = {
    "item_zh": "中文产品名称", "models_zh": "中文车型",
    "catalog_visible_en": "英文目录可见", "catalog_visible_zh": "中文目录可见",
}
CATALOG_FIELDS = ("item_zh", "models_zh", "catalog_visible_en", "catalog_visible_zh")


def translated_item(value: object) -> str:
    return ITEM_TRANSLATIONS.get(str(value or "").strip(), "")


def catalog_values(data: Mapping[str, object], before: Mapping[str, object]) -> dict[str, object]:
    """Missing legacy fields preserve existing values; explicit blanks stay blank."""
    values: dict[str, object] = {}
    for field in ("item_zh", "models_zh"):
        default = before.get(field, translated_item(data.get("item") or data.get("ITEM")) if field == "item_zh" else "")
        raw = data.get(field, default)
        values[field] = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for field in ("catalog_visible_en", "catalog_visible_zh"):
        raw = data.get(field, before.get(field, 1))
        if isinstance(raw, bool):
            values[field] = int(raw)
        elif str(raw).strip() in {"0", "1"}:
            values[field] = int(str(raw).strip())
        else:
            raise ValueError("目录可见设置必须为 0 或 1。")
    return values
