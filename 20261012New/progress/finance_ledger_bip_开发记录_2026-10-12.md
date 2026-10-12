# finance_ledger_bip 企业记账（用友 BIP）· 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_ledger_bip/` + `frontend/src/modules/finance-ledger-bip/`
> 级别：全新模块（BIP 导出兼容）｜ 联动：finance_vault 入仓契约

## 功能

兼容用友 BIP 三类导出 xlsx 的导入与查询：

| 类型 | 列签名（实测契约） |
|------|------|
| 凭证列表 voucher | 日期/核算账簿/凭证号/摘要/借方金额/贷方金额/会计凭证号 |
| 辅助明细账 aux | 科目编码/科目名称/日期/核算账簿/凭证号/摘要 + 借方/贷方本币 |
| 科目余额表 balance | 科目编码/科目名称/期初·本期·期末 借贷方常见变体（宽松匹配 + 人工映射兜底） |

导入向导：上传 → 列签名识别类型（识别不了可人工指定）→ 前 50 行预览 + 列映射建议 → 确认入库。入库规则：凭证级借贷平衡校验（不平衡记 warning 不阻断）；重复（核算账簿+凭证号）跳过计数；原文件留档 inbox。

## API（/api/finance/ledger/bip）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /imports/preview | 上传预览（类型识别 + 列映射建议 + token） |
| POST | /imports/confirm | 确认入库（token + 类型/映射/期间） |
| GET | /imports | 批次列表（含警告与统计） |
| GET | /vouchers | 凭证组合筛选（期间/账簿/凭证号/摘要关键词）+ 分页 |
| GET | /vouchers/{id} | 凭证详情（分录借贷两栏对称 + 平衡标记） |
| GET | /entries | 分录查询（科目编码前缀/期间等） |
| GET | /balances/summary?period= | 科目发生额汇总 |
| POST | /vouchers/export-to-vault | 凭证入仓（vault 不可用 → 503） |

## 表

- `mod_bip_batch`、`mod_bip_voucher`（period/ledger/voucher_no 唯一约束）、`mod_bip_entry`（line_no 借贷）、`mod_bip_balance`

## 安全

- token 格式白名单 `^[0-9a-f]{32}\.(xlsx|xls)$` 防路径穿越；上传扩展名/大小限制
- Agent 工具：`bip_voucher_query`、`bip_balance_query`（只读）

## 测试与验收

- pytest 21 例：三类识别、人工映射、平衡校验、重复跳过、组合查询、汇总、入仓联动（成功/503 降级/期间校验）、Agent 工具行为——全绿
