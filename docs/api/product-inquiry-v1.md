# Product And Inquiry API v1

本合同供 OpenClaw、Hermes、WorkBuddy、MCP 适配器和内部脚本使用。所有请求使用管理员生成的 Bearer API Key；完整机器合同以 `GET /api/v1/openapi.json` 和 `contracts/openapi-v1.json` 为准。

## Product Search

```http
GET /api/v1/products/search?oe=55270-2Z000&status=active&limit=50&offset=0
Authorization: Bearer <key>
```

Scope：`products:read`。

可用查询参数：

- `q`：兼容产品目录主搜索，匹配 BLD、品牌或车型。
- `bld`：BLD、品牌或车型查询。
- `oe`：标准化 OE/品牌号码查询；传入后优先于 `bld`。
- `series`、`model`：品牌和车型过滤。
- `status`：`active`、`inactive` 或 `all`。
- `limit`：1 至 200；`offset` 从 0 开始。

响应只包含稳定产品字段，不包含服务器图片路径或数据库实现字段。

## Inquiry Analysis

```http
POST /api/v1/inquiries/analyze
Authorization: Bearer <key>
Idempotency-Key: inquiry-analyze-20260711-001
Content-Type: application/json

{
  "numbers": ["55270-2Z000", "K6004LB"],
  "price_mode": "net",
  "rows_limit": 200,
  "unmatched_limit": 100
}
```

Scope：`inquiries:run`。也可以使用 `text` 传入逗号、分号、斜杠、换行或空格分隔的号码。`numbers` 与 `text` 至少有一项。

价格模式：

- `none`：不计算导出价。
- `tax`：含税单价。
- `net`：`含税单价 / 1.1` 四舍五入到整数。
- `usd`：`含税单价 / 1.1 / exchange_rate` 保留两位，必须提供正数汇率。

分析不会生成文件，`data.artifact` 为 `null`。响应行与网页快速查询、Excel 流程和旧内部接口来自同一 `InquiryService`。

## Inquiry Export

```http
POST /api/v1/inquiries/export
Authorization: Bearer <key>
Idempotency-Key: inquiry-export-20260711-001
Content-Type: application/json

{
  "numbers": ["55270-2Z000"],
  "source_name": "上海客户询价",
  "price_mode": "tax"
}
```

Scope：`inquiries:run`。成功返回 `201`，`data.artifact` 示例：

```json
{
  "id": "art_random",
  "filename": "re260711_上海客户询价.xlsx",
  "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "size_bytes": 6234,
  "sha256": "hex-checksum",
  "expires_at": "2026-07-12 14:30:00",
  "download_url": "/api/v1/artifacts/art_random"
}
```

响应不会出现 `output_path`、`source_path` 或其他本机绝对路径。同一个 Principal、端点、请求正文和 `Idempotency-Key` 重试时返回同一个 artifact ID。

## Artifact Download

```http
GET /api/v1/artifacts/{artifact_id}
Authorization: Bearer <key>
```

Scope：`artifacts:read`。必须使用创建 artifact 的同一 Principal；其他 Key、过期记录或不存在的 ID 都返回稳定错误 `artifact.not_found`。成功响应为二进制附件，包含 `Digest` 和 `Cache-Control: private, no-store`。

## Compatibility

旧 `/api/internal/inquiry/*` 继续使用原请求和响应，包括只供兼容消费者使用的绝对 `output_path`。新集成不得依赖该字段，应迁移到 v1 artifact。旧接口与 v1 的 BLD/OE/品牌号匹配结果由消费者合同测试保持一致。
