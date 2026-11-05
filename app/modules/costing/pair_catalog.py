"""Preview and explicitly adopt existing cost sheets as left/right pairs."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy

from .domain import CostingConflict, CostingNotFound, CostingValidationError, MAX_ITEMS
from .pair_codes import code_side, mirror_code, mirror_part_code
from .pairing import default_pair_kind, establish_pair, mirror_item_name
from .service import CostingService, _timestamp


def _signature(item: dict, *, mirror: bool) -> tuple[str, ...]:
    codes = [item.get(field, "") or "" for field in ("product_code", "part_code")]
    name = item.get("name") or ""
    if mirror:
        name = mirror_item_name(name)
        codes = [mirror_part_code(code) or code for code in codes]
    return (name, item.get("category") or "", (item.get("mode") or "fixed").strip(), *codes)


def _suggestions(record: dict, peer: dict | None) -> dict:
    if peer is None:
        result = {}
        for item in record["items"]:
            mode = default_pair_kind(item)
            result[item["id"]] = {
                "mode": mode,
                **{"peer_" + key: mirror_part_code(item.get(key) or "") if mode == "handed" else item.get(key, "")
                   for key in ("product_code", "part_code")},
            }
        return result
    # Suggest only unambiguous one-to-one identities. Row position may differ
    # and two bolts with identical descriptions can be different operations.
    targets = defaultdict(list)
    for item in peer["items"]:
        targets[_signature(item, mirror=False)].append(item)
    proposed = {}
    for item in record["items"]:
        found = {target["id"]: target for mirrored in (False, True)
                 for target in targets[_signature(item, mirror=mirrored)]}
        if len(found) == 1 and item.get("name"):
            target_id, target = next(iter(found.items()))
            shared = _signature(item, mirror=False) == _signature(target, mirror=False) and default_pair_kind(item) == "shared"
            proposed[item["id"]] = {"other_id": target_id, "mode": "shared" if shared else "handed"}
    counts = Counter(row["other_id"] for row in proposed.values())
    return {key: row for key, row in proposed.items() if counts[row["other_id"]] == 1}


class CostingPairCatalog:
    def __init__(self, service: CostingService):
        self.service = service

    def candidates(self) -> list[dict]:
        with self.service._uow(False) as uow:
            records = uow.repository.pair_candidates()
        by_code = {row["code"]: row for row in records}
        candidates = []
        for row in records:
            other_code = mirror_code(row["code"])
            if not other_code:
                continue
            other = by_code.get(other_code)
            if other and code_side(row["code"]) == "R":
                continue
            candidates.append({**row, "other_code": other_code, "other_id": other["id"] if other else None,
                               "other_version": other["version"] if other else None})
        return candidates

    @staticmethod
    def _lookup(repo, record_id: object) -> tuple[dict, str, dict | None]:
        if not isinstance(record_id, str):
            raise CostingValidationError("成本单编号无效。")
        record = repo.get(record_id)
        if record is None:
            raise CostingNotFound()
        other_code = mirror_code(record["code"])
        if not other_code:
            raise CostingValidationError("型号需要含有一个可识别的 LA、LB、LC、RA、RB 或 RC 位置。")
        peer = repo.find_code(other_code)
        if record.get("pair") or (peer and peer.get("pair")):
            raise CostingConflict("型号已建立配对，请从成本详情查看对侧。")
        return record, other_code, peer

    def existing(self, record_id: str) -> dict:
        with self.service._uow(False) as uow:
            record, code, peer = self._lookup(uow.repository, record_id)
            return {"record": record, "other": peer, "other_code": code, "suggestions": _suggestions(record, peer)}

    @staticmethod
    def _check_version(record: dict, version: object) -> None:
        if type(version) is not int or version != record["version"]:
            raise CostingConflict("成本单已变化，请重新核对左右件。")

    def mirror_selected(self, selected: list[dict], *, actor: str) -> int:
        actor = self.service._actor(actor)
        if not isinstance(selected, list) or not 1 <= len(selected) <= 500:
            raise CostingValidationError("请勾选 1 至 500 个待镜像的型号。")
        with self.service._uow(True) as uow:
            for selection in selected:
                if not isinstance(selection, dict):
                    raise CostingValidationError("勾选的成本单格式无效。")
                record, _, peer = self._lookup(uow.repository, selection.get("id"))
                self._check_version(record, selection.get("version"))
                if peer:
                    raise CostingConflict("对侧型号已存在，请打开明细逐行核对。")
                rows = selection.get("rows")
                if rows is not None and (not isinstance(rows, list) or len(rows) != len(record["items"])
                                         or not all(isinstance(row, dict) for row in rows)
                                         or {row.get("id") for row in rows if isinstance(row.get("id"), str)}
                                         != {item["id"] for item in record["items"]}):
                    raise CostingValidationError("请核对本张成本单的所有明细。")
                self.service._mirror_in_uow(uow.repository, record, rows, actor)
            uow.commit()
        return len(selected)

    def link_existing(self, record_id: str, *, version: int, other_version: int,
                      matches: list[dict], actor: str) -> dict:
        actor = self.service._actor(actor)
        if not isinstance(matches, list) or len(matches) > MAX_ITEMS:
            raise CostingValidationError("左右件明细对应关系无效。")
        with self.service._uow(True) as uow:
            repo = uow.repository
            source, _, peer = self._lookup(repo, record_id)
            if peer is None:
                raise CostingConflict("对侧型号不存在，请重新预览。")
            self._check_version(source, version)
            self._check_version(peer, other_version)
            selected = {}
            for match in matches:
                if (not isinstance(match, dict) or not isinstance(match.get("id"), str)
                        or not isinstance(match.get("other_id"), str) or match.get("mode") not in ("shared", "handed")
                        or match["id"] in selected):
                    raise CostingValidationError("请为每条对应明细选择有效的关系。")
                selected[match["id"]] = {"id": match["id"], "peer_item_id": match["other_id"],
                                         "pair_kind": match["mode"]}
            if selected.keys() - {item["id"] for item in source["items"]}:
                raise CostingValidationError("选中的明细已变化，请重新核对。")
            rows = [selected.get(item["id"], {"id": item["id"], "pair_kind": "local"}) for item in source["items"]]
            left, right = deepcopy(source), deepcopy(peer)
            establish_pair(left, right, rows, mirror=False)
            timestamp = _timestamp()
            for before, after in ((source, left), (peer, right)):
                after.update(version=before["version"] + 1, updated_at=timestamp, updated_by=actor)
                self.service._save_edit(repo, before, after, actor)
            uow.commit()
        return left
