# finance_cashflow 现金流填报 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_cashflow/` + `frontend/src/modules/finance-cashflow/`
> 级别：L1 任务型薄壳 ｜ ⚠️ 模块目录整体 `.gitignore`（本地专用）

## 为什么 gitignore

引擎「现金流滚动预测 V1」仓内声明**含真实业务数据、永不推远端**。凡引用它的模块代码
（含引擎路径推导、数据布局假设）一并保持本地，不进公开仓库——与该仓的数据安全声明
保持一致。重建成本低（方案文档 + 本记录齐全）。

## 机制

- 引擎 CLI 支持 `--source-dir/--output-dir/--actual-month` 重定向，薄壳无需改引擎
- 运行工作区：`data/finance/finance_cashflow/runs/<id>_<ts>/{input,output,artifacts}`
- 上传：ZIP 解包（zip-slip 防护：拒绝 `../` 越界条目）+ 扩展名白名单 + 50MB 上限；清单记原始文件名，磁盘名带时间戳防重名
- 执行：`sys.executable cashflow_autofill.py` cwd=引擎目录，超时 1800s；退出码非 0 → failed + 日志留痕 + 503 中文提示
- API：`POST /runs`（fail-fast 探测引擎）、`POST /runs/{id}/execute`、`GET /runs`、`GET /runs/{id}`、`GET /runs/{id}/artifacts/{name}`、`GET /runs/{id}/download`（ZIP 流式）

## 测试（stub 引擎，不触碰真实 V1 数据）

- pytest 5 例：模块注册、引擎缺失 503（create fail-fast）、全流程（多文件+ZIP→执行→产物→单文件/打包下载→越权名 404）、zip-slip 400、月份 422
- 真实冒烟：合成输入喂**真实引擎** → 退出码 1 → failed 留痕 + 503（降级路径实测，随后清理）

## 已知限制

- 引擎的自动识别假设数据源目录含必要文件；文件不齐时引擎自行报错（模块如实透传）
- 高级参数（收入口径 split_by_ledger、共享规则开关等）未透出，必要时在 args 构造处扩展
