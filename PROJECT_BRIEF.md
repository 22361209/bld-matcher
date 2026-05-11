# BLD Project Brief

更新时间：2026-05-11

这是给新接手 Codex 或开发者的短版项目说明。先读 `AGENTS.md`，再读本文件。详细历史在 `项目交接说明.md`，需要查旧决策时用 `rg` 搜索，不要默认整篇读取。

## 项目定位

BLD 是一个局域网内部使用的 Flask 业务系统，主要用于：

- 客户询价 Excel/OE/品牌号码/BLD 号匹配
- OpenClaw 机器人内部 API 查询和生成结果文件
- 产品目录、OE、车型、图片、PDF 图纸、含税单价维护
- 询价结果 Excel 和图纸压缩包下载
- 生产料单生成和冲压材料明细维护
- 合同管理和采购/销售合同 PDF 生成
- 多用户账号、权限和操作日志

## 位置和访问

本机项目：

```text
/Users/linzhenyue/Documents/New project 5
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
cd "/Users/linzhenyue/Documents/New project 5"
APP_DEBUG=0 SECRET_KEY=local-dev-bld-matcher .venv/bin/python app.py
```

本机也有 `/Applications/BLD.app` 启动器；它只用于通过 Terminal 启动本机 5055 并打开浏览器，不用于 NAS。启动日志在 `logs/bld-local-5055.log`。

常用检查：

```bash
.venv/bin/python -m unittest tests.test_app -v
git status --short
lsof -nP -iTCP:5055 -sTCP:LISTEN
```

## 数据归属

运行数据不进 Git，且 NAS 版本通常更权威：

- `data/products.sqlite3`：产品库、账号、日志、材料明细
- `data/catalog.xlsx`：产品目录源表
- `data/stamping_materials.xlsx`：冲压材料明细源表
- `data/drawings/`：PDF 图纸和归档
- `data/product_images/`：产品图片
- `data/product_images/thumbs/`：产品列表缩略图
- `uploads/`：用户上传源文件
- `outputs/`：用户导出结果

不要用本机数据覆盖 NAS 数据，除非用户明确指定。

## 当前关键行为

询价处理：

- 首页文件/号码输入框可点击选择或直接拖入询价 Excel，也可直接输入单个 OE、品牌号码或 BLD 号快速查询；输入 4 位纯数字时会按 BLD 号片段搜索。
- 粘贴多个号码会生成临时询价源表并进入匹配结果页。
- 上传 Excel 后必须手动选择要匹配的列，系统不自动猜列。
- 匹配结果页先展示网页结果，点击“下载 Excel”时才生成 Excel。
- 下载 Excel 时弹窗选择不带单价、含税单价、不含税单价或美金价；不含税单价为 `含税单价 / 1.1` 后四舍五入到整数，美金价为 `含税单价 / 1.1 / 汇率`。
- 匹配结果可下载图纸包，按 BLD NO. 查找 PDF。

OpenClaw 内部 API：

- 文档在 `OPENCLAW_API.md`，接口前缀为 `/api/internal/`。
- 管理员菜单里有“内部 API Key”页面，可生成、重新生成、停用 Key；完整 Key 只显示一次。
- `/api/internal/*` 必须带 `Authorization: Bearer <key>`，不允许匿名调用；`.env` 的 `INTERNAL_API_TOKEN` 仅作为应急 fallback。
- `/api/internal/inquiry/numbers`：号码数组或文字号码查询；默认仅分析，传 `export: true` 才生成新 Excel，输出到 `outputs/openclaw/`。
- `/api/internal/inquiry/file`：传本机 Excel 路径或上传文件；默认仅分析，传 `export: true` 才在原文件基础上追加结果列。
- `/api/internal/inquiry/analyze`：只返回命中摘要，不生成文件。
- API 导出文件名统一为 `reYYMMDD_源文件名称_openclaw.xls/xlsx`；号码数组/文字号码没有源文件，导出前必须由机器人询问并传 `source_name`；重名自动追加 `_2`、`_3`。
- API 的 `file_path` 只允许读取项目目录、`uploads/`、`outputs/` 下的 `.xls/.xlsx`。
- API 价格模式支持 `none`、`tax`、`net`、`usd`；`net` 为不含税价，`usd` 需要传汇率。

产品目录：

- 主搜索框同时按 BLD 号、品牌、车型搜索；OE 号有独立标准化搜索框。
- 产品目录每页 50 条，避免大量产品和图片导致滚动卡顿。
- 表格使用缩略图，点击图片浮层预览原图。
- 有 PDF 图纸时点击 BLD 号预览图纸。
- 导出目录只对管理员开放。
- 产品编辑页可上传/替换单个 PDF 图纸和最多 5 张产品图片，也可删除产品。

生产料单：

- 页面采用工作台风格。
- 新增/编辑材料明细使用浮层。
- 材料规格尺寸由规格输入解析生成。
- 材料明细搜索支持母件编码、零件编码、规格尺寸等。

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

权限：

- 管理员可管理用户、日志、导入目录、导出目录、维护单价和编辑产品。
- 合同管理、价格维护和目录导出仅管理员可见可用。
- 普通用户不能调用只开放给管理员的后端地址；权限由服务端装饰器拦截。

## NAS 更新流程

本机完成改动后：

```bash
git status --short
.venv/bin/python -m unittest tests.test_app -v
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
- `app/routes/inquiry.py`：询价上传、匹配、下载 Excel、图纸包
- `app/routes/internal_api.py`：OpenClaw 内部 API
- `app/routes/products.py`：产品目录、图片、图纸、单价导入、目录导入导出
- `app/routes/materials.py`：生产料单和材料明细
- `app/routes/purchase_contracts.py`：合同管理、采购/销售合同生成和 PDF 下载
- `app/routes/admin.py`：用户、日志、系统更新页面
- `app/database.py`：SQLite 表结构、查询、写入、迁移调用
- `app/matcher.py`：产品匹配逻辑
- `app/product_media.py`：产品图片上传、缩略图生成和读取
- `app/catalog_export.py`：产品目录 Excel 导出和图片嵌入
- `app/purchase_contract.py`：采购/销售合同表单校验和 PDF 生成
- `templates/products.html`：产品目录页面
- `templates/purchase_contracts.html`：合同管理和采购/销售合同页面
- `templates/_product_rows.html`：产品目录行模板
- `static/styles.css`：主要样式
- `tests/test_app.py`：主要回归测试

## 文档策略

- `AGENTS.md`：必须遵守的短规则。
- `PROJECT_BRIEF.md`：当前状态和快速接手说明，保持短。
- `项目交接说明.md`：详细历史和系统更新来源，按需搜索。
- `README.md`：安装、启动和通用说明。
- `OPENCLAW_API.md`：机器人内部 API 调用说明。
