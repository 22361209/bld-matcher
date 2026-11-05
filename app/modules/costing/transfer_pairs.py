"""Verify linked cost sheets and keep their revisions together during exchange."""
from __future__ import annotations

import re
from itertools import chain

from .domain import CostingValidationError, text
from .pair_codes import mirror_code
from .weight_cost import SCRAP_HEADER_FIELDS, SCRAP_ROW_FIELDS


def format_version_for(records: list[dict], revisions=()) -> int:
    # A historical snapshot can retain a sourced line that has since been removed.
    for row in chain(records, (revision["after"] for revision in revisions)):
        if (any(key in row for key in SCRAP_HEADER_FIELDS)
                or any(any(key in item for key in SCRAP_ROW_FIELDS) for item in row["items"])):
            return 3
    return 2 if any("pair" in record for record in records) else 1


def _check_sheet(record: dict, version: int) -> None:
    items = record["items"]
    if "pair" not in record:
        if any("pair_kind" in item or "peer_item_id" in item for item in items):
            raise CostingValidationError("独立成本单含有未配对的明细关系。")
        return
    if version < 2:
        raise CostingValidationError("旧版成本快照不能携带左右件关系。")
    pair = record["pair"]
    if (not isinstance(pair, dict) or set(pair) != {"id", "peer_id"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) for value in pair.values())
            or pair["peer_id"] == record["id"]):
        raise CostingValidationError("成本单左右件的配对标识无效。")
    for item in items:
        kind, peer_id = item.get("pair_kind"), item.get("peer_item_id")
        if kind == "local" and peer_id == "":
            continue
        if (kind not in ("shared", "handed") or not isinstance(peer_id, str)
                or not re.fullmatch(r"[0-9a-f]{32}", peer_id)):
            raise CostingValidationError("左右件的明细配对标识无效。")


def _check_linked_items(sheet: dict, peer: dict) -> None:
    counterparts = {item["id"]: item for item in peer["items"]}
    for item in sheet["items"]:
        if item["pair_kind"] == "local":
            continue
        other = counterparts.get(item["peer_item_id"])
        if other is None or other["peer_item_id"] != item["id"] or other["pair_kind"] != item["pair_kind"]:
            raise CostingValidationError("左右件的明细关系缺少对侧对应行或两侧不一致。")
        if text(item.get("mode", "fixed"), "mode") != text(other.get("mode", "fixed"), "mode"):
            raise CostingValidationError("左右对应明细的计价方式不一致。")
        if item["pair_kind"] == "shared" and any(
            text(item.get(field), field) != text(other.get(field), field) for field in ("product_code", "part_code")
        ):
            raise CostingValidationError("共用明细的编码不一致。")


def validate_pair_histories(history: dict[str, list[dict]], version: int) -> None:
    """Full history chains have been checked; verify each paired save across both sheets."""
    linked: dict[str, list[dict]] = {}
    for key, revisions in history.items():
        paired = []
        for revision in sorted(revisions, key=lambda row: row["version"]):
            sheet = revision["after"]
            _check_sheet(sheet, version)
            if paired and any(paired[-1].get(field) != sheet.get(field) for field in ("pair", "code", "kind")):
                raise CostingValidationError("成本修订中途更换了左右件关系或已配对的型号。")
            if "pair" in sheet:
                paired.append(sheet)
        if paired:
            linked[key] = paired

    pair_owners = {}
    for key, editions in linked.items():
        pair = editions[-1]["pair"]
        peer_id = pair["peer_id"]
        members = frozenset((key, peer_id))
        peers = linked.get(peer_id)
        if (pair_owners.setdefault(pair["id"], members) != members or peers is None
                or peers[-1]["pair"] != {"id": pair["id"], "peer_id": key} or len(peers) != len(editions)):
            raise CostingValidationError("左右件或其配对修订缺失，需完整同步两张成本单。")
        if key > peer_id:
            continue
        for sheet, peer in zip(editions, peers, strict=True):
            if (sheet["updated_at"] != peer["updated_at"] or sheet["updated_by"] != peer["updated_by"]
                    or sheet["kind"] != peer["kind"] or mirror_code(sheet["code"]) != peer["code"]):
                raise CostingValidationError("左右件的型号或配对修订不一致。")
            _check_linked_items(sheet, peer)
            _check_linked_items(peer, sheet)


def group_conflicts(records: list[dict], local_records: dict[str, dict], statuses: dict[str, str]) -> dict[str, str]:
    """A rejected side must never leave a successful write on its counterpart."""
    local_pairs = {sheet["pair"]["id"]: {key, sheet["pair"]["peer_id"]}
                   for key, sheet in local_records.items() if sheet.get("pair")}
    conflicts = {}
    for sheet in records:
        pair = sheet.get("pair")
        if not pair or sheet["id"] > pair["peer_id"]:
            continue
        members = {sheet["id"], pair["peer_id"]}
        states = {statuses[key] for key in members}
        if pair["id"] in local_pairs and local_pairs[pair["id"]] != members:
            reason = "左右件配对标识已被本机其他型号占用。"
        elif "conflict" in states or ("stale" in states and states.intersection({"updated", "new"})):
            reason = "左右件存在历史或编号冲突，已一起保留本机资料。"
        else:
            continue
        conflicts.update(dict.fromkeys(members, reason))
    return conflicts
