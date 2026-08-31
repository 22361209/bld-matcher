# BLD 询价处理系统

一个用于局域网内部使用的 Flask 系统，主要处理客户询价 Excel 匹配、产品目录维护、生产料单生成、账号权限、操作日志和系统更新记录。

## 新接手先读

为减少 Codex 压缩上下文后的负担，项目说明拆成短版和历史档案：

- `AGENTS.md`：必须遵守的操作规则，例如 NAS sudo 必须用可见 Terminal、超过 5 分钟先问、不要覆盖运行数据。
- `PROJECT_CONSTITUTION.md`：架构、页面、数据、安全、API 和变更治理硬规则。
- `docs/governance/enforcement-matrix.md`：每条硬规则当前由什么检查、测试或后续门禁负责。
- `PROJECT_BRIEF.md`：当前项目状态、关键路径、数据归属和部署流程。
- `changes/*.json`：系统更新事实来源；`项目交接说明.md` 仅保留详细历史。

## 本机启动

推荐使用锁定的 Python 3.12 环境：

```bash
uv sync --frozen
uv run python app.py
```

`requirements.txt` 由 `uv.lock` 生成，供 Docker 和兼容安装流程使用，不要手工维护依赖版本。

默认访问地址：

```text
http://127.0.0.1:5055/
```

### macOS 本机启动器

本机 5055 统一通过 `bash tools/restart_local_5055.sh` 或安装到“应用程序”文件夹的 `BLD` 启动器启动；两者复用同一套逻辑。它只负责通过 Terminal 启动当前工作区里的 5055 服务并打开浏览器，不用于 NAS。再次启动会先停止同一工作区的旧 5055 服务，并只在对应 Terminal 已空闲时精确关闭该窗口；若 5 秒内仍有前台任务，启动器会保留窗口而不会强行关闭或弹出终止进程提示。其他目录占用 5055 时会提示且不会停止它。启动日志写入 `logs/bld-local-5055.log`。

```bash
bash tools/install_bld_launcher.sh
```

启动器资料保存在 `tools/`：

- `tools/start_local_5055.applescript`：启动 5055 的 AppleScript 模板
- `tools/restart_local_5055.sh`：命令行和 BLD.app 共用的本机 5055 重启入口
- `tools/bld_launcher.applescript`：BLD.app 的薄包装模板
- `tools/BLD.icns`：BLD 启动器图标

换电脑或移动项目目录后，重新运行安装脚本即可重建 `/Applications/BLD.app`。

默认管理员账号(首启会自动创建):

```text
账号：007
密码：必须通过环境变量 DEFAULT_ADMIN_PASSWORD 显式设置
```

**首次部署前必须通过 `.env` 或环境变量设置 `DEFAULT_ADMIN_PASSWORD` 为强密码，否则空数据库会拒绝启动。** 已经存在的管理员不会被环境变量覆盖，也不会因密码哈希兼容处理而被自动改密。

## 配置

项目会自动读取根目录下的 `.env` 文件，也可以直接使用环境变量。可参考 `.env.example`：

- `SECRET_KEY`：Flask 会话密钥,**必须**改成随机长字符串,否则在 `APP_DEBUG=0`(生产)模式下会拒绝启动。可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成
- `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD`:首次启动创建管理员时使用,部署前覆盖
- `MAX_UPLOAD_MB`：普通上传文件大小限制，默认 `20`
- `PRODUCT_SYNC_MAX_UPLOAD_MB`：同步类数据包（产品数据同步、业务数据同步）上传大小限制，默认 `512`
- `PRODUCT_IMAGE_REQUEST_MAX_UPLOAD_MB`：一次产品保存或目录导入请求的总上传上限，默认 `160`；单张产品图片仍固定按 30 MB 校验
- `APP_HOST`：本机启动监听地址，默认 `127.0.0.1`
- `APP_PORT`：本机启动端口，默认 `5055`
- `BLD_DATA_DIR`：数据目录，默认 `data`
- `BLD_UPLOAD_DIR`：上传目录，默认 `uploads`
- `BLD_OUTPUT_DIR`：输出目录，默认 `outputs`
- `INTERNAL_API_TOKEN`：可选应急 fallback；日常在网页“内部 API Key”页面生成 Key。完整 Key 只在创建时显示一次，之后仅显示遮罩并支持单条停用；`/api/internal/*` 始终需要 `Authorization: Bearer <token>`

## 目录说明

- `app.py`：应用入口和全局配置
- `app/routes/`：页面路由，按功能拆分
- `app/database.py`：数据库访问和业务数据写入
- `app/migrations.py`：数据库结构迁移
- `app/excel_io.py`：询价 Excel 读写
- `app/drawings.py`：PDF 图纸上传、替换归档和询价图纸包
- `app/material_sheet.py`：生产料单生成
- `templates/`：页面模板
- `static/`：样式和产品图片
- `data/`：运行数据目录，业务 Excel、SQLite 数据、PDF 图纸和上传图片不提交 Git
- `uploads/`：运行时上传文件，按用户目录隔离，不提交 Git
- `outputs/`：运行时导出文件，按用户目录隔离，不提交 Git
- `OPENCLAW_API.md`：供 OpenClaw 机器人调用的内部 API 说明

## 数据库

默认数据库是 `data/products.sqlite3`。这个文件是业务数据，不进入 Git。产品目录 `data/catalog.xlsx`、材料明细 `data/stamping_materials.xlsx`、PDF 图纸目录 `data/drawings/`、物料图纸目录 `data/material_drawings/` 和上传产品图片目录 `data/product_images/` 也按运行数据处理，不进入 Git。每个产品可维护含税单价、产品状态和最多 5 张产品图片；产品状态用于记录球头/衬套配置，例如“1 个球头 2 个衬套”。网页编辑接收最大 30 MB、5000 万像素的 JPG/PNG/WebP 源图片，保存时只保留长边不超过 1920 像素且不超过 500 KB 的 WebP 大图以及最大 320×240 的 WebP 缩略图。产品目录列表只加载缩略图，点击预览时才加载大图。NAS 上的 `data/` 目录要按 NAS 备份策略保护，更新代码时不要用本机数据覆盖 NAS 数据。

管理员菜单里的“产品数据同步”用于两端系统之间交换产品数据包。导出包只包含 `products` 表和 `manifest.json`，可选包含 `data/drawings/`、`data/product_images/`；导入时先预览差异，再增量合并 `products` 表，不覆盖本机账号、内部 API Key 或操作日志。包内更新时间早于当前系统的同 BLD 产品会标记为“包内旧数据”并跳过，避免旧包覆盖新数据；勾选图纸/图片时才复制包内媒体文件，覆盖前会把本机对应文件备份到 `data/local-backups/`。

数据库结构变化集中放在 `app/migrations.py`。新增字段或表时，添加一个新的 migration id 和对应函数。容器会先运行 `python -m scripts.init_database`，初始化成功后才启动 Gunicorn worker；迁移本身也使用跨进程安全的 SQLite 写事务。

产品目录 Excel 中的单元格图片可用 `tools/import_catalog_cell_images.py` 提取到 `data/product_images/`，脚本会解析 `DISPIMG` 图片映射，并应用 Excel/WPS 中的水平或垂直翻转。先运行 dry-run 查看匹配统计，确认后再加 `--apply` 写入图片和数据库：

```bash
tools/import_catalog_cell_images.py "产品目录/BLD catalogue 2603 new(2个OE).xlsx"
tools/import_catalog_cell_images.py "产品目录/BLD catalogue 2603 new(2个OE).xlsx" --apply
```

历史图片可使用同一幂等工具检查或批量转为 WebP；部署重建也会先自动执行一次：

```bash
python tools/migrate_product_images.py
python tools/migrate_product_images.py --apply
```

## 多用户文件和导入规则

上传和输出文件按用户隔离：

```text
uploads/u用户ID-用户名/
outputs/u用户ID-用户名/
```

生成文件名会带用户名，例如 `re260429-007-客户询价.xlsx` 或 `catalog-export-bld-007-260429.xlsx`。普通用户只能看到和下载自己的输出文件，管理员可以在最近结果里看到所有用户和旧根目录输出。

OpenClaw 内部 API 的询价导出固定写入 `outputs/openclaw/`，文件名统一为 `reYYMMDD_源文件名称.xls/xlsx`。号码数组/文字号码没有源文件，导出前必须由机器人询问并传 `source_name`；重名时自动追加 `_2`、`_3`。

会修改全局数据的导入操作使用导入锁，避免多人同时覆盖数据：

- 产品目录导入
- 材料数据导入
- 单价确认导入
- 产品数据包导入

询价匹配和生产计划生成只处理当前用户的上传与输出，不使用全局导入锁。询价结果页生成的图纸压缩包保存在当前用户的 `outputs/u用户ID-用户名/` 下，原始 PDF 图纸保存在 `data/drawings/pdf/` 下，网页编辑上传的产品图片保存在 `data/product_images/` 下。

## 系统更新

右上角管理员菜单里的“系统更新”页面优先读取 `changes/*.json`，并继续读取 `项目交接说明.md` 的历史条目。新变更使用独立片段，避免多人或 AI 反复编辑同一篇长文档。

## 测试

```bash
uv run python scripts/verify.py
```

统一验收包括项目合同、依赖锁、Ruff、Python 语法和完整回归测试；GitHub CI 使用同一入口。
