# ADR 0005: Domain And Page Protocol Migration

- Status: accepted
- Date: 2026-07-11
- Owners: BLD

## Context

报价、产品和询价已经进入模块化单体边界，但合同、材料、发货通知和后台管理仍由 `app/routes/` 直接编排 SQLite 事务。除系统更新外，22 张完整页面各自重复 HTML 文档、导航、消息和资源入口；产品、材料和物料图纸还保留内联 JavaScript，四张表格用内联样式固定列宽。这样的页面和路由虽然能运行，却会让后续开发继续复制旧结构，也无法证明架构与 UI 协议不会漂移。

## Decision

1. 新增 `app/modules/contracts/`、`materials/`、`shipping/` 和 `admin/`。Web 适配器只解析请求和构造响应，事务、审计、文件生成与补偿由 Application Service 编排，SQLite 访问只存在于 Repository。
2. 保留所有既有 URL、端点名称、权限装饰器、表单字段、模板上下文、下载位置和旧页面可见文案；本阶段不新增 API v1 资源，也不改变 NAS 或运行数据。
3. 材料 Excel 使用同目录临时文件原子替换。数据库导入失败时恢复原 Excel；合同 PDF、发货通知、模板和物料图纸如果审计事务失败，则删除本次新文件。
4. 所有完整页面继承 `templates/base.html`，声明唯一 `page_id` 和批准的 `page_type`。基础模板统一负责文档、导航、消息、公共 CSS/JavaScript 和页面弹窗挂载位置。
5. 模板内联脚本、事件处理器和样式基线降为零。产品、材料和物料图纸脚本迁入按 `body[data-page]` 初始化的 ES Module；破坏性确认改用 `data-confirm` 和公共事件委托。
6. `policy/legacy_allowlist.json` 不再允许独立页面或模板内联资源。项目合同和单元测试共同检查基础模板继承、有效类型、唯一页面 ID 与零内联资源。

## Alternatives

- 只给旧模板外面套一层 include：仍会保留重复文档、导航和脚本所有权，无法形成页面协议。
- 一次改为 SPA：会扩大技术栈、部署和迁移风险，当前服务端页面没有明确需求支持这项成本。
- 仅用 Repository 包住旧路由：路由仍会承载事务、文件补偿和业务规则，不符合 Application Service 边界。
- 文件成功后再尽力写审计：会留下未审计输出；本决定把文件和数据库失败纳入同一用例的补偿逻辑。

## Consequences

- 后续页面必须选择现有 page type、复用基础壳并通过静态协议检查，不能重新加入例外名单。
- 合同、材料、发货通知和后台管理可以独立测试事务和文件补偿，不再需要 Flask 请求上下文才能验证核心用例。
- `app/database.py` 暂时继续保存历史 helper，Repository 通过 `commit=False` 兼容参数把提交权交给 Service；阶段 6 再完成剩余运行治理与历史债务清零。
- 页面外观和交互仍使用现有 CSS/DOM，迁移本身不重新设计业务页面。

## Verification

- `tests/test_domain_page_modules.py` 覆盖用户/API Key/日志事务、材料文件恢复、合同产品补全与失败补偿、发货模板/预览/生成和审计。
- `tests/test_project_contract.py` 验证全部协议页面继承基础模板、页面 ID 唯一且模板内联资源为零。
- `tests/test_app.py` 继续覆盖旧 URL、合同 PDF、材料明细、发货通知、产品嵌入编辑和询价下载弹窗兼容行为。
- `scripts/check_project_contract.py` 阻止独立 HTML、内联代码和已迁移路由数据库直连重新出现。
