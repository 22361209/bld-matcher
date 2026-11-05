"""Read-only spreadsheet values and bounded, explicit arithmetic evaluation."""
from __future__ import annotations

import ast
from datetime import date, datetime, time, timedelta
from decimal import Decimal, DecimalException
import re

from openpyxl.utils.cell import column_index_from_string, get_column_letter


MAX_DECIMAL = Decimal("1000000000000")
# Derived formula snapshots retain more precision than editable 18-place inputs.
# Bound their fixed-point representation before rendering workbook-controlled exponents.
MAX_DECIMAL_PLACES = 100
MAX_NUMERIC_TEXT = 128
TOLERANCE = Decimal("0.000001")


class WorkbookImportError(ValueError):
    """A workbook cannot be imported without changing its meaning."""


class FormulaUnavailable(ValueError):
    pass


def text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value).strip()


def decimal_value(value: object) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        literal = str(value).strip()
        if len(literal) > MAX_NUMERIC_TEXT:
            return None
        result = Decimal(literal)
    except (ValueError, DecimalException):
        return None
    if not result.is_finite() or result.copy_abs() > MAX_DECIMAL:
        return None
    exponent = result.as_tuple().exponent
    if not isinstance(exponent, int) or abs(exponent) > MAX_DECIMAL_PLACES:
        return None
    return result


def number(value: object) -> str | None:
    parsed = decimal_value(value)
    if parsed is None:
        return None
    return format(parsed, "f")


def json_value(value: object) -> object:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return number(value) or str(value)
    if not isinstance(value, (str, bool, type(None))):
        return text(getattr(value, "text", None)) or type(value).__name__
    return value


class WorkbookValues:
    """Detached raw and cached rows; reading one cell never rescans a worksheet."""

    def __init__(self, raw: dict[str, list[tuple]], cached: dict[str, list[tuple]]) -> None:
        self.raw = raw
        self.cached = cached
        self._resolved: dict[tuple[str, str], Decimal] = {}

    def cell(self, sheet: str, reference: str, *, cached: bool = False) -> object:
        match = re.fullmatch(r"\$?([A-Z]{1,3})\$?([1-9]\d*)", reference.upper())
        if not match:
            raise FormulaUnavailable("单元格引用格式不支持")
        rows = (self.cached if cached else self.raw).get(sheet, [])
        row_index = int(match[2]) - 1
        column_index = column_index_from_string(match[1]) - 1
        if row_index >= len(rows) or column_index >= len(rows[row_index]):
            return None
        return rows[row_index][column_index]

    def snapshot(self, sheet: str, row: int) -> dict:
        def cells(source: dict) -> dict:
            rows = source.get(sheet, [])
            values = rows[row - 1] if row <= len(rows) else ()
            return {
                get_column_letter(index): json_value(value)
                for index, value in enumerate(values, 1) if value is not None
            }
        return {"sheet": sheet, "row": row, "raw": cells(self.raw), "cached": cells(self.cached)}

    def resolve(self, sheet: str, reference: str, stack: tuple = ()) -> Decimal:
        key = (sheet, reference.replace("$", "").upper())
        if key in self._resolved:
            return self._resolved[key]
        if key in stack or len(stack) > 20:
            raise FormulaUnavailable("公式循环引用或依赖过深")
        value = self.cell(*key)
        if not isinstance(value, str) or not value.startswith("="):
            parsed = decimal_value(value)
            if parsed is None:
                raise FormulaUnavailable("计算输入为空或不是有效数字")
            return parsed
        formula = value[1:].replace("$", "").strip()
        if "XLOOKUP" in formula.upper():
            result = self._lookup(sheet, formula, (*stack, key))
        else:
            result = self.arithmetic(sheet, formula, (*stack, key))
        self._resolved[key] = result
        return result

    def _lookup(self, sheet: str, formula: str, stack: tuple) -> Decimal:
        match = re.fullmatch(
            r"(?:_xlfn\.)?XLOOKUP\(([A-Z]+[1-9]\d*),('?[^!,()']+'?)!([A-Z]+):\3,\2!([A-Z]+):\4\)",
            formula.replace(" ", ""), re.IGNORECASE,
        )
        if not match:
            raise FormulaUnavailable("仅支持明确的三参数精确查表")
        source_sheet = match[2].strip("'")
        if source_sheet not in self.raw:
            raise FormulaUnavailable("查表来源工作表不存在")
        key = text(self.cell(sheet, match[1])).casefold()
        key_col, result_col = column_index_from_string(match[3]), column_index_from_string(match[4])
        matches = [
            row for row, values in enumerate(self.raw[source_sheet], 1)
            if len(values) >= key_col and text(values[key_col - 1]).casefold() == key
        ]
        if not key or not matches:
            raise FormulaUnavailable("查表未找到对应规格")
        if len(matches) != 1:
            raise FormulaUnavailable("查表规格重复，不能确定采用哪一行")
        return self.resolve(source_sheet, f"{get_column_letter(result_col)}{matches[0]}", stack)

    def arithmetic(self, sheet: str, expression: str, stack: tuple = ()) -> Decimal:
        if len(expression) > 1500:
            raise FormulaUnavailable("公式长度超限")
        try:
            root = ast.parse(expression, mode="eval")
        except (SyntaxError, ValueError) as exc:
            raise FormulaUnavailable("公式不属于支持的算术表达式") from exc
        if sum(1 for _ in ast.walk(root)) > 150:
            raise FormulaUnavailable("公式复杂度超限")

        def visit(node: ast.AST, depth: int = 0) -> Decimal:
            if depth > 25:
                raise FormulaUnavailable("公式层级超限")
            if isinstance(node, ast.Expression):
                return visit(node.body, depth + 1)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                value = decimal_value(ast.get_source_segment(expression, node))
                if value is not None:
                    return value
            if isinstance(node, ast.Name) and re.fullmatch(r"[A-Z]{1,3}[1-9]\d*", node.id):
                return self.resolve(sheet, node.id, stack)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = visit(node.operand, depth + 1)
                return value if isinstance(node.op, ast.UAdd) else -value
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
                try:
                    if isinstance(node.op, ast.Add):
                        result = left + right
                    elif isinstance(node.op, ast.Sub):
                        result = left - right
                    elif isinstance(node.op, ast.Mult):
                        result = left * right
                    else:
                        result = left / right
                except DecimalException as exc:
                    raise FormulaUnavailable("公式除零或数字超限") from exc
                if decimal_value(result) is None:
                    raise FormulaUnavailable("公式计算金额或精度超出允许范围")
                return result
            raise FormulaUnavailable("公式含不支持的函数或运算")

        return visit(root)

    def numeric(self, sheet: str, reference: str) -> str | None:
        return number(self.resolve(sheet, reference))
