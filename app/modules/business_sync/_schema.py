from __future__ import annotations


PACKAGE_SUFFIX = ".tar.gz"
PACKAGE_TYPE = "bld_business_data"
PACKAGE_VERSION = 3
SUPPORTED_PACKAGE_VERSIONS = frozenset({1, 2, 3})

MAX_MEDIA_FILE_SIZE = 512 * 1024 * 1024
MAX_PACKAGE_METADATA_SIZE = 64 * 1024 * 1024
MAX_PACKAGE_MEMBER_COUNT = 10_000
MAX_PACKAGE_TOTAL_SIZE = 1024 * 1024 * 1024

MEDIA_DIRECTORIES = {
    "drawings": "data/drawings",
    "product_images": "data/product_images",
    "material_drawings": "data/material_drawings",
}
MEDIA_DATASETS = {
    "drawings": "products",
    "product_images": "products",
    "material_drawings": "materials",
}
DATASETS = {
    "customers": ("customers", "sync_id", "客户"),
    "products": ("products", "bld_no", "产品目录"),
    "quotes": ("quote_records", "sync_id", "报价记录"),
    "tubes": ("tube_items", "code", "管件资料"),
    "materials": ("material_items", "sync_id", "材料明细"),
}

FIELD_LABELS = {
    "active": "状态",
    "bld_no": "BLD NO.",
    "blank_length_text": "毛坯管长度",
    "borrowed_from": "借用编号",
    "car": "车型",
    "category": "类别",
    "code": "编号",
    "consumption_mm": "消耗长度",
    "currency": "币种",
    "customer_name": "客户",
    "customer_product_code": "客户产品编号",
    "inner_diameter_mm": "内径",
    "item": "产品名称",
    "length": "长度",
    "model": "母件编码",
    "models": "适用车型",
    "moq": "起订量",
    "name": "客户名称",
    "note": "备注",
    "oe_no_1": "OE 号 1",
    "oe_no_2": "OE 号 2",
    "outer_diameter_mm": "外径",
    "part": "零件",
    "pieces": "下料只数",
    "price": "报价",
    "price_cny": "价格",
    "product_model": "产品型号",
    "product_status": "产品状态",
    "purchase_base": "采购基数",
    "quote_date": "报价日期",
    "quote_no": "报价单号",
    "quoted_by": "报价人",
    "remark": "备注",
    "series": "系列",
    "source": "来源",
    "source_row": "来源行",
    "source_sheet": "来源工作表",
    "source_text": "来源内容",
    "source_type": "来源类型",
    "spec_text": "规格",
    "tax_price": "含税价",
    "net_price": "未税价",
    "thickness": "厚度",
    "tolerance_mm": "公差",
    "tube_type": "产品名称",
    "weight_kg": "重量",
    "width": "宽度",
}
COMPARISON_EXCLUDED_COLUMNS = {"sync_id", "attachment_path", "created_at", "updated_at", "version"}
LOCAL_MEDIA_COLUMNS = {
    # 负责人账号属于设备本地身份，不随客户主数据跨设备覆盖。
    "customers": {"owner_username"},
    "products": {
        "image_path",
        "image_path_2",
        "image_path_3",
        "image_path_4",
        "image_path_5",
        "drawing_path",
        "drawing_original_name",
        "drawing_updated_at",
    },
    # customer_id 是每台设备的本地主键，跨设备同步时按 customer_name 重新解析。
    "quotes": {"attachment_path", "customer_id"},
}
