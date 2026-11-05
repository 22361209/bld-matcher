# 群晖 NAS Docker 部署说明

## 0. 重要更新规则

NAS 上的程序更新**只能通过 Git 仓库发布**：

```text
局域网发布电脑验证代码 -> git push nas main -> 群晖从本地 Git 仓库取同一提交 -> 快速更新或完整重建
```

不要用 Finder、File Station、`scp`、`rsync`、压缩包或手动拖拽去覆盖 `/volume1/docker/bld-matcher` 的程序文件。直接覆盖会让 NAS 运行目录脱离 Git 管理，也容易误碰运行数据。

如果本机 Git 仓库异常、无法正常 push，应先修复本机仓库，或从 NAS 仓库干净克隆到临时目录后提交推送；仍然不要直接覆盖 NAS 运行目录。

群晖 BuildKit 使用纯 ASCII 的 `.dockerignore`。根目录下的 `产品目录/`、`询价/`、`单价/` 由通配规则排除，系统更新页使用的 `项目交接说明.md` 会随镜像打包。

## 1. 准备目录

在群晖 File Station 中创建目录：

```text
/docker/bld-matcher
```

首次部署时，从 NAS Git 仓库检出代码到这个目录。后续更新不要手动替换文件。

确认这些目录存在：

```text
/docker/bld-matcher/data
/docker/bld-matcher/data/customer_files
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
密码：必须由 DEFAULT_ADMIN_PASSWORD 环境变量显式设置
```

**首次启动前**请在 `.env` 中设置 `DEFAULT_ADMIN_PASSWORD` 和强随机的 `SECRET_KEY`；空数据库缺少管理员密码时会拒绝启动。登录后可在 `账号管理` 中再次修改密码。

登录后可以在 `账号管理` 中新增其他用户、修改角色、重置密码。

## 5. 数据备份

重点备份：

```text
/docker/bld-matcher/data/products.sqlite3
/docker/bld-matcher/data/customer_files
/docker/bld-matcher/data/drawings
/docker/bld-matcher/data/product_images
```

建议定期备份整个目录：

```text
/docker/bld-matcher/data
```

`data/customer_files` 是客户标贴、包装和单据模板等长期资料，必须与 `data/products.sqlite3` 成套备份和恢复，不能只恢复其中一项。`data/drawings` 是产品 PDF 图纸源文件和替换归档，`data/product_images` 是网页编辑上传的产品图片，`uploads` 是上传过的询价源文件，`outputs` 是生成的结果文件，也可以按需要备份。

## 6. 升级程序

以后更新程序时：

1. 本机确认代码已经 commit，并测试通过
2. 本机推送到 NAS Git 仓库
3. NAS 运行目录从 Git 更新
4. 普通应用改动走快速更新。首次接入、依赖、数据库、图片迁移或运维配置变动走完整发布。

### 局域网电脑中转前检查

每台发布电脑准备项目仓库、Git、OpenSSH、公钥登录和 uv。需要从 GitHub 同步时，在该电脑上拉取并核对目标提交。群晖只从本机的 `/volume1/git/bld-matcher.git` 取代码。首次连入群晖时先在可见终端核对 SSH 主机指纹，配置目标 remote（已有 `nas` 时先核对 URL）。

```bash
git remote add nas deploy@192.168.110.93:/volume1/git/bld-matcher.git
git remote get-url --push nas
git fetch nas main
git merge-base main nas/main
git ls-tree -r --name-only nas/main -- data uploads outputs .env static/product_images
```

如果 `git merge-base` 找不到共同祖先，或最后一行列出 `data/products.sqlite3`、`data/catalog.xlsx`、产品图片等运行数据，先做一次专门的 NAS Git 清理迁移；不要继续执行下面的 `git push nas main` 和 NAS `git reset --hard`。

### 首次启用快速更新

1. 先完成下面的完整发布，让正在运行的群晖容器带有新的快速更新程序及构建基线。确认容器、健康与登录探针正常。
2. 管理员在可见终端审阅该次提交的 `tools/nas/quick-bld-matcher`，将已核对的脚本安装到 root 管理的目录。脚本内的 Docker 安装路径为 `/usr/local/bin/docker`、Compose 工作目录为 `/volume1/docker/bld-matcher`。如需适配 DSM 安装路径，应先核对实际路径和所有者。
3. 用 `visudo` 创建单独的 sudoers 配置并校验。下面的 `""` 限定命令必须没有参数，提交号由入口从标准输入读取。

```sh
cd /volume1/docker/bld-matcher
sudo install -o root -g root -m 0755 tools/nas/quick-bld-matcher /usr/local/sbin/quick-bld-matcher
sudo visudo -f /etc/sudoers.d/bld-matcher-quick
# 添加下行并保存
# deploy ALL=(root) NOPASSWD: /usr/local/sbin/quick-bld-matcher ""
sudo visudo -c
```

脚本更新时需重新审阅和安装 root 副本。保留原有 `rebuild-bld-matcher` 完整发布命令。

### 日常快速更新

在能访问 NAS 的局域网电脑上，进入已提交且干净的 `main`。macOS、Linux 直接运行下列命令。Windows 可在 WSL 中使用同样的命令和该环境的 SSH 凭据，完整验收包含 Bash 等 POSIX 工具。

```bash
uv run python tools/nas_quick_update.py
```

可用 `--identity-file PATH` 指定该电脑的私钥。默认查找用户目录下的 `.ssh/bld_matcher_deploy`，也支持 OpenSSH 配置与 ssh-agent。入口以无交互模式取回 NAS 的 main，检查待推送历史中的运行文件，随后在本地临时 Git 副本检出指定提交并执行完整验收（`uv run python scripts/verify.py --base-ref <NAS原提交>`），通过后将同一提交快进推送到 `nas/main`，随后通过 SSH 请求群晖启用这个提交。不同电脑使用同一个 NAS remote 和非强制推送，即可接续发布。

群晖受限入口检查正式容器的身份，以 `deploy` 身份核对本地 Git 来源、受保护运行路径和干净的工作树。NAS 仅快进到已验证提交，从自己的 bare 仓库导出对应源码，交给容器内的发布工具判定、切换和探测。源代码需与容器的依赖和迁移基线兼容。快速更新失败时请查看报错；Git 推送和服务启用是两个独立的结果，即使 Git 已到目标提交，服务也可能仍运行旧版。依赖、迁移等超出快速范围的改动须走完整发布。群晖自己的 `.env` 独立存放，脚本无法从 Git 检测其修改。改过 `.env` 时请主动选完整发布，以便新容器载入配置。

发布成功以容器内两个 worker 的版本确认和业务探针为准；也可在可访问服务的电脑上执行 `curl -sD - -o /dev/null http://192.168.110.93:5055/health/live`，核对 `X-BLD-Release` 等于这次快速发布的完整 SHA。候选校验失败或服务回退时，NAS 工作树可能已经快进到新 SHA，排查时以实际服务版本为准。

若进程被强制杀死而遗留 `/var/run/quick-bld-matcher.lock`，由管理员先核实没有正在进行的发布，再在可见终端清理该锁目录。完整重建前照常停止写入并做好 NAS 自身运行数据的备份。

### 完整发布

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
sudo -n /usr/local/sbin/rebuild-bld-matcher rebuild
sudo -n /usr/local/sbin/rebuild-bld-matcher status
```

NAS `deploy` 用户已配置受限免密 sudo wrapper：`/usr/local/sbin/rebuild-bld-matcher`。常规部署时，Codex 应在隐藏 shell 中使用 `sudo -n /usr/local/sbin/rebuild-bld-matcher rebuild` 和 `status`；`sudo -n` 会在权限缺失时立刻失败，不会卡住等待密码。不要把它替换成 unrestricted `sudo /usr/local/bin/docker-compose ...`。

只有 wrapper 缺失、权限不足，或需要执行 wrapper 未覆盖的额外 sudo 命令时，才打开可见 Terminal 窗口让用户输入 NAS sudo 密码。

如需在大量产品图新增或替换后生成缩略图，先确认 wrapper 是否已覆盖该操作；如果没有覆盖，再使用可见 Terminal 执行：

```bash
sudo /usr/local/bin/docker-compose exec -T bld-matcher python tools/generate_product_thumbnails.py
```

`generate_product_thumbnails.py` 会在 NAS 的 `data/product_images/thumbs/` 下生成产品列表用的小缩略图。这个目录属于运行数据，不进 Git；如果产品图大量更新，重建容器后再跑一次即可。

确认 `git status -sb` 只显示 `## main`，表示 NAS 运行目录没有脱离 Git 管理。

不要删除或覆盖：

```text
data/
uploads/
outputs/
.env
```

当前 Compose 配置使用 `restart: unless-stopped`。Dockerfile 会先运行数据库初始化和迁移，成功后再启动 Gunicorn，并通过 `/health/ready` 健康检查，发布后再核对最小业务探针和登录页；初始化失败时不会带病启动 worker。这和本机临时 Flask 开发进程不同；如果容器进程异常退出，Docker 会按重启策略自动拉起。

## 7. 安全建议

建议只在办公室内网使用。不要直接把 5055 端口开放到公网。

如果需要外网访问，建议使用 VPN，或配置群晖反向代理、HTTPS 和强密码策略。
