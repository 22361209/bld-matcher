"""Commercial scope of a saved cost line when pricing variants of one product."""
from __future__ import annotations

import re
from unicodedata import normalize

from .line_identity import line_identity_changes


QUOTE_ROLES = {
    "weld": "焊接本体（材料及工费）",
    "ecoat": "本体电泳",
    "assembly": "其余成品零件及装配（含球头、衬套）",
    "inner": "内包装及专用工费",
    "outer": "外包装及专用工费",
    "packing_shared": "任一包装都发生的工费",
    "packing_mixed": "未拆分的内外包装",
    "freight": "成品运费",
}

# Names can qualify a broadly labelled section in historical sheets. For
# example, an inner bag sometimes appears under the ball-joint section.
_BALL_CATEGORIES = {"球头", "球销", "球肖", "球座", "封板", "防尘罩", "球头总成"}
_WELD_CATEGORIES = {"焊接本体", "焊接组件", "焊接件", "冲压件", "冲压组件", "焊接工费", "冲压工费"}
_WELD_NAMES = {"主板", "副板", "加强板", "焊接管", "焊接工费", "冲压工费", "冲压制造费", "委外拉伸工费", "焊接工费委外"}
_ASSEMBLY_CATEGORIES = {"装配", "成品装配", "衬套", "胶套", "大衬套", "小衬套", "配件", "标准件", "紧固件"}
_COAT_NAMES = {"电泳", "电泳费", "电泳工费", "电泳加工费", "本体电泳", "电泳处理", "本体电泳费", "电泳加工"}
_OUTER_NAMES = {"外箱", "纸箱", "外箱分摊", "纸箱分摊"}
_INNER_NAMES = {"内袋", "内盒", "内箱", "内包装", "内衬", "内包装工费"}
_UNALLOCATED_PACKING = {"包装", "包装费", "包装合计", "包装工费", "包装制造费", "包装人工", "包装物料", "包装袋", "打包带", "塑料袋", "泡沫"}
_BOTH_PACKAGES = frozenset(("inner", "outer"))
_OPTION_TERMS = {
    "包装(内外)": _BOTH_PACKAGES, "内外包装": _BOTH_PACKAGES, "内外箱": _BOTH_PACKAGES,
    **dict.fromkeys(("内包装", "内盒", "内箱", "内袋", "内衬"), ("inner",)),
    **dict.fromkeys(("外包装", "外箱", "纸箱"), ("outer",)),
    **dict.fromkeys(("成品运费", "运输费", "运费", "配送费", "物流费", "快递费", "邮费", "送货费"), ("freight",)),
    "电泳": ("ecoat",),
}
_OPTION_TERM = re.compile("|".join(map(re.escape, _OPTION_TERMS)))
_EXCLUDED_TERM = re.compile(rf"(?:不包含|不包括|不含|不带|未含)(?:{_OPTION_TERM.pattern})")
_BUNDLE_WORDS = re.compile(r"(?:[+&/、,()（）]|及|和|与|含|包含|包括|带|费|合计|总计|费用|的)*")
_CATEGORY_OPTION = {"内包装": "inner", "外包装": "outer", "外箱": "outer", "纸箱": "outer",
                    "运费": "freight", "运输费": "freight", "电泳": "ecoat"}
_SHIPPING = {"运费", "运输费", "成品运费", "出货运费", "发货运费", "运费分摊"}
FREIGHT_WORDS = ("运费", "运输", "运杂", "配送", "货运", "转运", "提货", "快递", "物流", "邮费", "送货")
_DELIVERY = re.compile(r"(?:成品|出货|发货|客户)(?:运费|运输费|物流费|快递费|配送费|送货费)")
_BALL_INSTALL = re.compile(r"^(?:压装|安装|压入|装配|装|压)球头(?:总成)?(?:工费)?$")
_SURFACE = re.compile(r"电泳|镀锌|喷塑|喷砂|表面处理")


def _label(value):
    return "".join(normalize("NFKC", str(value or "")).split())


def line_quote_role(item: dict) -> str | frozenset[str]:
    manual = item.get("quote_role")
    if manual in QUOTE_ROLES:
        return _BOTH_PACKAGES if manual == "packing_mixed" else manual
    name, category = _label(item.get("name")), _label(item.get("category"))
    mode = item.get("mode")
    # A total covering several optional charges cannot be assigned to just
    # the column it came from. Only a name made up of those charges and their
    # connectors can safely be dropped when all of them are omitted.
    name = _EXCLUDED_TERM.sub("", name).replace("()", "")
    terms = _OPTION_TERM.findall(name)
    options = frozenset(option for term in terms for option in _OPTION_TERMS[term])
    section = _CATEGORY_OPTION.get(category)
    if options and section and section not in options:
        return "unknown"
    if options - {"inner", "outer"} and re.search("包装|打包", _OPTION_TERM.sub("", name)):
        return "unknown"
    if len(options) > 1:
        return options if _BUNDLE_WORDS.fullmatch(_OPTION_TERM.sub("", name)) else "unknown"
    if name in _INNER_NAMES or category == "内包装":
        return "inner"
    if name in _OUTER_NAMES or line_identity_changes(item).get("name") == "纸箱":
        return "outer"
    if _DELIVERY.fullmatch(name) or (name in _SHIPPING and (not category or category in _SHIPPING)):
        return "freight"
    if name in _SHIPPING:
        # Older sheets sometimes have both a freight row inside 焊接组件
        # and a separate customer delivery row. Their scopes need confirmation.
        return "unknown"
    ball_section = category in _BALL_CATEGORIES or bool(re.fullmatch(r"球头[0-9]+", category))
    if name in _COAT_NAMES:
        if ball_section or category in {"衬套", "胶套", "大衬套", "小衬套"}:
            return "unknown" if "本体" in name else "assembly"
        return "ecoat"
    if _BALL_INSTALL.fullmatch(name) or name in {"球头", "球头总成", "球销", "球座", "防尘罩"}:
        return "assembly"
    if category in _SHIPPING:
        # A charge for transport between processes is not customer delivery.
        upstream = ("委外", "来料", "采购", "进货", "原料", "原材料", "供应商", "电泳", "焊接", "转运", "提货")
        return "unknown" if any(word in name for word in upstream) else "freight"
    if mode == "freight":
        return "unknown" if name not in _SHIPPING else "freight"
    if category in {"外箱", "纸箱", "外包装"} or name == "外包装工费":
        return "outer"
    if name in _UNALLOCATED_PACKING or category in {"包装", "包装工费"} or mode == "package":
        return "packing_unassigned"
    if any(word in name or word in category for word in FREIGHT_WORDS):
        return "unknown"
    if ball_section:
        return "assembly"
    if any(word in name for word in ("包装", "内袋", "外箱")):
        return "unknown"
    if _SURFACE.search(name) or category in {"表面处理", "表明处理", "电泳"}:
        return "unknown"
    if category in _WELD_CATEGORIES or name in _WELD_NAMES:
        return "weld"
    if category in _ASSEMBLY_CATEGORIES or name in {"衬套", "胶套"}:
        return "assembly"
    if category in {"安装", "安装工费"} or name in {"安装工费", "安装制造费"}:
        return "assembly"
    return "unknown"
