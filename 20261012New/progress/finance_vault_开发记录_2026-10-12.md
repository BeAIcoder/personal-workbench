# finance_vault 数据仓 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_vault/` + `frontend/src/modules/finance-vault/`
> 级别：首批底座模块 ｜ 依赖：duckdb 1.5.6（MIT）

## 功能

DuckDB 嵌入式分析仓（`data/finance/vault/vault.duckdb`）：上传 xlsx/csv 入仓留档；只读 SQL 查询（SELECT/WITH only、禁分号多语句、禁写/DDL/系统命令关键词、FROM/JOIN 表白名单=已注册数据集、强制 LIMIT≤1000、interrupt 超时 5s）；表预览；模块间入仓契约。

## 模块间契约（其他模块调用）

```python
from app.modules.finance_vault.service import ingest_dataframe
ingest_dataframe(table: str, df: pandas.DataFrame, mode: str = "replace") -> dict
# 返回 {"table": ..., "rows": 入仓后总行数}；表名仅小写字母/数字/下划线
```

finance_ledger_bip 的「入仓」按钮即走此契约；vault 不可用时对方 503 降级。

## API（/api/finance/vault）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /tables | 数据集清单（表名/行数/来源/注册时间） |
| POST | /ingest/file | 上传 xlsx/csv 入仓（白名单 + 大小限制 + 防穿越） |
| POST | /query | 只读 SQL 查询（强制限流 + 截断标记） |
| GET | /tables/{name}/preview | 前 100 行预览 |

## 表

- `mod_vault_dataset`：表名（唯一）/ 来源 / 行数 / 列信息 JSON / 注册与更新时间

## 前端

表清单页（上传对话框 + 预览 + 跳查询）与 SQL 查询页（textarea + 结果表格 + CSV 导出）。

## 测试与验收

- pytest 8 例：ingest 契约（replace/append/非法表名）、上传白名单与坏文件不留档、注入防护（DROP/DELETE/多语句/未注册表/read_csv 文件路径）、WITH+注释+LIMIT 改写、预览 404、HTTP 端点全链、Agent 工具行为——全绿
- Agent 工具：`vault_list_tables`、`vault_query`（均只读，走同一套校验）
