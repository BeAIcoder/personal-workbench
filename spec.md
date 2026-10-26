# 个人工作台 · 需求与设计规格（spec）

> **版本 v1.16.1（2026-10-19）**
>
> 历次演进：v1.0.0 基础工作台 → v1.1.0 Agent 团队 → v1.2.0 QwenPaw Agent 导入 → v1.3.0 Agent 团队配置化 + SSE 流式对话 → v1.4.0 定时 Agent → v1.7.0 安全加固轮（DPAPI / SSRF / 依赖锁定）→ v1.8.0 模块注册机制（Module Registry）→ v1.9.0 首批 5 个财务模块 → v1.10.0 企业所得税迁入 + 报表看板 → v1.11.0 L1 任务型薄壳 + cron 调度与模块动作 → v1.12.0 RAG / 长期记忆 / MCP → v1.13.0 模块工具预置 + 技能包 + 专家团文件化 → v1.14.0 厂商预设模块化 + 模型能力探测 → v1.15.0 会话归档与导出 + 聊天传图多模态理解 → **v1.15.1 复核加固轮（三路并行审计驱动：zip-slip 同前缀绕过 / 解压总量上限 / 事件循环阻塞 / MCP 超时失效等中危全修，前端 sandbox 与 SSE 防御等 23 项修复）** → v1.16.0 调试中心（问题记录 + E2E 运行归档 + 前端报错采集）→ v1.16.1 体验修复十一项（技能包停用对 Agent 生效、env 兜底来源标识、专家包导入确认、AI 配置子菜单收纳等）。
>
> 权威 as-built 快照：`20261012New/REVERSE_02-SPEC重构.md`；接口细节：`API.md`；架构决策：`20261012New/adr/ADR-001~005`。
>
> 本版为**文档校准 + 修复验收轮**（2026-10-22，版本号仍为 v1.16.1）：按当前工作区实现校正运行环境与安全规格细节
> （Node 22+ / Origin 守卫（不再用 Host 自证，保留配置 HOST 同源）/ MCP `readOnlyHint` 缺省拒绝与 env DPAPI 密封 /
> 上传有界读取与分模块限额 / 模型 URL 校验 /
> 超时口径 / 导入行数上限 / 现金流分页 / 看板日志 / E2E 隔离与构建检测）。第 13 节测试数字已回填
> **2026-10-22 第三轮实测**；第 15 节补充算法设计逻辑、模块实现状态与验证情况。
> 详细修复与验收记录见 `20261022-修复与验收报告.md`。
>
> **2026-10-23 外部验证轮（as-built 追平，版本号仍为 v1.16.1）**：依据 `20261022-外部验证计划与Checklist.md`
> 完成第 0–6 章验证——BIP 凭证列表缺口修复（列签名识别 + 多 sheet 合成账簿列 + 续行 ffill + 表头定位关键词类别数评分）、
> 五引擎路径配置（`FINANCE_{EXPENSE,INVOICE,POS,CASHFLOW,BOARD}_*_PATH`，目录或单一文件）、
> 引擎子进程 TEMP 显式化与上传文件名 mojibake 修复、POS `--pos`/`--project` 透传、
> 财务数据脱敏保勾稽随机（`dev/desensitize_finance_data.py`）。算法细节与功能拓扑另见
> `算法实现逻辑.md` 与 `功能模块拓扑图.md`。第 6 章可用性为自动化代行，真人阈值待复测。

## 1. 定位与硬约束（不可违反）

面向个人日常办公的**本机单人 Web 工作台**：任务、日程、笔记三大事项载体 + 概览与全局搜索；
外加一个**表驱动可配的 AI 助手 Agent 团队**（13 位专家）与一整套**财务业务模块群**，
让自然语言可以直接操作工作台与财务数据。

| 约束 | 内容 |
| --- | --- |
| 部署形态 | 仅监听 `127.0.0.1`、无登录鉴权、数据不出本机；启动时对非回环 HOST 打印醒目告警 |
| 存储 | SQLite 单文件 `backend/data/workbench.db`，零外部数据库依赖；不允许 Docker 与外部独立数据库 |
| 运行环境 | Windows 11 · Python 3.12+ · Node 22+ |
| 依赖红线 | 仅 MIT / Apache-2.0 / BSD；新增依赖需锁版本（`requirements-lock.txt`） |
| 业务值外置 | 真实业务数据放各引擎 `local_business.json` / `config.local.yaml`；**现金流 V1 数据永不推远端** |
| Agent 权限边界 | 删除类操作不开放给 Agent（仅页面物理删除；任务可由 Agent 标记完成） |
| 交付标准 | 测试、文档、双击 bat 启动三件套齐全 |

## 2. 架构总览

```
浏览器（Vue 3.4 纯 JS + Element Plus + ECharts + AG Grid，hash 路由）
   │ /api（axios；SSE 走原生 fetch）
FastAPI（app/main.py：lifespan = DPAPI 校验 → 建表 → 轻量迁移 → 专家 seed
         → 专家团/技能包种子 → 模块工具预置补齐 → 调度器 → MCP 启停）
   ├── 静态路由 ×12：tasks / schedules / notes / search / dashboard / assistant
   │                  / jobs / knowledge / mcp / skills / experts / model_probe（探测 + 厂商预设别名）
   ├── Module Registry（app/modules/）：目录扫描 manifest → 挂载路由/表/工具/预置
   ├── Agent 团队（agents/）：team 表驱动 + orchestrator 四级路由 + tools 白名单
   ├── 知识与记忆（rag BM25 / memory 召回注入 / mcp stdio 客户端）
   └── SQLite（35 张表：宿主 12 + 模块 22 + v1.2.0 遗留 1）
```

前端 10 个静态页（概览 / 任务 / 日程 / 笔记 / AI 助手 / 定时任务 / 知识与记忆 / MCP 工具 /
技能包管理 / 专家团文件）+ 调试中心 3 页（模块路由）+ 财务分组菜单（由前端模块 manifest 自动生成，
路由 `/finance/<name>/...` 懒加载）。

## 3. 模块机制（Module Registry）

### 3.1 目录与 manifest 契约

- 模块 = `backend/app/modules/<name>/`（`manifest.py` 五件套）+ `frontend/src/modules/<name>/`（`manifest.js`）。
- `manifest.py` 必须定义 `manifest = ModuleManifest(...)`，字段：

| 字段 | 说明 |
| --- | --- |
| `name` | 目录名（唯一），财务域模块统一 `finance_` 前缀 |
| `title` / `version` | 显示名与版本 |
| `prefix` | HTTP 路由前缀，财务模块**必须以 `/api/finance/` 开头**，调试类模块以 `/api/debug/` 开头，挂载时 `include_router(router, prefix=prefix)` |
| `tables` | 本模块创建的表名清单，**统一 `mod_` 前缀**（与宿主表隔离，便于盘点） |
| `menu` | 前端菜单元数据 `{group, icon, order}` |
| `agent_tools` | 模块贡献给 Agent 团队的工具 spec（`name`/`description`/`readonly` + `func`/`params`/`required`，与宿主 `TOOL_SPECS` 同构） |
| `column_patches` | 轻量迁移列补丁 `{表名: {列名: DDL}}`，宿主启动时合并执行 |
| `requires` | 依赖的其它模块 name；依赖未加载时本模块跳过 |
| `preset_experts` | 工具预置目标专家 name 列表 |
| `enabled` | `False` 时整个模块不加载（路由/表/工具均不生效） |

`manifest.validate()` 校验 name 非空、prefix 前缀、`mod_` 表前缀、menu 为 dict、工具字段齐全；
目录名与 `manifest.name` 不一致同样判失败。

### 3.2 加载语义与故障域隔离

- `discover_module_names()` 扫描含 `manifest.py` 且不以 `_` 开头的目录（`_template` 模板跳过）。
- **单模块失败仅记日志跳过**（导入失败 / 校验失败 / 挂载异常），绝不抛异常拖垮宿主与其它模块。
- `load_modules(app)` 必须在 `main.py` 模块级作用域调用（静态 `include_router` 之后、lifespan 之前）：
  导入 manifest 时连带导入模块 models，把模块表注册进 `Base.metadata`，lifespan 里的 `create_all` 才能建出模块表。
- `collect_column_patches()` 汇总各模块列补丁，供 `_ensure_sqlite_columns()` 合并执行。
- **工具预置**：`preset_experts` 把模块工具**幂等合并**进专家白名单。目标专家尚未入库（新库未 seed）时
  先暂存，`apply_pending_presets()` 在 seed 之后补齐；暂存记录不删除，每次启动重跑可自愈。
- 专家白名单引用已禁用模块的工具时自动跳过，不崩。
- 后端 `register_module_tools()` 与内置工具**重名不覆盖**。

### 3.3 四级接入策略

财务域统一按「不为整合而整合」的分级策略接入，引擎零重写：

| 级别 | 含义 | 适用对象 |
| --- | --- | --- |
| **L0 纯函数直挂** | `import` 原项目引擎（纯 Python、无 GUI 耦合），包一层 router + schema | 发票提取、费用分类、税额计算 |
| **L1 任务型薄壳** | 不重写引擎，包装为「上传输入文件 → 后台执行原脚本 → 产物入库供下载」 | 现金流 V1、POS 对账 |
| **L2 产物聚合** | 只接产物不接过程：静态托管 / iframe 嵌入 | 微信报表 HTML 看板 |
| **L3 仅登记** | 文档/手册类只在资料库建索引 | 合规手册等（未做成模块） |
| **底座（可选）** | DuckDB 只读数据仓，供其它模块 `export-to-vault` 落库 | finance_vault |

### 3.4 数据目录

财务模块数据目录 `data/finance/<module>/{inbox,outbox,tmp}`，由 `get_finance_data_dir()` 解析
（`FINANCE_DATA_DIR` 留空时默认 `backend/data/finance`）。

## 4. 功能规格

### 4.1 基础四件套 + 概览 + 全局搜索

| 域 | 要点 |
| --- | --- |
| 任务 `routers/tasks.py` + `TasksView.vue` | 标题（必填）、描述、状态（todo/in_progress/done）、优先级（low/medium/high/urgent）、截止时间、分类、完成时间。`completed_at` 由后端维护（改 done 写入、改回清空）；删除有前端二次确认。列表支持状态/优先级/分类/逾期过滤、标题与描述关键词、截止区间（含边界）、五种排序、分页 |
| 日程 `routers/schedules.py` + `ScheduleView.vue` | 月历 + 按天列表，有日程日期显示彩色圆点（最多 3 个，多出 +N）。结束必须晚于开始（否则 422）；全天落库 00:00:00–23:59:59；范围查询按「日程与区间有交集」匹配 |
| 笔记 `routers/notes.py` + `NotesView.vue` | 标题、正文、标签数组（自动去空格去重，最多 10 个、单个最长 20 字）、置顶。标签 JSON 存储且 `ensure_ascii=False`，LIKE 过滤；列表置顶在前、更新时间倒序 |
| 概览 | `GET /api/dashboard/summary` 一次取全：统计卡片、任务状态饼图、近 7 天完成趋势、即将到期任务/今日日程/最近笔记三栏 |
| 全局搜索 | `GET /api/search?q=` 一次检索任务/日程/笔记各前 5 条并返回上下文片段；前端 `Ctrl+K` 唤起，点击跳转并带入筛选 |

### 4.2 AI 助手（`routers/assistant.py` + `app/agents/`）

**对话与模式**

- 非流式 `POST /api/assistant/chat` 与 SSE 流式 `POST /api/assistant/chat/stream`，入参一致。
- 三种工作模式：`standard` 标准执行 / `readonly` 只读咨询（仅放行查询工具，不注入技能与插件）/ `deep` 深度研究（`max_iters` 抬到 ≥10 并附加深度研究提示）。
- 思考型模型的思考过程剥离到 `thinking` 字段，独立于正文展示。
- 多会话管理（新建/重命名/删除/归档/导出），消息持久化，刷新与重启后可回看。

**会话归档与导出（v1.15.0 新增）**

- `agent_sessions.archived` 列（`INTEGER DEFAULT 0`，带索引）：归档后不出现在默认列表。
- `POST /api/assistant/sessions/{id}/archive` · `POST /api/assistant/sessions/{id}/unarchive` → `{"ok": true}`，不存在 404。
- `GET /api/assistant/sessions/{id}/export` → Markdown 附件（`text/markdown; charset=utf-8`，
  `Content-Disposition: attachment; filename="session-{id}.md"`），不存在 404。
  内容 = 标题/会话 ID/创建时间/导出时间/消息数 + 逐条消息（序号 + 发言人 + 时间 + 正文），
  思考过程与工具执行轨迹分别以 `<details>` 折叠块附在对应消息后。
- 列表 `GET /api/assistant/sessions?archived=true|false` 按归档状态过滤，不传返回全部；条目含 `msg_count` / `archived` / `updated_at`。
- 前端 `SessionList.vue` 提供「进行中 / 已归档」页签与归档、恢复、导出图标（导出走 `window.open` 触发浏览器下载）。

**聊天传图与多模态理解（v1.15.0 新增）**

- 附件上传白名单在原有文本/数据类与 Excel 之外新增图片 `.png .jpg .jpeg .webp .gif`（10MB 上限），
  图片响应 `kind="image"`（Excel 转 CSV 为 `csv_converted`，其余为 `file`）。
- `provider_models.supports_vision` 列（`INTEGER DEFAULT 0`）：视觉（图片理解）能力开关。
- 厂商预设 JSON 新增 `vision_models` 字段（已知视觉模型清单，供界面预勾选 `supports_vision`）；
  每条预设另有 `models` / `reasoning_models`。
- 视觉模型下，图片附件读成 base64 经 AgentScope `DataBlock`（`Base64Source`）注入 user 消息：
  formatter 在 OpenAI 侧转 `image_url` 格式，Anthropic 侧转原生图片块；文本类附件仍按原机制注入。
- 非视觉模型带图请求：`POST /chat` 与 `/chat/stream` 均返回 **400**，
  `detail` 为中文提示「当前模型不支持图片理解，请在设置中为该模型开启视觉能力或切换视觉模型」。
  模型整体未配置时不拦截（走既有 `need_llm_config` 引导）。
- 单张图片上限与上传一致（10MB），越界路径与不存在的文件直接忽略并在文本中说明。

**供应商 / 模型两级管理**

- 一家供应商一张卡片（协议 openai/anthropic、base_url、api_key、启停），下挂多个模型
  （上下文/输出上限、多模态标记、视觉开关、温度、备注、思考档位、探测结果）。
- 跨供应商激活一个模型作为全局默认（`agent_settings.active_model_id`）；专家可独立绑定模型（`agent_specs.model_id`），
  不可用（删除/停用）时回退全局激活模型。
- 连通性 / 工具调用 / 多模态三类测试（`POST /api/assistant/config/test`）。

**扩展能力（默认关闭）**

本地技能池（`backend/data/skills/<name>/SKILL.md`；设置项名为「**技能池目录**」，单个 SKILL.md
目录称「**技能包**」，首次启动从 `backend/seed_skills/` 播种）、Bash 工具（自带危险文件黑名单）、
QwenPaw 插件兼容层，均有独立开关；技能池经 AgentScope `LocalSkillLoader` 注入
（已停用的包在 loader 侧过滤，停用对 Agent 生效），专家级 `skills` 为空列表时显式关闭。

### 4.3 定时任务（`routers/jobs.py` + `app/scheduler.py` + `JobsView.vue`）

- **定位**：到点自动把任务提示词交给 Agent 团队执行一次，回复写成笔记
  （标题「【定时任务】{名称} 时间」，标签含「定时任务」），在笔记页回看全部历史。
- **调度算法**（纯函数）：`cron_matches()` 五段 cron（`* , - /`，0/7=周日，日与周同时受限按「任一命中」语义，
  同自然分钟不重复触发）+ `job_due()` 覆盖 interval（距 `last_run_at` 达间隔即到期，从未运行立即到期）
  与 daily（到达当天 `daily_at` 且当天未跑过才到期；首次启动当天已过点补跑一次）。ADR-001：**不引入 APScheduler**。
- **执行链路**：复用 `orchestrator.chat`——路由（指定专家 > 关键词 > LLM 结构化调度）→ 专用会话 `job-{id}`
  → 回复写成笔记并回填 `last_status/last_error/recent_runs/last_note_id`；
  `POST /{id}/run` 立即执行与调度循环共用同一入口。
- **模块动作任务**：`action` 非空时到点执行注册的模块动作（`JOB_ACTIONS`，如 `finance_board.run`）而非 LLM 对话，
  走线程池执行、不写笔记；`GET /api/jobs/actions` 列出已注册动作。
- **并发与异常**：调度循环每 20s 检查一次；同一任务串行执行（上次未结束则本轮跳过）；
  LLM 未配置标记 skipped 不发请求；单次异常记入 `last_error` 与 recent_runs，循环吞掉异常继续下一轮。
- **校验**：`interval` 必须带 `interval_minutes`（5~10080）、`daily` 必须带 `daily_at`（HH:MM）、
  `cron` 必须带合法 `cron_expr`，否则 422。

### 4.4 财务模块（9 可入库 + 1 机制示例）

| 模块 | 级别 | 关键规格 |
| --- | --- | --- |
| finance_expense 费用分类 | L0 | v2.0 引擎直挂，规则只读权威源 |
| finance_invoice 发票处理 | L0 | 发票工具引擎直挂，仅文本型 PDF |
| finance_vault 数据仓 | 底座 | DuckDB 只读 SQL（SELECT-only + 表白名单 + 强制 LIMIT + interrupt 超时），`ingest_dataframe` 契约 |
| finance_ledger_personal 个人记账 | 全新 | 两级账户/分类/交易/预算，CSV 导入导出 |
| finance_ledger_bip 企业记账 | 全新 | BIP 三类导出列签名识别 + 人工映射，借贷平衡 warning，export-to-vault |
| finance_tax 企业所得税 | 整体迁入 | 原项目 5 routers + 10 services 零改动移植，`mod_tax_` 表前缀，迁移脚本幂等 |
| finance_board 报表看板 | L2 | m01–m04 管线子进程触发（单步 600s 超时），HTML 快照归档 + 管线日志尾部（入库截断至 4000 字符、读取只暴露末 500 字符），iframe 嵌入（响应带 CSP `frame-ancestors 'self'`） |
| finance_pos POS 对账 | L1 | 台账 + 差异分析两步执行（单步 900s 超时），产物差集归集，上传 50MB |
| finance_cashflow 现金流填报 | L1 | V1 引擎 CLI 重定向，ZIP 解包 + zip-slip 防护；运行记录列表分页（`page` ≥ 1、`page_size` 1–100，返回 `{total, items, page, page_size}`，按 id 倒序），上传 50MB |
| finance_hello 模块示例 | 机制实证 | 连通性自检 + 样例表 `mod_hello_entry` + 只读工具 `hello_ping`（财税专员白名单内） |

跨项目引擎/数据路径（费用分类 / 发票 / POS / 现金流 / 报表看板）经
`FINANCE_EXPENSE_ENGINE_PATH` / `FINANCE_INVOICE_ENGINE_PATH` / `FINANCE_POS_ENGINE_PATH` /
`FINANCE_CASHFLOW_ENGINE_PATH` / `FINANCE_BOARD_PIPELINE_PATH` 配置项解析，默认按AssetLib相对位置推导；
**路径接受目录或单一文件**（`utils.resolve_engine_root`：文件取其父目录，不存在原样返回交由调用方校验）；
引擎目录缺失时对应接口返回 503 中文提示，不影响其他模块。引擎子进程统一经
`utils.engine_subprocess_env()` 显式指定 TEMP/TMP/TMPDIR（修 Git Bash 继承破损 TEMP 导致引擎临时文件失败），
上传文件名经 `utils.repair_upload_mojibake()` 修复 GBK/Latin-1 乱码（依赖中文文件名的引擎匹配）。
BIP 导入的 `export-to-vault` 在数据仓模块不可用时同样 503 降级。

**上传限额与导入行数（分模块口径，as-built）**：聊天附件 10MB；费用分类 / 发票 / 数据仓 / 税务
各 20MB；BIP 10MB；个人记账 CSV 5MB；POS 与现金流各 50MB。所有上传路由统一走共享有界读取
`app.upload.read_upload_limited`（1MB 分块、累计超限即中断读取，抛 `UploadTooLargeError` 由
`main.py` 全局映射 400），不裸 `file.read()`；宿主另有全局 ASGI 请求体上限 128MB
（Content-Length 早拒 + chunked 累计，超限 413，宽容路由吞异常也照发）。
导入类（Excel/CSV）共享行数上限 `app.import_limits.MAX_IMPORT_ROWS = 200000`（缺失即失败关闭）；
费用分类在引擎执行与写库前按该上限护栏（openpyxl 只读流式迭代 + `reset_dimensions()`，
口径为各工作表非空行合计含表头，超限早停于 limit+1 行）。

各模块 HTTP 接口清单见 `API.md`「财务模块接口（/api/finance/*）」一节。

### 4.5 知识、记忆与 MCP

**RAG 知识库（ADR-003：BM25，不引向量库）**

- 笔记 BM25 检索，中文按**字符二元组（bigram）**分词 + 英文/数字单词，零外部依赖、完全离线。
- 索引常驻内存，按 `max(notes.updated_at)` + 块数失效自动重建（自愈），也提供手动重建。
- 笔记切块 240 字符、重叠一半，块内带标题便于定位；`search_knowledge` 注册为只读 Agent 工具。
- 端点：`GET /api/knowledge/rag/stats`、`POST /api/knowledge/rag/rebuild`、`POST /api/knowledge/rag/search`。

**长期记忆（ADR-004：手动为主，抽取为一次性动作）**

- `memories` 表（content/source/tags/pinned/hit_count/last_used_at）。
- 写入以手动保存为主；「从最近对话抽取」为用户触发的一次性动作（走既有 LLM 编排器，
  产物经近重复 containment 检查后入库）。
- 检索 = 关键词 LIKE 多词 OR + 置顶/新近度加权；`recall_memories` 注册为只读 Agent 工具。
- 编排器在每次对话前把 Top-3 相关记忆作为上下文前缀注入（失败静默，不影响对话）。
- 端点：`GET/POST /api/knowledge/memories`、`PUT/DELETE /api/knowledge/memories/{id}`、
  `POST /api/knowledge/memories/recall`、`POST /api/knowledge/memories/extract`。

**MCP 工具接入（ADR-002：自实现 stdio 客户端，不引 SDK）**

- 只做 **stdio 传输**（本地命令起的 MCP Server）；HTTP/SSE 传输预留。
- 协议按 MCP stdio 规范：换行分隔 JSON-RPC 2.0，握手 `initialize` → `notifications/initialized`
  → `tools/list` → `tools/call`；协议版本 `2024-11-05`。
- 每个 Server 一个子进程，同步请求/响应带超时（调用 30s / 握手 15s / 消息 4MB）；
  暴露给 Agent 的工具为 async 包装（`asyncio.to_thread`），不阻塞事件循环。
- 工具注册为 `mcp_<srv>__<tool>`；Server 清单持久化在 `mcp_servers` 表（env 含敏感值，仅本机库）；
  连接/断开/更新/删除服务器时经 `_unregister_server_tools()` 注销其旧工具注册，不残留。
- **只读判定（readOnlyHint 缺省拒绝）**：只读咨询模式下，以工具自带的 `annotations.readOnlyHint`
  为**唯一放行依据**——显式 `true` 才放行；**无注解/缺省一律视为可写（默认拒绝，宁可少用不可越权）**；
  名称启发式（write/create 之类）可被任意命名绕过，不作放行依据（v1.16.1 起唯一口径）。
- 端点：`GET/POST /api/mcp/servers`、`PUT/DELETE /api/mcp/servers/{id}`、
  `POST /api/mcp/servers/{id}/connect|disconnect|test-call`；启动时自动拉起启用的服务器。

### 4.6 技能包与专家团文件（v1.13.0）

- **技能包**：`backend/data/skills/<name>/SKILL.md`（zcode/WorkBuddy 同款格式），首次启动从
  `backend/seed_skills/` 播种；启停走 `_store.json`；AI 助手设置的「技能池目录」指向该目录即注入 Agent
  （停用的包不注入）。
  端点：`GET /api/skills/packs`、`POST /api/skills/packs`、`DELETE /api/skills/packs/{name}`、
  `PUT /api/skills/packs/{name}/enabled`、`GET /api/skills/packs/{name}/files/{filename}`、`POST /api/skills/rescan`。
- **专家团文件**：`backend/data/expert-packs/<name>.md`（frontmatter：name/label/emoji/color/description/
  keywords/tools/sort + 正文角色提示词），导入为 **upsert**（工具白名单取**并集**，导入不缩减），
  导出把数据库专家写回文件；CRLF 归一化。
  端点：`GET /api/experts/packs`、`POST /api/experts/packs/import-all`、`POST /api/experts/packs/import/{name}`、
  `POST /api/experts/packs/reset`、`POST /api/experts/packs/export/{expert_name}`、
  `GET /api/experts/packs/{name}/content`、`DELETE /api/experts/packs/{name}`。

### 4.7 调试中心（v1.16.0）

**定位**：问题记录 + E2E 测试运行归档 + 前端报错采集的一体化调试入口。后端模块
`debug_center`（`prefix=/api/debug`，manifest 校验已放开 `/api/debug/` 前缀族）；
**纯 JSON 文件存储，不建表**。

- **存储结构**（根目录默认 `backend/data/debug/`，`DEBUG_DATA_DIR` 可覆盖——运行时读取，
  供测试隔离；写入统一临时文件 + `os.replace` 原子写，读取对缺失/损坏宽容降级）：
  - `issues.json`：问题记录数组（id 短 uuid / title / severity(P0-P3) /
    status(open/in_progress/done/wontfix) / source(manual/e2e/analysis) / page /
    description / evidence / created_at / updated_at）。首次使用自动写入 11 条
    analysis 种子问题（`ensure_seed_issues()` 幂等，挂 lifespan + 接口首用双保险）。
  - `runs/<run_id>/result.json` + `runs/<run_id>/shots/*.png`：测试运行归档，
    run_id 形如 `20261013-153000_recon`。
  - `client_errors.jsonl`：前端报错，每行一条 `{ts, page, message, stack, ua}`；
    **有界存储**——文件超过 10MB 时按行裁剪，只保留最近 1000 条（裁剪阈值 × 保留量已错开，
    一次裁剪不会立即再触发；裁剪失败只告警，不影响本次上报）。
- **端点**：`/issues` CRUD（PUT 部分更新、自动刷 updated_at）；
  `/runs` 列表/详情/截图（FileResponse，`is_relative_to` 防穿越）/删除（整目录）；
  `/client-errors` 追加（**宽容解析，垃圾输入绝不 500**）/尾部读取/清空；
  `/summary` 总览（未决按严重度计数、问题总数、运行次数、最近一次运行、报错数）。
  清单详见 `API.md`「调试中心 /api/debug」一节。
- **E2E 接入**：`dev/e2e/debug_reporter.py` 的 `save_run(kind, ...)` 以同一文件格式
  直接落库（不经 HTTP）；`e2e_recon.py`（kind=recon，含截图）、`e2e_interactions.py`
  （kind=interactions）、`e2e_edge.py`（kind=edge）跑完自动归档。三脚本**固定端口 8123/8124**，
  经 `acquire_run_lock` 运行互斥（并行会互踩端口/目录），各自使用**独立测试库 / 独立财务目录 /
  独立调试归档目录**（均在 `dev/e2e` 下，绝不指向 `backend/data` 真实数据）。
  有错误的页面/失败用例自动建/更新问题（source=e2e、P1：
  同 title+page 且 open/in_progress 已存在则只刷 updated_at 与 evidence=run_id，
  否则新建；全绿不写 issues.json，不做自动关闭）。
- **AI 验收层**：`dev/e2e/ai_walkthrough.py` + `dev/e2e/AI-WALKTHROUGH.md`——
  AI 控制真实浏览器（desktop browser MCP / Kimi 网页桥）像人一样点击/悬停/看确认框，
  验收脚本断言不了的交互与文案（确认框、悬停提示、徽标状态、菜单结构、空态文案）。
  走查完执行 `python ai_walkthrough.py`（失败项 `--fail ID:原因`，可重复），
  经同一 `save_run()` 以 `kind=ai-verify` 归档；失败项自动建/更新问题（source=e2e、P1）。
  验收集 `CHECKLIST` 随功能演进维护；操作流程见 AI-WALKTHROUGH.md。
- **前端**：`调试` 菜单组三页——问题记录 `/debug/issues`（summary 卡片 + 级别彩色
  标签 P0 红/P1 橙/P2 黄/P3 灰 + 状态下拉即改 + 筛选 + 新建/编辑对话框 + 删除确认）、
  测试运行 `/debug/runs`（列表 + 详情抽屉：页面/用例行 + 错误清单展开 + 截图预览放大
  + 删除）、前端报错 `/debug/errors`（表格 + 堆栈展开 + 清空确认 + 自动刷新开关）。
  `main.js` 注册 `app.config.errorHandler` + `window error/unhandledrejection`
  全局采集：裸 axios 直 POST（不走拦截器）、同消息 5 秒去重、失败静默。

## 5. Agent 团队与四级路由

### 5.1 专家团队（表驱动，8 位内置出厂 + 5 位 QwenPaw 导入，合计 13 位）

专家 = `agent_specs` 行；`TEAM` 代码内置 8 位出厂专家（seed 数据源），
`seed_agent_specs()` 按 **增量 upsert**（缺谁补谁，ADR-005）灌入，绝不覆盖用户改动。
运行时经 `load_team()` 读取（60 秒进程内缓存），表为空或数据库不可用时回退内置 `TEAM` 保证离线可用。

| name | 中文名 | sort | 来源 | 关键词（内置专家出厂值，界面可改） |
| --- | --- | --- | --- | --- |
| security | 网络安全工程师 | 0 | 内置 | 网络安全、漏洞、等保、防火墙、渗透、数据安全 |
| leasing | 招商专员 | 1 | 内置 | 招商、租约、租赁、租金、免租、装补、空置、铺位 |
| operations | 运营专员 | 2 | 内置 | 运营、营运、客流、销售额、收缴率、坪效、商户经营 |
| marketing | 企划策划 | 3 | 内置 | 企划、营销、活动方案、推广、美陈、会员、造节 |
| property | 工程物业工程师 | 4 | 内置 | 工程、物业、维保、维修、能耗、巡检、空调、电梯、消防 |
| it_finance | 信息化财务分析师 | 5 | 内置 | 财务、报表、凭证、分录、报销、预算、税、发票、对账、信息化 |
| realestate | 商业地产管家 | 6 | 内置（默认兜底） | 商业地产、综合体、购物中心、资产管理、日程、待办 |
| finance_specialist | 财税专员 | 7 | 内置（预置全部财务模块工具） | 发票、税、凭证、台账、记账、报销、费用、财税、现金流 |
| cre_gm | 商业地产总经理 | 随导入文件 | QwenPaw 导入 | 随导入文件 |
| cloud_orchestrator | CloudPaw 主控编排 | 随导入文件 | QwenPaw 导入 | 随导入文件 |
| cloud_verifier | CloudPaw 验证器 | 随导入文件 | QwenPaw 导入 | 随导入文件 |
| cloud_executor | CloudPaw 执行器 | 随导入文件 | QwenPaw 导入 | 随导入文件 |
| datapaw | DataPaw 数据分析 | 随导入文件 | QwenPaw 导入 | 随导入文件 |

> 具体关键词清单以 `agent_specs.keywords`（界面可改）为准；`sort` 即路由优先级，小者优先（先专后宽）。

### 5.2 四级路由

路由顺序：**用户指定 > 关键词规则 > LLM 结构化调度 > 默认专家**（`_resolve_spec`）。

1. **指定**：入参 `agent` 命中启用中的专家，`routed_by="指定"`。
2. **关键词**：对每位启用中的专家统计 `keywords` 在消息（小写化）中的出现次数（子串包含），
   按命中数降序排列，**同分时 sort 小者胜**（稳定排序），`routed_by="关键词"`。
3. **LLM 结构化调度**：关键词未命中且模型可用时，调度员 Agent 以名册文本 + 用户消息做结构化输出
   `{agent, reason}`；输出不在名册内或任何异常都视为失败，回退下一步，`routed_by="AI 调度"`。
4. **默认专家**：优先 `realestate`（商业地产管家）；被删除/停用时取 sort 最小者，`routed_by="默认"`。

缓存与失效：`load_team()` 进程内 60 秒 TTL；任何专家/模型/供应商配置变更由路由层调用
`invalidate_team_cache()` + `orchestrator.invalidate()` 立即生效。
路由预览：`POST /api/assistant/agents/route-test` 输入 `{text}`，返回 `{routed, matched_by, matches}`。

### 5.3 工具白名单

- 内置业务工具 8 个：`query_tasks` / `create_task` / `complete_task` / `query_schedules` /
  `create_schedule` / `search_notes` / `create_note` / `workbench_stats`。
- 知识/记忆 2 个：`search_knowledge` / `recall_memories`（均只读）。
- 模块工具 10 个（`module_tool_preset.FINANCE_TOOL_NAMES`）：`hello_ping`、`expense_runs_summary`、
  `invoice_batch_stats`、`vault_list_tables`、`vault_query`、`pl_add_transaction`、`pl_monthly_summary`、
  `pl_recent`、`bip_voucher_query`、`bip_balance_query`；运行时权威来源是 Module Registry 的 `preset_experts`。
- MCP 工具：`mcp_<srv>__<tool>`，按服务器连接状态动态注册。
- `readonly=True` 的工具在只读模式下也可放行；专家 `tools` 为 NULL 表示用默认技能池
  （查询工具 + 常用写入工具），`skills` 为 NULL 表示跟随全局技能池配置。
- 工具执行轨迹经 contextvar 收集，随消息落库（`chat_messages.trace`）。

## 6. 模型管理与能力探测（v1.14）

- **18 家厂商预设**数据化（`app/agents/provider_presets.json`，`GET /api/providers/presets` ·
  `POST /api/providers/presets/reload` 热加载）；文件缺失/解析失败回退内置最小默认集并记警告。
  每条预设含 `name/label/protocol/base_url/models/reasoning_models/vision_models/note/docs_url`。
- **模型能力探测**（直连厂商 `/chat/completions`，仅 openai 协议）：

| 探测 | 方法 |
| --- | --- |
| 思考能力 `probe/reasoning` | `reasoning_effort` 逐档各发一发，判定 supported / accepted / unsupported |
| 上下文 `probe/context` | 二分查找，≤6 次小请求，`max_tokens=1` |
| 最大输出 `probe/output` | 阶梯 1024 → 131072 |
| `probe/all` | 三连探测，单步失败不中断，汇总写 `probe_result` + `probed_at` |
| `GET probe` | 只读当前探测状态 |

  探测失败一律 **200 + error** 返回——**不可达 ≠ 不支持**，`supports_thinking` 保持 `-1` 不误判。
- **模型级自定义**：`temperature`（空 = 跟随系统默认，已接入 `build_model` 运行时、openai/anthropic 双协议注入）、
  `note`、`supports_thinking` / `thinking_levels` 手动覆盖、`supports_vision`。
- API Key 存 `model_providers.api_key`，**DPAPI 静态加密**（`dpapi:` 前缀）；回传前端脱敏为 `••••••xxxx`，
  掩码哨兵 `********`（8 个星号）或脱敏值原样回传均表示保留现值（v1.15.1 起双形态均拒绝覆盖真实密钥）。

## 7. SSE 流式对话协议

`POST /api/assistant/chat/stream`，响应 `Content-Type: text/event-stream`，
每条 `data: {json}\n\n`，流末 `data: [DONE]`。

| type | 载荷 | 含义 |
| --- | --- | --- |
| `meta` | `{agent:{name,label,emoji,color}, routed_by, mode}` | 路由结果，先于正文 |
| `delta` | `{text}` | 正文增量 |
| `thinking_delta` | `{text}` | 思考过程增量（思考型模型） |
| `tool_start` | `{name}` | 一次工具调用开始 |
| `done` | `{session_id, reply, thinking, agent, routed_by, mode, trace}` | 最终消息（结构同非流式成功体） |
| `error` | `{message}`；模型未配置时带 `need_llm_config: true` | 失败事件 |

- **中止**：前端 `ChatPanel` 用 AbortController 持有当前流；发送新消息、切换/删除会话、组件卸载时 `abort()` 打断旧流。
- **错误落库保持历史成对**：用户消息先落库，流执行异常时补写一条 assistant 侧错误消息
  （`抱歉，本次回复生成失败：<异常类型>`），保证历史里一问一答成对出现。
- **非流式 `/chat`**：成功返回 `{ok:true, session_id, reply, thinking, agent, routed_by, mode, trace}`；
  模型未配置返回 `{ok:false, need_llm_config:true, message}`；带图但模型无视觉能力返回 **400**；
  **运行时异常返回 502**（日志记录完整堆栈，对外不回传内部细节）。

## 8. 数据模型（35 张表）

ORM 定义 **34 张表**：宿主表 12 张（`app/models.py`）+ 模块表 22 张（`mod_` 前缀，随模块加载注册进
`Base.metadata`）。老库中另有 v1.2.0 遗留表 `model_profiles`（ORM 已无对应模型，新库不会创建），
实测运行库合计 35 张表。

### 8.1 宿主表（12）

```
tasks            id, title, description, status, priority, due_date?, category, completed_at?, created_at, updated_at
schedules        id, title, description, location, start_time, end_time, all_day, color, created_at, updated_at
notes            id, title, content, tags(JSON), pinned, created_at, updated_at
chat_messages    id, session_id→agent_sessions, role(user/assistant), agent_name, content, trace(JSON),
                 thinking, created_at, updated_at
agent_sessions   session_id(PK), title, archived(bool, 索引), created_at, updated_at
model_providers  id, name, protocol(openai/anthropic), base_url, api_key(DPAPI), enabled, created_at, updated_at
provider_models  id, provider_id→model_providers, model, context_size, max_tokens, multimodal, enabled,
                 temperature(文本, 空=跟随), note, supports_thinking(-1/0/1), thinking_levels(JSON),
                 probed_context, probed_max_output, supports_vision(bool), probe_result(JSON), probed_at,
                 created_at, updated_at
agent_specs      id, name(唯一, ^[a-z][a-z0-9_]*$), display_name, emoji, color, description, role_prompt,
                 keywords(JSON), tools(JSON 可空), model_id(逻辑引用 provider_models.id), skills(JSON 可空),
                 enabled, sort(路由优先级，小者优先), is_builtin, created_at, updated_at
agent_settings   id=1 单行：provider/base_url/api_key/model, context_size, max_tokens, temperature,
                 multimodal, max_iters, enable_memory, history_inject, active_model_id→provider_models,
                 enable_skills/skills_dir, enable_bash, enable_plugins/plugins_dir, reasoning_effort, updated_at
scheduled_jobs   id, name, prompt, agent_name?(null=智能路由), mode, schedule_type(interval/daily/cron),
                 interval_minutes?(5-10080), daily_at?(HH:MM), cron_expr, action(模块动作名，空=Agent 对话),
                 enabled, last_run_at?, last_status(ok/error/skipped), last_error, last_note_id?,
                 recent_runs(JSON，最近20条 {at,status,summary}), created_at, updated_at
mcp_servers      id, name(唯一), command, args(JSON), env(JSON), enabled, status(stopped/connected/error),
                 last_error, tools_json(JSON), created_at
memories         id, content, source(manual/auto/import), session_id, tags(JSON), pinned, hit_count,
                 last_used_at?, created_at
```

### 8.2 模块表（22，`mod_` 前缀）

```
mod_hello_entry         finance_hello（机制示例）
mod_expense_run         finance_expense
mod_expense_row         finance_expense
mod_invoice_batch       finance_invoice
mod_invoice_item        finance_invoice
mod_vault_dataset       finance_vault
mod_pl_account          finance_ledger_personal
mod_pl_category         finance_ledger_personal
mod_pl_transaction      finance_ledger_personal
mod_pl_budget           finance_ledger_personal
mod_bip_batch           finance_ledger_bip
mod_bip_voucher         finance_ledger_bip
mod_bip_entry           finance_ledger_bip
mod_bip_balance         finance_ledger_bip
mod_tax_enterprise      finance_tax
mod_tax_financial_data  finance_tax
mod_tax_adjustment      finance_tax
mod_tax_return          finance_tax
mod_tax_policy          finance_tax
mod_board_snapshot      finance_board
mod_pos_run             finance_pos
mod_cashflow_run        finance_cashflow
```

要点：

- 状态/优先级用 Pydantic Literal 校验；时间统一本地 naive datetime。
- `model_id` / `active_model_id` 为**逻辑引用（无数据库外键）**，绑定的模型被删除/停用时自动回退全局激活模型。
- 老库中可能残留 v1.2.0 遗留表 `model_profiles`（ORM 已无对应模型，新库不会创建，不影响运行）。

## 9. 实现方法与算法说明

### 9.1 轻量列迁移机制（`main.py` 的 `_ensure_sqlite_columns`）

SQLite 的 `create_all` 不会修改已有表。启动时对历史库做补丁式迁移：
`PRAGMA table_info(表)` 读现有列，缺哪列补哪条 `ALTER TABLE ... ADD COLUMN`。
补丁来源 = 宿主内置补丁 + 各模块 `manifest.column_patches`。宿主内置补丁当前覆盖：

| 表 | 列 |
| --- | --- |
| `chat_messages` | `thinking` |
| `agent_sessions` | `archived` |
| `agent_settings` | `active_profile_id`、`active_model_id`、`enable_skills`、`skills_dir`、`enable_bash`、`enable_plugins`、`plugins_dir`、`reasoning_effort` |
| `scheduled_jobs` | `cron_expr`、`action` |
| `provider_models` | `temperature`、`note`、`supports_thinking`、`supports_vision`、`thinking_levels`、`probed_context`、`probed_max_output`、`probe_result`、`probed_at` |

表不存在则跳过，整体异常仅告警不阻断启动。专家团队 seed（`seed_agent_specs`）属配置数据，
不受 `SEED_ON_STARTUP` 控制；表为空时全量灌入，升级后新增的内置专家在下次启动自动补齐。

### 9.2 热备份（`backend/scripts/backup_db.py`）

用 `sqlite3 Connection.backup` API 在线热备份：源库以只读 URI 打开、逐页拷贝，
服务运行中也可安全执行，不会拷出半成品文件。产物带时间戳存到项目根 `backups/`，
自动滚动清理只保留最近 30 份。`备份数据.bat` 调用该脚本。

### 9.3 附件安全（`routers/assistant.py` 的 `/upload` 与编排器 `_attachments_block`）

- **上传**：`session_id` 白名单正则校验（防路径穿越；v1.15.1 起 rename/delete/archive/unarchive/export/history 等路径参数端点同口径校验）；文件名取 `Path(name).name` 去目录并做包含校验；
  扩展名白名单外返回 400（中文提示列出支持类型）；10MB 上限（超限 400）；
  落盘到 `data/uploads/{session_id}/`；xlsx/xls 用 openpyxl 读第一张工作表转 CSV（`utf-8-sig`）。
  读取统一走共享分块限流 `read_upload_limited`（1MB 分片、超限即抛 `UploadTooLargeError`，全局映射 400），
  不再裸 `file.read()`；宿主全局请求体上限 128MB（见第 10 节）。
- **注入**：对话时对每个附件路径做 `resolve() + relative_to(UPLOAD_DIR)` 校验，越界路径直接忽略；
  仅白名单文本后缀（txt/md/csv/json/log/py/html/xml/yaml/yml/sql）且 ≤300KB 的附件按文本注入消息
  （截断至 8 万字符）；其余仅告知路径，提示 Agent 用 Bash 工具处理（Bash 默认关闭）。
- **图片**：以 base64 DataBlock 注入（见 4.2「聊天传图与多模态理解」），同样受上传目录与大小上限约束。

### 9.4 模型接入与配置存储（`agents/llm.py` + `config_store.py`）

- 配置优先级：激活模型（`model_providers` + `provider_models`，界面管理）> `.env` 的 `LLM_*` 兜底。
- openai 协议 → `OpenAIChatModel`（覆盖 ModelScope/硅基流动/DeepSeek/火山方舟/DashScope 兼容/Ollama）；
  anthropic 协议 → `AnthropicChatModel`（强制流式聚合）。
- 思考深度 `reasoning_effort`（''/off/low/medium/high/max）映射到两协议各自的 thinking 参数；
  温度默认 0.3，模型级 `temperature` 非空时覆盖。
- 专家可独立绑定模型（`agent_specs.model_id`），编排器按 `model_cfg_by_id` 构建并缓存；
  不可用（删除/停用）时回退全局激活模型。

### 9.5 前端架构

- **无状态库的组件通信**：不使用 Pinia，页面内用 props/emit + ref 组合。`AssistantView.vue` 拆为容器
  （持有会话/团队/设置状态）+ `src/components/assistant/` 五个子组件：`SessionList`（会话列表，含归档页签与导出）、
  `AgentQuickList`（专家快选）、`ChatPanel`（对话区，SSE/中止/工具轨迹折叠/图片缩略图）、
  `SettingsDrawer`（设置抽屉，含模型能力探测）、`AgentTeamPanel`（专家团队管理）。
- **模块化前端**：`src/modules/*/manifest.js` 声明路由（`/finance/<name>/...`，懒加载）与菜单
  （group/icon/order），`import.meta.glob` 自动汇总进侧边栏财务分组。
- **列表请求序号防竞态**：任务/日程/笔记列表请求带自增序号（`++loadSeq`），响应返回时序号不是最新则丢弃。
- **统一 axios 拦截器**：`src/api/index.js` 把 422 detail 数组/超时/断网统一转成中文 ElMessage 提示；
  SSE 走原生 fetch 与 axios 共用 `VITE_API_BASE`（默认 `/api`）。
- **构建**：`npm run build`，各视图独立 chunk；生产模式前端产物由后端在 8000 端口直接托管，天然同源。

### 9.6 QwenPaw Agent 批量导入（`scripts/import_qwenpaw_agents.py`）

管道：**source files → sanitize → merge/create → DB**。

1. **source files**：读取 QwenPaw 工作区下每个 Agent 的角色定义文件——SOUL.md / PROFILE.md / AGENTS.md /
   00_系统总则.md / 06_协同规则.md（存在才读，按序拼接）。工作区默认 `D:/AIApps/QwenPaw/data/workspaces`，
   可用 `--from` 或环境变量 `QWENPAW_WORKSPACES` 指定；都没有则友好退出。
2. **sanitize**：脱敏——「某昵称」等称呼替换为「用户」，本机绝对路径替换为 `[本地路径]`。
3. **merge/create**：领域重叠的合并进内置专家（cre-leasing→leasing、cre-ops→operations、cre-mkt→marketing、
   cre-fm→property、cre-finance→it_finance、cre-it→security，追加角色定义 + 关键词取并集）；
   全新职责的新建专家（cre_gm、cloud_orchestrator、cloud_executor、cloud_verifier、datapaw），
   `sort=7..11` 排在内置专家 0–6 之后，同分优先路由到内置。
4. **DB**：写入 `agent_specs` 表，只进本机数据库、不进 git 仓库。`--dry-run` 只预览不写入；
   `--overwrite` 剥掉旧导入内容后重导；默认幂等——角色提示词带【QwenPaw 整合】标记，已导入则自动跳过不叠加。
   导入后 60 秒内团队缓存自动刷新，或重启服务立即生效。

`AgentSpecBase/Update.role_prompt` 上限从 8000 放宽到 60000 字符（QwenPaw 角色定义约 1.4 万字符），
脚本对拼接结果另做 60000 硬截断。配套测试 `backend/tests/test_import_qwenpaw_agents.py` 4 例，
静态校验、不需要本机装有 QwenPaw。

## 10. 安全规格

| 面 | 措施 |
| --- | --- |
| API Key 静态加密 | DPAPI（`dpapi:<base64>`，绑定本机 + 当前用户）；缺 pywin32 **拒绝启动，绝不降级明文**；回传前端脱敏 `••••••xxxx`，掩码哨兵 `********` 或脱敏值原样回传均表示保留现值 |
| SSRF 防护 | 网关/供应商地址校验（`validate_base_url` / `_check_http_url`，SDK 构造 / 网关探测 / httpx 测试同口径）：仅 http(s)、需有主机名、禁内嵌用户名密码与控制字符；本机单用户场景允许 127.0.0.1/内网（如 Ollama 本地网关），不封禁私网段；错误信息不回显 URL 主体 |
| 上传安全 | 扩展名白名单 + 分模块大小限制（聊天 10MB；费用/发票/数据仓/税务 20MB；BIP 10MB；个人 CSV 5MB；POS/现金流 50MB）+ 文件名包含校验 + `session_id` 白名单正则（防路径穿越）；读取统一共享分块限流 `read_upload_limited`（1MB 分块、超限即断 → 400）；宿主全局请求体上限 128MB（Content-Length 早拒 + chunked 累计 → 413）；导入类共享行数上限 `MAX_IMPORT_ROWS=200000`（失败关闭） |
| Origin 防护 | `OriginGuardMiddleware`：`/api` 状态变更方法（POST/PUT/PATCH/DELETE）与 `discover` 类 GET 校验 Origin——无 Origin 的 CLI/探针、回环开发来源、`CORS_ORIGINS` 配置项、运维配置 `HOST`（端口与 `PORT` 一致）的同源来源放行，其余 403；不再用「Origin 与请求 Host 自证同源」放行（避免 DNS rebinding 绕过）；普通 `/api` 读取不硬拦（跨站读取已由 CORS 兜底）；OPTIONS 预选不拦 |
| MCP env 保护 | `mcp_servers.env` 值落库前逐个 DPAPI 密封，连接/读取时还原；接口只回显键名与 `***`；存量明文在启动时由 `migrate_env_secrets()` 一次性迁移，DPAPI 不可用拒绝降级 |
| 超时口径 | LLM 客户端 120s；MCP 调用 30s / 握手 15s / 单条消息 4MB；模型探测 httpx 30s；DuckDB 查询 5s `interrupt()`；看板管线单步 600s、POS 单步 900s（超时落快照日志） |
| ZIP 安全 | zip-slip 防护：`is_relative_to` 判定拒绝越界与绝对路径条目 + 解压后总量上限 200MB（finance_cashflow / finance_pos / finance_invoice） |
| 数据仓 SQL | SELECT-only + 表名白名单 + 强制 LIMIT + 独立线程 `interrupt()` 超时；去注释后执行 |
| 执行面 | Bash 工具默认关闭且自带危险文件/目录黑名单；QwenPaw 插件默认关闭且只读模式跳过 |
| 监听面 | 仅 127.0.0.1；非回环 HOST 启动时告警（未做鉴权，勿在不可信网络开放） |
| 数据边界 | `gitignore` 分层：`data/`、`.env`、`backups/`、现金流引擎产出/真实数据；提交前无真实业务值 |

## 11. 接口设计

统一前缀 `/api`，业务接口详见 `API.md`。要点：

- 列表统一分页 `{items, total, page, page_size}`；PUT 均为部分更新（`exclude_unset`）。
- 删除返回 204、不存在 404、校验失败 422（含非法日期参数——`utils.parse_dt` 统一把日期解析错误转成 422 而非 500）。
- `/api/assistant/chat` 运行时异常返回 502；`/chat/stream` 保持 SSE 协议、在流内发 `error` 事件。
- 删除类数据操作不开放给 Agent（仅页面物理删除，任务可由 Agent 标记完成）。
- 模块路由按 `manifest.prefix`（`/api/finance/` 开头）挂载；模块自身接口清单见 `API.md`。

## 12. 配置与环境变量

对齐 `backend/.env.example`（全部有默认值，可不改直接运行）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 监听地址与端口（不要改成 `0.0.0.0`） |
| `DATABASE_URL` | `sqlite:///backend/data/workbench.db` | 数据库连接，默认 SQLite |
| `SEED_ON_STARTUP` | `1` | 空库时是否写入示例数据（0 关闭；专家团队 seed 不受此控制） |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 跨域来源（仅开发模式需要） |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | AI 助手模型兜底配置；填齐三项即激活。界面配置优先于它 |
| `FINANCE_DATA_DIR` | 空（→ `backend/data/finance`） | 财务模块数据目录 |
| `DEBUG_DATA_DIR` | 空（→ `backend/data/debug`） | 调试中心数据目录（运行时读取，测试隔离用） |
| `FINANCE_EXPENSE_ENGINE_PATH` / `FINANCE_INVOICE_ENGINE_PATH` / `FINANCE_POS_ENGINE_PATH` / `FINANCE_CASHFLOW_ENGINE_PATH` / `FINANCE_BOARD_PIPELINE_PATH` | 空（按AssetLib相对位置推导） | 跨项目引擎/管线路径；**接受目录或单一文件**（文件取父目录，`utils.resolve_engine_root`）；缺失时对应接口 503 中文，不影响其他模块 |

其余运行时配置（供应商、模型、激活模型、Agent 工作参数、技能/Bash/插件开关、MCP 服务器）
全部走界面存库（`agent_settings` 单行 + 各配置表），无需手改 `.env`。

## 13. 测试与验证

> 本节数字分为四类：**2026-10-20 历史快照**、**2026-10-22 第三轮实测**、**2026-10-23 外部验证轮实测**与 **2026-10-26 深测轮实测**。
> 2026-10-26 深测轮：后端 pytest **909/909**、前端 vitest **652/652（45 文件）**、生产构建通过；
> 17 区探针按 `progress.md` 记录实际复跑结果，不以历史数字代替本轮证据。
> **真实 LLM / 外部引擎 / 真实 MCP / 用友 BIP 原稿已于外部验证轮完成实测**（第 1–4 章，硅基 TPM 限流与
> 真人可用性为外部约束，见 `AGENTS.md` 验证状态节）；第 6 章可用性为自动化代行，真人阈值待复测。

- **后端 pytest（2026-10-22 第三轮实测）**：`backend/tests/` **888/888 通过**（257.75 秒；4 条上游弃用警告）。conftest 临时库隔离、
  引擎对拍、monkeypatch 探测/LLM，全部离线可重复。
  覆盖业务 CRUD/筛选/统计/搜索 + Agent 路由/工具/配置脱敏/供应商模型 CRUD/专家团队 CRUD 与恢复出厂/
  连通性测试 + 定时任务 + QwenPaw 导入静态校验 + RAG/记忆/MCP/技能包/专家团文件 +
  模型能力探测 + 各财务模块 + 会话归档导出（`test_session_archive.py`）+ 聊天传图（`test_chat_image.py`）
  + 调试中心（`test_module_debug_center.py`：种子幂等/issue CRUD/run 归档与防穿越/报错容错/summary）
  + v1.15.1 复核加固回归（zip-slip 同前缀绕过/解压炸弹/running 兜底/掩码双形态/session_id 校验/
  LIKE 转义/registry 异常隔离/manifest 校验/MCP 挂起超时）
  + 2026-10-19 审计修复回归（`test_bugfix_*.py` 五件 67 例：只读 readonly 放行/技能启停失效缓存/
  转义闭环/供应商级联删除/cron 更新校验与错过补偿/手动运行不吞定时/RAG 中文混合词/MCP 更新停旧进程/
  debug 并发锁/发票 failed 态/税务导出与校验/企业字段/记账账户警告/上传大小写与上限/aware 日期/
  引擎线程池/管线 409 等）
  + 2026-10-20 E2E 探测回归（MCP readonly 注解唯一依据与工具注销/cron 补偿不早于创建时间/
  专家包 NULL 工具池语义/导出文件名 CRLF 清洗/schedule_type 枚举与跨字段归一/调试中心空白标题 422）
  + 2026-10-20 五区补探回归（搜索空白关键词 422/今日日程 00:00 边界/笔记 null 与空白标题校验/
  标签 JSON 转义匹配/删除笔记清理定时任务悬挂引用/会话 session_id 校验与首条消息补标题/
  技能包描述 YAML 安全序列化/关键词逗号导出往返/恢复出厂重放模块预置工具）
  + 2026-10-20 加固轮回归（超 int64 路径 ID 入口拦截 `utils.DbId` 与全局
  `OverflowError`→422 兜底、调试中心 `client_errors.jsonl` 按大小裁剪有界且逐行仍可解析、
  ID 越界不泄露堆栈、深翻页 `PRAGMA temp_store=2`、conftest 钉 `tempfile.tempdir` 防临时件落进仓库）。
- **前端 vitest（2026-10-22 第三轮实测）**：`frontend/tests/` **44 个文件、648/648 通过**（3.51 秒）：注册断言 + 纯逻辑 + setup 级组件测试
  （格式化/日历范围/Agent 工具函数/定时任务展示/模型探测交互/各财务模块/调试中心三模块）
  + 审计修复回归（`bugfix-frontend-*.test.js` 两件 36 例：笔记新建定位/记忆表单重置/highlight 参数/
  MCP env 保护/附件-only 发送/会话切换不破图/全部导入确认/拦截器去重/本地时区日期/月份下拉/
  筛选复位页码/file input 重置）
  + E2E 探测回归（任务筛选同步与逾期排序/助手预设填充与专家模型下拉刷新/MCP 编辑保护/
  搜索建议竞态与占位项/日程搜索跳转定位/笔记草稿保护与失败反馈/删除会话失败保留状态/
  技能包目录名 URL 编码/看板失败快照刷新/记账支出分类口径）
  + 未捕获 rejection 守卫（`no-unhandled-request.test.js`：**扫描 `src` 全部 86 个 .vue/.js**，
  函数顶层直接发请求的 `await` 必须同行带 `.catch`，新增页面自动纳管）
  + 提交竞态回归（`interaction-submit-race.test.js`：连点单次提交、失败后锁复位）。
- **E2E 区域探针（2026-10-22 第三轮实测）**：`dev/e2e/probes/*.py` **17 区 656/656 通过**（各区固定端口
  8201–8221 + 独立库 `dev/e2e/probe_results/<area>.db`（每次运行重建）+ 独立 `FINANCE_DATA_DIR` /
  `DEBUG_DATA_DIR`（`<area>-fin` 等），不触碰真实 `backend/data/`；结果归档 `dev/e2e/probe_results/`）。
  2026-10-22 第三轮已把原先缺 `DEBUG_DATA_DIR` 的 12 个探针补齐，并以
  `backend/tests/test_swarm3_probes_isolation.py` + `test_swarm_probe_contracts.py` 静态守护
  （合计 78 通过）；首轮实跑的 12 个失败经根因分析确认是探针口径滞后，修正后复跑通过。
  其中 `interaction-ux` 为**自动化交互走查**，
  不等同于真人可用性测试（不含访谈、原话与主观评分）。
- **性能基线（2026-10-22 第三轮实测）**：`dev/e2e/perf_probe.py`（播种 tasks 5000 / notes 2000 / schedules 1000 /
  sessions 200 / messages 4000 / transactions 20000，预热 5 次取 30 次）——18 接口 p95 最慢 76.68ms
  （`stats/trend?months=24`），预算 health 20 / list 120 / aggregate 250；真实浏览器首屏
  DOMContentLoaded 207ms、资源 2.705MB、0 page error；唯一越线为 `/api/health` p95 21.81ms
  （静态字典端点，判为本机噪声，记录不掩盖、未改阈值）。
- **冒烟**：uvicorn 真实端点 + 冒烟数据清理。
- **构建**：`npm run build` 通过（8.56s，2026-10-20 历史快照；各视图独立 chunk，两个 index chunk 1.28MB + 1.03MB 为
  Element Plus / ECharts 全量引入的大头，分包留作专项）。
  **构建检测**：`frontend/scripts/needs-build.js` 比较 `src/`、`index.html`、`vite.config.js`、
  `package.json`、`package-lock.json` 与 `dist/index.html` 的修改时间——产物缺失或任一源更新即判定
  需要构建（退出码 1，打印原因）；`启动工作台.bat` 每次启动先调用它，按需才执行 `npm run build`。
  `prebuild` 的 `clean-dist.js` 手动递归清空 dist（规避本机 Node 24 `rmSync` 静默失效，见 R-11）。
- **手动验收**：十个静态页 + 设置抽屉 + 专家团队 tab + 技能包/专家团文件页 + 知识与记忆页 + MCP 页
  + 财务分组菜单 + SSE 对话流 + 路由测试工具 + 会话归档/导出 + 聊天传图。

## 14. 交付边界与后续方向

- 单用户、监听 127.0.0.1；删除类数据操作不开放给 Agent（仅页面物理删除，任务可由 Agent 标记完成）。
- 已完成的能力闭环：模块化（Module Registry）+ 四级接入 + Agent 工具预置 + 表驱动专家团 +
  流式对话 + 定时调度（cron/模块动作）+ 知识/记忆/MCP + 模型能力探测 + 会话归档导出 + 多模态传图。
- 后续候选（按价值排序）：聊天消息「存为记忆」按钮、
  MCP HTTP/SSE 传输与 RAG 向量融合（ADR 预留项）、现金流/POS 高级参数透出、
  团队共享场景再考虑多租户。

## 15. 算法设计逻辑与模块实现状态（2026-10-22）

### 15.1 关键算法与设计逻辑

- **有界上传**：`read_upload_limited()` 按 1MB 分块累计校验，超限即断；全局 ASGI 请求体上限 128MB，覆盖 `Content-Length` 早拒与 chunked receive 累计，413 去重。
- **导入行数护栏**：`MAX_IMPORT_ROWS=200000`，在解析或取数阶段 fail-closed；BIP 入仓用 `LIMIT 上限+1` 避免全量先进内存。
- **重活卸载**：同步解析/落盘/建运行统一进线程池，契约按线程标识与事件循环可调度判定。
- **事务隔离**：个人 CSV 导入按行 SAVEPOINT，坏行不回滚有效行。
- **Origin 判定**：仅放行无 Origin CLI/探针、回环、显式 CORS、运维配置 `HOST` 同源；不再用 Origin 与 Host 自证。
- **MCP env 保护**：逐值 DPAPI 密封 / 还原，输出掩码，启动时迁移存量明文。
- **路径与压缩包安全**：快照 HTML 限制在快照根内；ZIP 逐段校验 Windows 合法字符、设备保留名、穿越路径，并限制解压总量。
- **只读 SQL**：白名单 + 禁词 + 单语句 + 强制 LIMIT；LIMIT 匹配跳过字符串字面量。
- **CSV 公式注入防护**：用户可控文本以 `=`、`+`、`-`、`@` 开头时前置单引号。
- **前端竞态守卫**：列表/预览/详情统一请求序号，迟到响应丢弃，失败清理与 loading 复位只对最新请求生效。
- **概览截断**：今日日程有界读取，截断时返回总数与布尔截断标记，不静默丢数据。
- **BIP 表头定位**（`_locate_header_row`）：前 25 行内按「关键词类别数」评分（≥2 类候选），不用总命中数——用友元数据行「对方科目×2」与余额表两级表头都会骗过总命中评分（2026-10-23 外部验证修复）。
- **BIP 列签名识别**（`detect_file_type`）：分录摘要/制单人 → voucher（凭证列表独有，按账簿分 sheet 无账簿列）；对方科目 → aux；期初/本期/期末 → balance；unknown 进人工映射。多 sheet 以 sheet 名合成 `__sheet_ledger__` 账簿列；续行按账簿分组 ffill（凭证日期/凭证号不重复）。
- **引擎路径解析**（`utils.resolve_engine_root`）：目录原样 / 文件取父目录 / 不存在原样返回交由调用方 503；覆盖五模块。
- **引擎子进程环境**（`utils.engine_subprocess_env`）：TEMP/TMP/TMPDIR 显式钉到应用自建临时目录，修 Git Bash 继承破损 TEMP；上传文件名 mojibake 修复（`repair_upload_mojibake`，latin-1→gbk 回转，回转含中文才采纳）。
- **探测 429 退避**（`model_probe._chat_once`）：429 等 30s 重试至多 2 次，避免厂商 TPM 窗口把探测判成部分完成。
- **财务数据脱敏**（`dev/desensitize_finance_data.py`）：金额随机 + 勾稽重算（凭证借=贷、明细账余额链、余额表配平、报表合计行），泛化命名避开 BIP 表头关键词（防污染表头定位评分）；`dev/verify_desensitized.py` V1–V5 断言。

### 15.2 模块功能需求实现情况

| 模块 | 实现状态 | 验证状态 |
|---|---|---|
| 任务 / 日程 / 笔记 / 概览 / 搜索 | 已实现 | 后端 + 前端 + E2E + 可用性代行通过 |
| AI 助手 / Agent 团队 / 模型探测 | 已实现 | 真实 LLM 实测（硅基三模型；L8/L9 完全体经 Step5 适配补上）；GLM/Qwen 受硅基 TPM 429 截断记录在案 |
| 知识与记忆 / MCP / 技能包 / 专家团文件 | 已实现 | 真实 MCP（kimi-datasource）+ 本地 mock 实测通过 |
| 定时任务 / 调试中心 | 已实现 | E2E 通过 |
| 费用 / 发票 / POS / 现金流 / 报表看板 | 已实现 | 真实引擎 + 脱敏数据实测（对拍一致、E4 边界、E9 ±20% 复跑、W1–W9 全过） |
| 数据仓 / BIP / 个人记账 / 企业所得税 | 已实现 | BIP 原稿 13001 行实测入库；其余本地数据库与导入导出边界通过 |
| 可用性（第 6 章） | — | U1–U10 自动化代行完成；真人阈值（≥80% 无协助）待招募复测 |

### 15.3 已验证运行情况

- 后端 pytest：`909/909`（2026-10-26 深测轮，含停止脚本 findstr 回归）。
- 前端 vitest：`45 文件、652/652`；生产构建通过（10.48s）。
- E2E：17 区探针 `656/656`（2026-10-26 深测轮）；旧 E2E 三脚本历史通过。
- 性能：最慢 p95 `76.68ms`；`/api/health` p95 `21.81ms` 越 20ms 预算，未改阈值。
- 真实浏览器：临时数据库下完成主要菜单与新增入口验收。
- 外部验证（2026-10-23，隔离服务 + 脱敏数据，真实 `backend/data` 字节级零改动）：第 0 章 G1–G9、第 1 章 LLM、第 2 章 MCP、第 3 章 BIP 11/11、第 4 章五管线 + E4/E9、第 5 章 W1–W9、第 6 章 U1–U10 代理走查。
- 完整记录见 `20261022-修复与验收报告.md`。
