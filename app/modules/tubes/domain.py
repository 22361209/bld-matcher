from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


TUBE_TYPES = ("普通管", "单法兰管", "双法兰管", "双喇叭管", "拉杆轴", "其他管件")


def normalize_spec_search(value: object) -> str:
    return clean_text(value).replace(" ", "").replace("*", "×").replace("x", "×").replace("X", "×")


def spec_display_lines(value: object) -> tuple[str, ...]:
    spec_text = clean_text(value)
    match = re.match(r"^(.*?)(?:（法兰后）|（喇叭后）)(.*)$", spec_text)
    if match:
        return (match.group(1), match.group(2).strip())
    return (spec_text,)


def tolerance_only(value: object) -> str:
    match = re.search(r"(?:±|[+-])\s*\d.*$", clean_text(value))
    tolerance = match.group(0) if match else clean_text(value)
    if "/" not in tolerance:
        return tolerance
    sign = tolerance[0] if tolerance[:1] in {"±", "+", "-"} else ""
    return "\n".join(
        part if part[:1] in {"±", "+", "-"} or not sign else f"{sign}{part}"
        for part in (part.strip() for part in tolerance.split("/"))
        if part
    )


def clean_text(value: object) -> str:
    return str(value or "").strip()


def number_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_code(value: object) -> str:
    return re.sub(r"[（(].*?[）)]", "", clean_text(value)).strip()


def classify_tube(*, name: object, form_type: object, spec_text: object) -> str:
    source = " ".join((clean_text(name), clean_text(form_type), clean_text(spec_text)))
    if "拉杆轴" in source:
        return "拉杆轴"
    if re.search(r"单边(?:法兰|喇叭)", source):
        return "单法兰管"
    if re.search(r"双边喇叭|双喇叭", source):
        return "双喇叭管"
    if re.search(r"双边法兰|双法兰", source):
        return "双法兰管"
    if "管" in source:
        return "普通管"
    return "其他管件"


def borrowed_source(raw_code: object, note: object) -> str:
    raw = clean_text(raw_code)
    text = clean_text(note)
    matched = re.search(r"借用\s*([A-Za-z]*\d+)", text)
    if not matched:
        return ""
    target = matched.group(1).upper()
    if re.match(r"^[A-Z]+", target):
        return target
    prefix = re.match(r"^([A-Za-z]+)", raw)
    return f"{prefix.group(1).upper() if prefix else ''}{target}"


def calculate_weight(
    outer_diameter_mm: object,
    inner_diameter_mm: object,
    length_m: object,
    tolerance_m: object,
    consumption_m: object,
    quantity: object,
) -> float | None:
    outer = number_or_none(outer_diameter_mm)
    inner = number_or_none(inner_diameter_mm)
    length = number_or_none(length_m)
    tolerance = number_or_none(tolerance_m)
    consumption = number_or_none(consumption_m)
    count = number_or_none(quantity)
    if (
        outer is None
        or inner is None
        or length is None
        or tolerance is None
        or consumption is None
        or count is None
        or outer <= inner
        or count <= 0
    ):
        return None
    return ((outer * outer - inner * inner) / 4) * 0.02466 * (length + tolerance + consumption) * count


@dataclass(frozen=True, slots=True)
class TubeImportRow:
    code: str
    tube_type: str
    spec_text: str
    weight_kg: float | None
    tolerance_mm: float | None
    consumption_mm: float | None
    outer_diameter_mm: float | None
    inner_diameter_mm: float | None
    blank_length_text: str
    inner_diameter_tolerance: str
    purchase_base: int
    borrowed_from: str
    note: str
    source_sheet: str
    source_row: int


def row_from_2026(values: Sequence[Any], row_number: int) -> TubeImportRow | None:
    if len(values) < 19:
        return None
    raw_code = values[3]
    code = normalize_code(raw_code)
    if not code:
        return None
    spec_text = clean_text(values[4]) or clean_text(values[5])
    note = clean_text(values[11])
    tolerance_m = number_or_none(values[15])
    consumption_m = number_or_none(values[16])
    weight = number_or_none(values[18])
    if weight is None:
        weight = calculate_weight(values[12], values[13], values[14], tolerance_m, consumption_m, values[17])
    return TubeImportRow(
        code=code,
        tube_type=classify_tube(name=values[9], form_type=values[10], spec_text=spec_text),
        spec_text=spec_text,
        weight_kg=weight,
        tolerance_mm=tolerance_m * 1000 if tolerance_m is not None else None,
        consumption_mm=consumption_m * 1000 if consumption_m is not None else None,
        outer_diameter_mm=number_or_none(values[12]),
        inner_diameter_mm=number_or_none(values[13]),
        blank_length_text=clean_text(values[8]),
        inner_diameter_tolerance=clean_text(values[6]),
        purchase_base=_purchase_base(note),
        borrowed_from=borrowed_source(raw_code, note),
        note=note,
        source_sheet="2026",
        source_row=row_number,
    )


def _purchase_base(note: str) -> int:
    match = re.search(r"(?:一(?:个|只)?|1)\s*产品\s*(?:用)?\s*(\d+)\s*个", note)
    return int(match.group(1)) if match else 1
