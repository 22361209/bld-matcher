from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook


CATALOG_HEADER_ALIASES = {
    "BLD NO.": {"BLD NO.", "BLD NO", "BLDNO", "BOLAIDE NO", "PART NO"},
    "OE NO.1": {"OE NO.1", "OE NO1", "OE1", "OE NO.", "OE NO"},
    "OE NO.2": {"OE NO.2", "OE NO2", "OE2"},
    "SERIES": {"SERIES", "BRAND"},
    "ITEM": {"ITEM", "DESCRIPTION", "PRODUCT"},
    "Models": {"MODELS", "MODEL", "APPLICATION"},
}

LOOKALIKE_TRANSLATION = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "У": "Y",
        "Х": "X",
        "а": "A",
        "в": "B",
        "е": "E",
        "к": "K",
        "м": "M",
        "н": "H",
        "о": "O",
        "р": "P",
        "с": "C",
        "т": "T",
        "у": "Y",
        "х": "X",
    }
)


@dataclass(frozen=True)
class CatalogMatch:
    bld_no: str
    score: int
    reason: str
    row: dict
    matched_codes: tuple[str, ...] = ()
    unmatched_codes: tuple[str, ...] = ()


def normalize_code(value: object) -> str:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    text = ("" if value is None else str(value)).translate(LOOKALIKE_TRANSLATION)
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def zero_o_key(value: object) -> str:
    return normalize_code(value).replace("O", "0")


def split_codes(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[\n\r\t,;/，；、]+", text)
    return [part.strip() for part in parts if normalize_code(part)]


def compact_text(value: object) -> str:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def _canonical_header(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", "" if value is None else str(value).upper())


def _find_header_row(rows: list[tuple], max_scan: int = 20) -> tuple[int, list[str]]:
    alias_lookup = {
        _canonical_header(alias): canonical
        for canonical, aliases in CATALOG_HEADER_ALIASES.items()
        for alias in aliases
    }

    best_index = -1
    best_headers: list[str] = []
    best_score = 0
    for index, row in enumerate(rows[:max_scan]):
        headers: list[str] = []
        score = 0
        for cell in row:
            canonical = alias_lookup.get(_canonical_header(cell), compact_text(cell))
            headers.append(canonical)
            if canonical in CATALOG_HEADER_ALIASES:
                score += 1
        if score > best_score:
            best_index = index
            best_headers = headers
            best_score = score

    if best_score < 3:
        raise ValueError("没有在产品目录里找到可识别的表头，需要包含 BLD NO. 和 OE NO. 等字段。")
    return best_index, best_headers


class ProductCatalog:
    def __init__(self, rows: list[dict], manual_map: dict[str, str] | None = None):
        self.rows = rows
        self.manual_map = manual_map or {}
        self.by_bld: dict[str, dict] = {}
        self.by_oe: dict[str, list[dict]] = {}
        self.by_oe_zero_o: dict[str, list[dict]] = {}

        for row in rows:
            bld_no = compact_text(row.get("BLD NO."))
            if bld_no:
                self.by_bld[normalize_code(bld_no)] = row
            for field in ("OE NO.1", "OE NO.2"):
                for code in split_codes(row.get(field)):
                    self.by_oe.setdefault(normalize_code(code), []).append(row)
                    self.by_oe_zero_o.setdefault(zero_o_key(code), []).append(row)

    @classmethod
    def from_excel(cls, path: Path, manual_map: dict[str, str] | None = None) -> "ProductCatalog":
        workbook = load_workbook(path, read_only=True, data_only=True)
        worksheet = workbook.active
        raw_rows = list(worksheet.iter_rows(values_only=True))
        header_index, headers = _find_header_row(raw_rows)

        rows: list[dict] = []
        for raw in raw_rows[header_index + 1 :]:
            row = {}
            for header, value in zip(headers, raw):
                if header:
                    row[header] = value
            if compact_text(row.get("BLD NO.")):
                rows.append(row)
        return cls(rows, manual_map=manual_map)

    def match(self, inquiry_name: object, inquiry_oe: object, inquiry_desc: object = "") -> CatalogMatch | None:
        oe_key = normalize_code(inquiry_oe)
        name_key = normalize_code(inquiry_name)
        oe_parts = split_codes(inquiry_oe)

        manual_bld = self.manual_map.get(oe_key) or self.manual_map.get(name_key)
        if manual_bld:
            row = self.by_bld.get(normalize_code(manual_bld))
            if row:
                matched_codes = tuple(oe_parts) if len(oe_parts) > 1 else ((compact_text(inquiry_oe),) if compact_text(inquiry_oe) else ())
                return CatalogMatch(compact_text(row.get("BLD NO.")), 100, "人工确认映射", row, matched_codes=matched_codes)

        split_matches = self._match_split_oe_parts(oe_parts)
        if split_matches:
            return split_matches

        if oe_key and oe_key in self.by_oe:
            row = self.by_oe[oe_key][0]
            return CatalogMatch(compact_text(row.get("BLD NO.")), 95, "OE 精准命中", row, matched_codes=((compact_text(inquiry_oe),) if compact_text(inquiry_oe) else ()))

        oe_zero_o_key = zero_o_key(inquiry_oe)
        if oe_zero_o_key and oe_zero_o_key != oe_key and oe_zero_o_key in self.by_oe_zero_o:
            rows = self._unique_rows(self.by_oe_zero_o[oe_zero_o_key])
            if len(rows) == 1:
                row = rows[0]
                return CatalogMatch(compact_text(row.get("BLD NO.")), 88, "OE 字符容错命中", row, matched_codes=((compact_text(inquiry_oe),) if compact_text(inquiry_oe) else ()))

        if name_key and name_key in self.by_bld:
            row = self.by_bld[name_key]
            return CatalogMatch(compact_text(row.get("BLD NO.")), 92, "BLD NO. 精准命中", row)

        return None

    def _match_split_oe_parts(self, oe_parts: list[str]) -> CatalogMatch | None:
        if len(oe_parts) <= 1:
            return None

        matches: list[tuple[str, CatalogMatch]] = []
        for part in oe_parts:
            part_key = normalize_code(part)
            manual_bld = self.manual_map.get(part_key)
            if manual_bld:
                row = self.by_bld.get(normalize_code(manual_bld))
                if row:
                    matches.append((part, CatalogMatch(compact_text(row.get("BLD NO.")), 100, "OE 多号码人工确认映射", row)))
                    continue

            if part_key in self.by_oe:
                row = self.by_oe[part_key][0]
                matches.append((part, CatalogMatch(compact_text(row.get("BLD NO.")), 95, "OE 多号码精准命中", row)))
                continue

            part_zero_o_key = zero_o_key(part)
            if part_zero_o_key and part_zero_o_key != part_key and part_zero_o_key in self.by_oe_zero_o:
                rows = self._unique_rows(self.by_oe_zero_o[part_zero_o_key])
                if len(rows) == 1:
                    row = rows[0]
                    matches.append((part, CatalogMatch(compact_text(row.get("BLD NO.")), 88, "OE 多号码字符容错命中", row)))

        unique: dict[str, CatalogMatch] = {}
        matched_parts = tuple(part for part, _ in matches)
        matched_keys = {normalize_code(part) for part in matched_parts}
        unmatched_parts = tuple(part for part in oe_parts if normalize_code(part) not in matched_keys)
        for _, match in matches:
            unique.setdefault(match.bld_no, match)

        if not unique:
            return None
        if len(unique) == 1:
            match = next(iter(unique.values()))
            return CatalogMatch(match.bld_no, match.score, match.reason, match.row, matched_codes=matched_parts, unmatched_codes=unmatched_parts)

        bld_list = " / ".join(unique)
        first = next(iter(unique.values()))
        return CatalogMatch(bld_list, 80, "多个 OE 命中不同 BLD，请人工确认", first.row, matched_codes=matched_parts, unmatched_codes=unmatched_parts)

    @staticmethod
    def _unique_rows(rows: list[dict]) -> list[dict]:
        seen = set()
        unique = []
        for row in rows:
            key = compact_text(row.get("BLD NO."))
            if key and key not in seen:
                seen.add(key)
                unique.append(row)
        return unique


def load_manual_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {normalize_code(key): compact_text(value) for key, value in data.items() if compact_text(value)}


def save_manual_map(path: Path, mapping: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {normalize_code(key): compact_text(value) for key, value in mapping.items() if normalize_code(key) and compact_text(value)}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(clean, handle, ensure_ascii=False, indent=2, sort_keys=True)


def catalog_summary(catalog: ProductCatalog) -> dict[str, int]:
    return {
        "products": len(catalog.rows),
        "oe_keys": len(catalog.by_oe),
        "bld_keys": len(catalog.by_bld),
        "manual_mappings": len(catalog.manual_map),
    }
