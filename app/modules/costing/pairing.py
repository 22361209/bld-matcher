"""Stable row correspondence between two independently calculated cost sheets."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from uuid import uuid4

from .domain import MODE_INPUTS, CostingConflict, CostingValidationError, calculate_cost, number, text
from .pair_codes import mirror_code, mirror_part_code


PAIR_KINDS = ("shared", "handed", "local")
CODE_FIELDS = ("product_code", "part_code")
PRICE_FIELDS = ("amount", "unit_price", "processing_fee", "strap_rate")


def default_pair_kind(item: Mapping) -> str:
    if any(mirror_part_code(str(item.get(key) or "")) for key in CODE_FIELDS):
        return "handed"
    name = str(item.get("name") or "")
    return "handed" if ("左" in name) != ("右" in name) or mirror_part_code(name) else "shared"


def mirror_item_name(name: str) -> str:
    if ("左" in name) != ("右" in name):
        return name.translate(str.maketrans("左右", "右左"))
    return mirror_part_code(name) or name


def _kind(raw: Mapping, fallback: str) -> str:
    value = raw.get("pair_kind", fallback)
    if value not in PAIR_KINDS:
        raise CostingValidationError("请选择共用件、左右对应件或仅本件使用。", "pair_kind")
    return value


def _set_kind(item: dict, kind: str, peer_id: str = "") -> None:
    item.update(pair_kind=kind, peer_item_id=peer_id)


def _link(item: dict, other: dict, kind: str) -> None:
    _set_kind(item, kind, other["id"])
    _set_kind(other, kind, item["id"])


def _mirror_item(item: dict, raw: Mapping, kind: str) -> dict:
    other = deepcopy(item)
    other["id"] = uuid4().hex
    if kind == "handed":
        other["name"] = mirror_item_name(item["name"])
        for field in CODE_FIELDS:
            # An unrecognized handed part number must be entered for that side.
            suggested = mirror_part_code(item.get(field) or "") or ""
            other[field] = text(raw.get("peer_" + field, suggested), "peer_" + field)
    _link(item, other, kind)
    return other


def _costed_with_links(record: dict, items: list[dict], previous: dict, **fields) -> dict:
    result = calculate_cost({**record, **fields, "items": items}, previous=previous)
    for calculated, original in zip(result["items"], items, strict=True):
        _set_kind(calculated, original["pair_kind"], original["peer_item_id"])
    return {**record, **result, "pair": record["pair"]}


def establish_pair(record: dict, peer: dict, rows: list[dict] | None, *, mirror: bool) -> None:
    """Attach both sides to one pair; the caller owns versions, auditing and transaction."""
    if (record.get("pair") or peer.get("pair") or record["id"] == peer["id"]
            or mirror_code(record["code"]) != peer["code"] or record["kind"] != peer["kind"]):
        raise CostingConflict("这两张成本单无法建立左右配对，请重新预览。")
    if not mirror and rows is None:
        raise CostingValidationError("请先核对两侧的明细关系。")
    supplied = {}
    for raw in rows or []:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str) or raw["id"] in supplied:
            raise CostingValidationError("明细关系格式错误或重复。")
        supplied[raw["id"]] = raw
    if rows is not None and set(supplied) != {row["id"] for row in record["items"]}:
        raise CostingConflict("明细与预览不一致，请重新核对。")
    pair_id = uuid4().hex
    record["pair"] = {"id": pair_id, "peer_id": peer["id"]}
    peer["pair"] = {"id": pair_id, "peer_id": record["id"]}
    original_peer = deepcopy(peer) if mirror else None
    if mirror:
        peer["items"] = []
    targets = {row["id"]: row for row in peer["items"]}
    for item in targets.values():
        _set_kind(item, "local")
    for item in record["items"]:
        raw = supplied.get(item["id"], {})
        kind = _kind(raw, default_pair_kind(item) if mirror else "local")
        _set_kind(item, kind)
        if kind == "local":
            if raw.get("peer_item_id"):
                raise CostingValidationError("仅本件使用的明细不能同时指定对侧明细。")
        elif mirror:
            peer["items"].append(_mirror_item(item, raw, kind))
        else:
            target_id = text(raw.get("peer_item_id"), "peer_item_id")
            target = targets.pop(target_id, None)
            if target is None:
                raise CostingValidationError("每行只能对应唯一的对侧明细，请重新选择。")
            if text(target.get("mode", "fixed"), "mode") != text(item.get("mode", "fixed"), "mode"):
                raise CostingValidationError("两侧计价方式不同，请先分别核对计价方式。")
            if kind == "shared" and any(text(target.get(k), k) != text(item.get(k), k) for k in CODE_FIELDS):
                raise CostingValidationError("共用件的两项编码应相同，如使用不同编码，请选择左右对应件。")
            _link(item, target, kind)
    if mirror and original_peer is not None:
        # New identities/codes describe the opposite part. Its first snapshot should
        # inherit historical amounts whenever the actual cost structure is identical.
        new_rows = {item["id"]: item for item in peer["items"]}
        for old, source in zip(original_peer["items"], record["items"], strict=True):
            corresponding = new_rows.get(source["peer_item_id"])
            if corresponding is not None:
                old["id"] = corresponding["id"]
                if "product_code" in corresponding:
                    old["product_code"] = corresponding["product_code"]
        overrides = {}
        if len(peer["items"]) != len(record["items"]):
            overrides = {"approved_cost": None, "suggested_tax_price_override": None}
        peer.update(_costed_with_links(peer, peer["items"], original_peer, **overrides))


def _apply_linked_edits(item: dict, prior: dict, target: dict, kind: str) -> None:
    mode = text(item["mode"], "mode")
    mode_changed = mode != text(prior.get("mode", "fixed"), "mode")
    if mode_changed:
        target["mode"] = mode
    linked_values = PRICE_FIELDS
    if kind == "shared" and mode == "package":
        linked_values += ("length", "width", "height")  # Dimensions belong to the shared carton, not the assembly.
    for field in linked_values:
        if field not in MODE_INPUTS[mode]:
            continue
        old = number(prior.get(field), field, allow_negative=True)
        if mode_changed or number(item.get(field), field, allow_negative=True) != old:
            target[field] = item.get(field)
    if kind == "shared":
        for field in CODE_FIELDS:
            if prior["pair_kind"] != "shared" or text(item.get(field), field) != text(prior.get(field), field):
                target[field] = item.get(field) or ""
    _link(item, target, kind)


def update_peer_rows(before: dict, record: dict, peer: dict, raw_items: list) -> dict:
    """Apply the user's deltas only. Each side retains its own usage, notes and extra parts."""
    source_rows = {row["id"]: row for row in before["items"]}
    peer_items = deepcopy(peer["items"])
    targets = {row["id"]: row for row in peer_items}
    survivors = {row["id"] for row in record["items"]}
    removed = {row["peer_item_id"] for row in source_rows.values() if row["id"] not in survivors and row["peer_item_id"]}
    additions = []
    preceding_target = ""
    for item, raw in zip(record["items"], raw_items, strict=True):
        prior = source_rows.get(item["id"])
        kind = _kind(raw, prior["pair_kind"] if prior else default_pair_kind(item))
        target = targets.get(prior["peer_item_id"]) if prior and prior["peer_item_id"] else None
        if prior and prior["pair_kind"] != "local" and (target is None or target["peer_item_id"] != prior["id"]):
            raise CostingConflict("配对的明细关系不完整，请核对两侧成本单。")
        if kind == "local":
            _set_kind(item, kind)
            if target:
                _set_kind(target, kind)
        elif target is None:
            target = _mirror_item(item, raw, kind)
            additions.append((preceding_target, target))
        else:
            assert prior is not None  # Only a previously linked row can have a stored target.
            _apply_linked_edits(item, prior, target, kind)
        if target is not None:
            preceding_target = target["id"]
    peer_items = [row for row in peer_items if row["id"] not in removed]
    for preceding, added in additions:
        # Insert after the preceding paired line; preserve the peer's independent order.
        position = next((i + 1 for i, row in enumerate(peer_items) if row["id"] == preceding), 0)
        peer_items.insert(position, added)
    return _costed_with_links(peer, peer_items, peer)
