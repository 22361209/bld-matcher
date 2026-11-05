"""Resolve cost weights against the material and tube catalogs at selection time."""
from __future__ import annotations

from itsdangerous import BadData, URLSafeSerializer

from .domain import CostingConflict, CostingValidationError, text


class CostWeightLookup:
    def __init__(self, secret: str | bytes, *, materials=None, tubes=None):
        self.signer = URLSafeSerializer(secret, salt="costing-weight-choice-v1")
        self.materials = materials
        self.tubes = tubes
        self.available = materials is not None and tubes is not None

    def _matches(self, code: str, kind: str) -> list[dict]:
        candidates = []
        if self.materials is not None and kind in ("", "stamping"):
            for item in self.materials.costing_candidates(code):
                candidates.append({
                    "kind": "stamping", "product_code": item["code"], "name": item["name"],
                    "model": item["model"], "spec": item["spec"], "source_id": item["sync_id"],
                    "pieces": item["pieces"], "gross_weight": item["gross_weight"],
                    "net_weight": item["net_weight"], "scrap_weight": item["scrap_weight"],
                })
        if self.tubes is not None and kind in ("", "tube"):
            for tube in self.tubes.costing_candidates(code):
                candidates.append({
                    "kind": "tube", "product_code": tube["code"], "name": tube["name"], "model": "",
                    "spec": tube["spec"], "source_id": tube["source_code"], "pieces": None,
                    "gross_weight": tube["gross_weight"], "net_weight": None, "scrap_weight": None,
                })
        return candidates

    def candidates(self, code: object, kind: str = "") -> list[dict]:
        code = text(code, "product_code")
        if not code or kind not in ("", "stamping", "tube"):
            raise CostingValidationError("请输入产品编码并选择有效的资料类型。", "product_code")
        return [{**row, "token": self.signer.dumps(["catalog", row])} for row in self._matches(code, kind)]

    def copy_token(self, snapshot: dict) -> str:
        # Copies retain the figures of the source sheet even if the catalog has since changed.
        return self.signer.dumps(["copy", snapshot])

    def apply(self, items: list) -> None:
        cached: dict[tuple[str, str], list[dict]] = {}
        for row in items:
            if not isinstance(row, dict) or "weight_choice" not in row:
                continue
            choice = row.pop("weight_choice")
            if choice is None or choice == "":
                row["weight_source"] = None
                continue
            if not isinstance(choice, str) or len(choice) > 8192:
                raise CostingValidationError("重量来源无效，请重新按编码取重。", "product_code")
            try:
                origin, source = self.signer.loads(choice)
            except BadData as exc:
                raise CostingValidationError("重量来源无效，请重新按编码取重。", "product_code") from exc
            code = text(row.get("product_code"), "product_code")
            if source["product_code"].casefold() != code.casefold() or row.get("mode") != "weight":
                raise CostingValidationError("已选重量与当前编码或计价方式不符，请重新取重。", "product_code")
            if origin == "catalog":
                key = (code.casefold(), source["kind"])
                if key not in cached:
                    cached[key] = self._matches(code, source["kind"])
                if source not in cached[key]:
                    raise CostingConflict("材料或管件资料已变化，请重新按编码取重。")
            row["weight_source"] = source
