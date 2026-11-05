"""Match current costing rows to the active stamping material master without guessing a steel grade."""
from __future__ import annotations

from collections import Counter, defaultdict
import re
import unicodedata

from .pair_codes import mirror_part_code


def _key(value):
    # "440" and "４４０" identify different materials and can imply different grades.
    return str(value or "").strip().casefold()


def _label(value):
    return _key(unicodedata.normalize("NFKC", str(value or "")))


def _model_keys(code):
    code = _key(code)
    match = re.fullmatch(r"[a-z]+([0-9]{4})[lr]?[abc](?:[0-9-]+)?", code)
    return [code, match[1]] if match else [code]


def _is_other_charge(row):
    category, name = _label(row["category"]), _label(row["name"])
    if category in {"管件", "焊接管", "表面处理", "表明处理", "包装", "外包装", "内包装", "运费", "运输",
                    "衬套", "胶套", "大衬套", "小衬套"}:
        return True
    return name.endswith(("管", "管件", "衬套", "胶套", "球头总成")) or any(
        word in name for word in ("电泳", "电镀", "运费", "运输", "工费", "加工费", "废料", "纸箱", "包装")
    )


class MaterialMatches:
    def __init__(self, materials, lines):
        self.by_id = {row["sync_id"]: row for row in materials}
        self.by_code = defaultdict(list)
        self.by_model_name = defaultdict(list)
        self.name_counts = Counter((row["record_id"], _label(row["name"])) for row in lines)
        for row in materials:
            self.by_code[_key(row["code"])].append(row)
            self.by_model_name[_key(row["model"]), _label(row["part"])].append(row)

    def lookup(self, row):
        """Return (material, evidence/review reason); no reason denotes a non-stamping charge."""
        source = row["weight_source.kind"]
        if source:
            if source != "stamping":
                return None, ""
            material = self.by_id.get(row["weight_source.source_id"])
            if not material:
                return None, "原取重材料已停用或不存在"
            if _key(material["code"]) != _key(row["weight_source.product_code"]):
                return None, "原取重来源的材料编码已变化"
            return material, "已确认取重来源"
        if _is_other_charge(row):
            return None, ""
        codes = {_key(row[key]) for key in ("product_code", "part_code") if _key(row[key])}
        if len(codes) > 1:
            return None, "行内产品编码与零件编码不同，请确认取重来源"
        models, name = _model_keys(row["code"]), _label(row["name"])
        if codes:
            candidates = self.by_code[codes.pop()]
            if len(candidates) == 1:
                return candidates[0], "唯一材料编码"
            if not candidates:
                return None, "材料明细中没有此编码"
            for model in models:
                matched = [candidate for candidate in candidates if _key(candidate["model"]) == model
                           and name and _label(candidate["part"]) == name]
                if len(matched) == 1:
                    return matched[0], "编码、母件及名称一致"
                if matched:
                    break
            return None, "同编码的材料明细有多条，请确认取重来源"
        if not name:
            return None, "成本行缺少材料编码和零件名称"
        if self.name_counts[row["record_id"], name] > 1:
            return None, "成本单有多行同名零件，请确认材料编码或取重来源"
        for model in models:
            matched = self.by_model_name[model, name]
            if len(matched) > 1:
                return None, "同母件及零件名称的材料有多条，请确认取重来源"
            if matched:
                if mirror_part_code(str(matched[0]["code"] or "")):
                    return None, "材料编码区分左右，请确认本行零件编码或取重来源"
                return matched[0], "母件及零件名称唯一对应"
        return None, "材料明细中没有与此母件及零件名称对应的启用材料"
