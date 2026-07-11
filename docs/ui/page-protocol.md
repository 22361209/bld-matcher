# BLD Page Protocol

## Page Shell

所有完整页面最终继承 `templates/base.html`。基础模板负责语言、meta、资源版本、导航、消息区、页面宽度、全局对话框和脚本入口。业务模板只填充页面插槽。

每张协议页面必须声明：

- `page_id`：稳定的 `domain.view` 标识，例如 `admin.system_updates`，供测试和 JavaScript 定位。
- `page_type`：只能是 `workbench`、`list`、`edit`、`import-preview`、`background-job`、`system-admin` 之一。

标准插槽：

- `page_title`：浏览器标题。
- `page_header`：标题、说明和主要状态。
- `page_toolbar`：主要命令和视图切换。
- `page_filters`：可进入 URL 的筛选条件。
- `page_content`：表格、表单或工作台主体。
- `page_modals`：使用共享 Dialog 组件。
- `page_scripts`：只引用页面 ES Module，不写内联代码。

## Page Types

1. `workbench`：上传或录入、执行、进度、结果和历史。
2. `list`：标题、指标、筛选、表格、分页和行操作。
3. `edit`：对象身份、字段分组、校验、保存/取消和危险区。
4. `import-preview`：上传、解析摘要、差异、确认选项、应用结果。
5. `background-job`：状态、进度、取消、重试、结果和错误详情。
6. `system-admin`：配置、权限、审计和不可逆影响说明。

新页面必须声明所属类型。不能用“新建一套布局”回避协议。

## Shared Components

- `PageHeader`
- `Toolbar`
- `FilterBar`
- `MetricStrip`
- `DataTable`
- `Pagination`
- `FormField` / `FieldError` / `ErrorSummary`
- `FormActions`
- `StatusBadge`
- `EmptyState` / `ErrorState` / `LoadingState`
- `ConfirmDialog`
- `JobProgress`

组件使用语义名称；禁止继续把 `materials-*`、`inquiry-*` 等业务类名当作跨页面组件。

## Interaction Rules

- GET 只读。创建、修改、停用、删除、生成和导入应用使用 POST/PUT/PATCH/DELETE。
- 筛选、排序、分页、标签页和当前视图写入 query string。
- 提交失败保留用户输入，显示错误摘要和字段错误；不得只弹出原始异常字符串。
- 提交中禁用重复操作，但服务端仍必须有幂等或事务保护。
- 破坏性确认显示对象名称、影响和恢复方式。
- 长任务立即返回任务状态，不阻塞请求，也不伪造进度。
- 所有弹窗支持 Escape、焦点圈定、返回焦点和可访问名称。
- 主要操作在桌面与移动端均可见，长文本不得遮挡按钮或表格内容。

## CSS And JavaScript

- CSS 分为 tokens、base、components 和 pages；颜色、间距、字体、边框和 z-index 使用 token。
- 业务页面只能覆盖必要布局，不复制按钮、表单、表格和弹窗样式。
- JavaScript 使用 ES Module、事件委托和 `data-*` 钩子，不按可见文字查找元素。
- 模板禁止 `<script>`、`<style>`、`onclick`、`onchange`、`onsubmit`。
- 页面根节点使用 `data-page="domain.view"`，只初始化当前页面模块。

当前所有完整页面均已迁入 `base.html`，`policy/legacy_allowlist.json` 中独立页面、内联脚本/事件和内联样式基线均为零。`scripts/check_project_contract.py` 阻止重新出现例外，`tests/test_project_contract.py` 额外验证 page ID 全局唯一。

页面专用脚本放在 `static/pages/`，必须以 `body[data-page]` 作为初始化边界。公共交互放在 `static/app.js`；当前破坏性提交统一使用 `data-confirm`，不在模板写事件代码。

## Acceptance

每种页面类型至少维护一个桌面和移动端基准流程，验证正常、空、错误、无权限、提交中和长文本状态。静态协议、服务端渲染回归和真实浏览器验收缺一不可；页面协议变化需要 ADR 或 UI 协议版本说明。
