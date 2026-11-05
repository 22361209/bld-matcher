"""Match original scrap reference rows to current stamping materials without guessing part numbers."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import unicodedata

from openpyxl import load_workbook
from openpyxl.styles.colors import COLOR_INDEX

from app.modules.materials.weights import costing_material, validated_net_weight

from .pair_codes import code_side, mirror_code, mirror_part_code
from .values import CostingValidationError, number


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value) if value is not None else "")
    return " ".join(text.split()).upper()


def material_match_key(series: object, name: object) -> tuple[str, str]:
    return _normalized(series), _normalized(name)


def extract_estimated_rows(path: Path) -> set[int]:
    """The original workbook explicitly marks estimates with solid yellow cell fills."""
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        if "片件重量" not in book.sheetnames:
            raise ValueError("成本工作簿缺少「片件重量」工作表。")
        yellow = set()
        for index, (cell,) in enumerate(book["片件重量"].iter_rows(min_row=2, min_col=5, max_col=5), 2):
            if not cell.fill or cell.fill.patternType != "solid":
                continue
            color = cell.fill.fgColor
            rgb = None
            if color.type == "rgb":
                rgb = color.rgb
            elif color.type == "indexed" and 0 <= color.indexed < len(COLOR_INDEX):
                rgb = COLOR_INDEX[color.indexed]
            if rgb and rgb.upper().endswith("FFFF00"):
                yellow.add(index)
        return yellow
    finally:
        book.close()


def material_code_evidence(records: list[dict]) -> dict[tuple[str, str], set[str]]:
    """Only intact original stamping rows with a matching sheet-level scrap reference can identify a part."""
    evidence: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        source = record.get("source_info") or {}
        refs = source.get("scrap_reference_rows") or []
        if record.get("kind") != "arm" or source.get("sheet") != "摆臂成本核算" or not refs:
            continue
        ref_keys = {material_match_key(ref.get("code"), ref.get("name")) for ref in refs
                    if (ref.get("source_info") or {}).get("sheet") == "片件重量"}
        models = {model for model, _ in ref_keys}
        if len(models) != 1:
            continue
        model, = models
        original_codes = {_normalized(record.get("code"))}
        if record.get("source") == "copy" and record.get("pair"):
            original_codes.add(mirror_code(_normalized(record.get("code"))))
        row_codes: dict[tuple[str, str], list[set[str]]] = defaultdict(list)
        for item in record.get("items", []):
            info = item.get("source_info") or {}
            raw = info.get("raw") or {}
            key = material_match_key(model, item.get("name"))
            if (item.get("mode") != "weight" or info.get("sheet") != "摆臂成本核算" or not raw.get("A")
                    or _normalized(raw["A"]) not in original_codes or key not in ref_keys
                    or key[1] != _normalized(raw.get("E"))):
                continue
            original_part = raw.get("G")
            if isinstance(original_part, str) and original_part.startswith("="):
                original_part = (info.get("cached") or {}).get("G")
            codes = {_normalized(value) for value in (original_part, item.get("part_code"), item.get("product_code"))}
            row_codes[key].append(codes - {""})
        for key, parts in row_codes.items():
            explicit = set().union(*parts)
            if explicit:
                if len(parts) > 1 and not all(parts):
                    # A second homonymous part has no identifier. The first cannot safely disambiguate the group.
                    explicit.add("")
                evidence[key].update(explicit)
    return dict(evidence)


def _source_confirms_side(code: str, key: tuple[str, str], explicit_code: bool) -> bool:
    mirror = mirror_part_code(code)
    if not mirror:
        return True
    side = next(char for char, opposite in zip(code, mirror) if char != opposite)
    series, name = key
    named_side = re.search(r"(?<![A-Z0-9])([LR])(?:件)?$", name)
    sides = {mark for mark in (code_side(series), named_side[1] if named_side else "",
                               "L" if "左" in name else "", "R" if "右" in name else "") if mark}
    return sides == {side} if sides else explicit_code


def _match_row(key: tuple[str, str], ref: dict, material: dict, explicit_code: bool) -> tuple[dict | None, str]:
    source = ref.get("source_info") or {}
    raw_net = (source.get("raw") or {}).get("E")
    if ref.get("estimated") or (isinstance(raw_net, str) and raw_net.lstrip().startswith("=")):
        return None, "原表净重为估算或公式值"
    try:
        net = number(ref.get("net_weight"), "净重")
        gross = number(ref.get("gross_weight"), "原表毛重")
        if net is None or net <= 0 or gross is None or net > gross:
            return None, "原表毛重、净重缺失或不合理"
        if raw_net is not None and number(raw_net, "原表净重") != net:
            return None, "原表净重与参考值有差异"
    except CostingValidationError:
        return None, "原表毛重或净重无效"
    if type(material.get("id")) is not int or material["id"] <= 0 or not _normalized(material.get("code")):
        return None, "材料缺少零件编码或有效标识"
    if not _source_confirms_side(_normalized(material["code"]), key, explicit_code):
        return None, "原表未明确区分该零件的左右侧"
    candidate = costing_material(material)
    if candidate is None:
        return None, "材料下料重、已填净重或同步标识无效"
    try:
        normalized = validated_net_weight(material, net)
    except ValueError as exc:
        return None, str(exc)
    if candidate["net_weight"] is not None and candidate["net_weight"] != normalized:
        return None, "材料已有不同的净重"
    planned = costing_material({**material, "net_weight": normalized})
    assert planned is not None  # The same material and proposed net weight were just validated.
    return {"id": material["id"], **planned}, ""


def plan_materials(
    refs: list[dict], materials: list[dict], code_evidence: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[dict[int, str], dict[tuple[str, str], dict], list[dict]]:
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in materials:
        if row.get("active") == 1:
            by_key[material_match_key(row.get("model"), row.get("part"))].append(row)
    references: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ref in refs:
        references[material_match_key(ref.get("code"), ref.get("name"))].append(ref)

    updates: dict[int, str] = {}
    matches: dict[tuple[str, str], dict] = {}
    reviews = []
    for key, source_rows in references.items():
        candidates = by_key[key]
        codes = (code_evidence or {}).get(key) or set()
        eligible = [row for row in candidates if not codes or _normalized(row.get("code")) in codes]
        reason = ""
        if not all(key):
            reason = "原表缺少母件编码或零件名称"
        elif len(source_rows) != 1:
            reason = "原表同型号、同名称有多条记录"
        elif not candidates:
            reason = "没有相同母件编码和零件名称的启用材料"
        elif len(codes) > 1:
            reason = "同名成本行或左右件的零件编码存在歧义"
        elif not eligible:
            reason = "成本行明示编码与同型号、同名称材料编码不符"
        elif len(eligible) != 1:
            reason = "同一母件编码和零件名称匹配多条启用材料"
        else:
            material, = eligible
            matched, reason = _match_row(key, source_rows[0], material, bool(codes))
            if matched is not None:
                matches[key] = matched
                if material.get("net_weight") in (None, ""):
                    updates[material["id"]] = matched["net_weight"]
                continue
        for ref in source_rows:
            reviews.append({
                "code": key[0], "part": key[1], "source_row": (ref.get("source_info") or {}).get("row"),
                "reason": reason, "codes": [row.get("code") for row in candidates],
            })
    return updates, matches, reviews
