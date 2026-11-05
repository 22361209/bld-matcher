"""Input rules for product development sheets and their earlier paired-model format."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import NoReturn

from .domain import fail


# These are the non-empty worksheet materials supported by older sync readers.
LEGACY_MATERIALS = ("冲压", "铸铁", "锻打", "铸铝", "锻铝")
WORKSHEET_STATUSES = {"not_started": "未开始", "in_progress": "已开始", "completed": "已完成"}
MAX_WORKSHEET_ROWS = 500
DEFAULT_PARTS = (
    ("主体", ""), ("小衬套", "NR"), ("大衬套", "NR"), ("球头", ""),
    ("球壳", "45号钢"), ("球销", "40Cr"), ("球座", ""), ("防尘罩", "CR"),
)
ROW_DEFAULTS = {
    "name": "", "code": "", "material": "", "mode": "pending",
    "borrowed_code": "", "developer": "", "start_date": "", "due_date": "",
}


def default_worksheet() -> dict:
    return {"core_code": "", "started": False,
            "rows": [{**ROW_DEFAULTS, "name": name, "material": material}
                     for name, material in DEFAULT_PARTS]}


def worksheet_status(sheet: dict) -> str:
    if sheet.get("completed"):
        return "completed"
    return "in_progress" if sheet["started"] else "not_started"


def _text(value, field, *, limit=160):
    if not isinstance(value, str) or len(value.strip()) > limit:
        fail(f"请填写不超过 {limit} 个字符的文本。", field=field)
    return value.strip()


def _clean_row(raw, number, *, legacy_codes=None):
    prefix = f"第 {number} 行"
    defaults = {**ROW_DEFAULTS, "side": "shared"} if legacy_codes is not None else ROW_DEFAULTS
    if not isinstance(raw, dict) or set(raw) - defaults.keys():
        fail(f"{prefix}零件资料格式无效。", field="rows")
    row = {key: _text(raw.get(key, default), f"rows.{number - 1}.{key}",
                      limit=164 if key == "name" and legacy_codes is None else 160)
           for key, default in defaults.items()}

    def error(key, message) -> NoReturn:
        fail(f"{prefix}：{message}", field=f"rows.{number - 1}.{key}")

    if not row["name"]:
        error("name", "请填写零件名称。")
    if legacy_codes is not None:
        if row["side"] not in ("shared", "left", "right"):
            error("side", "请选择共用、L 或 R。")
        if (row["side"] == "left" and not legacy_codes[0]) or (row["side"] == "right" and not legacy_codes[1]):
            error("side", "请先填写这一侧的成品型号。")
    if row["mode"] not in ("pending", "new", "borrowed"):
        error("mode", "请选择待定、开发或借用。")
    if row["mode"] == "borrowed" and not row["borrowed_code"]:
        error("borrowed_code", "借用零件请填写借用编码。")
    if row["mode"] != "borrowed" and row["borrowed_code"]:
        error("borrowed_code", "请先选择借用，再填写借用编码。")
    if row["mode"] != "new" and any(row[key] for key in ("developer", "start_date", "due_date")):
        error("mode", "请先选择开发，再登记开发方或日期。")
    for key in ("start_date", "due_date"):
        if row[key]:
            try:
                parsed = date.fromisoformat(row[key])
            except ValueError:
                error(key, "请填写有效的日期。")
            if parsed.isoformat() != row[key]:
                error(key, "日期格式应为 YYYY-MM-DD。")
    if row["start_date"] and row["due_date"] and row["due_date"] < row["start_date"]:
        error("due_date", "预计完成日期不能早于开始日期。")
    return row


def _clean_contents(data, *, legacy_codes=None):
    if type(data["started"]) is not bool:
        fail("请选择开发是否已开始。", field="started")
    if not isinstance(data["rows"], list) or len(data["rows"]) > MAX_WORKSHEET_ROWS:
        fail(f"每张开发表最多登记 {MAX_WORKSHEET_ROWS} 行零件。", field="rows")
    rows = [_clean_row(row, i, legacy_codes=legacy_codes) for i, row in enumerate(data["rows"], 1)]
    return {"started": data["started"], "rows": rows}


def clean_worksheet(data) -> dict:
    keys = {"core_code", "started", "rows"}
    if not isinstance(data, dict) or set(data) not in (keys, keys | {"completed"}):
        fail("新品开发表格式无效。")
    if "completed" in data and (data["completed"] is not True or data["started"] is not True):
        fail("已完成的开发表必须已开始。", field="status")
    code = _text(data["core_code"], "core_code", limit=200)
    if not code:
        fail("请填写核心编码。", field="core_code")
    return {"core_code": code, **_clean_contents(data), **({"completed": True} if "completed" in data else {})}


def validated_stored_worksheet(data) -> dict:
    if isinstance(data, dict) and set(data) == {"left_code", "right_code", "started", "rows"}:
        left = _text(data["left_code"], "left_code", limit=96)
        right = _text(data["right_code"], "right_code", limit=96)
        if not (left or right) or left.casefold() == right.casefold():
            fail("历史开发表的左右成品型号无效。")
        return {"left_code": left, "right_code": right, **_clean_contents(data, legacy_codes=(left, right))}
    return clean_worksheet(data)


_SERIES = re.compile(r"(?<![0-9])([0-9]{4})(?![0-9])")


def worksheet_for_display(sheet) -> dict:
    if "core_code" in sheet:
        return sheet
    codes = [code for code in (sheet["left_code"], sheet["right_code"]) if code]
    # A core shared unambiguously by every original code is safe to display.
    # Otherwise retain the complete original model(s), including single products.
    series = [_SERIES.findall(code) for code in codes]
    core = series[0][0] if len(series[0]) == 1 and all(found == series[0] for found in series) else " / ".join(codes)
    identities = Counter((row["name"], row["code"].casefold()) for row in sheet["rows"])
    rows = []
    for original in sheet["rows"]:
        row = dict(original)
        side = row.pop("side")
        if side != "shared" and identities[(row["name"], row["code"].casefold())] > 1:
            row["name"] += " (L)" if side == "left" else " (R)"
        rows.append(row)
    return {"core_code": core, "started": sheet["started"], "rows": rows}


def project_fields(sheet, *, notes: str | None = None):
    if "core_code" in sheet:
        fields = {"core_number": sheet["core_code"], "title": sheet["core_code"]}
        if notes is not None:
            fields["notes"] = notes.replace("\r\n", "\n").strip()
        return fields
    codes = [code for code in (sheet["left_code"], sheet["right_code"]) if code]
    series = _SERIES.search(codes[0])
    return {"core_number": series[1] if series else "", "title": " / ".join(codes)}
