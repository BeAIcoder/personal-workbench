# finance_pos POS 对账 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_pos/` + `frontend/src/modules/finance-pos/`
> 级别：L1 任务型薄壳（可入库公开；运行期数据在 gitignore 的 data/ 下）

## 机制

- 引擎两脚本（原项目 `收款认领POS台账`）：
  1. `generate_pos_ledger.py --root <input> --output <output>/POS项目台账.xlsx`（SHOP 对账单 → 项目台账）
  2. `analyze_pos_reconciliation.py --root <input>`（四源交叉差异分析，产物写进 input 目录）
- 模块依次子进程执行（各 900s 超时），产物归集 = `output/*` + `input` 下**新生成文件**（与上传清单差集，避免把上传的原件当产物）
- API：`POST /runs`、`POST /runs/{id}/execute`、`GET /runs`、`GET /runs/{id}`、`GET /runs/{id}/artifacts/{name}`、`GET /runs/{id}/download`

## 测试（stub 引擎）

- pytest 4 例：模块注册、引擎缺失 503、全流程（三文件→执行→台账+差异文件差集归集→下载）、ZIP 输入与 zip-slip 400

## 已知限制

- 未接入合并打印 PDF（`merge_pos_pdf_v4.py`）步骤，需要时在 execute 末尾追加第三步
- 差异分析的 `--project` 参数未透出（默认全部分析）
