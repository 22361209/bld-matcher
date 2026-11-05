# 本机 Docker Desktop 运维

本配置用于 Mac mini 开发与本机验证。NAS 继续使用 `docker-compose.yml` 和既有 Git 发布流程；本机操作不连接 NAS、不同步两端运行数据。

## 日常启动与停止

1. 打开 Docker Desktop，在 Containers 中找到 `bld-local` 项目。
2. 对 `bld-matcher-local` 容器点击启动、停止或重启按钮。
3. 等待服务启动完成，访问 <http://127.0.0.1:5055/>。

容器保留 `data/`、`uploads/`、`outputs/` 的本机目录挂载，`.env` 从当前工作区加载；镜像不携带运行数据或密钥。不要移动项目目录后继续使用旧容器，也不要同时启动原生 BLD.app。主服务的每次启动只做既有数据库初始化流程，然后启动 Gunicorn；图片转换只在受控更新流程中执行。

构建上下文也排除本机 `产品目录/`、`询价/`、`单价/` 原始 Excel 目录和开发缓存；这些文件仍保留在宿主机，不随镜像复制。

命令行等价入口：

```bash
bash tools/docker_local.sh start
bash tools/docker_local.sh stop
bash tools/docker_local.sh status
bash tools/docker_local.sh logs
```

`start` 只启动已经创建的容器。首次没有容器时，先执行下面的更新流程。脚本固定使用本机 `desktop-linux` context 和显式 `compose.local.yml`，不会采用默认 NAS Compose 配置或远程 Docker context。

Docker Desktop 必须运行，容器才能运行。可在 Docker Desktop Settings / General 中选择登录后启动 Docker Desktop；容器使用 `unless-stopped`，Docker 恢复后会恢复之前运行中的容器，手动停止的容器仍需点击启动。本次部署不修改 Docker Desktop 的全局偏好。

## 选择更新入口

在 Mac mini 项目目录执行命令。先看 `git status --short` 和变更片段，核对本次要发布的内容。本机快速入口以**当前工作树**生成快照，因此尚未提交的代码也可能进入本次发布。修改源文件或刷新浏览器都不会单独触发发布。

| 本次改动 | 入口 |
| --- | --- |
| 调整页面布局、CSS、JavaScript 或普通 Python 业务逻辑 | `bash tools/docker_local.sh quick-update` |
| 首次安装快速更新、改依赖、`.env` 或 Compose 配置、数据库结构或历史数据迁移、图片迁移 | `bash tools/docker_local.sh update` |

快速入口检查待发布内容与当前容器镜像的差异，通过检查后发布一个固定版本并验证本机 5055 服务。刷新浏览器即可查看切换后的页面。若入口提示需要完整更新，先查看列出的原因，再按下文操作。若版本或业务探针失败，按命令输出核对当前实际运行版本及错误，排除原因后重试。

可从 Mac mini 独立核对快速发布的版本：`curl -sSI http://127.0.0.1:5055/health/live` 响应中的 `X-BLD-Release` 应与命令返回的版本一致。再运行 `uv run python -m scripts.runtime_probe --base-url http://127.0.0.1:5055` 检查就绪状态和登录页，打开本次修改的页面验证实际效果。

普通页面或业务逻辑改动的片段可声明 `{"data":"none","api":"compatible","operations":"quick-update"}` 作为 `impact`。依赖、数据库和图片迁移等改动按实际影响声明，即使随批还有界面改动，也应执行完整更新。

重启同一个容器会保留上次快速发布的代码快照。完整更新用新镜像重新创建容器时会丢弃旧容器内的快照，并从镜像中的版本启动。需要保留或传到另一台电脑的改动应先提交到 Git。

## 首次部署与完整更新

先完成代码修改及变更片段；从远程同步代码时，先处理本地未提交改动，再选择正确分支更新。AI 执行预计超过 5 分钟的操作前，遵守 `AGENTS.md` 的长操作规则。

```bash
bash tools/docker_local.sh update
```

更新依次执行：

1. 在本机锁定环境运行 `uv run python scripts/verify.py`，失败则停止。
2. 构建 `bld-matcher:local` 镜像；基础镜像、依赖清单和安装指令匹配时可复用 Docker 缓存。构建期间旧容器继续运行，构建失败不会停止旧容器。
3. 停止本机主容器；如其他进程占用 5055，拒绝迁移，不强行终止它。
4. 对本机运行数据做成套备份。
5. 用新镜像运行数据库初始化与严格图片迁移；缺图、转换失败等情况返回失败，不使用 `--allow-failures`。
6. 启动主容器并等待健康检查，再运行健康及最小业务探针。

停机、备份和迁移阶段本机页面暂不可用。NAS 不需要停机。失败后保留日志、迁移报告和备份；不会自动用旧镜像接管已经迁移过的数据。

## 完整更新的备份与验收

更新会调用：

```bash
uv run python tools/backup_local_runtime.py
```

只有本机 5055 已停止、当前工作区采用默认本机运行目录时才允许备份。备份在 `~/Library/Application Support/BLD/backups/docker-desktop-<时间戳>/`，该目录不进入 Git、构建上下文或 NAS。主 SQLite 库及存在的 `data/costing-trial/costing.sqlite3` 均通过 SQLite Backup API 生成一致性副本，相关 WAL/SHM/journal 不裸拷；完整性检查作用于副本，不通过业务连接触发迁移。更新前也停止独立成本试用服务，避免备份期间源文件变化。配套保存 `data/`、`uploads/`、`outputs/`、`.env`，并生成校验清单。备份目录权限为 0700，`.env` 为 0600；失败备份保留明确的不完整标记。

验收命令：

```bash
bash tools/docker_local.sh status
uv run python -m scripts.runtime_probe --base-url http://127.0.0.1:5055
```

同时核对 `data/product_image_migration_report.json` 的 `status`、`missing`、`failed`、`errors`；不能仅因容器正在运行就认为迁移成功。打开登录页并确认页面可用。首次接入还需实际执行一次停止、再启动并重跑探针，确认日常点击启动能够复用容器和持久化数据。

## 回退

原生模式的启动脚本和 BLD.app 保留。需要切换时，先停止本机 Docker 容器，再使用 `bash tools/restart_local_5055.sh`。原生模式与 Docker 复用这份本机运行数据，不能同时运行。

如果新版本改变了数据库或图片，回滚版本前先核对迁移报告，按成套备份恢复数据库与配套文件；不能只换回旧镜像。运行数据恢复属于覆盖操作，需要用户对具体恢复方向单独授权。本手册不授权覆盖或恢复 NAS 数据。
