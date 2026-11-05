"""Preview and atomically adopt verified original plate weights in the formal application."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from itsdangerous import BadData, URLSafeTimedSerializer

from .domain import CostingConflict, CostingValidationError
from .historical_cost_amounts import plan_historical_costs
from .historical_materials import extract_estimated_rows, material_code_evidence, material_match_key, plan_materials
from .import_references import read_references
from .import_values import WorkbookImportError
from .historical_scrap_repository import HistoricalScrapUnitOfWork
from .service import CostingService, _timestamp
from .trial_import_service import MAX_WORKBOOK_BYTES, PREVIEW_TTL, TrialImportService
from .workbook_import import load_workbook_values


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _source(upload) -> dict:
    if not upload or not str(upload.filename or "").lower().endswith(".xlsx"):
        raise CostingValidationError("请选择当初导入的成本核算 .xlsx 工作簿。")
    with TemporaryDirectory(prefix="costing-scrap-") as temp:
        path = Path(temp) / "source.xlsx"
        upload.save(path)
        if path.stat().st_size > MAX_WORKBOOK_BYTES:
            raise CostingValidationError("工作簿超过 12 MB。")
        TrialImportService._validate_archive(path)
        try:
            values = load_workbook_values(path, sheets_to_read={"片件重量"})
            refs = read_references(values)["scrap"]
            if not refs:
                raise CostingValidationError("工作簿缺少片件重量明细。")
            estimated = extract_estimated_rows(path)
        except WorkbookImportError as exc:
            raise CostingValidationError(str(exc)) from exc
        return {"sha": hashlib.sha256(path.read_bytes()).hexdigest(),
                "refs": _digest(refs), "estimated": sorted(estimated)}


class HistoricalScrapService:
    def __init__(self, db_path: Path, secret: str | bytes):
        self.db_path = db_path
        self.signer = URLSafeTimedSerializer(secret, salt="verified-historical-scrap-v1")

    @staticmethod
    def _plan(uow: HistoricalScrapUnitOfWork, source: dict):
        cost_repo = uow.repository
        material_repo = uow.materials
        refs = cost_repo.references().get("scrap", [])
        if _digest(refs) != source["refs"]:
            raise CostingConflict("片件重量资料与当前导入记录不一致，请选择当初导入的工作簿。")
        all_costs = cost_repo.list(q="", kind="", status="", sort="code", limit=-1, offset=0)
        records = [r for r in all_costs if r.get("source_info", {}).get("sha256") == source["sha"]]
        if not records:
            raise CostingValidationError("原工作簿指纹与当前成本单不一致。")
        estimated = set(source["estimated"])
        for ref in refs:
            ref["estimated"] = ref["source_info"]["row"] in estimated
        materials = material_repo.active_for_weight_backfill()
        updates, matches, reviews = plan_materials(refs, materials, code_evidence=material_code_evidence(records))
        changes, cost_reviews = plan_historical_costs(records, matches, cost_repo.revisions)
        rows_by_id = {row["id"]: row for row in materials}
        evidence = {material_match_key(ref["code"], ref["name"]): ref["source_info"]["row"] for ref in refs}
        material_edits = []
        for item_id, net in updates.items():
            row = rows_by_id[item_id]
            matched_row = evidence[material_match_key(row["model"], row["part"])]
            material_edits.append({"id": item_id, "model": row["model"], "code": row["code"], "part": row["part"],
                                   "net_weight": net, "source_row": matched_row})
        changes_summary = [{"code": before["code"], "old_theory": before.get("theoretical_scrap"),
                            "new_theory": after.get("theoretical_scrap"), "old_credit": before.get("scrap_credit"),
                            "new_credit": after.get("scrap_credit")} for before, after in changes]
        summary = {"material_count": len(material_edits), "cost_count": len(changes),
                   "materials": material_edits, "costs": changes_summary,
                   "reviews": [{"scope": "材料", **r} for r in reviews] + [{"scope": "成本", **r} for r in cost_reviews]}
        # Include material revisions/specs and cost versions/row snapshots, even if the rounded totals agree.
        signature = _digest([summary, [[rows_by_id[m["id"]], m["net_weight"]] for m in material_edits],
                             [[b["id"], b["version"], _digest(a)] for b, a in changes]])
        return summary, changes, signature

    def preview(self, upload, *, owner: str) -> dict:
        source = _source(upload)
        with HistoricalScrapUnitOfWork(self.db_path) as uow:
            summary, _, signature = self._plan(uow, source)
        summary["digest"] = self.signer.dumps([owner, source, signature])
        return summary

    def apply(self, digest: str, *, actor: str, owner: str) -> dict:
        try:
            if not isinstance(digest, str) or len(digest) > 32000:
                raise BadData("malformed")
            signed_owner, source, expected = self.signer.loads(digest, max_age=PREVIEW_TTL)
        except BadData as exc:
            raise CostingValidationError("预览无效或已过期，请重新核对原工作簿。") from exc
        if signed_owner != owner:
            raise CostingValidationError("请用预览时的账号和登录会话应用。")
        with HistoricalScrapUnitOfWork(self.db_path, write=True) as uow:
            summary, changes, actual = self._plan(uow, source)
            if expected != actual:
                raise CostingConflict("材料或成本记录已变化，请重新预览。")
            for row in summary["materials"]:
                evidence = f"原表 {source['sha'][:12]} 片件重量!E{row['source_row']}"
                if not uow.materials.fill_missing_net_weight(row, row["net_weight"], evidence=evidence, actor=actor):
                    raise CostingConflict("材料净重已变更，请重新预览。")
            now = _timestamp()
            for before, after in changes:
                after.update(version=before["version"] + 1, updated_at=now, updated_by=actor)
                CostingService._save_edit(uow.repository, before, after, actor)
            if summary["material_count"] or changes:
                uow.materials.audit("按原表净重补录并重算废料", "costing", source["sha"],
                                    f"材料 {summary['material_count']} 条，成本单 {len(changes)} 张", actor=actor)
            uow.commit()
        return {"material_count": summary["material_count"], "cost_count": summary["cost_count"]}
