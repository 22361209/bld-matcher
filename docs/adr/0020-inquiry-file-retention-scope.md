# ADR 0020: Inquiry, Material And Contract File Retention Scope

- Status: accepted
- Date: 2026-07-29
- Owners: BLD

## Context

询价源文件、生成的询价 Excel、询价图纸压缩包和与报价关联的询价文件，以及材料来源/料单与采购/销售合同，需要在 NAS 上长期保留。统一运行保留期还管理同步包和其他网页上传/输出，不能因为这些保存要求而停止清理无关文件。

## Decision

1. 通用 `BLD_UPLOAD_RETENTION_DAYS` 与 `BLD_OUTPUT_RETENTION_DAYS` 保持 30 天默认值。
2. 新增 `BLD_INQUIRY_*`、`BLD_MATERIAL_*` 和 `BLD_CONTRACT_OUTPUT_RETENTION_DAYS`，默认 `0`（长期保留）。
3. 询价上传以 `inquiry-`、`inquiry-text-` 文件前缀识别；询价输出以 `reYYMMDD-` 和 `drawings-` 文件前缀识别。材料以 `material-plan-`、`material-data-` 和 `*料单.xlsx`（包括重复生成的 `*料单_2.xlsx`）识别；合同以用户输出目录下的采购合同、销售合同目录识别。其余受控文件继续走通用保留期。
4. API artifact 仍由自身到期时间管理，不因询价长期保留策略改变。

## Consequences

- 新写入报价的 `attachment_path` 指向长期保留的询价结果，报价单号弹窗可持续提供下载；料单与合同不会因通用清理策略删除。
- 其他工作流不会无限累积临时上传和生成文件。
- 新增询价文件命名规则时，必须同步更新本 ADR、运行文档、保留期分类函数和测试。

## Verification

- 运行保留期测试同时覆盖长期保留的询价文件与按通用保留期清理的非询价文件。
- `scripts/verify.py` 通过配置、类型、运行治理和回归检查。
