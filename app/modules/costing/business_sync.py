"""Exchange costing records inside a caller-owned business-sync transaction."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import sqlite3

from .domain import CostingValidationError
from .repository import SQLiteCostingRepository
from .transfer_pairs import group_conflicts
from .transfer_repository import read_content
from .transfer_service import _encoded, _validated_snapshot, snapshot_digest


class _ValidatedSnapshot(dict):
    """Reuse expensive history validation for the same request-owned archive tree."""

    def __init__(self, content: dict) -> None:
        super().__init__(content)
        self.verified_digest = content["digest"]


def validate_costing(snapshot: object) -> dict:
    return _ValidatedSnapshot(_validated_snapshot(snapshot, allow_empty=True, detach=False))


def export_costing(connection: sqlite3.Connection) -> dict:
    content = read_content(connection)
    return validate_costing({**content, "digest": snapshot_digest(content)})


def costing_state(connection: sqlite3.Connection) -> str:
    """Include even history-only changes in the outer package's stale-preview token."""
    state = hashlib.sha256()
    for query in ("SELECT * FROM costing_records ORDER BY id",
                  "SELECT * FROM costing_revisions ORDER BY id",
                  "SELECT * FROM costing_reference_data ORDER BY name"):
        state.update(query.encode("ascii"))
        for row in connection.execute(query):
            encoded = _encoded(tuple(row))
            state.update(len(encoded).to_bytes(8, "big"))
            state.update(encoded)
    return state.hexdigest()


def current_stamping_source_ids(connection: sqlite3.Connection) -> set[str]:
    """Material identities referenced by current cost lines; no prices or historical revisions are exposed."""
    return {row[0] for row in connection.execute("""
        SELECT DISTINCT json_extract(item.value, '$.weight_source.source_id')
        FROM costing_records AS record, json_each(record.payload_json, '$.items') AS item
        WHERE json_extract(item.value, '$.weight_source.kind') = 'stamping'
          AND json_type(item.value, '$.weight_source.source_id') = 'text'
    """)}


def _histories(revisions: list[dict]) -> dict[str, list[dict]]:
    result = defaultdict(list)
    for revision in sorted(revisions, key=lambda row: row["version"]):
        result[revision["record_id"]].append(revision)
    return result


def _history_status(record: dict, local: dict | None, incoming_history: list, local_history: list) -> tuple[str, str]:
    if local is None:
        return "new", ""
    if (len(local_history) != local["version"] or not local_history
            or _encoded(local_history[-1]["after"]) != _encoded(local)):
        raise CostingValidationError(f"本机型号 {local['code']} 与修改历史不一致，请核对后再同步。")
    # Revision IDs are SQLite row numbers allocated independently on each device.
    # Compare every common version including actor, action, source and exact amounts.
    for incoming, current in zip(incoming_history, local_history):
        if _encoded({k: v for k, v in incoming.items() if k != "id"}) != _encoded({k: v for k, v in current.items() if k != "id"}):
            return "conflict", "两端的修改历史存在分歧，已保留本机成本单。"
    if record["version"] < local["version"]:
        return "stale", ""
    if record["version"] > local["version"]:
        return "updated", ""
    return "unchanged", ""


def _plan(connection: sqlite3.Connection, snapshot: dict):
    if not isinstance(snapshot, _ValidatedSnapshot):
        snapshot = validate_costing(snapshot)
    elif snapshot.get("digest") != snapshot.verified_digest or snapshot_digest(snapshot) != snapshot.verified_digest:
        raise CostingValidationError("已核对的成本数据包发生变化，请重新上传。")
    target = read_content(connection)
    local_records = {row["id"]: row for row in target["records"]}
    codes = {row["code"].casefold(): row for row in local_records.values()}
    owners = {item["id"]: revision["record_id"] for revision in target["revisions"] for item in revision["after"]["items"]}
    local_history = _histories(target["revisions"])
    incoming_history = _histories(snapshot["revisions"])
    counts = dict(new=0, updated=0, unchanged=0, conflict=0, stale=0, ignored_inactive=0)
    rows, conflicts, writes = [], [], []

    def describe(status, key, label, reason="", *, local_time="", incoming_time="", peer_code=""):
        counts[status] += 1
        row = {"status": status, "key": key, "label": label, "local_updated_at": local_time,
               "incoming_updated_at": incoming_time, "peer_code": peer_code}
        if status == "conflict":
            conflicts.append({**row, "selectable": key == "references", "reason": reason, "fields": [], "all_fields": []})
        elif status in {"new", "updated"}:
            rows.append(row)

    statuses, reasons, local_times = {}, {}, {}
    incoming = {row["id"]: row for row in snapshot["records"]}
    for record in incoming.values():
        key = record["id"]
        local = local_records.get(key)
        revisions = incoming_history[key]
        status, reason = _history_status(record, local, revisions, local_history.get(key, []))
        if status in {"new", "updated"}:
            other = codes.get(record["code"].casefold())
            if other is not None and other["id"] != key:
                local = other
                status, reason = "conflict", "本机已有相同型号的另一张成本单。"
            elif any(owners.get(item["id"], key) != key for revision in revisions for item in revision["after"]["items"]):
                status, reason = "conflict", "明细编号已用于本机另一张成本单的现有或历史明细。"
        statuses[key], reasons[key], local_times[key] = status, reason, local["updated_at"] if local else ""

    for key, reason in group_conflicts(snapshot["records"], local_records, statuses).items():
        if statuses[key] != "conflict":
            statuses[key], reasons[key] = "conflict", reason
    for record in incoming.values():
        key = record["id"]
        status = statuses[key]
        describe(status, key, record["code"], reasons[key], local_time=local_times[key],
                 incoming_time=record["updated_at"],
                 peer_code=incoming[record["pair"]["peer_id"]]["code"] if record.get("pair") else "")
        if status in {"new", "updated"}:
            local = local_records.get(key)
            prior_version = local["version"] if local else 0
            writes.append((record, prior_version, incoming_history[key][prior_version:]))

    refs = snapshot["references"]
    reference_status = None
    if refs is not None:
        if target["references"] is None:
            reference_status = "new"
        elif _encoded(target["references"]) == _encoded(refs):
            reference_status = "unchanged"
        else:
            reference_status = "conflict"
        describe(reference_status, "references", "成本参考资料", "两端的成本参考资料不同，可选择采用包内参考资料。")
    for section in (rows, conflicts):
        section.sort(key=lambda row: (min(row["label"], row["peer_code"] or row["label"]), row["label"]))
    summary = {"label": "成本核算", "counts": counts, "rows": rows, "conflicts": conflicts,
               "pairs": sum(1 for record in incoming.values() if record.get("pair")) // 2,
               "records": len(snapshot["records"]), "items": sum(len(r["items"]) for r in snapshot["records"]),
               "revisions": len(snapshot["revisions"]), "has_references": refs is not None}
    return summary, writes, reference_status


def preview_costing(connection: sqlite3.Connection, snapshot: dict) -> dict:
    summary, _, _ = _plan(connection, snapshot)
    return summary


def apply_costing(connection: sqlite3.Connection, snapshot: dict, selected_conflicts: set[str]) -> dict:
    """Only append to a shared history; the caller controls commit, rollback, backup, and audit."""
    if not connection.in_transaction:
        raise RuntimeError("成本同步需要在业务数据同步事务内执行。")
    summary, writes, reference_status = _plan(connection, snapshot)
    allowed = {"references"} if reference_status == "conflict" else set()
    if not selected_conflicts.issubset(allowed):
        raise CostingValidationError("成本冲突选择无效，请重新预览。")
    repository = SQLiteCostingRepository(connection)
    for record, prior_version, revisions in writes:
        if prior_version == 0:
            repository.insert(record)
        elif not repository.update(record, expected_version=prior_version):
            raise CostingValidationError("成本单在预览后已改变，请重新预览。")
        for revision in revisions:
            repository.add_revision(revision["before"], revision["after"], actor=revision["actor"], action=revision["action"])
    counts = summary["counts"]
    if reference_status == "new" or selected_conflicts:
        repository.save_references(snapshot["references"])
        if selected_conflicts:
            counts["conflict"] -= 1
            counts["updated"] += 1
    return counts
