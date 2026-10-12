# finance_ledger_personal 个人记账 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_ledger_personal/` + `frontend/src/modules/finance-ledger-personal/`
> 级别：全新模块 ｜ 数据模型参考：ezBookkeeping（MIT）｜ 前端表格：ag-grid-vue3 36.2.0（MIT）

## 功能

个人复式之外的轻记账：两级账户（现金/银行卡/电子钱包）、两级收支分类、交易（收/支、金额、日期、账户、分类、备注、标签）、月度预算（分类维度）。CSV 导入导出（utf-8-sig 中文列名）；月度汇总与最近 N 月趋势。

## API（/api/finance/ledger/personal）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | /accounts | 账户列表 / 新建 |
| PUT/DELETE | /accounts/{id} | 更新 / 删除（有交易引用时拒绝） |
| GET/POST | /categories | 分类列表（可按类型过滤）/ 新建（最多两级） |
| PUT/DELETE | /categories/{id} | 更新 / 删除 |
| GET/POST | /transactions | 列表（月份/分类/账户/关键词 + 分页）/ 记一笔 |
| PUT/DELETE | /transactions/{id} | 更新 / 删除 |
| GET | /stats/monthly?month= | 月度汇总（收入/支出/结余 + 分类占比） |
| GET | /stats/trend?months= | 最近 N 月收支趋势 |
| GET/PUT | /budgets | 某月预算视图 / 整月保存（幂等，≤0 清除） |
| POST | /import/csv | CSV 导入（上限 5MB） |
| GET | /export/csv | 导出（可带筛选条件） |

## 表

- `mod_pl_account`、`mod_pl_category`（两级，parent_id）、`mod_pl_transaction`、`mod_pl_budget`
- seed：仅空表时灌入默认账户（现金/银行卡/微信/支付宝）与常用收支分类，幂等

## 前端

快速记账页（默认路由，表单 + 当日流水 + 账户/分类管理抽屉）、流水页（AG Grid 虚拟表格 + 筛选 + 导出）、统计页（ECharts 月度收支柱状 + 分类占比环图 + 预算进度）。

## 测试与验收

- pytest：CRUD 全链、统计口径、CSV 往返、边界（负数/大金额/空分类/非法日期）——全绿
- Agent 工具：`pl_add_transaction`（创建）、`pl_monthly_summary`、`pl_recent`（只读）
