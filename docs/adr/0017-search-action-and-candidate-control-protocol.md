# ADR 0017: Search, Action And Candidate Control Protocol

- Status: accepted
- Date: 2026-07-29
- Owners: BLD

## Context

不同页面曾分别为搜索框、紧邻的新增按钮和候选下拉补写页面级 CSS。搜索清除功能会把输入框包入 `.search-input-wrap`，其通用 `width: 100%` 在行内新增表单中会把按钮挤开；候选下拉如果只依赖浏览器按钮默认文字色，又会出现默认状态文字与背景对比不足的问题。反复逐页修正会继续造成尺寸、位置和按钮语义漂移。

## Decision

1. 保留现有 `search-form` 作为搜索字段皮肤和默认桌面字段宽度的共享入口，字段宽度通过共享 `--search-field-width` token 定义为 320px。
2. 新增跨页面 `inline-search-command` 布局，专用于「一个输入 + 一个立即执行操作」：桌面端 16px 内容缩进、8px 间距、不换行，输入（包括搜索清除生成的包裹层）固定为共享字段宽度，操作保持紧邻且不拉伸；窄屏才换行并改用 12px 横向缩进。既有 `search-command` 保留给询价和料单首页的命令面板。
3. 按钮继续使用已有 `linear-button` 的 `primary`、`subtle`、`danger` 语义：每个操作组只有一个当前主动作，次要/可逆和破坏性动作分别使用后两者。工具栏、表单和行内命令使用各自的共享容器，不以页面级按钮尺寸或颜色对齐。
4. 所有候选下拉显式声明既有工作台 token：`--linear-surface`、`--linear-text`、`--linear-muted`、`--linear-accent` 与 `--linear-subtle`。默认候选项就必须可读，悬浮或键盘激活只表示状态，不负责修复文字可见性。
5. 这些约束由页面协议和静态回归测试共同维护；新增候选控件或例外必须更新两者。

## Consequences

- 客户和产品候选值管理页删除重复的页面级行内布局，使用同一共享实现。
- 产品候选选择器和列筛选候选切换为既有 token，不引入新的颜色值。
- 后续页面可区分单字段命令行与多条件筛选、表格列筛选、弹窗表单，避免把所有输入强制成同一布局。
- 不涉及数据模型、API、权限或运行部署合同；静态资源更新后本地服务需重启或刷新以取得新版本。

## Verification

- `tests/test_admin_add_form_layout.py`：共享行内命令的宽度、缩进、换行和页面复用。
- `tests/test_combobox_styles.py`：combobox、产品候选选择器和列筛选候选的显式文字与背景 token。
- `scripts/check_project_contract.py`：共享样式职责、页面协议与变更片段门禁。
