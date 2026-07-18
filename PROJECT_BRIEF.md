# BLD Project Brief

更新时间：2026-07-18

这是给新接手 Codex 或开发者的短版项目说明。先读 `AGENTS.md`，再读本文件。详细历史在 `项目交接说明.md`，需要查旧决策时用 `rg` 搜索，不要默认整篇读取。

## 项目定位

BLD 是一个局域网内部使用的 Flask 业务系统，主要用于：

- 客户询价 Excel/OE/品牌号码/BLD 号匹配
- OpenClaw 机器人内部 API 查询和生成结果文件
- 产品目录、OE、车型、图片、PDF 图纸、含税单价和产品状态维护
- 多台设备之间产品、报价、管件和材料明细的数据包导入/导出与增量同步
- 询价结果 Excel 和图纸压缩包下载
- 生产料单生成和冲压材料明细维护
- 管件资料查看、分类检索和手工新建
- 发货通知客户模板管理和发货数据 Excel 生成
- 发货照片标签识别和 Excel 汇总草稿
- 合同管理和采购/销售合同 PDF 生成
- 多用户账号、权限和操作日志

## 位置和访问

常用本机项目路径：

```text
MacBook Air: /Users/linzhenyue/Project5inMBA
Mac mini: /Users/linzhenyue/Projects/bld-matcher
```

本机访问：

```text
http://127.0.0.1:5055/
```

NAS 访问：

```text
http://192.168.110.93:5055/
```

NAS 信息：

- SSH：`deploy@192.168.110.93`
- SSH key：`~/.ssh/bld_matcher_deploy`
- Git 仓库：`/volume1/git/bld-matcher.git`
- 运行目录：`/volume1/docker/bld-matcher`
- Docker Compose：`/usr/local/bin/docker-compose`

## 本机启动

```bash
cd "/Users/linzhenyue/Projects/bld-matcher"
uv run python -m scripts.init_database
APP_DEBUG=0 SECRET_KEY=local-dev-bld-matcher uv run python app.py
```

另开一个终端运行持久任务 Worker：

```bash
uv run python -m scripts.run_worker
```

本机也有 `/Applications/BLD.app` 启动器；它只用于通过 Terminal 启动本机 5055 并打开浏览器，不用于 NAS。启动日志在 `logs/bld-local-5055.log`。

常用检查：

```bash
uv run python scripts/verify.py
uv run python -m scripts.runtime_probe --base-url http://127.0.0.1:5055
git status --short
lsof -nP -iTCP:5055 -sTCP:LISTEN
```

## 数据归属

运行数据不进 Git。NAS 和本机都可能是较新版本，数据同步前必须比较；只有用户明确说“以 NAS 覆盖本机”或“以本机覆盖 NAS”时，才按指定方向覆盖：

- `data/products.sqlite3`：产品库、账号、日志、材料明细
- `data/catalog.xlsx`：产品目录源表
- `data/stamping_materials.xlsx`：冲压材料明细源表
- `data/drawings/`：PDF 图纸和归档
- `data/material_drawings/`：零配件和物料 PDF 图纸
- `data/product_images/`：产品图片
- `data/product_images/thumbs/`：产品列表缩略图
- `uploads/`：用户上传源文件
- `outputs/`：用户导出结果

不要自行决定运行数据的覆盖方向。

## 当前关键行为

询价处理：

- 首页文件/号码输入框可点击选择或直接拖入询价 Excel，也可直接输入单个 OE、品牌号码或 BLD 号快速查询；输入 4 位纯数字时会按 BLD 号片段搜索。
- 粘贴多个号码会生成临时询价源表并进入匹配结果页。
- 上传 Excel 后必须手动选择要匹配的列，系统不自动猜列。
- 匹配结果页先展示网页结果，点击“下载 Excel”时才生成 Excel。
- 下载 Excel 时弹窗选择不带单价、含税单价、不含税单价或美金价；不含税单价为 `含税单价 / 1.1` 后四舍五入到整数，美金价为 `含税单价 / 1.1 / 汇率`。
- 匹配结果可下载图纸包，按 BLD NO. 查找 PDF。

机器与 AI API：

- 新应用使用 `/api/v1`；`GET /api/v1` 返回平台能力，`GET /api/v1/openapi.json` 返回 OpenAPI 3.1 合同。
- API v1 使用强类型 Principal、最小权限 Scope、Pydantic Schema、稳定错误码、请求 ID 和持久幂等记录；写操作由服务端 Key 身份审计。
- `/api/v1/products/search` 提供稳定产品查询；`POST /api/v1/products/{product_id}/price` 以产品版本时间戳和幂等键安全更新含税单价，供 AI Agent 等机器调用；`/api/v1/inquiries/analyze` 与 `/export` 和网页、旧内部接口共用 InquiryService。
- `/api/v1/jobs/{id}`、`/result` 与 `/cancel` 提供持久任务状态、结果和幂等取消；任务绑定 API Principal，不返回内部请求路径。
- v1 导出返回限时 artifact ID；下载绑定创建它的 API Principal，响应不包含服务器绝对路径。OpenAPI 提交快照由统一验收阻断漂移。
- API Key 可设置 Scopes 和到期日期；历史 Key 保留兼容权限，新 Key 默认只有读取和询价权限，写权限需要管理员明确选择；管理页按默认 90 天周期提示轮换，但不自动删除。
- `/api/internal/*` 与 `/api/quotes` 是兼容接口，继续可用但不再扩展新能力。

- 文档在 `OPENCLAW_API.md`，接口前缀为 `/api/internal/`。
- 管理员菜单里有“内部 API Key”页面，可生成多条 Key 并按条删除；删除会立即使 Key 失效且不可恢复。完整 Key 只在创建响应显示一次，数据库只保留哈希和遮罩后缀，历史明文字段会在迁移时清空并删除。
- `/api/internal/*` 必须带 `Authorization: Bearer <key>`，不允许匿名调用；`.env` 的 `INTERNAL_API_TOKEN` 仅作为应急 fallback。
- `/api/internal/inquiry/numbers`：号码数组或文字号码查询；默认仅分析，传 `export: true` 才生成新 Excel，输出到 `outputs/openclaw/`。
- `/api/internal/inquiry/file`：传本机 Excel 路径或上传文件；默认仅分析，传 `export: true` 才在原文件基础上追加结果列。
- `/api/internal/inquiry/analyze`：只返回命中摘要，不生成文件；号码分析用临时工作簿，不长期写入 `uploads/openclaw/`。
- API 导出文件名统一为 `reYYMMDD_源文件名称.xls/xlsx`；号码数组/文字号码没有源文件，导出前必须由机器人询问并传 `source_name`；重名自动追加 `_2`、`_3`。
- API 的 `file_path` 只允许读取项目目录、`uploads/`、`outputs/` 下的 `.xls/.xlsx`。
- API 价格模式支持 `none`、`tax`、`net`、`usd`；`net` 为不含税价，`usd` 需要传汇率。

产品目录：

- 管理员将鼠标悬停在“导入目录”即可选择“下载模板”或“上传文件”。模板按当前产品库生成；必填列为 `BLD NO.`、`SERIES`、`ITEM`、`OE NO.1`、`Models`、`产品状态`、`导入单价`，`OE NO.2` 和`图片`可选。`SERIES` 通过 `SERIES` 至 `SERIES 6` 的多个下拉选择位实现多选，导入时合并为多品牌；`ITEM` 为单选下拉。导入会拒绝下拉选项之外的 SERIES 或 ITEM。上传后先预览新增、无变化和逐条 BLD NO. 冲突；冲突默认完整保留现有资料，只有明确勾选后才使用 Excel 更新。Excel 内部重复 BLD NO. 或任一必填项缺失都会阻断导入。确认失败时目录文件、产品资料和图片会一起恢复。图片支持 JPG、PNG、WEBP，单张不超过 5 MB、任一边不超过 6000 像素，建议长边不超过 2000 像素。
- 主搜索框同时按 BLD 号、品牌、车型搜索；OE 号有独立标准化搜索框。
- 产品目录每页 50 条，避免大量产品和图片导致滚动卡顿。
- 除固定在最右侧的操作列外，业务列可从列头文字区域拖动换序；顺序按当前浏览器登录用户保存，并可恢复默认。
- 品牌、产品名称和产品状态支持列头多选筛选；候选关键词可直接形成匹配选择，并支持全选、全不选；筛选与搜索、启用状态和分页共同保存在 URL，重置某列等于恢复该列全选。
- 产品品牌按“每行一个品牌”存储并统一为大写；历史 `RAM` 归入 `DODGE`，手工保存、目录导入和产品数据包同步共用同一套幂等规范化规则。
- 筛选面板支持中文输入法组合输入；单列选择超过 200 项或单项超过 256 字符时会明确拒绝，不会静默放宽筛选或误导出全量数据；空白值与字面值 `__blank__` 分开处理。
- 导出目录继承当前搜索和全部列筛选，导出完整命中集合而不是仅导出当前页；网页列顺序不改变 Excel 的既有格式。
- 表格使用缩略图，点击图片浮层预览原图。
- 有 PDF 图纸时点击 BLD 号预览图纸。
- 表格在含税单价后显示“产品状态”，数据库保存中文配置，产品目录默认显示英文；询价结果预览和人民币报价导出显示中文，美金报价导出显示英文。
- 导出目录只对管理员开放。
- 管理员菜单有“产品数据同步”入口，可导出产品数据包；导出包包含 `products` 表和 `manifest.json`，可选带 `data/drawings/` 和 `data/product_images/`。
- “产品数据同步”导入会先预览新增/更新/包内旧数据/无变化/本机独有数量，再增量合并 `products` 表；包内更新时间早于当前系统或时间戳无效的同 BLD 产品会跳过；数据包逐成员阻断穿越、链接、特殊文件和解压膨胀；导入前使用 SQLite Backup API 生成一致性备份，媒体文件原子复制，数据库应用失败时自动恢复本次媒体变更。
- 管理员菜单另有“业务数据同步”：同一个 `.tar.gz` 数据包可选择产品目录、报价记录、管件资料和材料明细。导入必须先预览；产品和管件按编号、报价和材料按跨设备同步标识增量合并。材料标识由业务字段生成，不依赖 Excel 文件名或行号；历史记录首次同步可接管统一标识。较旧数据会跳过，报价存在差异时始终标为冲突并保留当前记录；重复编号包，或预览后本机数据/数据包发生变化时均会阻断导入。每次导入前生成 SQLite 一致性备份并占用全局导入锁。该包不包含账号、权限、审计日志、产品图片/图纸或报价附件。
- 产品编辑页可上传/替换单个 PDF 图纸和最多 5 张产品图片，也可删除产品；手工清空含税单价会写入空值，图片/图纸格式校验失败时不会先更新产品文字资料。

外部品牌号码审核工具：

- `tools/manual_rockauto_lookup.py` 可在本机用浏览器逐个 OE 查询 RockAuto，提取 Mevotech、MOOG、Dorman、Delphi 号码。
- 该工具单线程、随机延时、写 JSON 缓存和失败记录，输出 `outputs/cross_reference_work/rockauto_manual_review.xlsx` 审核表。
- 它只生成审核材料，不自动修改 `data/products.sqlite3`；如遇验证码、Cloudflare 或访问限制，应暂停人工处理，不绕过限制。

生产料单：

- 页面采用工作台风格。
- 新增/编辑材料明细使用浮层。
- 材料规格尺寸由规格输入解析生成。
- 材料明细搜索支持母件编码、零件编码、规格尺寸等。
- 物料图纸页读取 `data/material_drawings/*.pdf`，产品编码取 PDF 文件名 stem，当前类别默认“球销”；支持按编码搜索、类别筛选、编码自然排序，页面采用左侧编码列表、右侧固定 PDF 预览的工作台布局。

管件资料：

- 导航栏“管件资料”提供独立于产品目录和生产料单的管件明细页；可按编号、规格、类型和借用来源检索。
- 页面列出编号、产品名称、成品规格、毛坯管长度、内径公差、采购基数、材质占位、重量、公差（mm）、消耗（mm）和借用编号；支持产品名称、公差、消耗、重量区间及外径/内径组合筛选，业务列支持拖拽换序，管理员可手工新增或编辑。
- 初始数据使用 `scripts/import_tubes_from_workbook.py` 从管件尺寸工作簿导入；导入记录保留来源工作表和行号，借用关系单独保存。

发货通知：

- 导航栏“发货通知”用于按客户模板生成发货通知 Excel。
- 支持单个模板上传和 zip 批量模板上传。
- 模板按客户分组选择，允许同一个客户维护多个模板。
- 模板 Excel 需要包含商品编码和数量列，或使用 `{{商品编码}}`、`{{数量}}` 占位符。
- 选择模板后可预览模板前几行；上传发货数据后先预览商品编码和数量，再确认生成 Excel。

发货照片识别：

- `tools/shipment_photo_recognition.py` 可读取 NAS 挂载目录或本机照片文件夹，识别货物白色标签并生成 Excel 和 JSON。
- 导航栏“货物识别”是试验入口，页面支持选择多张照片或拖入照片文件夹，上传后保存到当前用户 `uploads/u*/shipment_photos/`；Web 只提交 `background_jobs` 持久任务，独立 Worker 执行识别，页面可刷新续查并取消，检查点会续租，过期租约可恢复，正常停机会把未提交任务重新排队。
- 第一版不写入产品库、不匹配现有目录；按标签内容汇总日期、标签号码、BLD号、产品名称、数量、车型和箱数。
- 箱数按识别出的标签张数计算；数量只使用标签上明确写出的数量，看不清时为 0 并保留低置信备注。
- 支持 jpg、png、webp、bmp、tif、heic/heif 图片；HEIC 解码依赖 `pillow-heif`。
- 默认支持 OpenAI-compatible 视觉 Chat Completions 接口；Provider、地址、模型、密钥、代理和 allowlist 只从运行环境读取，页面请求不能覆盖。统一 Provider 执行超时、2 MiB 响应上限、可中断有限重试、并发限制和同 origin 安全重定向校验；命令行仍可用 `--provider tesseract` 做本机 OCR 草稿。
- 每次逻辑 AI 调用关联任务记录供应商、模型、尝试次数、Token、估算费用、耗时和成功/失败/中断结果；Excel“照片清单”和页面继续展示本次可用指标。

合同管理：

- `/contracts` 提供合同管理页面，包含采购合同和销售合同两个入口；旧路径 `/purchase-contracts` 仍保留兼容。
- 采购合同按“采购合同范本_玉环博莱德.docx”结构生成 PDF；销售合同按“产品销售合同范本_玉环博莱德.docx”的条款生成 PDF。
- 两类合同共用采购合同式明细字段：BLD号、OE号、产品名称、适用车型、数量、单价、金额、备注和交期；销售合同明细额外包含客户编码。
- 甲方默认带入“玉环博莱德机械有限公司”，并默认带入对应合同的价格说明、付款方式、质量、包装、验收、违约、保密和争议解决条款。
- PDF 抬头甲乙双方均显示公司名称、联系人和电话；交货地点只放在交货条款中。
- 页面按 A4 合同纸面组织，合同抬头、正文条款和签章区与 PDF 基本一致；明细表为了录入保留更宽的表格区域。
- 页面可录入位置使用独立颜色提示；签章区不显示统一社会信用代码；甲乙双方的地址、电话、开户行、账号和签章日期均为可选字段，留空也可生成 PDF。
- 手动填写 BLD号、数量、单价、交期和备注，页面实时预览金额和合计；BLD号输入后自动从产品目录带入 OE号、产品名称和适用车型。销售合同在单价为空时还会带入当前目录含税单价。
- 点击“生成 PDF”后先二次确认，再生成合同 PDF；采购合同保存到当前用户 `outputs/u用户ID-用户名/采购合同/乙方公司名称/`，销售合同保存到 `outputs/u用户ID-用户名/销售合同/客户名称/`，文件名为 `合同编号公司名称.pdf`，并写入操作日志。
- 合同管理页顶部可按类型、公司名、合同编号、文件名或操作用户搜索已生成合同。
- PDF 生成使用 `reportlab`；中文使用 `STSong-Light` 宋体风格字体，英文和数字优先使用 Arial，找不到 Arial 时兜底为 Helvetica。

报价记录：

- `/quotes` 提供报价记录页面，可新增报价、按客户/型号/日期/币种/报价人筛选、查看同一客户和型号的历史报价，并显示最近一次报价。
- 新集成通过 `/api/v1/quotes` 调用报价；读取使用 `quotes:read`，写入使用 `quotes:write`，创建必须带 `Idempotency-Key`，修订还必须带当前 ETag 对应的 `If-Match`。
- `/api/quotes`、`/api/quotes/latest` 和 `/api/quotes/<id>` 继续作为旧消费者兼容接口，所有入口与网页、Excel 导入共用 `QuoteService`。
- 新增和修正报价弹窗只录入业务字段，不显示报价人、来源、原文或附件路径；网页、Excel 导入和 API 新增分别由服务端自动记录可信账号及 `manual`、`excel`、`api` 来源，客户端提交的报价人和来源不会覆盖系统识别结果，也不能在修订中修改。
- 报价数据写入 `quote_records`，整数 `version` 防止并发覆盖；修订 before/after 写入 `quote_record_revisions`，报价不提供删除接口。

权限：

- 管理员可管理用户、日志、导入目录、导出目录和编辑产品；单价维护仅通过受控 API 提供给机器调用。
- 合同管理、报价记录和目录导出仅管理员可见可用。
- 普通用户不能调用只开放给管理员的后端地址；权限由服务端装饰器拦截。

页面与业务边界：

- 所有完整页面继承 `templates/base.html`，以唯一 `page_id` 和六类 `page_type` 进入统一页面协议；模板禁止内联脚本、事件处理器和样式。
- 产品目录、材料明细、管件资料和报价记录使用统一数据表框体：表头固定在框体内，内容支持双向滚动，列间使用浅色细分隔线，底栏整合当前范围、筛选总数和分页；所有列宽可拖动调整并按当前登录用户保存在浏览器中，拖动中断时会自动恢复页面交互状态。
- CSS 按 `static/styles.css` 基础层、`static/components/` 共享组件层和 `static/pages/` 页面层归属；页面资产由所属模板加载，共享层禁止业务选择器，项目门禁执行文件容量、零 ID 选择器与禁止新增 `!important`。
- 全部现有业务、登录、产品同步和货物识别由领域 Service/Repository 负责事务、审计和文件补偿；Web、API 与 Worker 适配器不直接访问 SQLite。
- 产品与询价 Web 路由已按职责拆分；单个路由适配器最多 320 行、15 个 endpoint，统一验收禁止动态路由注册绕过检查。
- 询价 Excel 按读取、清理、分析、价格和导出职责位于 `app/modules/inquiry/excel/`；`app/excel_io.py` 仅保留旧导入兼容门面，门禁分别限制处理模块 360 行和兼容门面 80 行。
- 合同文档按默认条款、表单解析、金额规则、PDF 样式及采购/销售渲染职责位于 `app/modules/contracts/`；`app/purchase_contract.py` 仅保留旧导入兼容门面，采购与销售 PDF 输出受结构和像素基准保护。
- 材料持久化按规格解析、明细 SQL 与 Excel 导入/启动引导职责位于 `app/modules/materials/`；`persistence.py` 仅保留旧导入兼容门面，数据库 Schema 与迁移不因拆分而变化。
- 材料 Excel 更新采用原子替换；数据库导入失败会恢复旧文件。合同、发货通知、模板和物料图纸若审计失败，会删除本次未完成输出。
- `app/database.py` 只保留 Schema、连接与迁移；路由数据库直连、daemon 后台线程和异常文本外泄债务已清零。
- `/health/ready` 通过只读连接检查数据库、迁移、最小业务条件与 Worker 心跳，不负责初始化；运行数据清理由默认 dry-run 的 `scripts/cleanup_runtime.py` 统一规划，详见 `docs/operations/runtime.md`。

## NAS 更新流程

本机完成改动后：

首次在 MacBook Air 上通过 GitHub 中转到 NAS 前，先按 `NAS_DEPLOY.md` 的“MacBook Air 中转前检查”确认 Git 历史和 NAS 运行数据状态；检查未通过时不要执行 NAS `reset --hard`。

```bash
git status --short
uv run python scripts/verify.py
git pull --ff-only origin main
git add ...
git commit -m "..."
git push nas main
```

NAS 运行目录更新：

```bash
ssh -tt -i ~/.ssh/bld_matcher_deploy deploy@192.168.110.93 'cd /volume1/docker/bld-matcher && git fetch origin main && git reset --hard origin/main && git status -sb && sudo /usr/local/bin/docker-compose up -d --build && sudo /usr/local/bin/docker-compose ps'
```

需要 `sudo` 时必须打开可见 macOS Terminal，让用户直接输入密码。

如果大量产品图片被新增或替换，部署后可生成缩略图：

```bash
sudo /usr/local/bin/docker-compose exec -T bld-matcher python tools/generate_product_thumbnails.py
```

## 重要代码入口

- `app.py`：应用入口、全局 before_request、模板全局函数
- `app/platform/`：API Principal、Key、Scope、错误、请求 ID、审计、Schema、OpenAPI、持久任务、AI Provider、健康检查和保留期基础设施
- `app/api/v1/`：稳定机器接口的版本入口与 OpenAPI 组装
- `app/routes/inquiry.py`：询价 Web 适配器注册入口
- `app/modules/inquiry/api.py`：OpenClaw 询价兼容 API 与 v1 询价适配器
- `app/modules/quotes/`：报价 Domain、Service、Repository、Web、API v1 与旧 API 兼容适配器
- `app/modules/products/`：产品 Domain、Repository、Service、目录/记录/媒体 Web 适配器、产品搜索/单价更新 API 和安全数据包同步
- `app/modules/inquiry/`：询价 Service、按职责拆分的 Excel 引擎、匹配/下载/映射 Web 适配器、旧内部 API 与 v1 适配器
- `app/platform/artifacts.py`：Principal 所有权、校验值和保留期 artifact 存储
- `app/routes/products.py`：产品 Web 适配器注册入口
- `app/modules/materials/`：生产料单、材料明细、物料图纸、原子文件更新和事务补偿
- `app/modules/contracts/`：采购/销售合同产品补全、PDF 生成、历史和审计
- `app/modules/shipping/`：发货通知、持久货物识别任务、Excel 生成和审计补偿
- `app/modules/admin/`：登录、账号、API Key、操作日志和系统更新
- `app/database.py`：SQLite Schema、连接和迁移入口，不保存业务查询
- `app/matcher.py`：产品匹配逻辑
- `app/product_media.py`：产品图片上传、缩略图生成和读取
- `app/catalog_export.py`：产品目录 Excel 导出和图片嵌入
- `app/purchase_contract.py`：采购/销售合同表单校验和 PDF 生成
- `tools/shipment_photo_recognition.py`：发货照片标签识别批处理，输出汇总 Excel 和原始 JSON，支持 HEIC/HEIF
- `templates/products.html`：产品目录页面
- `templates/purchase_contracts.html`：合同管理和采购/销售合同页面
- `templates/_product_rows.html`：产品目录行模板
- `static/styles.css`：token、reset 和基础页面壳
- `static/components/workspace.css`：跨页面工作台、搜索、表格和文件选择组件
- `static/pages/`：由所属模板显式加载的页面 CSS/JavaScript
- `tests/test_app.py`：主要回归测试
- `PROJECT_CONSTITUTION.md`：长期架构、安全、页面、API 和变更治理硬规则
- `scripts/init_database.py`：容器启动 Gunicorn 前执行迁移和首启管理员初始化
- `scripts/run_worker.py`：独立持久任务 Worker 入口
- `scripts/runtime_probe.py`、`scripts/cleanup_runtime.py`：部署业务探针和默认 dry-run 的保留期执行器
- `scripts/verify.py`：本机、AI 和 CI 共用的统一验收入口，包含项目合同、锁文件、Ruff、平台/运行边界 Pyright、语法、OpenAPI 快照和回归测试
- `contracts/openapi-v1.json`：API v1 提交快照，由 `scripts/openapi_snapshot.py --check` 精确比较
- `policy/legacy_allowlist.json`：现有架构债务棘轮白名单，部分债务精确到出现次数，只能缩小

## 文档策略

- `AGENTS.md`：必须遵守的短规则。
- `PROJECT_CONSTITUTION.md`：长期工程边界和 Definition of Done。
- `docs/governance/enforcement-matrix.md`：宪章规则的自动化覆盖、现有债务和下一道门禁。
- `PROJECT_BRIEF.md`：当前状态和快速接手说明，保持短。
- `changes/*.json`：系统更新的当前事实来源，由检查器强制。
- `项目交接说明.md`：详细历史档案，按需搜索。
- `README.md`：安装、启动和通用说明。
- `OPENCLAW_API.md`：机器人内部 API 调用说明。
- 强制规则：提交或部署前运行 `uv run python scripts/verify.py`；涉及行为或运行影响的改动必须包含 `changes/*.json` 片段。
