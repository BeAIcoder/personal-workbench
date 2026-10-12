# finance_expense 费用分类 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_expense/` + `frontend/src/modules/finance-expense/`
> 级别：L0（引擎直挂）｜ 引擎：`01-财税自动化/现金流滚动预测/费用分类汇总系统-v2.0/expense_classification.py`

## 功能

上传银行明细账/凭证列表 xlsx → 调用 v2.0 分类引擎 → 分类结果与未命中行入库 → 结果 xlsx 下载。规则只读引用权威源 `classification_rules.json`（未复制内容）。

## API（/api/finance/expense）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /classify | 上传 xlsx 并分类（multipart），返回 run 摘要 |
| GET | /runs | 批次分页列表 |
| GET | /runs/{id} | 详情含分类汇总统计与未命中行 |
| GET | /runs/{id}/download | 结果 xlsx 下载 |
| GET | /rules/summary | 规则条数/分类数/来源路径 |
| POST | /rules/reload | 重新加载规则 |

## 表

- `mod_expense_run`：id / 原文件名 / 行数 / 命中数 / 状态 / 耗时 / 产物路径 / created_at
- `mod_expense_row`：run_id / 行号 / 摘要 / 金额 / 命中分类 / 是否命中

## 前端

- 上传向导页 `/finance/expense`、结果页 `/finance/expense/runs/:id`（汇总卡片 + ECharts 分类饼图 + 未命中筛选 + 下载）

## 测试与验收

- pytest `tests/test_module_finance_expense.py`：合成银行明细账对拍分类结果、503 降级、rules summary——全绿
- Agent 工具：`expense_runs_summary`（只读）
- 注意：仅文本型数据；引擎目录缺失 → 503 不拖垮宿主
