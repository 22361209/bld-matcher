# Change Fragments

每次用户可见、数据、权限、接口、配置或运维变化新增一个 JSON 文件。文件名使用 `YYYYMMDD-short-title.json`，内容至少包括：

```json
{
  "date": "2026-07-11",
  "version": "unreleased",
  "title": "标题",
  "entries": ["用户和运维能够理解的变化"],
  "impact": {"data": "none", "api": "compatible", "operations": "restart-required"}
}
```

系统更新页面将逐步以变更片段为事实来源。发布后由生成器汇总，详细历史不再依赖人工编辑同一篇长文档。
