# 群晖 NAS Docker 部署说明

## 0. 重要更新规则

NAS 上的程序更新**只能通过 Git 仓库发布**：

```text
本机代码 -> git push nas main -> NAS 运行目录 git pull/reset -> docker-compose 重建
```

不要用 Finder、File Station、`scp`、`rsync`、压缩包或手动拖拽去覆盖 `/volume1/docker/bld-matcher` 的程序文件。直接覆盖会让 NAS 运行目录脱离 Git 管理，也容易误碰运行数据。

如果本机 Git 仓库异常、无法正常 push，应先修复本机仓库，或从 NAS 仓库干净克隆到临时目录后提交推送；仍然不要直接覆盖 NAS 运行目录。

## 1. 准备目录

在群晖 File Station 中创建目录：

```text
/docker/bld-matcher
```

首次部署时，从 NAS Git 仓库检出代码到这个目录。后续更新不要手动替换文件。

确认这些目录存在：

```text
/docker/bld-matcher/data
/docker/bld-matcher/data/drawings
/docker/bld-matcher/data/product_images
/docker/bld-matcher/uploads
/docker/bld-matcher/outputs
```

如果没有，可以手动创建。`data` 目录最重要，里面的 `products.sqlite3` 是产品库、账号和日志。

## 2. 使用 Container Manager 启动

1. 打开群晖 `Container Manager`
2. 进入 `项目`
3. 点击 `新增`
4. 项目名称填写：`bld-matcher`
5. 路径选择：`/docker/bld-matcher`
6. 选择使用现有 `docker-compose.yml`
7. 构建并启动

## 3. 访问地址

启动成功后，在办公室电脑浏览器访问：

```text
http://群晖局域网IP:5055
```

例如：

```text
http://192.168.1.20:5055
```

## 4. 首次登录

管理员账号：

```text
登录名：007
密码：由 DEFAULT_ADMIN_PASSWORD 环境变量决定 (默认 change-me-on-first-login)
```

**首次启动前**请在 `docker-compose.yml` 的 `environment` 中设置 `DEFAULT_ADMIN_PASSWORD` 和强随机的 `SECRET_KEY`,登录后立即在 `账号管理` 中再次修改密码。

登录后可以在 `账号管理` 中新增其他用户、修改角色、重置密码。

## 5. 数据备份

重点备份：

```text
/docker/bld-matcher/data/products.sqlite3
/docker/bld-matcher/data/drawings
/docker/bld-matcher/data/product_images
```

建议定期备份整个目录：

```text
/docker/bld-matcher/data
```

`data/drawings` 是产品 PDF 图纸源文件和替换归档，`data/product_images` 是网页编辑上传的产品图片，`uploads` 是上传过的询价源文件，`outputs` 是生成的结果文件，也可以按需要备份。

## 6. 升级程序

以后更新程序时：

1. 本机确认代码已经 commit，并测试通过
2. 本机推送到 NAS Git 仓库
3. NAS 运行目录从 Git 更新
4. 重新构建并启动项目

### MacBook Air 中转前检查

第一次在 MacBook Air 上接入 GitHub -> NAS 流程时，先确认本机代码历史和 NAS 历史已经连上，且 NAS Git 仓库不再跟踪运行数据：

```bash
git fetch nas main
git merge-base main nas/main >/dev/null || echo "STOP: GitHub 和 NAS Git 历史尚未打通，不能直接 push/reset"
git ls-tree -r --name-only nas/main -- data static/product_images | sed -n '1,40p'
```

如果第二行输出 `STOP`，或第三行列出 `data/products.sqlite3`、`data/catalog.xlsx`、产品图片等运行数据，先做一次专门的 NAS Git 清理迁移；不要继续执行下面的 `git push nas main` 和 NAS `git reset --hard`。

本机：

```bash
# Mac mini 本机开发目录
cd "/Users/linzhenyue/Projects/bld-matcher"
# MacBook Air 中转目录为 /Users/linzhenyue/Project5inMBA；中转前先 git pull --ff-only github main
git status -sb
git push nas main
```

如果是在 Mac mini 上开发，先 `cd` 到 Mac mini 的实际项目目录；如果是在 MacBook Air 上做公司内网中转，使用 `/Users/linzhenyue/Project5inMBA`。

NAS：

```bash
ssh -i ~/.ssh/bld_matcher_deploy deploy@192.168.110.93
cd /volume1/docker/bld-matcher
git fetch origin main
git reset --hard origin/main
git status -sb
sudo /usr/local/bin/docker-compose up -d --build
sudo /usr/local/bin/docker-compose exec -T bld-matcher python tools/generate_product_thumbnails.py
sudo /usr/local/bin/docker-compose ps
```

上述 Docker 重启命令会要求输入 NAS sudo 密码。使用 Codex 操作时，必须打开可见 macOS Terminal 窗口让用户输入密码；不要在隐藏执行会话中运行会等待密码的 sudo 命令。

`generate_product_thumbnails.py` 会在 NAS 的 `data/product_images/thumbs/` 下生成产品列表用的小缩略图。这个目录属于运行数据，不进 Git；如果产品图大量更新，重建容器后再跑一次即可。

确认 `git status -sb` 只显示 `## main`，表示 NAS 运行目录没有脱离 Git 管理。

不要删除或覆盖：

```text
data/
uploads/
outputs/
.env
```

当前 Compose 配置使用 `restart: unless-stopped`，Dockerfile 使用 Gunicorn 运行 `wsgi:app`。这和本机临时 Flask 开发进程不同；如果容器进程异常退出，Docker 会按重启策略自动拉起。

## 7. 安全建议

建议只在办公室内网使用。不要直接把 5055 端口开放到公网。

如果需要外网访问，建议使用 VPN，或配置群晖反向代理、HTTPS 和强密码策略。
