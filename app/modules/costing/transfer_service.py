"""Validated, non-overwriting transfer between authenticated cost workspaces."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
from pathlib import Path
import re

from .domain import (
    MAX_VALUE, CostingConflict, CostingValidationError, calculate_cost, old_pending_scrap,
    reference_snapshot, text, with_pricing,
)
from .transfer_pairs import format_version_for, validate_pair_histories
from .transfer_repository import CostingTransferRepository
from .weight_cost import SCRAP_HEADER_FIELDS, SCRAP_ROW_FIELDS


LEGACY_TRANSFER_MIB = 64
MAX_TRANSFER_MIB = 128
MAX_TRANSFER_BYTES = MAX_TRANSFER_MIB * 1024 * 1024
MAX_RECORDS = 5000
MAX_REVISIONS = 100000
SNAPSHOT_FIELDS = {"format_version", "records", "revisions", "references", "digest"}
REVISION_FIELDS = {"id", "record_id", "version", "action", "actor", "created_at", "before", "after"}
DERIVED_AMOUNTS = ("known_subtotal", "calculated_cost", "effective_cost", "net_cost", "minimum_tax_price", "suggested_tax_price")


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CostingValidationError("成本迁移包必须是有效的 JSON 资料。") from exc


def snapshot_digest(content: dict) -> str:
    """Content checksum, excluding the checksum itself and independent of row order."""
    digest = hashlib.sha256()
    digest.update(b'{"format_version":' + _encoded(content["format_version"]) + b',"records":[')
    # Same sorted JSON bytes as the original v1 format, keeping only one
    # record or revision's encoded bytes at a time even for a full workspace.
    for key in ("records", "revisions"):
        if key == "revisions":
            digest.update(b'],"references":' + _encoded(content["references"]) + b',"revisions":[')
        for index, row in enumerate(sorted(content[key], key=lambda row: row["id"])):
            if index:
                digest.update(b",")
            digest.update(_encoded(row))
    digest.update(b"]}")
    return digest.hexdigest()


def _bounded_content(snapshot: dict, limit_mib: int, detach: bool) -> dict:
    metadata = _encoded({**snapshot, "records": [], "revisions": []})
    size = len(metadata)
    limit = min(MAX_TRANSFER_BYTES, limit_mib * 1024 * 1024)
    if size > limit:
        raise CostingValidationError(f"成本迁移包不能超过 {limit_mib} MiB。")
    result = json.loads(metadata) if detach else snapshot
    for key, max_count in (("records", MAX_RECORDS), ("revisions", MAX_REVISIONS)):
        rows = snapshot[key]
        if not isinstance(rows, list) or len(rows) > max_count:
            raise CostingValidationError("成本迁移包的型号或修订数量无效。")
        size += max(0, len(rows) - 1)  # JSON array separators.
        for row in rows:
            encoded = _encoded(row)
            size += len(encoded)
            if size > limit:
                raise CostingValidationError(f"成本迁移包不能超过 {limit_mib} MiB。")
            if detach:
                result[key].append(json.loads(encoded))
    return result


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or not 0 < value < 2 ** 63:
        raise CostingValidationError(f"{label}必须是有效正整数。")
    return value


def _identity(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise CostingValidationError("成本单或明细的永久编号无效。")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise CostingValidationError("成本修订时间无效。")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CostingValidationError("成本修订时间无效。") from exc
    if parsed.tzinfo is None:
        raise CostingValidationError("成本修订时间缺少时区。")
    return parsed


def _stored_amount(value: object) -> None:
    """Historical derived amounts can have more precision than editable inputs."""
    if value is None:
        return
    if not isinstance(value, str) or not value or len(value) > 128:
        raise CostingValidationError("成本快照金额格式无效。")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise CostingValidationError("成本快照金额格式无效。") from exc
    exponent = amount.as_tuple().exponent
    if (not amount.is_finite() or amount.copy_abs() > MAX_VALUE * 4
            or not isinstance(exponent, int) or abs(exponent) > 128):
        raise CostingValidationError("成本快照金额超出范围。")


def _validate_record(record: object) -> dict:
    if not isinstance(record, dict):
        raise CostingValidationError("迁移成本单格式无效。")
    required = {"id", "version", "created_at", "updated_at", "created_by", "updated_by", "source", "status",
                "code", "kind", "name", "brand", "models", "remark", "items", "source_info", "issues",
                "approved_cost", "scrap_credit", "theoretical_scrap", "known_subtotal", "calculated_cost", "effective_cost", "net_cost"}
    if not required.issubset(record):
        raise CostingValidationError("成本记录字段不完整。")
    _identity(record.get("id"))
    _positive_integer(record.get("version"), "成本版本")
    if _timestamp(record.get("updated_at")) < _timestamp(record.get("created_at")):
        raise CostingValidationError("成本修改时间早于创建时间。")
    for field in ("created_by", "updated_by"):
        if not text(record.get(field), field):
            raise CostingValidationError("成本记录缺少原操作人。")
    if (not isinstance(record["source"], str) or record["source"] not in {"manual", "excel", "api", "copy"}
            or not isinstance(record["status"], str) or record["status"] not in {"ready", "draft"}):
        raise CostingValidationError("成本记录的来源或状态无效。")
    if not isinstance(record["issues"], list) or any(not isinstance(issue, str) for issue in record["issues"]):
        raise CostingValidationError("成本核对提示格式无效。")
    # Validate editability with today's rules, but retain every original value,
    # including historical derived sale prices and source evidence.
    for field in DERIVED_AMOUNTS:
        _stored_amount(record.get(field))
    calculated = calculate_cost(record)
    with_pricing(record)
    if any(key in record for key in SCRAP_HEADER_FIELDS):
        amounts = ("theoretical_scrap", "scrap_credit", "net_cost")
        if old_pending_scrap(record) and calculated.get("scrap_complete") is False:
            # Earlier v3 ledgers recorded an unknown deduction and net before any manual override.
            amounts = ("theoretical_scrap",)
        for key in (*SCRAP_HEADER_FIELDS, *amounts):
            if (key not in calculated or key not in record or type(record[key]) is not type(calculated[key])
                    or record[key] != calculated[key]):
                raise CostingValidationError("废料核算快照的单价、抵减额或合计与明细不一致。")
        if not calculated["scrap_complete"] and record["status"] != "draft":
            raise CostingValidationError("缺少冲压件废料明细的成本单应保留待核对状态。")
    for field in ("code", "name", "brand", "models", "kind"):
        if record.get(field) != calculated[field]:
            raise CostingValidationError("成本型号或索引字段格式无效。")
    items = record.get("items")
    if not isinstance(items, list):
        raise CostingValidationError("成本明细格式无效。")
    item_ids = [_identity(item.get("id")) for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise CostingValidationError("同一成本单的明细编号重复。")
    for item, checked in zip(items, calculated["items"], strict=True):
        _stored_amount(item.get("line_total"))
        if "quote_role" in item and ("quote_role" not in checked or item["quote_role"] != checked["quote_role"]):
            raise CostingValidationError("成本快照报价归类无效。")
        if "weight_choice" in item:
            raise CostingValidationError("成本快照应保存重量来源资料，不能携带临时取重凭据。")
        if record.get("scrap_mode") == "auto" or any(key in item for key in SCRAP_ROW_FIELDS):
            for key in SCRAP_ROW_FIELDS:
                if (key in checked) != (key in item) or item.get(key) != checked.get(key):
                    raise CostingValidationError("成本行的重量来源与废料明细不一致。")
            if item.get("weight_source") and item.get("weight") != checked["weight"]:
                raise CostingValidationError("成本行重量与来源快照不一致。")
        issues = item.get("issues")
        if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
            raise CostingValidationError("成本明细的核对提示格式无效。")
    return record


def _validated_snapshot(snapshot: object, *, allow_empty: bool = False, detach: bool = True) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        raise CostingValidationError("成本迁移包字段不完整或含有未知字段。")
    version = snapshot["format_version"]
    if type(version) is not int or version not in (1, 2, 3, 4):
        raise CostingValidationError("成本迁移包版本不受支持。")
    limit_mib = MAX_TRANSFER_MIB if version >= 2 else LEGACY_TRANSFER_MIB
    # The standalone transfer owns a detached input; business sync already owns
    # the parsed archive tree. Neither path mutates the supplied snapshot.
    content = _bounded_content(snapshot, limit_mib, detach)
    records, revisions = content["records"], content["revisions"]
    minimum = 0 if allow_empty else 1
    if not isinstance(records, list) or not minimum <= len(records) <= MAX_RECORDS:
        raise CostingValidationError("成本迁移包需要包含 1 至 5000 个型号。")
    if not isinstance(revisions, list) or not minimum <= len(revisions) <= MAX_REVISIONS:
        raise CostingValidationError("成本迁移包的修订数量无效。")
    if content["references"] is not None:
        reference_snapshot(content["references"])
        for key in ("cartons", "rates", "ball_seats", "covers", "scrap"):
            if key in content["references"]:
                options = content["references"][key]
                if not isinstance(options, list) or any(not isinstance(option, dict) for option in options):
                    raise CostingValidationError("成本参考候选资料必须是对象数组。")
    record_map = {}
    codes = set()
    item_owners = {}
    for row in records:
        record = _validate_record(row)
        if record["id"] in record_map or record["code"].casefold() in codes:
            raise CostingValidationError("成本迁移包存在重复型号或编号。")
        record_map[record["id"]] = record
        codes.add(record["code"].casefold())
    histories = defaultdict(list)
    revision_ids = set()
    for revision in revisions:
        if not isinstance(revision, dict) or set(revision) != REVISION_FIELDS:
            raise CostingValidationError("成本修订字段无效。")
        revision_id = _positive_integer(revision["id"], "修订编号")
        record_id = _identity(revision["record_id"])
        if revision_id in revision_ids or record_id not in record_map:
            raise CostingValidationError("成本修订存在重复编号或无对应成本单。")
        revision_ids.add(revision_id)
        _positive_integer(revision["version"], "修订版本")
        after = _validate_record(revision["after"])
        for item in after["items"]:
            if item_owners.setdefault(item["id"], record_id) != record_id:
                raise CostingValidationError("不同成本单共用了同一明细编号。")
        if (after["id"] != record_id or after["version"] != revision["version"]
                or after["updated_at"] != revision["created_at"] or after["updated_by"] != revision["actor"]):
            raise CostingValidationError("成本修订与其记录快照不一致。")
        histories[record_id].append(revision)
    if format_version_for(records, revisions) != version:
        raise CostingValidationError("成本迁移包版本与报价归类、左右件或重量来源资料不一致。")
    for record_id, record in record_map.items():
        history = sorted(histories[record_id], key=lambda row: row["version"])
        if len(history) != record["version"]:
            raise CostingValidationError("成本修订历史不完整。")
        previous = None
        origin = history[0]["after"]
        for version, revision in enumerate(history, 1):
            if revision["version"] != version or _encoded(revision["before"]) != _encoded(previous):
                raise CostingValidationError("成本修订顺序或前后快照不一致。")
            after = revision["after"]
            if any(after[key] != origin[key] for key in ("created_at", "created_by", "source")):
                raise CostingValidationError("成本修订改变了原始创建信息。")
            if previous is not None and _timestamp(after["updated_at"]) < _timestamp(previous["updated_at"]):
                raise CostingValidationError("成本修订时间顺序不一致。")
            expected_actions = {"create", "import"} if version == 1 else {"update"}
            if not isinstance(revision["action"], str) or revision["action"] not in expected_actions:
                raise CostingValidationError("成本修订操作无效。")
            previous = revision["after"]
        if _encoded(previous) != _encoded(record):
            raise CostingValidationError("成本单与最后一版修订不一致。")
    validate_pair_histories(histories, content["format_version"])
    digest = content["digest"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not hmac.compare_digest(digest, snapshot_digest(content)):
        raise CostingValidationError("成本迁移包校验失败，资料可能已被修改。")
    return {**content, "records": sorted(records, key=lambda row: row["id"]),
            "revisions": sorted(revisions, key=lambda row: row["id"])}


class CostingTransferService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def export_snapshot(self) -> dict:
        with CostingTransferRepository(self.db_path) as repository:
            content = repository.read_content()
        return _validated_snapshot({**content, "digest": snapshot_digest(content)}, detach=False)

    @staticmethod
    def _status(snapshot: dict, target: dict) -> dict:
        already = snapshot["digest"] == snapshot_digest(target)
        empty = not target["records"] and not target["revisions"] and target["references"] is None
        if not empty and not already:
            raise CostingConflict("正式成本资料已存在且与迁移包不完全一致，不能覆盖或部分合并。")
        return {"digest": snapshot["digest"], "records": len(snapshot["records"]),
                "revisions": len(snapshot["revisions"]), "items": sum(len(row["items"]) for row in snapshot["records"]),
                "already_imported": already, "can_apply": True}

    def preview(self, snapshot: object) -> dict:
        prepared = _validated_snapshot(snapshot)
        with CostingTransferRepository(self.db_path) as repository:
            return self._status(prepared, repository.read_content())

    def apply(self, snapshot: object, *, actor: str, expected_digest: str) -> dict:
        actor = text(actor, "actor")
        if not actor:
            raise CostingValidationError("迁入成本资料必须记录当前操作人。")
        prepared = _validated_snapshot(snapshot)
        if (not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
                or not hmac.compare_digest(prepared["digest"], expected_digest)):
            raise CostingConflict("成本迁移包已变更，请重新预览核对。")
        with CostingTransferRepository(self.db_path, write=True) as repository:
            result = self._status(prepared, repository.read_content())
            if result["already_imported"]:
                return result
            repository.restore(prepared)
            repository.audit(actor=actor, digest=prepared["digest"], records=result["records"], revisions=result["revisions"])
            repository.commit()
            return result
