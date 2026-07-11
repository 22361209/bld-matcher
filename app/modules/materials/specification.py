from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.matcher import compact_text


def _parse_required_float(value: object, label: str) -> float:
    text = compact_text(value)
    if not text:
        raise ValueError(f"{label}不能为空。")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{label}必须是数字：{text}") from exc


def _format_material_int(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _format_material_thickness(value: float) -> str:
    text = f"{value:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _format_material_spec(thickness: float, width: float, length: float) -> str:
    return f"{_format_material_thickness(thickness)}×{_format_material_int(width)}×{_format_material_int(length)}"


def _parse_material_spec_text(value: object) -> tuple[float, float, float]:
    text = compact_text(value)
    if not text:
        raise ValueError("规格尺寸不能为空。")
    normalized = re.sub(r"[×xX*＊/／\\\-－—]+", " ", text)
    parts = re.sub(r"\s+", " ", normalized).strip().split(" ")
    if len(parts) != 3:
        raise ValueError("规格尺寸请按“厚度 宽度 长度”填写，例如 2.5 357 1260。")
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError as exc:
        raise ValueError(f"规格尺寸必须包含 3 个数字：{text}") from exc


def _parse_material_spec_query(value: object) -> list[float]:
    text = compact_text(value)
    if not text:
        return []
    normalized = re.sub(r"[×xX*＊/／\\\-－—]+", " ", text)
    parts = re.sub(r"\s+", " ", normalized).strip().split(" ")
    if not 1 <= len(parts) <= 3:
        return []
    try:
        return [float(part) for part in parts]
    except ValueError:
        return []


def _material_values_from_data(
    data: Mapping[str, object],
    *,
    source: str = "web",
    source_row: int = 0,
    require_detail_fields: bool = True,
) -> dict[str, Any]:
    model = compact_text(data.get("model"))
    if not model:
        raise ValueError("母件编码不能为空。")
    code = compact_text(data.get("code"))
    if require_detail_fields and not code:
        raise ValueError("零件编码不能为空。")
    part = compact_text(data.get("part"))
    if require_detail_fields and not part:
        raise ValueError("零件名称不能为空。")
    pieces = _parse_required_float(data.get("pieces"), "下料只数")
    if compact_text(data.get("spec_text")):
        thickness, width, length = _parse_material_spec_text(data.get("spec_text"))
    else:
        thickness = _parse_required_float(data.get("thickness"), "规格1")
        width = _parse_required_float(data.get("width"), "规格2")
        length = _parse_required_float(data.get("length"), "规格3")
    if pieces <= 0:
        raise ValueError("下料只数必须大于 0。")
    return {
        "model": model,
        "code": code,
        "category": compact_text(data.get("category")),
        "car": compact_text(data.get("car")),
        "part": part,
        "spec_text": _format_material_spec(thickness, width, length),
        "pieces": pieces,
        "thickness": thickness,
        "width": width,
        "length": length,
        "active": 1 if str(data.get("active", "1")) != "0" else 0,
        "source": source,
        "source_row": int(source_row or 0),
    }
