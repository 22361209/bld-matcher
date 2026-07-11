from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, SimpleDocTemplate, Spacer, Table, TableStyle

from .document_values import _money, _quantity
from .pdf_support import PDF_FONT, _p, _styles


def generate_purchase_contract_pdf(contract: dict[str, Any], output_path: Path) -> None:
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

    story: list[Flowable] = [_p("采 购 合 同", styles["title"])]
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
                _p(
                    f"联系人：{contract.get('supplier_contact', '')}",
                    styles["supplier_detail"],
                ),
            ],
            [
                _p(f"电话：{contract.get('buyer_phone', '')}", styles["body"]),
                _p(
                    f"电话：{contract.get('supplier_phone', '')}",
                    styles["supplier_detail"],
                ),
            ],
        ]
    )

    meta = Table(meta_rows, colWidths=[97 * mm, 97 * mm], hAlign="LEFT")
    meta.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.extend(
        [
            meta,
            Spacer(1, 5 * mm),
            _p(
                "根据《中华人民共和国民法典》及相关法律法规，甲乙双方经平等协商，就甲方向乙方采购产品事宜，达成如下协议：",
                styles["body_indent"],
            ),
            Spacer(1, 5 * mm),
            _p("第一条　产品名称、规格、数量及价格", styles["body"]),
            Spacer(1, 2 * mm),
        ]
    )

    table_data: list[list[Any]] = [
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
        colWidths=[
            8 * mm,
            19 * mm,
            22 * mm,
            30 * mm,
            35 * mm,
            10 * mm,
            16 * mm,
            18 * mm,
            18 * mm,
            18 * mm,
        ],
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
            [
                _p(f"甲方（盖章）：{contract['buyer_name']}", styles["body"]),
                _p(f"乙方（盖章）：{contract['supplier_name']}", styles["body"]),
            ],
            [
                _p(f"地址：{buyer_signature['address']}", styles["body"]),
                _p(f"地址：{supplier_signature['address']}", styles["body"]),
            ],
            [
                _p(f"电话：{buyer_signature['phone']}", styles["body"]),
                _p(f"电话：{supplier_signature['phone']}", styles["body"]),
            ],
            [
                _p(f"开户行：{buyer_signature['bank']}", styles["body"]),
                _p(f"开户行：{supplier_signature['bank']}", styles["body"]),
            ],
            [
                _p(f"账号：{buyer_signature['account']}", styles["body"]),
                _p(f"账号：{supplier_signature['account']}", styles["body"]),
            ],
            [
                _p("法定代表人或授权代表（签字）：", styles["body"]),
                _p("法定代表人或授权代表（签字）：", styles["body"]),
            ],
            [
                _p(f"日期：{buyer_signature['date']}", styles["body"]),
                _p(f"日期：{supplier_signature['date']}", styles["body"]),
            ],
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
