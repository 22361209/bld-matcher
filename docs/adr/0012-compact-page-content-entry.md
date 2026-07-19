# ADR 0012: Compact Page Content Entry

- Status: accepted
- Date: 2026-07-20
- Owners: BLD

## Context

全站精密工业工作台改造保留了每页独立的标题、说明和指标摘要。密集业务页面中，这些内容与导航高亮、浏览器标题以及紧随其后的检索或表单重复，持续占用纵向工作空间。

## Decision

1. 所有保留 Web 页面不再渲染 `workspace-header` 或 `search-hero` 作为独立页首；询价首页和生产料单可在主搜索控件前使用紧凑 `context-strip` 提供必要的工作区定位与关键统计。
2. 导航高亮和浏览器标题承担页面身份；页面从首个检索、操作、表单、列表或内容区直接开始。
3. 业务状态、匹配汇总和文件提示必须留在其所属的操作或内容区，不能因移除页首而消失。
4. 不更改 Flask/Jinja 渲染方式、路由、权限、CSRF、数据、API、下载或表格协议。

## Consequences

- 所有页面在导航下获得更紧凑的可用高度，列表和表单优先可见。
- 页面模板不得重新引入 `workspace-header` 或 `search-hero`；回归测试覆盖这一规则。
- 用户仍可从页面内的操作区、数据区标题和浏览器标题识别当前工作内容。

## Verification

- `scripts/check_project_contract.py` 通过。
- 服务端页面回归测试确认匹配汇总、文件提示、筛选、表单和操作继续出现。
- 独立 `5066` 预览服务用于人工浏览器验收。
