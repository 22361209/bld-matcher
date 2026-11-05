"""Review and apply one raw sheet-steel rate across confirmed stamping lines."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import contextmanager
from decimal import Decimal

from app.modules.materials.costing_grades import MATERIAL_GRADES, read_costing_grade_rows

from .domain import CostingConflict, CostingValidationError, calculate_cost, displayed_cost
from .material_price_match import MaterialMatches
from .repository import SQLiteCostingUnitOfWork
from .service import CostingService, _timestamp
from .values import decimal_text, number, text


def _price(value):
    return number(value, "unit_price")


def _after(record, changes, new_price):
    inputs = [{**row, "unit_price": new_price} if row["id"] in changes else row
              for row in record["items"]]
    evaluated = calculate_cost({**record, "items": inputs}, previous=record)
    items = []
    for prior, revised in zip(record["items"], evaluated["items"], strict=True):
        if prior["id"] in changes:
            # Keep all identity, left/right links and original workbook evidence.
            items.append({**prior, "unit_price": new_price,
                          "line_total": revised["line_total"], "issues": revised["issues"]})
        else:
            old, new = prior.get("line_total"), revised.get("line_total")
            if (Decimal(old) if old is not None else None) != (Decimal(new) if new is not None else None):
                raise CostingValidationError(f"第 {len(items) + 1} 行未参与调价，按当前规则复算会改变其历史金额，请先核对。")
            items.append(prior)
    return {**record, **evaluated, "items": items}


class MaterialPriceService:
    def __init__(self, costing: CostingService):
        self.costing = costing

    @contextmanager
    def _work(self, write):
        with SQLiteCostingUnitOfWork(self.costing.db_path, write=write) as uow:
            assert uow.connection is not None
            uow.connection.row_factory = sqlite3.Row
            materials = read_costing_grade_rows(uow.connection)
            yield uow, materials

    @staticmethod
    def request(grade, price):
        grade = text(grade, "原材料牌号", 120).upper()
        price = _price(price)
        if grade not in MATERIAL_GRADES or price is None:
            raise CostingValidationError("请选择材料牌号并填写新单价（元/kg）。")
        return {"grade": grade, "price": decimal_text(price)}

    def catalog(self):
        with self._work(False) as (uow, materials):
            matches, reviews, _ = self._matches(uow.repository, materials)
            return {"grades": MATERIAL_GRADES, "counts": Counter(row["grade"] for row in matches.values()),
                    "reviews": reviews}

    @staticmethod
    def _matches(repo, materials):
        lines = repo.weight_price_lines()
        catalog = MaterialMatches(materials, lines)
        matches, reviews = {}, []
        for line in lines:
            material, evidence = catalog.lookup(line)
            if material and material.get("material_grade") in MATERIAL_GRADES:
                matches[line["id"]] = {**line, "grade": material["material_grade"], "material_code": material["code"],
                                       "material_id": material["sync_id"], "evidence": evidence}
            elif evidence:
                reviews.append({"id": line["record_id"], "code": line["code"], "position": line["position"],
                                "name": line["name"], "material_code": material["code"] if material else
                                (line.get("product_code") or line.get("part_code") or ""),
                                "reason": "已匹配材料明细，牌号缺失或无效" if material else evidence})
        return matches, reviews, {line["id"]: line for line in lines}

    def preview(self, change):
        with self._work(False) as (uow, materials):
            result, _ = self._plan(uow.repository, materials, change)
            return result

    def apply(self, change, digest, *, actor):
        actor = self.costing._actor(actor)
        with self._work(True) as (uow, materials):
            result, updates = self._plan(uow.repository, materials, change)
            if digest != result["digest"] or not updates:
                raise CostingConflict("调价范围已变化或已应用，请重新预览。")
            now = _timestamp()
            for before, after in updates:
                saved = {**after, "version": before["version"] + 1, "updated_by": actor, "updated_at": now}
                self.costing._save_edit(uow.repository, before, saved, actor)
            uow.commit()
        return result

    def _plan(self, repo, materials, change):
        grade, price = change["grade"], change["price"]
        matches, reviews, index = self._matches(repo, materials)
        review_reasons = {(row["id"], row["position"]): row["reason"] for row in reviews}
        group = {key: row for key, row in matches.items() if row["grade"] == grade}
        by_record = defaultdict(dict)
        for key, row in group.items():
            if _price(row["unit_price"]) != Decimal(price):
                by_record[row["record_id"]][key] = row
        result = {"grade": grade, "price": price, "matched": len(group), "updated_rows": 0, "updated_models": 0,
                  "details": [], "blocked": [], "audit_peers": [], "reviews": reviews}
        updates = []
        visited = set()
        fingerprint = [[key, row["record_id"], row["version"]] for key, row in group.items()]
        for record_id in by_record:
            if record_id in visited:
                continue
            record = repo.get(record_id)
            peer = CostingService._peer(repo, record)
            members = [record, peer] if peer else [record]
            visited.update(member["id"] for member in members)
            fingerprint.extend([member["id"], member["version"]] for member in members)
            try:
                pending = []
                for member in members:
                    edits = by_record.get(member["id"], {})
                    for row in edits.values():
                        if row["pair_kind"] in {"shared", "handed"}:
                            other = index.get(row["peer_item_id"])
                            if (not other or other["peer_item_id"] != row["id"] or other["record_id"] != row["peer_id"]
                                    or other["pair_kind"] != row["pair_kind"]):
                                raise CostingValidationError(f"{member['code']} 第 {row['position']} 行的左右对应关系不完整。")
                            if other["id"] not in group:
                                peer_grade = matches.get(other["id"], {}).get("grade")
                                reason = (f"材料牌号为 {peer_grade}" if peer_grade else
                                          review_reasons.get((other["record_id"], other["position"]), "未匹配冲压材料"))
                                raise CostingValidationError(
                                    f"{other['code']} 第 {other['position']} 行{reason}，请先核对两侧归属。")
                    after = _after(member, edits, price) if edits else member
                    pending.append((member, after, edits))
            except CostingValidationError as exc:
                result["blocked"].append({"codes": " / ".join(m["code"] for m in members), "reason": exc.message})
                continue
            for before, after, edits in pending:
                updates.append((before, after))
                if not edits:
                    result["audit_peers"].append(before["code"])
                    continue
                result["updated_models"] += 1
                result["updated_rows"] += len(edits)
                old, new = displayed_cost(before), displayed_cost(after)
                result["details"].append({
                    "code": before["code"], "id": before["id"], "old_cost": old["calculated_cost"],
                    "new_cost": new["calculated_cost"], "old_sale": old["suggested_tax_price"],
                    "new_sale": new["suggested_tax_price"], "manual_cost": before.get("approved_cost") is not None,
                    "manual_sale": before.get("suggested_tax_price_override") is not None,
                    "rows": list(edits.values()),
                })
        data = json.dumps([change, fingerprint, result], ensure_ascii=False, sort_keys=True)
        result["digest"] = hashlib.sha256(data.encode()).hexdigest()
        return result, updates
