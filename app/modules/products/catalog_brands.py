from __future__ import annotations


# Display labels only. Database values, filter keys and export ordering stay original.
BRAND_TRANSLATIONS = {
    "AUDI": "奥迪", "BUICK": "别克", "BYD": "比亚迪", "CADILLAC": "凯迪拉克",
    "CHANGAN": "长安", "CHERY": "奇瑞", "CHEVROLET": "雪佛兰", "CHRYSLER": "克莱斯勒",
    "CITROEN": "雪铁龙", "DACIA": "达契亚", "DAEWOO": "大宇", "DAIHATSU": "大发",
    "DODGE": "道奇", "FAW": "一汽", "FIAT": "菲亚特", "FORD": "福特", "GEELY": "吉利",
    "GREAT WALL": "长城", "HONDA": "本田", "HYUNDAI": "现代", "INFINITI": "英菲尼迪",
    "ISUZU": "五十铃", "JAC": "江淮", "JEEP": "吉普", "KIA": "起亚", "LEXUS": "雷克萨斯",
    "LINCOLN": "林肯", "MAXUS": "上汽大通", "MAZDA": "马自达", "MERCEDES-BENZ": "奔驰",
    "MG": "名爵", "MITSUBISHI": "三菱", "NISSAN": "日产", "OPEL": "欧宝", "PEUGEOT": "标致",
    "PROTON": "宝腾", "RENAULT": "雷诺", "ROEWE": "荣威", "SEAT": "西雅特", "SKODA": "斯柯达",
    "SUBARU": "斯巴鲁", "SUZUKI": "铃木", "TESLA": "特斯拉", "TOYOTA": "丰田",
    "VAUXHALL": "沃克斯豪尔", "VOLKSWAGEN": "大众", "VOLVO": "沃尔沃",
}
ORIGINAL_BRAND_LABELS = ("DS", "GMC")


def catalog_brand_label(value: str, language: str) -> str:
    if language != "zh":
        return value
    return BRAND_TRANSLATIONS.get(" ".join(value.split()).upper(), value)


def format_catalog_brands(value: object, language: str) -> str:
    original = str(value or "")
    if language != "zh":
        return original
    return "\n".join(catalog_brand_label(line.strip(), language) for line in original.splitlines())
