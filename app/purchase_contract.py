from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PDF_FONT = "STSong-Light"
PDF_ASCII_FONT = "Arial"
PDF_ASCII_FALLBACK_FONT = "Helvetica"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_ASCII_FONT_CANDIDATES = (
    PROJECT_ROOT / "data" / "fonts" / "Arial.ttf",
    PROJECT_ROOT / "static" / "fonts" / "Arial.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/arial.ttf"),
)
_pdf_ascii_font_name = PDF_ASCII_FALLBACK_FONT
MONEY_QUANT = Decimal("0.01")
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


def _text(value: object) -> str:
    return str(value or "").strip()


def _money(value: Decimal) -> str:
    return f"{value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP):,.2f}"


def _quantity(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rmb_upper(value: Decimal) -> str:
    value = value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if value < 0:
        raise ValueError("金额不能为负数。")
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ["", "拾", "佰", "仟"]
    sections = ["", "万", "亿", "兆"]

    def section_to_upper(number: int) -> str:
        result = ""
        zero_pending = False
        for index in range(4):
            digit = number % 10
            if digit:
                if zero_pending:
                    result = digits[0] + result
                    zero_pending = False
                result = digits[digit] + units[index] + result
            elif result:
                zero_pending = True
            number //= 10
        return result

    yuan = int(value)
    fraction = int((value - Decimal(yuan)) * 100)
    if yuan == 0:
        yuan_text = "零元"
    else:
        parts = []
        section_index = 0
        zero_pending = False
        while yuan:
            section = yuan % 10000
            if section:
                prefix = digits[0] if zero_pending and parts else ""
                parts.append(prefix + section_to_upper(section) + sections[section_index])
                zero_pending = section < 1000
            elif parts:
                zero_pending = True
            yuan //= 10000
            section_index += 1
        yuan_text = "".join(reversed(parts)) + "元"
    jiao, fen = divmod(fraction, 10)
    if not fraction:
        return yuan_text + "整"
    fraction_text = ""
    if jiao:
        fraction_text += digits[jiao] + "角"
    elif yuan:
        fraction_text += "零"
    if fen:
        fraction_text += digits[fen] + "分"
    return yuan_text + fraction_text


def _parse_decimal(value: object, label: str, *, positive: bool = False, allow_zero: bool = True) -> Decimal:
    text = _text(value)
    if not text:
        raise ValueError(f"{label}不能为空。")
    try:
        number = Decimal(text.replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{label}必须是数字：{text}") from exc
    if positive and number <= 0:
        raise ValueError(f"{label}必须大于 0。")
    if not allow_zero and number == 0:
        raise ValueError(f"{label}不能为 0。")
    if number < 0:
        raise ValueError(f"{label}不能为负数。")
    return number


def default_contract_no(username: str = "") -> str:
    user_part = _text(username) or "user"
    return f"CG-{datetime.now().strftime('%y%m%d-%H%M')}-{user_part}"


def default_sales_contract_no(username: str = "") -> str:
    user_part = _text(username) or "user"
    return f"BLZ-XS-{datetime.now().strftime('%Y%m%d-%H%M')}-{user_part}"


def purchase_contract_from_form(form) -> dict:
    contract = {
        "contract_no": _text(form.get("contract_no")),
        "contract_date": _text(form.get("contract_date")) or date.today().isoformat(),
        "buyer_name": _text(form.get("buyer_name")) or DEFAULT_BUYER_NAME,
        "buyer_contact": _text(form.get("buyer_contact")),
        "buyer_phone": _text(form.get("buyer_phone")),
        "supplier_name": _text(form.get("supplier_name")),
        "supplier_contact": _text(form.get("supplier_contact")),
        "supplier_phone": _text(form.get("supplier_phone")),
        "buyer_signature_address": _text(form.get("buyer_signature_address")),
        "supplier_signature_address": _text(form.get("supplier_signature_address")),
        "buyer_signature_phone": _text(form.get("buyer_signature_phone")),
        "supplier_signature_phone": _text(form.get("supplier_signature_phone")),
        "buyer_bank": _text(form.get("buyer_bank")),
        "supplier_bank": _text(form.get("supplier_bank")),
        "buyer_bank_account": _text(form.get("buyer_bank_account")),
        "supplier_bank_account": _text(form.get("supplier_bank_account")),
        "buyer_signature_date": _text(form.get("buyer_signature_date")),
        "supplier_signature_date": _text(form.get("supplier_signature_date")),
        "delivery_address": _text(form.get("delivery_address")) or DEFAULT_DELIVERY_ADDRESS,
        "payment_terms": _text(form.get("payment_terms")) or DEFAULT_PAYMENT_TERMS,
        "price_note": _text(form.get("price_note")) or DEFAULT_PRICE_NOTE,
        "quality_terms": _text(form.get("quality_terms")) or DEFAULT_QUALITY_TERMS,
        "remark": _text(form.get("remark")),
        "items": [],
    }
    if not contract["contract_no"]:
        raise ValueError("合同编号不能为空。")
    if not contract["supplier_name"]:
        raise ValueError("供应商不能为空。")

    codes = form.getlist("product_code[]")
    oe_numbers = form.getlist("oe_no[]")
    names = form.getlist("product_name[]")
    models = form.getlist("models[]")
    quantities = form.getlist("quantity[]")
    prices = form.getlist("unit_price[]")
    deliveries = form.getlist("delivery_date[]")
    notes = form.getlist("item_note[]")
    row_count = max(len(codes), len(oe_numbers), len(names), len(models), len(quantities), len(prices), len(deliveries), len(notes))

    total = Decimal("0")
    total_quantity = Decimal("0")
    for index in range(row_count):
        code = _text(codes[index] if index < len(codes) else "")
        oe_no = _text(oe_numbers[index] if index < len(oe_numbers) else "")
        name = _text(names[index] if index < len(names) else "")
        model_text = _text(models[index] if index < len(models) else "")
        quantity_text = _text(quantities[index] if index < len(quantities) else "")
        price_text = _text(prices[index] if index < len(prices) else "")
        delivery = _text(deliveries[index] if index < len(deliveries) else "")
        note = _text(notes[index] if index < len(notes) else "")
        if not any([code, oe_no, name, model_text, quantity_text, price_text, delivery, note]):
            continue
        row_label = f"第 {index + 1} 行"
        if not code:
            raise ValueError(f"{row_label}型号不能为空。")
        quantity = _parse_decimal(quantity_text, f"{row_label}数量", positive=True)
        unit_price = _parse_decimal(price_text, f"{row_label}单价")
        amount = (quantity * unit_price).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_quantity += quantity
        total += amount
        contract["items"].append(
            {
                "product_code": code,
                "oe_no": oe_no,
                "product_name": name,
                "models": model_text,
                "image_path": "",
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "delivery_date": delivery,
                "note": note,
            }
        )

    if not contract["items"]:
        raise ValueError("请至少填写一条采购明细。")

    contract["total_amount"] = total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    contract["total_quantity"] = total_quantity
    contract["total_amount_upper"] = _rmb_upper(contract["total_amount"])
    return contract


def sales_contract_from_form(form) -> dict:
    contract = {
        "contract_no": _text(form.get("contract_no")),
        "contract_date": _text(form.get("contract_date")) or date.today().isoformat(),
        "seller_name": _text(form.get("seller_name") or form.get("buyer_name")) or DEFAULT_BUYER_NAME,
        "seller_contact": _text(form.get("seller_contact") or form.get("buyer_contact")),
        "seller_phone": _text(form.get("seller_phone") or form.get("buyer_phone")),
        "customer_name": _text(form.get("customer_name") or form.get("supplier_name")),
        "customer_contact": _text(form.get("customer_contact") or form.get("supplier_contact")),
        "customer_phone": _text(form.get("customer_phone") or form.get("supplier_phone")),
        "seller_credit_code": _text(form.get("seller_credit_code")),
        "customer_credit_code": _text(form.get("customer_credit_code")),
        "seller_signature_address": _text(form.get("seller_signature_address") or form.get("buyer_signature_address")),
        "customer_signature_address": _text(form.get("customer_signature_address") or form.get("supplier_signature_address")),
        "seller_signature_phone": _text(form.get("seller_signature_phone") or form.get("buyer_signature_phone")),
        "customer_signature_phone": _text(form.get("customer_signature_phone") or form.get("supplier_signature_phone")),
        "seller_fax": _text(form.get("seller_fax")),
        "customer_fax": _text(form.get("customer_fax")),
        "seller_bank": _text(form.get("seller_bank") or form.get("buyer_bank")),
        "customer_bank": _text(form.get("customer_bank") or form.get("supplier_bank")),
        "seller_bank_account": _text(form.get("seller_bank_account") or form.get("buyer_bank_account")),
        "customer_bank_account": _text(form.get("customer_bank_account") or form.get("supplier_bank_account")),
        "seller_signature_date": _text(form.get("seller_signature_date") or form.get("buyer_signature_date")),
        "customer_signature_date": _text(form.get("customer_signature_date") or form.get("supplier_signature_date")),
        "delivery_address": _text(form.get("delivery_address")),
        "payment_terms": _text(form.get("payment_terms")) or DEFAULT_SALES_PAYMENT_TERMS,
        "price_note": _text(form.get("price_note")) or DEFAULT_SALES_PRICE_NOTE,
        "quality_terms": _text(form.get("quality_terms")) or DEFAULT_SALES_QUALITY_TERMS,
        "remark": _text(form.get("remark")),
        "items": [],
    }
    if not contract["contract_no"]:
        raise ValueError("合同编号不能为空。")
    if not contract["customer_name"]:
        raise ValueError("需方不能为空。")

    codes = form.getlist("product_code[]")
    customer_codes = form.getlist("customer_code[]")
    oe_numbers = form.getlist("oe_no[]")
    names = form.getlist("product_name[]")
    models = form.getlist("models[]")
    quantities = form.getlist("quantity[]")
    prices = form.getlist("unit_price[]")
    deliveries = form.getlist("delivery_date[]")
    notes = form.getlist("item_note[]")
    row_count = max(
        len(codes),
        len(customer_codes),
        len(oe_numbers),
        len(names),
        len(models),
        len(quantities),
        len(prices),
        len(deliveries),
        len(notes),
    )

    total = Decimal("0")
    total_quantity = Decimal("0")
    for index in range(row_count):
        code = _text(codes[index] if index < len(codes) else "")
        customer_code = _text(customer_codes[index] if index < len(customer_codes) else "")
        oe_no = _text(oe_numbers[index] if index < len(oe_numbers) else "")
        name = _text(names[index] if index < len(names) else "")
        model_text = _text(models[index] if index < len(models) else "")
        quantity_text = _text(quantities[index] if index < len(quantities) else "")
        price_text = _text(prices[index] if index < len(prices) else "")
        delivery = _text(deliveries[index] if index < len(deliveries) else "")
        note = _text(notes[index] if index < len(notes) else "")
        if not any([code, customer_code, oe_no, name, model_text, quantity_text, price_text, delivery, note]):
            continue
        row_label = f"第 {index + 1} 行"
        if not code:
            raise ValueError(f"{row_label}型号不能为空。")
        quantity = _parse_decimal(quantity_text, f"{row_label}数量", positive=True)
        unit_price = _parse_decimal(price_text, f"{row_label}单价")
        amount = (quantity * unit_price).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_quantity += quantity
        total += amount
        contract["items"].append(
            {
                "product_code": code,
                "customer_code": customer_code,
                "oe_no": oe_no,
                "product_name": name,
                "models": model_text,
                "image_path": "",
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "delivery_date": delivery,
                "note": note,
            }
        )

    if not contract["items"]:
        raise ValueError("请至少填写一条销售明细。")

    contract["total_amount"] = total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    contract["total_quantity"] = total_quantity
    contract["total_amount_upper"] = _rmb_upper(contract["total_amount"])
    return contract


def _font_registered(name: str) -> bool:
    try:
        pdfmetrics.getFont(name)
        return True
    except KeyError:
        return False


def _register_pdf_fonts() -> str:
    global _pdf_ascii_font_name
    if not _font_registered(PDF_FONT):
        pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT))

    if _font_registered(PDF_ASCII_FONT):
        _pdf_ascii_font_name = PDF_ASCII_FONT
        return _pdf_ascii_font_name

    for candidate in PDF_ASCII_FONT_CANDIDATES:
        if not candidate.exists():
            continue
        pdfmetrics.registerFont(TTFont(PDF_ASCII_FONT, str(candidate)))
        _pdf_ascii_font_name = PDF_ASCII_FONT
        return _pdf_ascii_font_name

    _pdf_ascii_font_name = PDF_ASCII_FALLBACK_FONT
    return _pdf_ascii_font_name


def _styles() -> dict[str, ParagraphStyle]:
    ascii_font = _register_pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ContractTitle",
            parent=base["Title"],
            fontName=PDF_FONT,
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "body": ParagraphStyle(
            "ContractBody",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10.5,
            leading=15,
        ),
        "body_indent": ParagraphStyle(
            "ContractBodyIndent",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10.5,
            leading=15,
            firstLineIndent=21,
        ),
        "small": ParagraphStyle(
            "ContractSmall",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=9,
            leading=12,
        ),
        "right": ParagraphStyle(
            "ContractRight",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10,
            leading=14,
            alignment=TA_RIGHT,
        ),
        "supplier_detail": ParagraphStyle(
            "ContractSupplierDetail",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10.5,
            leading=15,
        ),
        "table": ParagraphStyle(
            "ContractTable",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=6.8,
            leading=8.4,
        ),
        "table_center": ParagraphStyle(
            "ContractTableCenter",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=6.8,
            leading=8.4,
            alignment=TA_CENTER,
        ),
        "table_right": ParagraphStyle(
            "ContractTableRight",
            parent=base["BodyText"],
            fontName=ascii_font,
            fontSize=6.8,
            leading=8.4,
            alignment=TA_RIGHT,
        ),
    }


def _p(text: object, style: ParagraphStyle) -> Paragraph:
    def escape(segment: str) -> str:
        return segment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    raw = _text(text)
    parts: list[str] = []
    buffer: list[str] = []
    ascii_mode: bool | None = None

    def flush() -> None:
        nonlocal buffer, ascii_mode
        if not buffer:
            return
        segment = "".join(buffer)
        escaped = escape(segment)
        if ascii_mode and segment.strip():
            parts.append(f'<font name="{_pdf_ascii_font_name}">{escaped}</font>')
        else:
            parts.append(escaped)
        buffer = []

    for char in raw:
        if char == "\n":
            flush()
            parts.append("<br/>")
            ascii_mode = None
            continue
        is_ascii = 32 <= ord(char) <= 126
        if ascii_mode is not None and is_ascii != ascii_mode:
            flush()
        ascii_mode = is_ascii
        buffer.append(char)
    flush()
    return Paragraph("".join(parts) or "&nbsp;", style)


def generate_purchase_contract_pdf(contract: dict, output_path: Path) -> None:
    styles = _styles()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=11 * mm,
        bottomMargin=11 * mm,
        title=f"采购合同 {contract['contract_no']}",
    )

    story = [_p("采 购 合 同", styles["title"])]
    meta_rows = [
        [
            _p(f"合同编号：{contract['contract_no']}", styles["body"]),
            _p(f"签订日期：{contract['contract_date']}", styles["body"]),
        ],
        [
            _p(f"采购方（甲方）：{contract['buyer_name']}", styles["body"]),
            _p(f"供应方（乙方）：{contract['supplier_name']}", styles["body"]),
        ],
    ]
    meta_rows.extend(
        [
            [
                _p(f"联系人：{contract.get('buyer_contact', '')}", styles["body"]),
                _p(f"联系人：{contract.get('supplier_contact', '')}", styles["supplier_detail"]),
            ],
            [
                _p(f"电话：{contract.get('buyer_phone', '')}", styles["body"]),
                _p(f"电话：{contract.get('supplier_phone', '')}", styles["supplier_detail"]),
            ],
        ]
    )

    meta = Table(meta_rows, colWidths=[97 * mm, 97 * mm], hAlign="LEFT")
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.extend(
        [
            meta,
            Spacer(1, 5 * mm),
            _p("根据《中华人民共和国民法典》及相关法律法规，甲乙双方经平等协商，就甲方向乙方采购产品事宜，达成如下协议：", styles["body_indent"]),
            Spacer(1, 5 * mm),
            _p("第一条　产品名称、规格、数量及价格", styles["body"]),
            Spacer(1, 2 * mm),
        ]
    )

    table_data = [
        [
            _p("序号", styles["table_center"]),
            _p("BLD号", styles["table_center"]),
            _p("OE号", styles["table_center"]),
            _p("产品名称", styles["table_center"]),
            _p("适用车型", styles["table_center"]),
            _p("数量", styles["table_center"]),
            _p("单价（元）", styles["table_center"]),
            _p("金额", styles["table_center"]),
            _p("备注", styles["table_center"]),
            _p("交期", styles["table_center"]),
        ]
    ]
    for index, item in enumerate(contract["items"], start=1):
        table_data.append(
            [
                _p(index, styles["table_center"]),
                _p(item["product_code"], styles["table"]),
                _p(item["oe_no"], styles["table"]),
                _p(item["product_name"], styles["table"]),
                _p(item.get("models", ""), styles["table"]),
                _p(_quantity(item["quantity"]), styles["table_right"]),
                _p(_money(item["unit_price"]), styles["table_right"]),
                _p(_money(item["amount"]), styles["table_right"]),
                _p(item["note"], styles["table"]),
                _p(item["delivery_date"], styles["table"]),
            ]
        )
    table_data.append(
        [
            _p("合计", styles["table_center"]),
            "",
            "",
            "",
            "",
            _p(_quantity(contract["total_quantity"]), styles["table_right"]),
            "",
            _p(_money(contract["total_amount"]), styles["table_right"]),
            "",
            "",
        ]
    )

    items_table = Table(
        table_data,
        colWidths=[8 * mm, 19 * mm, 22 * mm, 30 * mm, 35 * mm, 10 * mm, 16 * mm, 18 * mm, 18 * mm, 18 * mm],
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#aeb9c5")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f8")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f7fafc")),
                ("SPAN", (0, -1), (4, -1)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.extend([items_table, Spacer(1, 6 * mm)])

    clauses = [
        f"注：{contract['price_note']}",
        f"合计金额（大写）：{contract['total_amount_upper']}　　¥：{_money(contract['total_amount'])}",
        "第二条　质量标准",
        contract["quality_terms"],
        "第三条　包装要求",
        "1. 包装应牢固可靠，具备防潮、防锈、防碰撞措施，适应运输及仓储要求。",
        "2. 每件外包装须标明：BLD号、OE号、数量、生产日期。",
        "3. 因包装不当造成的产品损坏、锈蚀等外观或性能缺陷，由乙方承担全部损失。",
        "第四条　交货时间、地点及方式",
        "1. 交货时间：各产品交期见第一条表格，乙方须按期交付。",
        f"2. 交货地点：{contract['delivery_address']}。",
        "3. 运输方式：由乙方负责运输并承担运费及运输途中的风险。",
        "第五条　验收",
        "1. 甲方收到货物后 7 个工作日内进行验收。",
        "2. 验收不合格的，甲方有权拒收或要求退换货，产生费用由乙方承担。",
        "3. 甲方签署验收单后视为乙方已交货，但不免除乙方质保责任。",
        "第六条　付款方式",
        f"1. 付款方式：{contract['payment_terms']}",
        "2. 甲方付款前，乙方须开具合法有效的增值税专用发票（税率 13%）。",
        "第七条　违约责任",
        "1. 乙方逾期交货的，每逾期一日按该批货款总额的 0.5% 向甲方支付违约金；逾期超过 15 日的，甲方有权单方解除合同，并追究乙方赔偿责任。",
        "2. 产品质量不符合约定的，乙方应无条件退换货并承担全部费用；逾期退换的按逾期交货处理。",
        "3. 甲方逾期付款的，每逾期一日按应付未付金额的 0.5% 向乙方支付违约金。",
        "第八条　保密条款",
        "双方对合同内容及履行过程中获知的对方商业秘密（包括但不限于价格、数量、图纸、技术参数等）予以保密，未经对方书面同意不得向第三方披露。违反方赔偿对方全部损失。保密义务自合同终止后 2 年内继续有效。",
        "第九条　争议解决",
        "因本合同引起的或与本合同有关的争议，双方友好协商解决；协商不成的，任何一方有权向甲方所在地有管辖权的人民法院提起诉讼。",
        "第十条　其他约定",
        "本合同未尽事宜，双方可另行签订补充协议，补充协议与本合同具有同等法律效力。",
    ]
    if contract.get("remark"):
        clauses.append(f"备注：{contract['remark']}")
    heading_prefixes = ("第", "注：", "合计金额")
    for clause in clauses:
        lines = str(clause).split("\n")
        for line in lines:
            text = line.strip()
            if not text:
                continue
            style = styles["body"] if text.startswith(heading_prefixes) else styles["body_indent"]
            story.append(_p(text, style))
            story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 8 * mm))
    buyer_signature = {
        "address": contract.get("buyer_signature_address", ""),
        "phone": contract.get("buyer_signature_phone", ""),
        "bank": contract.get("buyer_bank", ""),
        "account": contract.get("buyer_bank_account", ""),
        "date": contract.get("buyer_signature_date", ""),
    }
    supplier_signature = {
        "address": contract.get("supplier_signature_address", ""),
        "phone": contract.get("supplier_signature_phone", ""),
        "bank": contract.get("supplier_bank", ""),
        "account": contract.get("supplier_bank_account", ""),
        "date": contract.get("supplier_signature_date", ""),
    }
    signature = Table(
        [
            [_p("（以下为签章区）", styles["body"]), ""],
            [_p(f"甲方（盖章）：{contract['buyer_name']}", styles["body"]), _p(f"乙方（盖章）：{contract['supplier_name']}", styles["body"])],
            [_p(f"地址：{buyer_signature['address']}", styles["body"]), _p(f"地址：{supplier_signature['address']}", styles["body"])],
            [_p(f"电话：{buyer_signature['phone']}", styles["body"]), _p(f"电话：{supplier_signature['phone']}", styles["body"])],
            [_p(f"开户行：{buyer_signature['bank']}", styles["body"]), _p(f"开户行：{supplier_signature['bank']}", styles["body"])],
            [_p(f"账号：{buyer_signature['account']}", styles["body"]), _p(f"账号：{supplier_signature['account']}", styles["body"])],
            [_p("法定代表人或授权代表（签字）：", styles["body"]), _p("法定代表人或授权代表（签字）：", styles["body"])],
            [_p(f"日期：{buyer_signature['date']}", styles["body"]), _p(f"日期：{supplier_signature['date']}", styles["body"])],
        ],
        colWidths=[92 * mm, 92 * mm],
    )
    signature.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (-1, 0)),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(signature)

    doc.build(story)


def generate_sales_contract_pdf(contract: dict, output_path: Path) -> None:
    styles = _styles()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=11 * mm,
        bottomMargin=11 * mm,
        title=f"产品销售合同 {contract['contract_no']}",
    )

    story = [_p("产 品 销 售 合 同", styles["title"])]
    meta_rows = [
        [
            _p(f"合同编号：{contract['contract_no']}", styles["body"]),
            _p(f"签订日期：{contract['contract_date']}", styles["body"]),
        ],
        [
            _p(f"甲方（供方）：{contract['seller_name']}", styles["body"]),
            _p(f"乙方（需方）：{contract['customer_name']}", styles["body"]),
        ],
        [
            _p(f"联系人：{contract.get('seller_contact', '')}", styles["body"]),
            _p(f"联系人：{contract.get('customer_contact', '')}", styles["supplier_detail"]),
        ],
        [
            _p(f"电话：{contract.get('seller_phone', '')}", styles["body"]),
            _p(f"电话：{contract.get('customer_phone', '')}", styles["supplier_detail"]),
        ],
    ]

    meta = Table(meta_rows, colWidths=[97 * mm, 97 * mm], hAlign="LEFT")
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.extend(
        [
            meta,
            Spacer(1, 5 * mm),
            _p("根据《中华人民共和国民法典》及相关法律法规，甲乙双方经平等协商，就甲方向乙方销售汽车控制臂产品事宜，达成如下协议：", styles["body_indent"]),
            Spacer(1, 5 * mm),
            _p("第一条　产品名称、规格、数量及价格", styles["body"]),
            Spacer(1, 2 * mm),
        ]
    )

    table_data = [
        [
            _p("序号", styles["table_center"]),
            _p("BLD号", styles["table_center"]),
            _p("客户编码", styles["table_center"]),
            _p("OE号", styles["table_center"]),
            _p("产品名称", styles["table_center"]),
            _p("适用车型", styles["table_center"]),
            _p("数量", styles["table_center"]),
            _p("单价（元）", styles["table_center"]),
            _p("金额", styles["table_center"]),
            _p("备注", styles["table_center"]),
            _p("交期", styles["table_center"]),
        ]
    ]
    for index, item in enumerate(contract["items"], start=1):
        table_data.append(
            [
                _p(index, styles["table_center"]),
                _p(item["product_code"], styles["table"]),
                _p(item.get("customer_code", ""), styles["table"]),
                _p(item["oe_no"], styles["table"]),
                _p(item["product_name"], styles["table"]),
                _p(item.get("models", ""), styles["table"]),
                _p(_quantity(item["quantity"]), styles["table_right"]),
                _p(_money(item["unit_price"]), styles["table_right"]),
                _p(_money(item["amount"]), styles["table_right"]),
                _p(item["note"], styles["table"]),
                _p(item["delivery_date"], styles["table"]),
            ]
        )
    table_data.append(
        [
            _p("合计", styles["table_center"]),
            "",
            "",
            "",
            "",
            "",
            _p(_quantity(contract["total_quantity"]), styles["table_right"]),
            "",
            _p(_money(contract["total_amount"]), styles["table_right"]),
            "",
            "",
        ]
    )

    items_table = Table(
        table_data,
        colWidths=[8 * mm, 18 * mm, 18 * mm, 22 * mm, 28 * mm, 31 * mm, 10 * mm, 15 * mm, 17 * mm, 14 * mm, 13 * mm],
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#aeb9c5")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f8")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f7fafc")),
                ("SPAN", (0, -1), (5, -1)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.extend([items_table, Spacer(1, 6 * mm)])

    clauses = [
        f"注：{contract['price_note']}",
        f"合计金额（大写）：{contract['total_amount_upper']}　　¥：{_money(contract['total_amount'])}",
        "第二条　质量标准",
        contract["quality_terms"],
        "第三条　包装要求",
        "1. 甲方按行业通用标准及乙方要求进行包装，确保产品在运输和仓储过程中完好无损。",
        "2. 每件外包装标明：产品名称、规格型号、数量等或按乙方要求的信息。",
        "3. 特殊包装要求由乙方书面提出，费用由乙方承担。",
        "第四条　交货时间、地点及方式",
        "1. 交货时间：各产品交期见第一条表格，甲方按期交付。逾期交货的，按本合同第七条承担违约责任。",
        f"2. 交货地点：{contract['delivery_address']}（乙方指定地址）。",
        "3. 运输方式：由甲方负责安排运输并承担运费。货物交付乙方签收后，损毁、灭失风险转移至乙方。",
        "第五条　验收",
        "1. 乙方收到货物后应在3 个工作日内进行验收。",
        "2. 验收不合格的，乙方须在验收期内书面通知甲方，甲方负责退换货或补足，费用由甲方承担。",
        "3. 乙方逾期未提出异议的，视为验收合格。验收合格后乙方不得以数量、外观等事由要求退换。",
        "第六条　付款方式",
        f"1. 付款方式：{contract['payment_terms']}",
        "2. 增值税专用发票（税率 13%）的开具时间由双方另行约定。",
        "第七条　违约责任",
        "1. 甲方如无法按约定时间交付货物，应提前告知乙方；如无通知每逾期一日按该批货款总额的 0.5% 向乙方支付违约金；逾期超过 15 日的，乙方有权单方解除合同。",
        "2. 经双方确认产品质量不符合约定的，甲方应无条件退换货并承担全部费用。",
        "3. 乙方逾期付款的，每逾期一日按应付未付金额的 0.5% 向甲方支付违约金；逾期超过 15 日的，甲方有权暂停后续供货并追索全部欠款。",
        "第八条　保密条款",
        "双方对合同内容及履行过程中获知的对方商业秘密（包括但不限于价格、数量、图纸、技术参数、客户信息等）予以保密，未经对方书面同意不得向第三方披露。违反方赔偿对方全部损失。保密义务自合同终止后 2 年内继续有效。",
        "第九条　争议解决",
        "因本合同引起的或与本合同有关的争议，双方友好协商解决；协商不成的，任何一方有权向甲方所在地有管辖权的人民法院提起诉讼。",
        "第十条　其他约定",
        "本合同未尽事宜，双方可另行签订补充协议，补充协议与本合同具有同等法律效力。",
    ]
    if contract.get("remark"):
        clauses.append(f"备注：{contract['remark']}")
    heading_prefixes = ("第", "注：", "合计金额")
    for clause in clauses:
        lines = str(clause).split("\n")
        for line in lines:
            text = line.strip()
            if not text:
                continue
            style = styles["body"] if text.startswith(heading_prefixes) else styles["body_indent"]
            story.append(_p(text, style))
            story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 8 * mm))
    signature = Table(
        [
            [_p("（以下为签章区）", styles["body"]), ""],
            [_p(f"甲方（盖章）：{contract['seller_name']}", styles["body"]), _p(f"乙方（盖章）：{contract['customer_name']}", styles["body"])],
            [_p(f"统一社会信用代码：{contract.get('seller_credit_code', '')}", styles["body"]), _p(f"统一社会信用代码：{contract.get('customer_credit_code', '')}", styles["body"])],
            [_p(f"地址：{contract.get('seller_signature_address', '')}", styles["body"]), _p(f"地址：{contract.get('customer_signature_address', '')}", styles["body"])],
            [_p(f"电话：{contract.get('seller_signature_phone', '')}", styles["body"]), _p(f"电话：{contract.get('customer_signature_phone', '')}", styles["body"])],
            [_p(f"传真：{contract.get('seller_fax', '')}", styles["body"]), _p(f"传真：{contract.get('customer_fax', '')}", styles["body"])],
            [_p(f"开户行：{contract.get('seller_bank', '')}", styles["body"]), _p(f"开户行：{contract.get('customer_bank', '')}", styles["body"])],
            [_p(f"账号：{contract.get('seller_bank_account', '')}", styles["body"]), _p(f"账号：{contract.get('customer_bank_account', '')}", styles["body"])],
            [_p("法定代表人或授权代表（签字）：", styles["body"]), _p("法定代表人或授权代表（签字）：", styles["body"])],
            [_p(f"日期：{contract.get('seller_signature_date', '')}", styles["body"]), _p(f"日期：{contract.get('customer_signature_date', '')}", styles["body"])],
        ],
        colWidths=[92 * mm, 92 * mm],
    )
    signature.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (-1, 0)),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(signature)

    doc.build(story)
