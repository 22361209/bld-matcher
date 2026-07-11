from __future__ import annotations


DEFAULT_BUYER_NAME = "玉环博莱德机械有限公司"
DEFAULT_DELIVERY_ADDRESS = "浙江省玉环市金汇路11号"
DEFAULT_PRICE_NOTE = "以上价格为含税价（增值税税率13%），含包装费及运费，送达甲方指定地点。"
DEFAULT_PAYMENT_TERMS = "月结 30 天"
DEFAULT_QUALITY_TERMS = "\n".join(
    [
        "1. 尺寸以OE样件为准。性能以行业主流标准为准。",
        "2. 乙方交货时须随货提供：出厂检验报告、材质检测报告（如适用）。",
        "3. 产品外观应无裂纹、变形、锈蚀、毛刺、碰伤、划痕等缺陷；关键尺寸公差符合图纸规定。",
        "4. 质保期为甲方收货验收合格之日起 12 个月。质保期内因产品质量问题导致的损失由乙方承担。",
    ]
)
DEFAULT_SALES_PRICE_NOTE = "以上价格为含税价（增值税税率13%），含包装费及运费，送达乙方指定地点。"
DEFAULT_SALES_PAYMENT_TERMS = "□ 预付 ____ %，发货前付清余款　□ 货到验收合格后 ____ 日内付清　□ 月结 ____ 天"
DEFAULT_SALES_QUALITY_TERMS = "\n".join(
    [
        "1. 产品质量应符合甲方的技术图纸或封样样品。",
        "2. 产品外观应无裂纹、变形、锈蚀等缺陷；关键尺寸公差符合图纸规定。",
        "3. 质保期为乙方收货验收合格之日起 12 个月。质保期内确属甲方产品质量问题的，甲方负责退换货或维修。",
    ]
)
