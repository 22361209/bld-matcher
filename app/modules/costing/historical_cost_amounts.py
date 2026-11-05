"""Plan complete, evidence-backed updates without writing any costs or audit history."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from .domain import MODE_INPUTS, CostingValidationError, calculate_cost
from .historical_materials import material_match_key
from .pair_codes import mirror_code, mirror_part_code
from .weight_cost import weight_snapshot


class _Review(ValueError):
    pass


def _amount(value):
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise _Review("历史金额或重量缺少可核对的数值") from exc
    if not result.is_finite():
        raise _Review("历史金额或重量无效")
    return result


def _pivot_amount(pivot, column):
    raw = pivot.get("raw", {}).get(column)
    if isinstance(raw, str) and raw.startswith("="):
        raw = pivot.get("cached", {}).get(column)
        if raw is None:
            raise _Review("历史透视表公式缺少已保存的金额")
    return _amount(raw)


def _original(record):
    pivot = record.get("source_info", {}).get("pivot") or {}
    if not isinstance(pivot, dict) or not isinstance(pivot.get("raw"), dict):
        return ""
    return str(pivot.get("raw", {}).get("A") or "").strip().upper()


def _history(record, revisions):
    rows = sorted(revisions(record["id"]), key=lambda row: row["version"])
    if ([row["version"] for row in rows] != list(range(1, record["version"] + 1))
            or rows[-1]["after"] != record):
        raise _Review("成本修订不完整，无法区分历史采用额与手工修改")
    return rows


def _credit_override(record, history):
    if record.get("scrap_mode") == "auto":
        return record.get("scrap_credit_override")
    pivot = record["source_info"]["pivot"]
    changed = set()
    for key, column in (("scrap_credit", "F"), ("theoretical_scrap", "K")):
        if _amount(record.get(key)) != _pivot_amount(pivot, column):
            changed.add(key)
        for rev in history:
            if rev["before"] and _amount(rev["before"].get(key)) != _amount(rev["after"].get(key)):
                changed.add(key)
    if "theoretical_scrap" in changed:
        raise _Review("理论参考额曾经手工调整，保留当前成本单")
    if "scrap_credit" in changed and record.get("scrap_credit") is None:
        raise _Review("实际抵减额曾被清空，需确认是否启用自动抵减")
    return record.get("scrap_credit") if "scrap_credit" in changed else None


def _weight_history(history, row):
    # A detached snapshot or changed manual weight is a deliberate user decision.
    key = row["id"]
    if (row.get("weight") is not None
            and not any(item["id"] == key for item in history[0]["after"]["items"])):
        raise _Review(f"{row['name']} 是后续新增且已有手填重量的成本行")
    previous = None
    for revision in history:
        item = next((i for i in revision["after"]["items"] if i["id"] == key), None)
        if item is not None and previous is not None and not item.get("weight_source"):
            if previous.get("weight_source") or _amount(previous.get("weight")) != _amount(item.get("weight")):
                raise _Review(f"{row['name']} 的重量曾被手工修改或解除关联")
        if item is not None:
            previous = item


def _snapshot(material):
    candidate = {key: material.get(key) for key in ("name", "model", "spec", "pieces", "gross_weight", "net_weight", "scrap_weight")}
    candidate.update(kind="stamping", product_code=material.get("code"), source_id=material.get("sync_id"))
    with localcontext() as context:
        context.prec = 64
        return weight_snapshot(candidate)


def _source_rows(record, matches):
    refs = record.get("source_info", {}).get("scrap_reference_rows")
    if not isinstance(refs, list) or not refs:
        raise _Review("原表未附可核对的片件明细")
    result = {}
    source_ids = set()
    for ref in refs:
        if not isinstance(ref, dict):
            raise _Review("原表片件明细缺少可核对的型号与部件")
        key = material_match_key(ref.get("code"), ref.get("name"))
        if key in result or key not in matches:
            raise _Review(f"{' / '.join(key)} 缺少唯一、已核实净重的材料")
        source = _snapshot(matches[key])
        if (source["source_id"] in source_ids or source["net_weight"] is None
                or _amount(source["net_weight"]) != _amount(ref.get("net_weight"))):
            raise _Review(f"{key[1]} 的材料身份或原表净重不一致")
        source_ids.add(source["source_id"])
        result[key] = source
    return result


def _direct_lines(record, sources):
    lines = {}
    ambiguous = {}
    for key, source in sources.items():
        code = str(source["product_code"]).casefold()
        by_code = [row for row in record["items"] if row.get("product_code", "").strip().casefold() == code]
        by_name = [row for row in record["items"] if material_match_key("", row["name"])[1] == key[1]]
        if len(by_name) > 1:
            if (len(by_code) != 1 or by_code[0] not in by_name
                    or (by_code[0].get("weight_source") or {}).get("source_id") != source["source_id"]):
                raise _Review(f"{key[1]} 无法唯一对应成本行")
            ambiguous.update((row["id"], row) for row in by_name)
        candidates = by_code or by_name
        if len(candidates) != 1 or candidates[0]["id"] in lines:
            raise _Review(f"{key[1]} 无法唯一对应成本行")
        row = candidates[0]
        if by_code and material_match_key("", row["name"])[1] != key[1]:
            # An existing code can disambiguate wording only when it identifies a verified source.
            if (row.get("weight_source") or {}).get("source_id") != source["source_id"]:
                raise _Review(f"{key[1]} 的已有编码对应不同名称")
        lines[row["id"]] = source
    for item_id, row in ambiguous.items():
        if item_id in lines:
            continue
        existing = row.get("weight_source") or {}
        if (not existing.get("source_id") or row.get("mode") != "weight"
                or not row.get("product_code") or row["product_code"].casefold() != str(existing.get("product_code")).casefold()
                or _amount(row.get("weight")) != _amount(existing.get("gross_weight"))
                or (existing.get("kind") == "stamping" and existing.get("net_weight") is None)):
            raise _Review(f"{row['name']} 的另一同名行仍缺少独立核实的重量来源")
    return lines


def _mirror_lines(record, original, lines):
    via = {row.get("peer_item_id"): row for row in record["items"] if row.get("pair_kind") == "shared"}
    source_rows = {row["id"]: row for row in original["items"]}
    result = {}
    for item_id, material in lines.items():
        other = via.get(item_id)
        first = source_rows[item_id]
        if first.get("pair_kind") == "local" and not first.get("peer_item_id"):
            continue
        if (not other or first.get("pair_kind") != "shared" or first.get("peer_item_id") != other["id"]
                or mirror_part_code(str(material["product_code"]))):
            raise _Review(f"{first['name']} 缺少经确认的左右共用编码及行关系")
        result[other["id"]] = material
    return result


def _same_unselected_amount(prior, item):
    stored = _amount(prior.get("line_total"))
    proposed = _amount(item.get("line_total"))
    if stored == proposed:
        return True
    if prior.get("mode") != "package" or item["mode"] != "package" or stored is None or proposed is None:
        return False
    if any(_amount(prior.get(key)) != _amount(item.get(key)) for key in MODE_INPUTS["package"]):
        return False
    # Legacy carton allocation used 50 significant digits; the new sheet uses 100.
    # Require the original expression to reproduce the entire historical number.
    legacy = calculate_cost({"code": "核对", "items": [prior]})["items"][0]["line_total"]
    with localcontext() as context:
        context.prec = 50
        return _amount(legacy) == stored == context.create_decimal(proposed)


def _propose(record, lines, revisions):
    history = _history(record, revisions)
    override = _credit_override(record, history)
    if not lines:
        raise _Review("没有可以核算的冲压行")
    items = []
    for row in record["items"]:
        source = lines.get(row["id"])
        if source is None:
            items.append(row)
            continue
        if row["mode"] != "weight":
            raise _Review(f"{row['name']} 当前未按重量计价")
        code = str(row.get("product_code") or "").strip()
        if code and code.casefold() != str(source["product_code"]).casefold():
            raise _Review(f"{row['name']} 已有不同的产品编码 {code}")
        part_code = str(row.get("part_code") or "").strip()
        if not code and part_code and part_code.casefold() != str(source["product_code"]).casefold():
            raise _Review(f"{row['name']} 的既有零件编码与拟填产品编码不同，需核对")
        old_source = row.get("weight_source")
        if old_source and old_source != source:
            fills_missing_net = (old_source.get("net_weight") is None and all(old_source.get(k) == source[k]
                                 for k in ("kind", "source_id", "product_code", "gross_weight", "pieces", "model", "spec")))
            if not fills_missing_net:
                raise _Review(f"{row['name']} 已有不同的取重来源或重量")
        if not old_source:
            _weight_history(history, row)
            raw = (row.get("source_info") or {}).get("raw", {}).get("K")
            if raw not in (None, "") and not str(raw).startswith("=") and _amount(raw) != _amount(row.get("weight")):
                raise _Review(f"{row['name']} 的当前重量与原表不同")
        items.append({**row, "weight_source": source, "product_code": source["product_code"], "weight": source["gross_weight"]})
    calculated = calculate_cost({**record, "items": items, "scrap_mode": "auto",
                                 "scrap_credit_override": override}, previous=record)
    if not calculated["scrap_complete"]:
        raise _Review("还有冲压行缺少已确认的净重或来源")
    for prior, item in zip(record["items"], calculated["items"], strict=True):
        if prior["id"] not in lines and not _same_unselected_amount(prior, item):
            raise _Review(f"{prior['name']} 未参与本次补录，重算将改变其历史金额")
        for field in ("pair_kind", "peer_item_id"):
            if field in prior:
                item[field] = prior[field]
    return {**record, **calculated}


def _plan_group(members, matches, revisions):
    direct = {}
    for record in members:
        if any("透视表重复" in note for note in record.get("source_info", {}).get("review_notes", [])):
            raise _Review("透视表型号重复，无法确认采用哪张原表")
        if record.get("source") == "excel" and record["code"].upper() == _original(record):
            direct[record["id"]] = _direct_lines(record, _source_rows(record, matches))
    planned = []
    for record in members:
        lines = direct.get(record["id"])
        if lines is None:
            peer = next((r for r in members if r["id"] != record["id"]), None)
            if (record.get("source") != "copy" or not peer or peer["id"] not in direct
                    or _original(record) != peer["code"] or mirror_code(peer["code"]) != record["code"]
                    or record.get("source_info") != peer.get("source_info")):
                raise _Review("不能证明本单来自对应原型号或其已建立配对的对侧")
            lines = _mirror_lines(record, peer, direct[peer["id"]])
        planned.append(_propose(record, lines, revisions))
    if len(planned) == 2:
        other_rows = {row["id"]: row for row in planned[1]["items"]}
        for item in planned[0]["items"]:
            if item.get("pair_kind") != "shared":
                continue
            other = other_rows.get(item.get("peer_item_id"))
            if (not other or other.get("pair_kind") != "shared" or other.get("peer_item_id") != item["id"]
                    or any(item.get(k, "") != other.get(k, "") for k in ("product_code", "part_code"))):
                raise _Review("两侧共用行的零件关系或编码有冲突")
    return planned


def plan_historical_costs(records, matches, revisions):
    """Return atomic (before, after) proposals, carrying unchanged paired peers for the audit."""
    if callable(revisions):
        load_history = revisions
    else:
        histories = revisions
        if not isinstance(histories, dict):
            histories = defaultdict(list)
            for revision in revisions:
                histories[revision["record_id"]].append(revision)
        def load_history(key):
            return histories.get(key, ())
    by_id = {r["id"]: r for r in records}
    visited, updates, reviews = set(), [], []
    for record in records:
        if record["id"] in visited or record["kind"] != "arm":
            continue
        members = [record]
        try:
            if pair := record.get("pair"):
                peer = by_id.get(pair["peer_id"])
                if not peer or peer.get("pair") != {"id": pair["id"], "peer_id": record["id"]}:
                    raise _Review("对侧成本单或配对关系不完整")
                members.append(peer)
            planned = _plan_group(members, matches, load_history)
            group = list(zip(members, planned, strict=True))
            if any(before != after for before, after in group):
                updates.extend(group)
        except (_Review, CostingValidationError) as exc:
            reviews.extend({"code": r["code"], "reason": str(exc)} for r in members)
        visited.update(r["id"] for r in members)
    return updates, reviews
