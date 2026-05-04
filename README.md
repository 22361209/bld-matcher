# BLD 询价处理系统

一个用于局域网内部使用的 Flask 系统，主要处理客户询价 Excel 匹配、产品目录维护、生产料单生成、账号权限、操作日志和系统更新记录。

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

本机可以安装一个名为 `BLD` 的启动器到“应用程序”文件夹，并自动放到 Dock。它只负责启动当前工作区里的 5055 服务并打开浏览器，不用于 NAS。

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
- `MAX_UPLOAD_MB`：上传文件大小限制，默认 `20`
- `APP_HOST`：本机启动监听地址，默认 `127.0.0.1`
- `APP_PORT`：本机启动端口，默认 `5055`
- `BLD_DATA_DIR`：数据目录，默认 `data`
- `BLD_UPLOAD_DIR`：上传目录，默认 `uploads`
- `BLD_OUTPUT_DIR`：输出目录，默认 `outputs`

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

## 数据库

默认数据库是 `data/products.sqlite3`。这个文件是业务数据，不进入 Git。产品目录 `data/catalog.xlsx`、材料明细 `data/stamping_materials.xlsx`、PDF 图纸目录 `data/drawings/` 和上传产品图片目录 `data/product_images/` 也按运行数据处理，不进入 Git。每个产品最多可维护 5 张产品图片，网页编辑上传的图片文件保存在 `data/product_images/`。NAS 上的 `data/` 目录要按 NAS 备份策略保护，更新代码时不要用本机数据覆盖 NAS 数据。

数据库结构变化集中放在 `app/migrations.py`。新增字段或表时，添加一个新的 migration id 和对应函数，让本机和 NAS 在启动连接数据库时自动补齐结构。

产品目录 Excel 中的单元格图片可用 `tools/import_catalog_cell_images.py` 提取到 `data/product_images/`，脚本会解析 `DISPIMG` 图片映射，并应用 Excel/WPS 中的水平或垂直翻转。先运行 dry-run 查看匹配统计，确认后再加 `--apply` 写入图片和数据库：

```bash
tools/import_catalog_cell_images.py "产品目录/BLD catalogue 2603 new(2个OE).xlsx"
tools/import_catalog_cell_images.py "产品目录/BLD catalogue 2603 new(2个OE).xlsx" --apply
```

## 多用户文件和导入规则

上传和输出文件按用户隔离：

```text
uploads/u用户ID-用户名/
outputs/u用户ID-用户名/
```

生成文件名会带用户名，例如 `re260429-007-客户询价.xlsx` 或 `catalog-export-bld-007-260429.xlsx`。普通用户只能看到和下载自己的输出文件，管理员可以在最近结果里看到所有用户和旧根目录输出。

会修改全局数据的导入操作使用导入锁，避免多人同时覆盖数据：

- 产品目录导入
- 材料数据导入
- 单价确认导入

询价匹配和生产计划生成只处理当前用户的上传与输出，不使用全局导入锁。询价结果页生成的图纸压缩包保存在当前用户的 `outputs/u用户ID-用户名/` 下，原始 PDF 图纸保存在 `data/drawings/pdf/` 下，网页编辑上传的产品图片保存在 `data/product_images/` 下。

## 系统更新

右上角管理员菜单里的“系统更新”页面会读取 `项目交接说明.md` 中的“当前最近重要变更”，用于在网页内查看项目代码、页面、权限、部署和核心业务规则的更新记录。更新条目使用 `YYYY-MM-DD · commit · 标题` 格式，同一天多次更新会拆成多条版本记录。

## 测试

```bash
.venv/bin/python -m unittest discover
```

当前测试覆盖登录、核心页面访问、系统更新页面、20MB 上传限制、用户文件隔离、导入锁、迁移记录、PDF 图纸上传和询价图纸包。
