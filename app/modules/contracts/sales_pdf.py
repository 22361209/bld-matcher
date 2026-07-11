from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, SimpleDocTemplate, Spacer, Table, TableStyle

from .document_values import _money, _quantity
from .pdf_support import PDF_FONT, _p, _styles


def generate_sales_contract_pdf(contract: dict[str, Any], output_path: Path) -> None:
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

    story: list[Flowable] = [_p("产 品 销 售 合 同", styles["title"])]
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
            _p(
                f"联系人：{contract.get('customer_contact', '')}",
                styles["supplier_detail"],
            ),
        ],
        [
            _p(f"电话：{contract.get('seller_phone', '')}", styles["body"]),
            _p(
                f"电话：{contract.get('customer_phone', '')}",
                styles["supplier_detail"],
            ),
        ],
    ]

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
                "根据《中华人民共和国民法典》及相关法律法规，甲乙双方经平等协商，就甲方向乙方销售汽车控制臂产品事宜，达成如下协议：",
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
        colWidths=[
            8 * mm,
            18 * mm,
            18 * mm,
            22 * mm,
            28 * mm,
            31 * mm,
            10 * mm,
            15 * mm,
            17 * mm,
            14 * mm,
            13 * mm,
        ],
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
            [
                _p(f"甲方（盖章）：{contract['seller_name']}", styles["body"]),
                _p(f"乙方（盖章）：{contract['customer_name']}", styles["body"]),
            ],
            [
                _p(
                    f"统一社会信用代码：{contract.get('seller_credit_code', '')}",
                    styles["body"],
                ),
                _p(
                    f"统一社会信用代码：{contract.get('customer_credit_code', '')}",
                    styles["body"],
                ),
            ],
            [
                _p(
                    f"地址：{contract.get('seller_signature_address', '')}",
                    styles["body"],
                ),
                _p(
                    f"地址：{contract.get('customer_signature_address', '')}",
                    styles["body"],
                ),
            ],
            [
                _p(
                    f"电话：{contract.get('seller_signature_phone', '')}",
                    styles["body"],
                ),
                _p(
                    f"电话：{contract.get('customer_signature_phone', '')}",
                    styles["body"],
                ),
            ],
            [
                _p(f"传真：{contract.get('seller_fax', '')}", styles["body"]),
                _p(f"传真：{contract.get('customer_fax', '')}", styles["body"]),
            ],
            [
                _p(f"开户行：{contract.get('seller_bank', '')}", styles["body"]),
                _p(f"开户行：{contract.get('customer_bank', '')}", styles["body"]),
            ],
            [
                _p(
                    f"账号：{contract.get('seller_bank_account', '')}",
                    styles["body"],
                ),
                _p(
                    f"账号：{contract.get('customer_bank_account', '')}",
                    styles["body"],
                ),
            ],
            [
                _p("法定代表人或授权代表（签字）：", styles["body"]),
                _p("法定代表人或授权代表（签字）：", styles["body"]),
            ],
            [
                _p(
                    f"日期：{contract.get('seller_signature_date', '')}",
                    styles["body"],
                ),
                _p(
                    f"日期：{contract.get('customer_signature_date', '')}",
                    styles["body"],
                ),
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
