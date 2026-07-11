# OpenClaw Internal API

供本机 OpenClaw 机器人调用。目标是让机器人直接走程序接口，不再模拟网页登录、抓 CSRF 或解析 HTML。

## 访问边界

- 路径前缀：`/api/internal/`
- 必须先在网页右上角管理员菜单进入“内部 API Key”生成 Key。完整 Key 只在创建时显示一次；页面之后只显示遮罩标识，并支持逐条停用。
- 调用方必须传：

```text
Authorization: Bearer <API Key>
```

- `.env` 中的 `INTERNAL_API_TOKEN` 仅作为部署/应急兼容方式；日常建议使用网页生成的 Key。

结果文件统一输出到：

```text
outputs/openclaw/
```

兼容接口返回值里的 `output_path` 是本机绝对路径，现有机器人可以继续使用。新集成应改用 `/api/v1/inquiries/*` 和受 Principal 约束的 artifact；v1 不返回服务器绝对路径。详见 `docs/api/product-inquiry-v1.md`。

## 导出文件名规则

只有显式传 `export: true` 时才会生成 Excel。API 生成的询价文件名统一为：

```text
reYYMMDD_源文件名称.xls/xlsx
```

规则：

- `YYMMDD` 使用生成当天日期，例如 `2026-05-11` 为 `260511`
- `源文件名称` 取文件名去掉后缀；路径会被去掉
- `/inquiry/file` 使用上传文件或 `file_path` 的源文件名，并沿用源文件后缀 `.xls` 或 `.xlsx`
- `/inquiry/numbers` 没有实际源文件；如果要 `export: true` 生成 Excel，必须先询问并传入 `source_name` 作为中间的“源文件名称”
- 如果目标文件名已存在，系统自动追加 `_2`、`_3`，不会覆盖旧文件
- 最终文件名以返回值里的 `output_name` 为准

示例：`re260511_customer.xlsx`、`re260511_上海客户询价.xlsx`

`file_path` 只允许读取项目目录、`uploads/`、`outputs/` 里的 `.xls/.xlsx` 文件。范围外的本机绝对路径会被拒绝，避免 API Key 变成任意本机文件读取权限。

## 价格模式

所有接口都支持 `price_mode`：

- `none`：不导出价格列
- `tax`：含税单价
- `net`：不含税单价，按 `含税单价 / 1.1` 四舍五入到整数
- `usd`：美金价，按 `含税单价 / 1.1 / exchange_rate` 保留两位小数

选择 `usd` 时必须传 `exchange_rate`。

## 号码列表生成 Excel

适合截图 OCR 后得到号码数组，或用户直接发文字号码。

```http
POST /api/internal/inquiry/numbers
Content-Type: application/json
```

```json
{
  "numbers": ["55270-2Z000", "K6004LB"],
  "source_name": "上海客户询价",
  "price_mode": "net",
  "export": true,
  "rows_limit": 200
}
```

也可用 `text` 传整段文字，系统会按逗号、斜杠、分号、顿号、换行等拆分；若只有空格分隔，也会尝试拆分。

不传 `export` 时默认只分析、不生成文件；需要 Excel 时必须显式传 `export: true`，并传 `source_name`。

## 客户原始文件增强

适合客户发来的 `.xls` / `.xlsx`。系统在原文件基础上追加 `BLD NO.`、价格列和 `匹配说明` 列，尽量保留原文件结构。

```http
POST /api/internal/inquiry/file
Content-Type: application/json
```

```json
{
  "file_path": "/Users/linzhenyue/Projects/bld-matcher/uploads/customer.xlsx",
  "match_column": "A",
  "price_mode": "tax",
  "export": true
}
```

`match_column` 可传 0 起始列号，也可传 Excel 字母列，例如 `A`。不传时系统按表头自动识别；识别失败会返回少量列预览，供机器人或用户选择。

也支持 `multipart/form-data` 上传文件，字段名为 `file` 或 `inquiry`。

不传 `export` 时默认只分析、不生成文件；需要增强版 Excel 时必须显式传 `export: true`。

## 仅分析不导出

适合机器人先判断一批号码或客户文件的命中情况。

```http
POST /api/internal/inquiry/analyze
Content-Type: application/json
```

传 `numbers` / `text` 时按号码列表分析；传 `file_path` 或上传文件时按客户原始文件分析。该接口不生成结果文件，`output_path` 为 `null`。

## 统一返回结构

```json
{
  "ok": true,
  "mode": "new-workbook",
  "summary": {
    "total_rows": 2,
    "matched_count": 1,
    "unmatched_count": 1,
    "returned_rows": 2,
    "rows_truncated": false,
    "invalid_items": [],
    "price_mode": "net",
    "export_price_label": "不含税单价",
    "output_generated": true
  },
  "matched_count": 1,
  "unmatched_count": 1,
  "rows": [
    {
      "row": 2,
      "original_number": "55270-2Z000",
      "matched": true,
      "bld_no": "K6004LB",
      "match_reason": "OE 精准命中",
      "match_note": "OE 精准命中",
      "score": 95,
      "price_cny": 88.8,
      "export_price": 81,
      "export_price_label": "不含税单价",
      "product": {
        "bld_no": "K6004LB",
        "series": "HYUNDAI",
        "item": "Rear Left Lower Control Arm",
        "oe_no_1": "55270-2Z000",
        "oe_no_2": "品牌交叉号",
        "models": "车型信息",
        "price_cny": 88.8,
        "image_paths": ["data_product_images/K6004LB.png"]
      }
    }
  ],
  "unmatched_list": ["NO-MATCH"],
  "output_path": "/absolute/path/outputs/openclaw/re260511_上海客户询价.xlsx",
  "output_name": "re260511_上海客户询价.xlsx"
}
```

`mode` 取值：

- `new-workbook`：截图/文字号码生成新 Excel
- `augment-source-workbook`：客户原始文件增强
