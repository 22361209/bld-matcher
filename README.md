# BLD 询价处理系统

一个用于局域网内部使用的 Flask 系统，主要处理客户询价 Excel 匹配、产品目录维护、生产料单生成、账号权限和操作日志。

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

默认管理员：

```text
账号：007
密码：4r3e2w1q
```

首次部署后建议立即登录后台修改默认密码。

## 配置

项目会自动读取根目录下的 `.env` 文件，也可以直接使用环境变量。可参考 `.env.example`：

- `SECRET_KEY`：Flask 会话密钥，正式使用时应改成随机长字符串
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
- `app/material_sheet.py`：生产料单生成
- `templates/`：页面模板
- `static/`：样式和产品图片
- `data/`：基础 Excel 和本地 SQLite 数据
- `uploads/`：运行时上传文件，不提交 Git
- `outputs/`：运行时导出文件，不提交 Git

## 数据库

默认数据库是 `data/products.sqlite3`。这个文件是业务数据，不进入 Git。NAS 上的数据库要按 NAS 备份策略保护，更新代码时不要用本机数据库覆盖 NAS 数据库。

数据库结构变化集中放在 `app/migrations.py`。新增字段或表时，添加一个新的 migration id 和对应函数，让本机和 NAS 在启动连接数据库时自动补齐结构。

## 测试

```bash
.venv/bin/python -m unittest discover
```

当前测试覆盖登录、核心页面访问和 20MB 上传限制。
