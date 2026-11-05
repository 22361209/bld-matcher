"""Classify a cost line from its own description and its workbook category."""
from __future__ import annotations

import re
from collections.abc import Mapping
from unicodedata import normalize


_FINISH = r"(?:电泳|电镀|镀(?:锌镍|蓝白|白锌|彩锌|黑锌|白|锌|镍|铬)|喷(?:砂|塑|粉|漆|涂)|磷化|发黑|达克罗|锌镍[蓝彩]?|三价彩|蓝白|阳极氧化|钝化)"
_FINISH_WORK = re.compile(rf"表面处理|(?:表面|送|做){_FINISH}|{_FINISH}(?:处理费|加工费|工费|费用|费)")
_FINISH_AT_END = re.compile(rf"{_FINISH}(?:处理|加工)?$")
_OUTSOURCED_FEES = {"委外拉伸工费", "焊接工费委外"}

# Installation descriptions also occur in the 球头 and 衬套 sections. Distinguish
# 上卡簧 (an upper ring) from 装上卡簧 (installing it), and 焊接螺母 from 焊接工费.
_OPERATION = re.compile(
    r"工费|加工费|制造费|安装|装配|压装(?!式)|铆接|铆压|旋铆|压入(?!式)|装入|打紧|旋螺母|抹油|"
    r"^装|装(?:护|防尘|球|上|下|双层|滚花|胶套|压入式|螺母|[大小]套)|手装|机装|"
    r"^(?:手|机|倒|定位)?压(?:[大小中]套|[小大]?衬套|芯轴|防尘|骨架|螺栓|滚花|球|C型)|"
    r"入球[座壳]|上(?:螺母|防尘罩|球壳卡环|球壳C卡)|打(?:中心)?螺栓"
)

_FASTENER = re.compile(r"(?:螺栓|螺母|螺帽|螺丝|螺钉|铆钉|卡环|卡簧|挡圈|止动环|球壳C卡)(?!支架|座板|垫片|孔|模具)")
_PURCHASED_PART = re.compile(
    r"(?:衬套|胶套|球销|球肖|球座|防尘罩|防尘套|护套)(?![A-Z]型支架)"
    r"(?=$|[^\u3400-\u9fff]|本色|磷化|达克罗|镀|双层|胶芯|铝$)"
)
_PACKING = {"纸箱", "外箱", "内外箱", "外包装", "内包装", "内袋", "内盒", "包装袋", "袋子", "包装(内外)"}
_PURCHASED_CATEGORIES = {
    "衬套", "胶套", "大衬套", "小衬套", "吊杆衬套", "球销", "球肖", "球座", "护套", "防尘罩",
    "标准件", "紧固件", "螺栓", "螺母", "铆钉", "卡环", "球壳卡环", "卡簧", "上卡簧", "下卡簧", "挡圈",
}
_SPECIFICATION = re.compile(r"^(?:[A-Z]{1,6}-?\d|\d)", re.IGNORECASE)
_BALL_SECTION_SPEC = re.compile(
    r"(?:\d{1,3}[#号]|HT\d+|\d{2}球(?:磷化|达克罗|本色)?|[BCD]-\d{3}(?:\.\d+)?|"
    r"(?:GD-?\d{2}|\d{2}[CD])-\d+(?:精品)?|(?:BOLT|BLT-)[A-Z0-9-]+|"
    r"(?:M\d+[×*]\d+(?:\.\d+)?尼龙|白色尼龙防松M\d+[×*]\d+(?:\.\d+)?))", re.IGNORECASE,
)


def suggested_material_attribute(item: Mapping[str, object]) -> str:
    def label(key: str) -> str:
        return "".join(normalize("NFKC", str(item.get(key) or "")).split())

    category, name = label("category"), label("name")
    if name in _OUTSOURCED_FEES or category in {"表面处理", "表明处理"} or _FINISH_WORK.search(name):
        return "委外加工"
    if _OPERATION.search(name) or category == "安装工费":
        return "自制"
    if _FASTENER.search(name) or _PURCHASED_PART.search(name) or name in _PACKING:
        return "外购"
    if category in {"外箱", "纸箱", "外包装", "内包装"} or name.endswith("纸箱") or (
        (item.get("mode") == "package" or category == "包装")
        and re.fullmatch(r"\d+(?:\.\d+)?(?:[xX×*]\d+(?:\.\d+)?){2}(?:cm|mm|厘米|毫米)?", name, re.I)
    ):
        return "外购"
    if category in _PURCHASED_CATEGORIES and (
        not name or _SPECIFICATION.match(name) or name in {"大套", "中套", "小套"}
    ):
        return "外购"
    if category in {"球头", "球头1"} and _BALL_SECTION_SPEC.fullmatch(name):
        return "外购"
    if category == "球座" and re.search(r"\d{2}[CD]-\d+$", name, re.I):
        return "外购"
    if _FINISH_AT_END.search(name):
        return "委外加工"
    return "外购" if category in {"标准件", "紧固件"} else "自制"
