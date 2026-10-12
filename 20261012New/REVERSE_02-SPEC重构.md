# 逆向分析 · SPEC 重构（2026-10-12）

> 从 7 个版本的开发记录、5 份 ADR、模块 manifest 与数据库 schema 反推，重构出当前系统的规范化 SPEC。
> 本文是当前实现的**权威快照**（as-built spec），后续变更以本文 + ADR 为准。

## 1. 定位与约束（不可违反）

- 本机单人 Web 工作台：仅监听 127.0.0.1、无鉴权、SQLite 单文件、不用 Docker
- 依赖红线：仅 MIT/Apache-2.0/BSD；新增依赖需锁版本
- 真实业务值外置（各引擎 local_business.json/config.local.yaml）；现金流 V1 数据永不推远端
- 删除类操作不开放给 Agent

## 2. 架构总览

```
浏览器（Vue3.4 纯JS + Element Plus + ECharts + AG Grid，hash 路由）
   │ /api（axios；SSE 走原生 fetch）
FastAPI（app/main.py：lifespan=DPAPI校验→建表→轻量迁移→专家seed→技能播种→MCP启停→调度器）
   ├── 静态路由 ×11（tasks/schedules/notes/search/dashboard/assistant/jobs/knowledge/mcp/skills/experts）
   ├── Module Registry（app/modules：目录扫描 manifest → 挂载路由/表/工具/预置）
   ├── Agent 团队（agents/：team 表驱动 + orchestrator 四级路由 + tools 白名单）
   ├── 知识与记忆（rag BM25 / memory 召回注入 / mcp stdio 客户端）
   └── SQLite（35 张表：宿主 10 + 模块 mod_* + mcp_servers/memories）
```

## 3. 模块机制（Module Registry）契约

- 模块 = `backend/app/modules/<name>/`（manifest.py 五件套）+ `frontend/src/modules/<name>/`（manifest.js）
- manifest 字段：name/title/version/prefix(`/api/finance/` 开头)/tables(`mod_` 前缀)/menu/agent_tools/column_patches/requires/**preset_experts**/enabled
- 加载语义：单模块失败仅记日志跳过（故障域隔离）；preset_experts 把模块工具幂等合并进专家白名单（暂存→seed 后补齐）
- 数据目录：`data/finance/<module>/{inbox,outbox,tmp}`（`get_finance_data_dir()`）

## 4. 功能规格（按域）

### 4.1 基础四件套 + AI 助手
任务/日程/笔记 CRUD、全局搜索(Ctrl+K)、概览；AI 助手：SSE 流式、三种模式（standard/readonly/deep）、多会话、专家团队（13 位，表驱动可配）、供应商/模型两级管理、附件上传（xlsx→CSV）、技能池、Bash（默认关）、QwenPaw 兼容（默认关）

### 4.2 财务模块（8 可入库 + 1 本地专用）
| 模块 | 级别 | 关键规格 |
|------|------|---------|
| finance_expense | L0 | v2.0 引擎直挂，规则只读权威源 |
| finance_invoice | L0 | 发票工具引擎直挂，仅文本型 PDF |
| finance_vault | 底座 | DuckDB 只读 SQL（SELECT-only+白名单+限流+超时），ingest_dataframe 契约 |
| finance_ledger_personal | 全新 | 两级账户/分类/交易/预算，CSV 导入导出 |
| finance_ledger_bip | 全新 | BIP 三类导出列签名识别+人工映射，借贷平衡 warning，export-to-vault |
| finance_tax | 整体迁入 | 原项目 5 routers+10 services 零改动移植，mod_tax_ 表前缀，迁移脚本幂等 |
| finance_board | L2 | m01-m04 管线子进程触发，HTML 快照归档，iframe 嵌入 |
| finance_pos | L1 | 台账+差异分析两步执行，产物差集归集 |
| finance_cashflow（本地专用） | L1 | V1 引擎 CLI 重定向，ZIP 解包+zip-slip 防护，**gitignore** |

### 4.3 调度器
- cron 五段（`* , - /`，0/7=周日，同分钟不触发）+ interval/daily 平迁；模块动作任务（JOB_ACTIONS，线程池执行）
- ADR-001：不引入 APScheduler

### 4.4 知识、记忆与 MCP
- RAG：笔记 BM25（中文 bigram），索引按 updated_at+count 失效自愈；`search_knowledge` 工具（ADR-003）
- 记忆：memories 表，召回=bigram 重叠+置顶+命中数；对话前注入 Top3（失败静默）；extract 一次性 LLM 抽取（ADR-004）
- MCP：自实现 stdio 客户端（initialize→tools/list→tools/call），工具注册为 `mcp_<srv>__<tool>`，async 线程池执行；HTTP/SSE 预留（ADR-002）

### 4.5 模型管理（v1.14）
- 18 家预设 JSON 数据化（provider_presets.json，reload 热加载）
- 模型能力探测：reasoning 逐档（supported/accepted/unsupported）、context 二分（≤6 次）、output 阶梯（1024→131072）；不可达保持 -1（不把不可达误判为不支持）
- 模型级自定义：temperature（运行时双协议注入）/note/思考手动覆盖

### 4.6 专家团与技能包（v1.13）
- 专家 = agent_specs 行；seed 增量 upsert（缺谁补谁，ADR-005）；专家包 .md 导入（工具并集）/导出
- 技能包 = data/skills/<name>/SKILL.md（zcode/WorkBuddy 同款），启停走 _store.json；AI 助手设置指向该目录即注入

## 5. 安全规格

- DPAPI 加密 API Key（缺 pywin32 拒绝启动，不降级明文）
- SSRF 防护（协议白名单 http/https，禁内嵌凭据）
- 上传：扩展名白名单 + 大小限制 + 防穿越（ZIP zip-slip）
- 数据仓 SQL：SELECT-only + 表白名单 + LIMIT + interrupt 超时
- gitignore 分层：data/、.env、finance_cashflow 全目录；提交前无真实业务值

## 6. 测试规格

- 后端 pytest（294）：conftest 临时库隔离、引擎对拍、monkeypatch 探测/LLM
- 前端 vitest（82）：注册断言 + 纯逻辑 + setup 级组件测试
- 冒烟：uvicorn 真实端点 + 冒烟数据清理
- 构建：`npm run build`（各视图独立 chunk）
