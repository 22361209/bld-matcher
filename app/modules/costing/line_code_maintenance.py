"""Plan the same field-level updates for preview and for a locked application."""
from __future__ import annotations

import hashlib
import json

from .line_identity import inspect_line_identity
from .material_attributes import suggested_material_attribute
from .ports import CostingRepository


def _identity(item: dict) -> dict:
    return {key: item.get(key, "") for key in ("category", "name", "product_code", "material_attribute")}


def plan_line_codes(repository: CostingRepository) -> tuple[dict, list[tuple[dict, dict]]]:
    updates: list[tuple[dict, dict]] = []
    samples: list[dict] = []
    reviews: list[dict] = []
    attributes = {"外购": 0, "自制": 0, "委外加工": 0}
    changed_items = cartons = codes = 0
    digest = hashlib.sha256()
    for record in repository.list(q="", kind="", status="", sort="code", limit=-1, offset=0):
        items = list(record["items"])
        changes: list = []
        resolved = set()
        for index, item in enumerate(items, start=1):
            patch, reason = inspect_line_identity(item)
            if reason:
                reviews.append({"code": record["code"], "record_id": record["id"], "index": index,
                                **_identity(item), "reason": reason})
            if patch:
                if patch.get("name", item["name"]) == "纸箱" and patch.get("category", item["category"]) == "外包装":
                    cartons += 1
                else:
                    codes += 1
            if "material_attribute" not in item:
                attribute = suggested_material_attribute({**item, **patch})
                attributes[attribute] += 1
                patch["material_attribute"] = attribute
            if not patch:
                continue
            revised = {**item, **patch}
            if not item.get("name") and revised["name"] and "请填写成本项名称" in item.get("issues", []):
                revised["issues"] = [issue for issue in item["issues"] if issue != "请填写成本项名称"]
                resolved.add(f"第 {index} 项：请填写成本项名称")
            items[index - 1] = revised
            changes.append([item["id"], patch])
            changed_items += 1
            if len(samples) < 30:
                samples.append({"code": record["code"], "index": index,
                                "before": _identity(item), "after": _identity(revised)})
        if changes:
            after = {**record, "items": items}
            if resolved:
                after["issues"] = [issue for issue in record["issues"] if issue not in resolved]
                after["status"] = "draft" if after["issues"] else "ready"
            updates.append((record, after))
            fingerprint = [record["id"], record["version"], changes]
            digest.update(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    result = {"records": len(updates), "items": changed_items, "cartons": cartons, "codes": codes, "attributes": attributes,
              "samples": samples, "reviews": reviews, "digest": digest.hexdigest()}
    return result, updates
