# BLD 询价处理系统

一个用于局域网内部使用的 Flask 系统，主要处理客户询价 Excel 匹配、产品目录维护、生产料单生成、账号权限、操作日志和系统更新记录。

## 新接手先读

为减少 Codex 压缩上下文后的负担，项目说明拆成短版和历史档案：

- `AGENTS.md`：必须遵守的操作规则，例如 NAS sudo 必须用可见 Terminal、超过 5 分钟先问、不要覆盖运行数据。
- `PROJECT_BRIEF.md`：当前项目状态、关键路径、数据归属和部署流程。
- `项目交接说明.md`：详细历史和系统更新页面来源，按需搜索，不建议默认整篇读取。

## 本机启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

默认访问地址：

```text
http://127.0.0.1:5055/
```

### macOS 本机启动器

本机可以安装一个名为 `BLD` 的启动器到“应用程序”文件夹，并自动放到 Dock。它只负责通过 Terminal 启动当前工作区里的 5055 服务并打开浏览器，不用于 NAS。启动日志写入 `logs/bld-local-5055.log`。

```bash
bash tools/install_bld_launcher.sh
```

启动器资料保存在 `tools/`：

- `tools/start_local_5055.applescript`：启动 5055 的 AppleScript 模板
- `tools/BLD.icns`：BLD 启动器图标

换电脑或移动项目目录后，重新运行安装脚本即可重建 `/Applications/BLD.app`。

默认管理员账号(首启会自动创建):

```text
账号：007
密码：通过环境变量 DEFAULT_ADMIN_PASSWORD 设置,默认值 change-me-on-first-login
```

**首次部署前请通过 `.env` 或环境变量设置 `DEFAULT_ADMIN_PASSWORD` 为强密码,登录后立即从后台再次修改。** 已经存在的管理员不会被环境变量覆盖。

## 配置

项目会自动读取根目录下的 `.env` 文件，也可以直接使用环境变量。可参考 `.env.example`：

- `SECRET_KEY`：Flask 会话密钥,**必须**改成随机长字符串,否则在 `APP_DEBUG=0`(生产)模式下会拒绝启动。可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成
- `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD`:首次启动创建管理员时使用,部署前覆盖
- `MAX_UPLOAD_MB`：普通上传文件大小限制，默认 `20`
- `PRODUCT_SYNC_MAX_UPLOAD_MB`：产品数据包上传大小限制，默认 `512`
- `APP_HOST`：本机启动监听地址，默认 `127.0.0.1`
- `APP_PORT`：本机启动端口，默认 `5055`
- `BLD_DATA_DIR`：数据目录，默认 `data`
- `BLD_UPLOAD_DIR`：上传目录，默认 `uploads`
- `BLD_OUTPUT_DIR`：输出目录，默认 `outputs`
- `INTERNAL_API_TOKEN`：可选应急 fallback；日常在网页“内部 API Key”页面生成 Key。页面支持多条 Key、完整显示和单条停用；`/api/internal/*` 始终需要 `Authorization: Bearer <token>`

## 目录说明

- `app.py`：应用入口和全局配置
- `app/routes/`：页面路由，按功能拆分
- `app/database.py`：数据库访问和业务数据写入
- `app/migrations.py`：数据库结构迁移
- `app/excel_io.py`：询价 Excel 读写
- `app/routes/shipment_notice.py`：发货通知模板管理和客户模板 Excel 生成
- `app/drawings.py`：PDF 图纸上传、替换归档和询价图纸包
- `app/material_sheet.py`：生产料单生成
- `templates/`：页面模板
- `static/`：样式和产品图片
- `data/`：运行数据目录，业务 Excel、SQLite 数据、PDF 图纸和上传图片不提交 Git
- `uploads/`：运行时上传文件，按用户目录隔离，不提交 Git
- `outputs/`：运行时导出文件，按用户目录隔离，不提交 Git
- `OPENCLAW_API.md`：供 OpenClaw 机器人调用的内部 API 说明

## 数据库

默认数据库是 `data/products.sqlite3`。这个文件是业务数据，不进入 Git。产品目录 `data/catalog.xlsx`、材料明细 `data/stamping_materials.xlsx`、PDF 图纸目录 `data/drawings/`、物料图纸目录 `data/material_drawings/` 和上传产品图片目录 `data/product_images/` 也按运行数据处理，不进入 Git。每个产品可维护含税单价、产品状态和最多 5 张产品图片；产品状态用于记录球头/衬套配置，例如“1 个球头 2 个衬套”。网页编辑上传的图片文件保存在 `data/product_images/`。产品目录列表使用 `data/product_images/thumbs/` 下的运行时缩略图，点击预览时才加载原图。NAS 上的 `data/` 目录要按 NAS 备份策略保护，更新代码时不要用本机数据覆盖 NAS 数据。

管理员菜单里的“产品数据同步”用于两端系统之间交换产品数据包。导出包只包含 `products` 表和 `manifest.json`，可选包含 `data/drawings/`、`data/product_images/`；导入时先预览差异，再增量合并 `products` 表，不覆盖本机账号、内部 API Key 或操作日志。包内更新时间早于当前系统的同 BLD 产品会标记为“包内旧数据”并跳过，避免旧包覆盖新数据；勾选图纸/图片时才复制包内媒体文件，覆盖前会把本机对应文件备份到 `data/local-backups/`。

数据库结构变化集中放在 `app/migrations.py`。新增字段或表时，添加一个新的 migration id 和对应函数，让本机和 NAS 在启动连接数据库时自动补齐结构。

产品目录 Excel 中的单元格图片可用 `tools/import_catalog_cell_images.py` 提取到 `data/product_images/`，脚本会解析 `DISPIMG` 图片映射，并应用 Excel/WPS 中的水平或垂直翻转。先运行 dry-run 查看匹配统计，确认后再加 `--apply` 写入图片和数据库：

```bash
tools/import_catalog_cell_images.py "产品目录/BLD catalogue 2603 new(2个OE).xlsx"
tools/import_catalog_cell_images.py "产品目录/BLD catalogue 2603 new(2个OE).xlsx" --apply
```

如果产品图片是历史导入或批量复制进去的，可以预先生成缩略图，避免第一次滚动产品目录时边访问边生成：

```bash
python tools/generate_product_thumbnails.py
```

## 多用户文件和导入规则

上传和输出文件按用户隔离：

```text
uploads/u用户ID-用户名/
outputs/u用户ID-用户名/
```

生成文件名会带用户名，例如 `re260429-007-客户询价.xlsx` 或 `catalog-export-bld-007-260429.xlsx`。普通用户只能看到和下载自己的输出文件，管理员可以在最近结果里看到所有用户和旧根目录输出。

OpenClaw 内部 API 的询价导出固定写入 `outputs/openclaw/`，文件名统一为 `reYYMMDD_源文件名称_openclaw.xls/xlsx`。号码数组/文字号码没有源文件，导出前必须由机器人询问并传 `source_name`；重名时自动追加 `_2`、`_3`。

会修改全局数据的导入操作使用导入锁，避免多人同时覆盖数据：

- 产品目录导入
- 材料数据导入
- 单价确认导入
- 产品数据包导入

询价匹配和生产计划生成只处理当前用户的上传与输出，不使用全局导入锁。询价结果页生成的图纸压缩包保存在当前用户的 `outputs/u用户ID-用户名/` 下，原始 PDF 图纸保存在 `data/drawings/pdf/` 下，网页编辑上传的产品图片保存在 `data/product_images/` 下。

## 发货照片标签识别

导航栏“货物识别”提供试验入口，员工可直接选择多张照片，或把照片文件夹拖入页面，系统会上传到当前账号的 `uploads/u*/shipment_photos/` 下并生成 Excel。当前网页入口不直接读取 NAS 文件夹路径；如需处理 NAS 上的大批量照片，可先把 NAS 目录挂载到本机/容器后使用命令行工具。

底层工具 `tools/shipment_photo_recognition.py` 可以读取一个本机或 NAS 挂载后的照片文件夹，识别每张发货照片里的货物标签，并生成 Excel。第一版不写入产品库、不匹配现有目录，只按标签内容汇总：

- 汇总列：日期、标签上的号码、BLD号、产品名称、数量、车型、箱数、来源照片、低置信标签数、备注
- 箱数按识别到的标签张数计算
- 数量只取标签上明确写出的数量；看不清会写 0 并进入低置信复核
- 支持 jpg、png、webp、bmp、tif、heic/heif 图片；HEIC 依赖 `pillow-heif`
- 输出包含“汇总”“标签明细”“照片清单”“原始结果”工作表，同时保存 JSON 方便追溯

使用 GPT 或其他 OpenAI-compatible 视觉模型：

```bash
export SHIPMENT_VISION_API_KEY="你的视觉模型 API Key"
export SHIPMENT_VISION_BASE_URL="https://api.openai.com/v1"
export SHIPMENT_VISION_MODEL="你的视觉模型名"
.venv/bin/python tools/shipment_photo_recognition.py "/path/to/nas/shipment_photos/2026-05-19"
```

如果只是本机 OCR 草稿，可用 Tesseract：

```bash
.venv/bin/python tools/shipment_photo_recognition.py "/path/to/photos" --provider tesseract --limit 5
```

建议先用 `--limit 5` 试跑真实照片，确认标签格式和识别质量后再处理整批。

## 系统更新

右上角管理员菜单里的“系统更新”页面会读取 `项目交接说明.md` 中的“当前最近重要变更”，用于在网页内查看项目代码、页面、权限、部署和核心业务规则的更新记录。更新条目使用 `YYYY-MM-DD · commit · 标题` 格式，同一天多次更新会拆成多条版本记录。

## 测试

```bash
.venv/bin/python -m unittest discover
```

当前测试覆盖登录、核心页面访问、系统更新页面、20MB 上传限制、用户文件隔离、导入锁、迁移记录、PDF 图纸上传和询价图纸包。
