"""Read-only tax-inclusive reference prices for alternative customer supply scopes."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext

from .domain import with_pricing
from .quote_roles import FREIGHT_WORDS, QUOTE_ROLES, line_quote_role
from .values import MAX_VALUE, CostingValidationError, decimal_text
from .weight_cost import automatic_scrap_credit


OPTIONS = {"ecoat": "电泳", "inner": "内包装", "outer": "外包装", "freight": "运费"}
_ROLE_LABEL = {**QUOTE_ROLES, "unknown": "待归类", "packing_unassigned": "包装费（归属待确认）"}
_OPTION_WORDS = {
    "ecoat": ("电泳", "表面处理", "表明处理"),
    "inner": ("包装", "内袋", "袋子", "内箱", "内盒", "内衬"),
    "outer": ("包装", "外箱", "纸箱", "打包"),
    "freight": FREIGHT_WORDS,
}


@dataclass(frozen=True)
class Scope:
    stage: str = "finished"
    ecoat: bool = True
    inner: bool = True
    outer: bool = True
    freight: bool = True

    @classmethod
    def from_query(cls, query: dict[str, list[str]]) -> Scope:
        if not query:
            return cls()
        if (set(query) - {"stage", *OPTIONS} or any(len(values) != 1 for values in query.values())
                or query.get("stage") not in (["finished"], ["weld"])):
            raise CostingValidationError("请选择有效的供货阶段及报价选项。")
        flags = {key: query.get(key, ["0"])[0] for key in OPTIONS}
        if any(value not in ("0", "1") for value in flags.values()):
            raise CostingValidationError("报价选项仅支持勾选或取消。")
        return cls(stage=query["stage"][0], **{key: value == "1" for key, value in flags.items()})


def _problem_rows(prefix: str, rows: list[dict]) -> str:
    details = "、".join(f"第{row['index']}行「{row['name']}」" for row in rows[:4])
    return f"{prefix}：{details}" + (f"等 {len(rows)} 行" if len(rows) > 4 else "")


def _selection(scope: Scope, rows: list[dict], chosen: set[str]) -> tuple[list[dict], str]:
    roles = {row["role"] for row in rows}
    # For a whole product, unclassified components still belong to the selected
    # scope unless their description overlaps an omitted option. A welded stage
    # needs a positive distinction between the body and finished assemblies.
    omitted_words = tuple(word for key in OPTIONS if key not in chosen for word in _OPTION_WORDS[key])
    unresolved = [row for row in rows if row["role"] == "unknown" and (
        scope.stage == "weld" or any(word in row["name"] + row["category"] for word in omitted_words))]
    if unresolved:
        return [], _problem_rows("请确认报价归类或拆分合并项目", unresolved)
    bundles = {role for role in roles if isinstance(role, frozenset)}
    partial = [row for row in rows if row["role"] in bundles and row["role"] & chosen and row["role"] - chosen]
    if partial:
        return [], _problem_rows("请拆分合并计价的项目，或确认报价归类", partial)
    if scope.inner != scope.outer:
        mixed = [row for row in rows if row["role"] == "packing_unassigned"]
        if mixed:
            return [], _problem_rows("请拆分内外包装，或确认包装工费的报价归类", mixed)
    evidence = roles.union(*bundles)
    missing = [OPTIONS[key] for key in OPTIONS if key in chosen and key not in evidence]
    if scope.stage == "weld" and "weld" not in roles:
        missing.insert(0, "焊接本体")
    if missing:
        return [], "请确认所选范围的费用：" + "、".join(missing) + "缺少可识别的明细。"
    keep: set[str | frozenset[str]] = {"weld", *chosen}
    if scope.stage == "finished":
        keep.update(("assembly", "unknown"))
    if not roles.intersection(keep - set(OPTIONS)):
        return [], "缺少可识别的产品本体明细。"
    if scope.inner or scope.outer:
        keep.add("packing_shared")
    if scope.inner and scope.outer:
        keep.add("packing_unassigned")
    keep.update(bundle for bundle in bundles if bundle <= chosen)
    return [row for row in rows if row["role"] in keep], ""


def _credit(record: dict, included: list[dict], removed: list[dict]) -> tuple[Decimal, str]:
    applied = Decimal(record.get("scrap_credit") or "0")
    auto = record.get("scrap_mode") == "auto"
    if auto and record.get("scrap_credit_override") is None:
        if any(row["item"].get("scrap_issue") for row in included):
            return Decimal(0), ""
        weight = sum((Decimal(row["item"].get("scrap_total_weight") or "0") for row in included), Decimal(0))
        unit_price = Decimal(record.get("scrap_unit_price") or "2")
        return automatic_scrap_credit(weight * unit_price), ""
    if not applied or not removed:
        return applied, ""
    optional_roles = {*OPTIONS, "packing_shared", "packing_unassigned"}
    if all((isinstance(row["role"], frozenset) or row["role"] in optional_roles)
           and not row["item"].get("scrap_expected") for row in removed):
        return applied, ""
    if auto:
        # A manual override cannot be apportioned to removed stamping rows.
        kept_stamps = any(row["item"].get("scrap_expected") for row in included)
        if kept_stamps and not any(row["item"].get("scrap_expected") for row in removed):
            return applied, ""
    else:
        # The legacy credit belongs to the whole sheet. Preserve it if every
        # named scrap component has a unique saved row and all of them remain.
        refs = (record.get("source_info") or {}).get("scrap_reference_rows")
        if isinstance(refs, list) and refs:
            retained = {row["name"] for row in included}
            counts = Counter(row["name"] for row in [*included, *removed])
            if all(isinstance(ref, dict) and isinstance(ref.get("name"), str) and
                   ref["name"] in retained and counts[ref["name"]] == 1 for ref in refs):
                return applied, ""
    return Decimal(0), "整单废料抵减无法分摊，请先核对冲压件取重及实际抵减。"


def _price(record, scope, rows, *, details):
    chosen = {key for key in OPTIONS if getattr(scope, key)}
    label = "整件" if scope.stage == "finished" else "焊接件"
    label += " · " + "、".join(("含" if key in chosen else "不含") + name for key, name in OPTIONS.items())
    selected, problem = _selection(scope, rows, chosen)
    detail = {"label": label, "minimum_tax_price": None, "net_cost": None,
              "scrap_credit": None, "included_lines": [], "problem": problem}
    if details:
        detail["included_lines"] = [
            {key: row[key] for key in ("index", "name", "line_total", "role_label")} for row in selected
        ]
    if problem:
        return detail
    if not selected:
        return {**detail, "problem": "该口径没有可计价的成本明细。"}
    missing = [row for row in selected if row["line_total"] in (None, "")]
    if missing:
        return {**detail, "problem": _problem_rows("请补齐计入项目的金额", missing)}
    if scope.stage == "finished" and chosen == set(OPTIONS):
        minimum = record.get("minimum_tax_price")
        return {**detail, "net_cost": record.get("net_cost"), "scrap_credit": record.get("scrap_credit") or "0",
                "minimum_tax_price": minimum, "problem": "" if minimum is not None else "请核对当前成本单的最低含税售价。"}
    indices = {row["index"] for row in selected}
    removed = [row for row in rows if row["index"] not in indices]
    with localcontext() as context:
        context.prec = 110
        credit, problem = _credit(record, selected, removed)
        if problem:
            return {**detail, "problem": problem}
        net = sum((Decimal(row["line_total"]) for row in selected), Decimal(0)) - credit
        if net < 0 or net > MAX_VALUE * 2:
            return {**detail, "problem": "所选明细抵减后成本超出核算范围，请核对。"}
        net_value = decimal_text(net)
        minimum = with_pricing({"net_cost": net_value})["minimum_tax_price"]
        return {**detail, "net_cost": net_value, "scrap_credit": decimal_text(credit), "minimum_tax_price": minimum}


def scope_quote(record: dict, scope: Scope, *, details: bool) -> dict:
    if scope.stage == "weld" and record["kind"] != "arm":
        return {"label": "焊接件", "minimum_tax_price": None, "problem": "不适用：此型号为悬挂球头。"}
    rows = []
    for index, item in enumerate(record["items"], start=1):
        role = line_quote_role(item)
        role_label = ("、".join(label for key, label in OPTIONS.items() if key in role) + "合计"
                      if isinstance(role, frozenset) else _ROLE_LABEL[role])
        rows.append({"index": index, "item": item, "role": role, "role_label": role_label,
                     "category": item.get("category") or "", "name": item.get("name") or "未命名项目",
                     "line_total": item.get("line_total")})
    quote = _price(record, scope, rows, details=details)
    if not details:
        quote = {**{key: quote[key] for key in ("label", "minimum_tax_price")},
                 "problem": "当前组合待成本维护人员核对。" if quote["problem"] else ""}
    return quote
