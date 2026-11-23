# 个人工作台 · 需求与设计规格（spec）

> **版本 v1.19.0（与 `backend/app/config.py` 一致；数据池清洗——列画像/配方清洗/整合分析随本版提供）**
>
> 历次演进：v1.0.0 基础工作台 → v1.1.0 Agent 团队 → v1.2.0 QwenPaw Agent 导入 → v1.3.0 Agent 团队配置化 + SSE 流式对话 → v1.4.0 定时 Agent → v1.7.0 安全加固轮（DPAPI / SSRF / 依赖锁定）→ v1.8.0 模块注册机制（Module Registry）→ v1.9.0 首批 5 个财务模块 → v1.10.0 企业所得税迁入 + 报表看板 → v1.11.0 L1 任务型薄壳 + cron 调度与模块动作 → v1.12.0 RAG / 长期记忆 / MCP → v1.13.0 模块工具预置 + 技能包 + 专家团文件化 → v1.14.0 厂商预设模块化 + 模型能力探测 → v1.15.0 会话归档与导出 + 聊天传图多模态理解 → v1.15.1 复核加固轮（三路并行审计驱动，23 项修复）→ v1.16.0 调试中心 → v1.16.1 体验修复十一项 → v1.17.0 经营管理模块（内控/两份周报/月度工作情况四份报表导入 + 指标与叙述块）→ v1.18.0 知识库（制度/手册文档库 + FTS5 关键词 + sqlite-vec 向量混合检索 + 版本/生效期 + 扫描件 OCR + Agent 引用）→ v1.19.0 数据池清洗（finance_vault 升级：列画像 + 配方清洗 + 整合分析 + 配方日志）→ **未发版增量：政策有效期登记（POLICY_REGISTRY）+ 四税种填报（印花税/增值税/个税/残保金）+ WorkBuddy 同步导入（技能包/记忆/身份文件，幂等）+ 记忆类型/统计/MD 导入导出/注入预算（2026-10-30 五部分轮）**。
>
> 权威 as-built 快照：`20261012New/REVERSE_02-SPEC重构.md`；接口细节：`API.md`；架构决策：`20261012New/adr/ADR-001~005`。
>
> 本版为**文档校准 + 修复验收轮**（2026-10-22，版本号仍为 v1.16.1）：按当前工作区实现校正运行环境与安全规格细节
> （Node 24.22.1+ / Origin 守卫（不再用 Host 自证，保留配置 HOST 同源）/ MCP `readOnlyHint` 缺省拒绝与 env DPAPI 密封 /
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
外加一个**表驱动可配的 AI 助手 Agent 团队**（内置 8 位，可按需导入 QwenPaw 角色）与一整套**财务业务模块群**，
让自然语言可以直接操作工作台与财务数据。

| 约束 | 内容 |
| --- | --- |
| 部署形态 | 仅监听 `127.0.0.1`、无登录鉴权、数据不出本机；非回环监听且未开 `ALLOW_NON_LOOPBACK=1` 时除健康检查外全部 503 |
| 存储 | SQLite 单文件 `backend/data/workbench.db`，零外部数据库依赖；不允许 Docker 与外部独立数据库 |
| 运行环境 | Windows 11 · Python 3.12+ · Node 24.22.1+ |
| 依赖红线 | 仅 MIT / Apache-2.0 / BSD；新增依赖需锁版本（`requirements-lock.txt`） |
| 业务值外置 | 真实业务数据放各引擎 `local_business.json` / `config.local.yaml`；**现金流 V1 数据永不推远端** |
| Agent 权限边界 | 不开放物理删除（仅页面删除；任务可由 Agent 标记完成）；Agent 可新建/完成任务、日程、笔记与查询；财税专员另有 `pl_add_transaction` 可新增一笔个人记账（只新增、不删除） |
| 交付标准 | 测试、文档、双击 bat 启动三件套齐全 |

## 2. 架构总览

```
浏览器（Vue 3.4 纯 JS + Element Plus + ECharts + AG Grid，hash 路由）
   │ /api（axios；SSE 走原生 fetch）
FastAPI（app/main.py：lifespan 只编排 → DPAPI 校验 → 建表/轻量迁移/启动种子（app/bootstrap）
         → 调度器 → MCP 启停；一次性迁移见 backend/scripts/migrate.py）
   ├── 静态路由 ×12：tasks / schedules / notes / search / dashboard / assistant
   │                  / jobs / knowledge / mcp / skills / experts / model_probe（探测 + 厂商预设别名）
   ├── Module Registry（app/modules/）：目录扫描 manifest → 挂载路由/表/工具/预置
   ├── Agent 团队（agents/）：team 表驱动 + orchestrator 五级路由 + tools 白名单
   ├── 知识与记忆（rag BM25 / memory 召回注入 / mcp stdio 客户端）
   └── SQLite（86 张表：宿主 17 + 模块 68 + v1.2.0 遗留 1）
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
| **底座（可选）** | DuckDB 只读底座（数据池清洗），供其它模块 `export-to-vault` 落库 | finance_vault |

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

**聊天文档分析与多入口上传（2026-10-27）**

- 白名单新增 `.pdf .doc .docx .ppt .pptx`，文件选择、聊天区拖放、输入框剪贴板粘贴共用前端 `uploadFiles`；保留单文件 10MB、并发 loading、图片预览和普通文字粘贴行为。
- 上传保留原件并返回 `kind="document_text"` 与原文件 `name/path/size`；服务端在同会话 `.extracted/` 写 UTF-8 sidecar，编排层校验原路径与 sidecar 均在 `UPLOAD_DIR` 后把正文直接注入消息，普通文本模型即可分析。
- PDF 由 pdfplumber 分页提取；DOCX/PPTX 由 python-docx/python-pptx 提取段落、表格、幻灯片与已有备注；DOC/PPT 经 LibreOffice 隔离无界面转换后复用新版解析。解析/转换在线程执行，失败清理并返回中文 400。
- 每附件正文最多 80,000 字符，PDF 前 300 页、PPT 前 500 页，截断显式标注；OOXML 成员数/解压大小与旧版转换时间均有限额。扫描件和文档内图片不做 OCR，无文字时明确拒绝而非假成功。

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
- **套餐边界**（Task 5）：`job-*` 会话选中 `usage_scope=plan_interactive_only`（仅限交互式使用的套餐）的
  生效模型（专家绑定或全局激活）时，聊天与流式同口径返回中文拒绝、不发起推理；**自动路由**在路由前
  预检全局模型（路由本身可能调用全局模型仲裁），**明确指定专家**不预检（路由不触碰全局模型），
  两种情况都由路由后的专家绑定模型复核兜底；普通聊天会话不受影响。
- **模块动作任务**：`action` 非空时到点执行注册的模块动作（`JOB_ACTIONS`，如 `finance_board.run`）而非 LLM 对话，
  走线程池执行、不写笔记；`GET /api/jobs/actions` 列出已注册动作。
- **并发与异常**：调度循环每 20s 检查一次；同一任务串行执行（上次未结束则本轮跳过）；
  LLM 未配置标记 skipped 不发请求；单次异常记入 `last_error` 与 recent_runs，循环吞掉异常继续下一轮。
- **校验**：`interval` 必须带 `interval_minutes`（5~10080）、`daily` 必须带 `daily_at`（HH:MM）、
  `cron` 必须带合法 `cron_expr`，否则 422。

### 4.4 财务模块（19 可入库/可用 + 1 机制示例）

| 模块 | 级别 | 关键规格 |
| --- | --- | --- |
| finance_expense 费用分类 | L0 | v2.0 引擎直挂，规则只读权威源 |
| finance_invoice 发票处理 | L0 | 发票工具引擎直挂，仅文本型 PDF |
| finance_vault 数据池清洗 | 底座 | DuckDB 底座 + 列画像/配方清洗/整合分析/SQL 查询（SELECT-only + 表白名单 + 强制 LIMIT + interrupt 超时），`ingest_dataframe` 契约；v1.19.0 增清洗与聚合能力；数据集删除需 `confirm=数据集名` 二次确认、被引用或残留需 `force=true`，重命名同步视图/物理修订表/登记/清洗日志引用；清洗与聚合另存的结果表默认不得与源表同名或已存在（409，`allow_overwrite=true` 才覆盖） |
| finance_ledger_personal 个人记账 | 全新 | 两级账户/分类/交易/预算，CSV 导入导出 |
| finance_ledger_bip 企业记账 | 全新 | BIP 三类导出列签名识别 + 人工映射，借贷平衡 warning，export-to-vault |
| finance_tax 企业所得税 | 整体迁入 | 原项目 5 routers + 10 services 移植，`mod_tax_` 表前缀，迁移脚本幂等；财报导入能识别税号或企业名称才真实落库（否则仅预览 `persisted=false`，可用 `/import/{enterprise_id}` 指定企业）；申报草稿可 `PUT` 更正重算，已提交不可更正/删除（409），企业存在已提交申报时删除 409；CIT 计税入口过 `require_tax_scope` |
| finance_receipt 银行回单整理 | L2 | 账期（人工起止日期）扫描建行/平安/中行回单，页内按表头坐标逐笔切分并五类识别（待清算收入/正常转账收入/转账支出/结息/手续费，无法判断记「待确认」）；待确认未清零禁止生成；按账户×类别固定槽位排版（建行、平安 3 笔页，中行 2 笔页），原子替换产物，提供单份/合并下载与打印、整批 ZIP、原单预览与 XLSX 清单（防公式注入）；详见 §19 |
| finance_board 报表看板 | L2 | 优先计划任务「微信报表监控」通道（以 `logs/run.lock` 判在途、轮询 `last_run.json`；无任务回退直接子进程），单次尝试 15s 查询 + 15s 复查 + 30s 触发 + `max(通道 600s, 直接回退总预算 600s)` = **660s**，最多尝试 2 次（重试 1 次/间隔 5s/残留锁等待 ≤60s，最坏 1385s，前端超时 1500s），HTML 快照归档 + 日志尾部（入库截断 4000、读取末 500），iframe 嵌入（CSP `frame-ancestors 'self'`），产物校验 `GET /verify`（仅接口） |
| finance_pos POS 对账 | L1 | 台账 + 差异分析两步执行（单步 900s 超时），产物差集归集，上传 50MB；执行互斥（并发 409），只有 0 字节产物判失败 503；v1.19.0 增刷卡凭证（回单×对账单合并凭证 + 月度手续费凭证，报表日期容差 `max(10, date_tolerance+7)` 天），详见下方小节 |
| finance_bankflow 银行流水模板 | 纯转换 | 建行/中行流水 xlsx → BIP 银行对账单模板；列名匹配、账号映射（全号/尾号）、无流水号合成 `SYN`、官方骨架保格式、20MB、零新表；预览**只返回掩码 + `account_token`**，导出按令牌服务端反查（仅兼容唯一掩码，歧义 400），完整账号不出后端 |
| finance_cashflow 现金流填报 | L1 | V1 引擎 CLI 重定向，ZIP 解包 + zip-slip 防护；运行记录带 `actual_year`（2000–2100，缺省当前年，历史 0 按创建年兜底）；运行记录列表分页（`page` ≥ 1、`page_size` 1–100，返回 `{total, items, page, page_size}`，按 id 倒序），上传 50MB；任何失败/重跑分支清空 `artifacts` |
| finance_recon 商户核销对账 | L2 | 四源（核销/认领/欠费/账单 PDF）导入 + 合同范围显式勾选 + 机检勾稽 + 人工复核（确认/排除永不被重跑覆盖）+ 三格式报告；`ever_ready` 批次审计留痕不可删，源文件仅 draft/failed 可删 |
| finance_lease 租赁核算 | 新增（工作树，未发版） | 承租人使用权资产/租赁负债**初始 + 正常后续计量**：合同台账、付款计划（逐期/整表替换/xlsx 预览→确认导入）、测算（评估表 + 期间表）、三组恒等式与滚动校验、确认快照、五页签 xlsx 底稿；**简化口径** 使用权资产 = 租赁负债现值；计划口径不对账实付、不回写 BIP（另有 BIP 科目余额**只读**对账，见 §20）；变更/终止/长摊/工会不在本模块 |
| finance_hello 模块示例 | 机制实证 | 连通性自检 + 样例表 `mod_hello_entry` + 只读工具 `hello_ping`（财税专员白名单内） |
| finance_deferred 长期待摊 | 新增（工作树，未发版） | 仅租入资产改良支出的直线摊销：净额=含税÷(1+税率)、月摊=净额÷月数（HALF_UP 两位）；末期尾差如实留在末期余额，**不吸收、不转销、不倒挤**；摊销到期晚于租赁到期只 `warning`，确认须 `risk_acknowledged` + 非空 `risk_note`；三页签底稿；不做非租赁长摊 |
| finance_union 工会经费计提 | 新增（工作树，未发版） | 手工工资基数 + **批次费率**（必填、行级无覆盖）；本期计提=基数×费率、上期差异=上期实际−上期已计提（负数=冲回）、本期建议入账=本期计提+上期差异；缺输入 `unknown` 不按 0；确认后只读；三页签导出；不读 HR、不做 60/40、凭据、税前判断、支付与 BIP 回写 |
| finance_stamp_duty 印花税填报 | 新增（未发版，随 v1.16.1） | 13 税目税率表（印花税法）+ 免税标记 + 六税两费减半（policy_status 有效期校验），申报底稿 xlsx 导出；同纳税人同期间重复 409，`PUT` 受控更正（已导出/已提交 409），已提交删除 409，计税入口过 `require_tax_scope` |
| finance_vat 增值税填报 | 新增（未发版，随 v1.16.1） | 一般纳税人（13%/9%/6% 白名单销项−进项，留抵归 0）/ 小规模（季度 30 万免征、超限全额 1%，19号公告有效期校验）双模式，底稿导出；同纳税人同期间重复 409，`PUT` 受控更正（不换纳税人/期间，已导出/已提交 409），已提交删除 409，计税入口过 `require_tax_scope` |
| finance_iit 个人所得税填报 | 新增（未发版，随 v1.16.1） | 工资薪金累计预扣法逐月模拟（5000 减除+社保+专项附加+其他，七级预扣率跨档），扣缴底稿导出；专项附加按国发〔2023〕13号：住房贷款利息 1000 元/月（≤240 个月）、住房租金 1500/1100/800 元/月（城市档位）、学历继续教育 400 元/月×≤48 个月、职业资格继续教育 3600 元/年；`PUT` 受控更正，计税入口过 `require_tax_scope` |
| finance_dsf 残保金填报 | 新增（未发版，随 v1.16.1） | （职工总数×比例−安排数）×年平均工资；30 人免征；安排比例分档减收 50%/90%（8号公告有效期校验），底稿导出 |

跨项目引擎/数据路径（费用分类 / 发票 / POS / 现金流 / 报表看板）经
`FINANCE_EXPENSE_ENGINE_PATH` / `FINANCE_INVOICE_ENGINE_PATH` / `FINANCE_POS_ENGINE_PATH` /
`FINANCE_CASHFLOW_ENGINE_PATH` / `FINANCE_BOARD_PIPELINE_PATH` 配置项解析，默认按示例主体22相对位置推导；
**路径接受目录或单一文件**（`utils.resolve_engine_root`：文件取其父目录，不存在原样返回交由调用方校验）；
引擎目录缺失时对应接口返回 503 中文提示，不影响其他模块。引擎子进程统一经
`utils.engine_subprocess_env()` 显式指定 TEMP/TMP/TMPDIR（修 Git Bash 继承破损 TEMP 导致引擎临时文件失败），
上传文件名经 `utils.repair_upload_mojibake()` 修复 GBK/Latin-1 乱码（依赖中文文件名的引擎匹配）。
BIP 导入的 `export-to-vault` 在数据池清洗模块不可用时同样 503 降级。

**上传限额与导入行数（分模块口径，as-built）**：聊天附件 10MB；费用分类 / 发票 / 数据池清洗 / 税务
各 20MB；BIP 10MB；个人记账 CSV 5MB；POS 与现金流各 50MB。所有上传路由统一走共享有界读取
`app.upload.read_upload_limited`（1MB 分块、累计超限即中断读取，抛 `UploadTooLargeError` 由
`main.py` 全局映射 400），不裸 `file.read()`；宿主另有全局 ASGI 请求体上限 128MB
（Content-Length 早拒 + chunked 累计，超限 413，宽容路由吞异常也照发）。
导入类（Excel/CSV）共享行数上限 `app.import_limits.MAX_IMPORT_ROWS = 200000`（缺失即失败关闭）；
费用分类在引擎执行与写库前按该上限护栏（openpyxl 只读流式迭代 + `reset_dimensions()`，
口径为各工作表非空行合计含表头，超限早停于 limit+1 行）。

各模块 HTTP 接口清单见 `API.md`「财务模块接口（/api/finance/*）」一节。

#### 数据池清洗（v1.19.0）

`finance_vault` 由「数据仓」原地升级为「数据池清洗」：DuckDB 底座与 `ingest_dataframe` 契约不变，
原「表清单 + SQL 查询」两视图改为前端三页签 `PoolView`（数据池 / 整合分析 / SQL 查询，
URL `?tab=` 驱动、`?table=` 预选表）。新增 `cleaning.py` 与 `mod_vault_clean_log` 表：

- **列画像 `GET /api/finance/vault/tables/{table}/profile`**：每列 DuckDB 原生类型 + 文本列采样
  （≥80% 可解析才判 numeric/date，采样 ≤500 值）三态类型推断；缺失率、唯一率、top5 高频值、
  数值列 min/max；表级警告（全空列、疑似常量列、缺失率 >30%、唯一率≈100% 疑似 ID 列、整行重复）。
  行数超扫描上限降级 sampled（仅缺失率）。
- **配方清洗 `POST .../clean/preview | clean/apply`**：配方 JSON（4 算子开关 + required_columns +
  invalid_policy + mark_outliers_iqr），固定顺序执行 7 步（脏值归一→整行全空剔除→全空列剔除→整行去重→
  类型校验（无效值 drop_row/null_cell 二选一）→关键列必填（缺列跳过并警告）→IQR 离群只标记不剔除）。
  预览不写库；应用后结果落 `{表名}_clean` 新数据集并写配方日志，原始数据不动；配方未知键忽略并警告
  （跨表复用容错）。
- **单表聚合 `POST .../aggregate | aggregate/export | aggregate/save`**：维度分组 + 指标
  （sum/avg/count/min/max 白名单）+ 过滤（9 算子白名单）+ 排序；**在 pandas 内存分组完成，不拼 SQL**
  （Mimosa PreToolUse 会拦截 SQL 字符串拼接，且规避注入面）；sum/avg 作用于文本型数值列时先数值化
  （与清洗同口径）；导出 xlsx 走 `safe_cell` 防公式注入；另存经 `ingest_dataframe` 登记可再查询再清洗。
- **清洗日志 `GET .../clean/log`**：配方 + 逐算子报告（单元 rows/cells/cols 区分）+ 样本留痕（≤10 行）
  + 双侧缺失率对照，按 id 倒序，供配方复用与重放。
- **数据集使用预检 `GET .../usage`**：返回登记/视图/物理修订表/清洗日志引用（源表与结果表计数）/
  `consistent` 与 `issues`/`requires_force`，供删除与重命名二次确认。
- **安全删除 `DELETE .../tables/{table}?confirm=&force=`**：`confirm` 必须等于数据集名（缺失 422、不符 400）；
  被清洗日志引用或存在未登记残留时需 `force=true`，否则 409；成功同时清理视图、物理修订表与登记。
- **重命名 `POST .../tables/{table}/rename`**：`{new_table}` 过同一标识符白名单；同锁内改视图/物理修订表/登记/
  清洗日志引用，目标已存在或与源同名 409。
- **另存覆盖语义**：`clean/apply` 与 `aggregate/save` 的结果表默认**不得与源表同名、且不得已存在**
  （409「结果表名与源表相同/结果表已存在」），显式 `allow_overwrite=true` 才覆盖；目标检查与写入在同一写锁临界区。

#### POS 刷卡凭证与目录浏览（v1.19.0）

`finance_pos` 新增月度刷卡凭证生成（`vouchers_service.py` + `mod_pos_voucher_batch` 表），
完整算法见《算法实现逻辑》§11：

- **端点**：`GET /api/finance/pos/vouchers/config`（源/输出目录回退值）、
  `POST /api/finance/pos/vouchers/generate`（月份 + 可选源/输出目录，503=运行期失败、400=输入问题）、
  `GET /api/finance/pos/vouchers`（批次列表）、`GET .../files/{name}`（单文件下载）、
  `POST .../merge`（选中凭证合并 PDF）、`DELETE /api/finance/pos/vouchers/{batch_id}`（删批次）、
  `DELETE /api/finance/pos/runs/{run_id}`（删运行，running 拒绝）。
- **算法概要**：按月扫描源目录（`FINANCE_POS_STATEMENT_DIR` 或人工指定，支持归档基目录或当月目录两种布局）
  → 解析 SHOP 对账单「终端小计」行 + 回单 PDF 文本层「单位客户专用回单」分块 →
  双键匹配（终端一致 + 报表日期 ≤ 清算日 ≤ 报表日期+容差天；金额命中优先、日期最近兜底）→
  对账单打印补丁（隐藏其他终端数据行）→ LibreOffice 转 A4 横排 PDF → 裁切回单 →
  每笔交易生成「对账单页+回单页」合并凭证 + 月度手续费凭证（汇总首页 + 全部对账单页）→
  中间产物清理，`manifest.json` 留痕。
- **配置**：`finance_pos_statement_dir / finance_pos_output_dir / finance_pos_project_map /
  finance_pos_receipt_glob / finance_pos_match_date_tolerance / finance_pos_match_amount_tolerance`
  六项（详见 §12）。
- **目录浏览**：`GET /api/fs/drives`（可用盘符，非 Windows 返回单根）与 `GET /api/fs/browse`
  （列子目录，不含文件，绝对路径校验、隐藏 `$`/`.` 条目、上限 500 项）——只读、不读文件内容，
  前端 `DirectoryPicker.vue` 弹窗逐层选择，供凭证源/输出目录与后续目录类配置复用。

### 4.5 知识、记忆与 MCP

**知识库（v1.18.0：文档库 + 关键词/向量混合检索；ADR-003 增补）**

- 文档库 `kb_docs` / `kb_chunks`：制度、流程、软件手册等长文档；保留标题层级、页码、版本与生效期。
- 解析复用 pdfplumber / python-docx / python-pptx；按标题边界切块（目标 600 字、重叠 80 字），单篇正文上限 30 万字。
- 扫描件 OCR（可选，默认关）：RapidOCR + onnxruntime，模型随 wheel 分发、运行时不联网；只对单页文本少于 20 字的**无文本层** PDF 页识别，200/250 DPI 渲染，单文档最多 50 页；低置信度行丢弃，失败页跳过，`ocr_pages` 记录页数供界面提示核对。
- 关键词索引走 SQLite **FTS5**：先切 bigram 再写入 `unicode61` 索引（原生 trigram 对 2 字中文词无法命中，已实测）；`rowid = kb_chunks.id`，原文留在块表、snippet 从原文生成。
- 向量索引走 **sqlite-vec** `vec0`，embedding 走 OpenAI 兼容 `/embeddings`（复用当前激活供应商，可单独覆盖端点）；归一化后存 float[N]，失败自动降级关键词，不影响上传与搜索。
- 检索用 **RRF** 融合「文档关键词 + 文档向量 + 笔记 BM25」，默认只返回 `effective` 文档；结果带 `《标题》· 版本 · 章节 · 页码` 引用。
- 制度版本以 `doc_key = 制度编号（空则规范化标题）` 为链，`(doc_key, version)` 唯一；新版本生效时旧版自动 `superseded`。
- Agent 只读工具：`search_knowledge`（合并检索，带出处）与 `read_knowledge_doc`（按行读全文），自动补进所有专家白名单。
- 端点：`/api/knowledge/docs*`（CRUD/上传/重解析/下载/上下文）、`/api/knowledge/settings*`（OCR/向量设置）、`/api/knowledge/rag/*`（统计/重建/混合检索）。

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

### 4.8 经营/职能与经营聚合（`/api/business/*`，2026-10-31 as-built）

| 模块 | 表 | 关键规格 |
| --- | --- | --- |
| business_leasing | unit / lead / followup | 铺位-线索-签约勾稽，signed 必须有可用 unit_id 并原子占用；租决五字段；出租率/开业率/在谈面积；真实分页/导出 |
| business_operations | contract / bill / work_order / followup | 合同-账单-收款；账龄四桶；租期/状态出账守卫；工单 urgent/due_at/parent/设备关联/SLA；首次完成时间不覆盖；筛选一致导出 |
| business_planning | campaign / vendor | 活动预算服务端重算；ROI 基线/期间销售/商户分摊计算，calculated/manual 不混算；档期冲突 409+显式覆盖；跨月日历 |
| business_legal | contract_review / case / seal | 审查与案件合法迁移矩阵、终态保护；临期/逾期分列；可空日期显式 null 清空 |
| business_hr | employee / attendance / payroll | 考勤缺录 warning；任职月数与个税公共累计预扣；薪资确认留痕、级联 |
| business_facility | equipment / compliance | 设备一机一档 ABC 分级；年检/消防/维保到期，pending/done/overdue 派生；工单可空关联设备 |
| business_manage | report / metric / record / text | 四份脱敏报表（内控 6 表 / 云南周报 / 曲靖周报 / 月度工作情况）按标签文本锚点解析、不锁行号；导入粒度＝报表类型+项目+期间，同粒度整批覆盖；指标/明细手工补录与叙述块；图表有源才画、不补 0；导出 xlsx |
| business_gm | 无表 | 六域只读聚合；失败域 available=false，缺源 null+reason；12 个月到期面积集中度 strict >25%；`gm_summary` 只读工具 |
| business_dq | 无表 | 七域×五维只读巡检；score=100−Σ(error10/warning3/info1)，issue_count=Σ命中行数，top_issues≤20；财务域含数据池清洗画像/看板陈旧与产物校验/KPI 双源对照/税务勾稽/BIP 借贷不平；空库不误报；`dq_summary` 工具 |

跨模块边界仍是只导出 xlsx、不写财务表。经营聚合按域延迟导入，单域失败不拖垮宿主。

## 5. Agent 团队与五级路由

### 5.1 专家团队（表驱动，8 位内置出厂 + 5 位 QwenPaw 导入，合计 13 位）

专家 = `agent_specs` 行；`TEAM` 代码内置 8 位出厂专家（seed 数据源），
`seed_agent_specs()` 按 **增量 upsert**（缺谁补谁，ADR-005）灌入，尊重删除墓碑（`agent_spec_tombstones`）：删掉的内置专家不会在重启时复活，只有「恢复出厂」清墓碑后才重建；绝不覆盖用户改动。
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

### 5.2 五级路由（含续聊保持）

路由顺序：**用户指定 > 高置信关键词 > 续聊保持 > LLM 结构化调度 > 关键词回退/默认专家**（`_resolve_route`）。

1. **指定**：入参 `agent` 命中启用中的专家，`routed_by="指定"`。
2. **高置信关键词**：统计每位启用中专家 `keywords` 的命中数与具体度，唯一最高分且命中具体词/label 才直路，`routed_by="关键词"`；同分或仅泛词不抢路。
3. **续聊保持**：没有高置信关键词且消息含「继续/接着/上面/刚才」等续聊标记时，沿用当前会话最近一次 assistant 的专家，`routed_by="续聊"`；内部 `job-*` / `memory-extract` 会话不参与。
4. **LLM 结构化调度**：仍未命中且模型可用时，调度员 Agent 以名册文本 + 用户消息做结构化输出 `{agent, reason}`；输出不在名册内或异常视为失败，下一步，`routed_by="AI 调度"`。
5. **关键词回退/默认**：LLM 失败先保留最佳弱关键词候选（`routed_by="关键词回退"`）；完全无候选才取 `realestate` 或 sort 最小者（`routed_by="默认"`）。

缓存与失效：`load_team()` 进程内 60 秒 TTL；路由仲裁结果缓存 10 分钟（键=名册+问题前缀，失败不缓存）；任何专家/模型/供应商配置变更由路由层调用 `invalidate_team_cache()` + `orchestrator.invalidate()` 立即生效。
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

- **33 家厂商预设**数据化（`app/agents/provider_presets.json`，`GET /api/providers/presets` ·
  `POST /api/providers/presets/reload` 热加载；含按量、仅交互（`plan_interactive_only`）、
  交互+API（`plan_api_allowed`）三类套餐与本地/自定义）；文件缺失/解析失败回退内置最小默认集并记警告。
  每条预设含 `name/label/protocol/base_url/models/reasoning_models/vision_models/note/docs_url`；
  v3 起新增 `session_header`/`user_agent`（官方要求会话头的套餐，如 OpenCode Go 的
  `x-opencode-session` + 自定义 UA：由 `llm.provider_headers` 生成默认请求头，模型实例按
  session 维度缓存；密钥只走 SDK 鉴权，绝不进自定义头）。
- **模型能力探测**（直连厂商 `/chat/completions`；anthropic 协议按 `/v1/messages` 实测，不再一律跳过）：

| 探测 | 方法 |
| --- | --- |
| 思考能力 `probe/reasoning` | 档位取模型已同步/手动的 `thinking_levels`，参数风格取 `capability_meta.reasoning_style`（effort / `thinking{type}` / `enable_thinking+thinking_budget`；anthropic 走 `output_config.effort`），逐档判定 supported / accepted / unsupported——**accepted（接受参数但无思考输出）≠ supported**；获得明确结论（supported/accepted/unsupported）时回写 `supports_thinking`/`thinking_levels` 并记 `manual_fields` + 来源 `probe` 作为本地覆盖（默认同步不覆盖实测值，`override_manual`/reset-overrides 可清除恢复）；全部请求失败不写结论、不记覆盖 |
| 上下文 `probe/context` | 二分查找，≤6 次小请求，`max_tokens=1` |
| 最大输出 `probe/output` | 阶梯 1024 → 131072 |
| `probe/all` | 三连探测，单步失败不中断，汇总写 `probe_result` + `probed_at` |
| `GET probe` | 只读当前探测状态 |

  探测失败一律 **200 + error** 返回——**不可达 ≠ 不支持**，`supports_thinking` 保持 `-1` 不误判。
- **模型级自定义**：`temperature`（空 = 跟随系统默认，已接入 `build_model` 运行时、openai/anthropic 双协议注入）、
  `note`、`supports_thinking` / `thinking_levels` 手动覆盖、`supports_vision`。
- **工具/图片实测落库**（`POST /api/assistant/config/test` 带 `model_id`）：按该模型执行，
  明确结论才回写 `supports_tools` / `supports_vision`（来源 `probe`：成功→1、400/422 参数拒绝→0、
  401/403/404/408/429/5xx 与网络错误保持原值，不把「没测到」当成「不支持」）。probe 结论记入
  `manual_fields` 作为本地覆盖：默认目录同步不覆盖它，`override_manual=true` 或 reset-overrides
  可清除并恢复同步值。
- API Key 存 `model_providers.api_key`，**DPAPI 静态加密**（`dpapi:` 前缀）；回传前端脱敏为 `••••••xxxx`，
  掩码哨兵 `********`（8 个星号）或脱敏值原样回传均表示保留现值（v1.15.1 起双形态均拒绝覆盖真实密钥）。
  可选管控面凭证（火山 AK/SK）同口径密封存储，任何 GET 不回传。
- **在线模型同步**（Task 4，`POST /api/assistant/providers/{id}/sync-models`）：设置页**每供应商**卡片
  「同步模型」按钮触发（33 家预设中 22 家带官方列表适配器，其余按能力目录兜底并如实标注）。
  在线适配器优先（payload→`RemoteModel` 纯映射）；无官方列表
  （`models_url` 空）/无凭证/在线失败 → 能力目录兜底，响应 `online=false` + `partial=true` + `errors`
  如实标注，**绝不假成功**（200+空列表或结构不可识别一律 `RemoteFetchError`）。单事务幂等 upsert：
  并发 409、批内去重、一条非法记录整批回滚（`ok=false`，不裸 500）、第二次同步全 unchanged、
  远端消失不删本地；manual/probe/note/temperature/enabled/active 全保留。记录数组元素必须是
  JSON 对象（`_require_entries`/阿里 entries 入口统一守卫，字符串/null/数组/数字抛
  `SyncRecordError`）；fetch 阶段对适配器未预期缺陷另有结构化兜底（只记异常类型，不裸 500）。
  `override_manual` 默认 false：手动能力字段与实测（probe）结论不被覆盖；界面勾选
  「覆盖手动/实测」后传 `override_manual=true`，且每次同步前弹确认警告。有界分页
  （OpenRouter 同源 ≤5 页、Anthropic `has_more`、阿里 `page_no`、火山 `PageNumber`），
  到上限仍有后续页标 `truncated=true`。同步只发目录/管控请求、绝不推理（唯一例外：火山官方管控面
  三个只读列表 Action 的只读 POST，body 仅分页字段，发送前强制官方 host + Action 白名单 + 根路径）。
- **能力三态与来源**（Task 6）：模型能力统一三态——支持（1）/ 不支持（0）/ 未知（-1），
  未知不显示红叉、标「未确认」；每条能力带来源（manual / probe / official_catalog / sync），
  界面按 `modelCapabilities.js` 纯函数渲染徽章。`reasoning_effort` 全局白名单
  `''/off/none/minimal/low/medium/high/xhigh/max`，`off` 为旧版「关闭思考」兼容保留、语义同 `none`；
  前端 `normalizeEffort` 把旧 `off` 值在可关闭模型上迁移为 `none` 并保存。
- **动态思考档位**（Task 6）：聊天面板思考下拉由激活模型（及各专家生效模型）的
  `thinking_levels` + `reasoning_style` + `thinking_can_disable` 动态生成；`off/none`
  不受声明集门控（可关闭模型始终提供「关闭」项，保证迁移值有可选项目），其余 canonical 档位
  受声明集门控；已知不支持思考的模型禁用该下拉。

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

## 8. 数据模型（85 张表）

ORM 定义 **85 张表**：宿主表 17 张（`app/models.py`）+ 模块表 68 张（`mod_` 前缀，随模块加载注册进
`Base.metadata`；其中 business_gm 无表）。老库中另有 v1.2.0 遗留表 `model_profiles`（ORM 已无对应模型，新库不会创建），
实测运行库合计 86 张表。<!-- as-of: 2026-11-20 工作树（17+68） -->

### 8.1 宿主表（17）

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
agent_spec_tombstones  id, name(唯一), deleted_at（专家档案墓碑，防重名复用旧 tools 预置）
scheduled_job_run_facts  id, job_id→scheduled_jobs, event(started/finished/unknown/skipped), status,
                 summary, detail(JSON), started_at, finished_at, duration_sec, created_at（只追加事实表，每任务上限 500 条）
kb_docs          id, title, source_path, file_name, file_type, pages, chars, chunk_count, status(effective/
                 archived/superseded), version, effective_from, effective_to, note, created_at, updated_at
kb_chunks        id, doc_id→kb_docs, seq, heading_path, content, page_from, page_to, created_at（FTS5 虚表
                 kb_chunks_fts + 向量表 kb_vec（sqlite-vec，可选）随本表派生）
kb_settings      id=1 单行：chunk_size, overlap, embedding_dims, embedding_model, vector_enabled, updated_at
```

### 8.2 模块表（68，`mod_` 前缀）

```
mod_hello_entry         finance_hello（机制示例）
mod_expense_run         finance_expense
mod_expense_row         finance_expense
mod_invoice_batch       finance_invoice
mod_invoice_item        finance_invoice
mod_vault_dataset       finance_vault
mod_vault_clean_log     finance_vault
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
mod_pos_voucher_batch   finance_pos
mod_cashflow_run        finance_cashflow
mod_stamp_duty_filing   finance_stamp_duty
mod_vat_filing          finance_vat
mod_iit_filing          finance_iit
mod_dsf_filing          finance_dsf
mod_receipt_batch       finance_receipt
mod_receipt_item        finance_receipt
mod_recon_batch         finance_recon
mod_recon_source        finance_recon
mod_recon_record        finance_recon
mod_recon_match         finance_recon
mod_recon_issue         finance_recon
mod_recon_term          finance_recon
mod_lease_contract      finance_lease
mod_lease_payment       finance_lease
mod_lease_calculation   finance_lease
mod_lease_assessment    finance_lease
mod_lease_period        finance_lease
mod_lease_bip_map       finance_lease
mod_deferred_item       finance_deferred
mod_deferred_run        finance_deferred
mod_deferred_period     finance_deferred
mod_union_batch         finance_union
mod_union_line          finance_union
mod_leasing_unit        business_leasing
mod_leasing_lead        business_leasing
mod_leasing_followup    business_leasing
mod_ops_contract        business_operations
mod_ops_bill            business_operations
mod_ops_work_order      business_operations
mod_ops_work_order_followup business_operations
mod_plan_campaign       business_planning
mod_plan_vendor         business_planning
mod_legal_contract_review business_legal
mod_legal_case          business_legal
mod_legal_seal          business_legal
mod_hr_employee         business_hr
mod_hr_attendance       business_hr
mod_hr_payroll          business_hr
mod_fac_equipment       business_facility
mod_fac_compliance      business_facility
mod_mg_report           business_manage
mod_mg_metric           business_manage
mod_mg_record           business_manage
mod_mg_text             business_manage
```

要点：

- 状态/优先级用 Pydantic Literal 校验；时间统一本地 naive datetime。
- `model_id` / `active_model_id` 为**逻辑引用（无数据库外键）**，绑定的模型被删除/停用时自动回退全局激活模型。
- 老库中可能残留 v1.2.0 遗留表 `model_profiles`（ORM 已无对应模型，新库不会创建，不影响运行）。

## 9. 实现方法与算法说明

### 9.1 轻量列迁移机制（`app/bootstrap/schema.py`，`main.py` 保留同名包装）

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

表不存在则跳过，整体异常仅告警不阻断启动。启动路径只做幂等建表/补列/补索引与启动种子；一次性秘密迁移（Provider 明文 Key、MCP env、旧 `agent_settings.api_key`）不在启动执行，由 `backend/scripts/migrate.py secrets` 显式触发，启动仅做只读计数与告警。专家团队 seed（`seed_agent_specs`）属配置数据，
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
- **文档解析**：PDF/DOCX/PPTX 与经 LibreOffice 转换的 DOC/PPT 均在线程提取，原件保留、正文写入
  `.extracted/`；OOXML ≤5000 成员/解压 ≤100MB，正文 ≤8 万字符，PDF ≤300 页、PPT ≤500 页，旧版转换 60s。
  失败清理原件与 sidecar；无文字扫描件明确拒绝，不做 OCR。
- **注入**：对话时对每个附件路径做 `resolve() + relative_to(UPLOAD_DIR)` 校验，越界路径直接忽略；
  文档 sidecar 同样校验并直接注入提取正文，缺失时提示重新上传；普通白名单文本后缀
  （txt/md/csv/json/log/py/html/xml/yaml/yml/sql）且 ≤300KB 的附件按文本注入消息（截断至 8 万字符）；
  其余仅告知路径，提示 Agent 用 Bash 工具处理（Bash 默认关闭）。
- **图片**：以 base64 DataBlock 注入（见 4.2「聊天传图与多模态理解」），同样受上传目录与大小上限约束。

### 9.4 模型接入与配置存储（`agents/llm.py` + `config_store.py`）

- 配置优先级：激活模型（`model_providers` + `provider_models`，界面管理）> `.env` 的 `LLM_*` 兜底。
- openai 协议 → `OpenAIChatModel`（覆盖 ModelScope/硅基流动/DeepSeek/火山方舟/DashScope 兼容/Ollama）；
  anthropic 协议 → `AnthropicChatModel`（强制流式聚合）。
- 思考参数由 `reasoning_request(cfg, effort)` 按能力契约生成（Task 5，见 15.1）：
  档位不写死表——AgentScope 不认识的档位（openai 侧 `max`）经 `extra_body` 原样发送且不与顶层重复；
  **模型已声明 `thinking_levels` 时，声明集之外的档位（含 `max`）视为不支持，回退声明默认档/跟随模型，
  不原样发给供应商**；未声明档位集时保持兼容；未知/非法已存档档位回退「跟随模型」
  （不擅改 medium/xhigh），强制思考模型（`thinking_can_disable=False`）不可被关成 off；
  anthropic 原生 `reasoning_effort`（含 max，内部转 `output_config.effort`），toggle 风格走 `thinking_mode`。
  温度默认 0.3，模型级 `temperature` 非空时覆盖。
- 专家可独立绑定模型（`agent_specs.model_id`），编排器按 `model_cfg_by_id` 构建并缓存；
  不可用（删除/停用）时回退全局激活模型。官方要求会话头的供应商（`preset.session_header`）按
  (模型, 会话) 维度缓存（LRU 上限 64，淘汰即释放实例），保证 `x-opencode-session` 等会话头值
  在同一会话内稳定；其余供应商沿用共享缓存。

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
- **构建与版本切换**：`npm run build`，各视图独立 chunk；生产模式前端产物由后端在 8000 端口直接托管，天然同源。
  入口 HTML 返回 `Cache-Control: no-cache`，带哈希的 JS/CSS 不额外改变缓存策略；`vite:preloadError` 对同一次同 URL
  加载失败只自动刷新一次，只有无 `failure` 的成功导航才清除防循环标记，持续失败保留原始错误，避免无限刷新。

### 9.6 QwenPaw Agent 批量导入（`scripts/import_qwenpaw_agents.py`）

管道：**source files → sanitize → merge/create → DB**。

1. **source files**：读取 QwenPaw 工作区下每个 Agent 的角色定义文件——SOUL.md / PROFILE.md / AGENTS.md /
   00_系统总则.md / 06_协同规则.md（存在才读，按序拼接）。工作区默认 `示例主体19/QwenPaw/data/workspaces`，
   可用 `--from` 或环境变量 `QWENPAW_WORKSPACES` 指定；都没有则友好退出。
2. **sanitize**：脱敏——「示例主体25」等称呼替换为「用户」，本机绝对路径替换为 `[本地路径]`。
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
| 上传安全 | 扩展名白名单 + 分模块大小限制（聊天 10MB；费用/发票/数据池清洗/税务 20MB；BIP 10MB；个人 CSV 5MB；POS/现金流 50MB）+ 文件名包含校验 + `session_id` 白名单正则（防路径穿越）；读取统一共享分块限流 `read_upload_limited`（1MB 分块、超限即断 → 400）；宿主全局请求体上限 128MB（Content-Length 早拒 + chunked 累计 → 413）；导入类共享行数上限 `MAX_IMPORT_ROWS=200000`（失败关闭） |
| Origin 防护 | `OriginGuardMiddleware`：`/api` 状态变更方法（POST/PUT/PATCH/DELETE）与 `discover` 类 GET 校验 Origin——无 Origin 的 CLI/探针、回环开发来源、`CORS_ORIGINS` 配置项、运维配置 `HOST`（端口与 `PORT` 一致）的同源来源放行，其余 403；不再用「Origin 与请求 Host 自证同源」放行（避免 DNS rebinding 绕过）；普通 `/api` 读取不硬拦（跨站读取已由 CORS 兜底）；OPTIONS 预选不拦 |
| MCP env 保护 | `mcp_servers.env` 值落库前逐个 DPAPI 密封，连接/读取时还原；接口只回显键名与 `***`；存量明文由 `backend/scripts/migrate.py secrets` 调 `migrate_env_secrets()` 一次性迁移（启动只做只读统计与告警），DPAPI 不可用拒绝降级 |
| 超时口径 | LLM 客户端 120s；MCP 调用 30s / 握手 15s / 单条消息 4MB，入站单行 ≤1,048,576 字符（≈4MB UTF-8 最坏）；模型探测 httpx 30s；DuckDB 查询 5s `interrupt()`；看板单次尝试 660s（直接回退总预算 600s）、POS 单步 900s（超时落快照日志） |
| 目录浏览 | `/api/fs` 只读；根目录默认用户主目录 + 数据根 + 财务目录（`FINANCE_DATA_DIR`）+ POS 源/输出目录，`FS_BROWSE_ROOTS` 可覆盖；resolve 后判包含（`..`/符号链接同判），越界 403；不读文件内容、不返回文件 |
| ZIP 安全 | zip-slip 防护：`is_relative_to` 判定拒绝越界与绝对路径条目 + 解压后总量上限 200MB（finance_cashflow / finance_pos / finance_invoice） |
| 数据池清洗 SQL | SELECT-only + 表名白名单 + 强制 LIMIT + 独立线程 `interrupt()` 超时；去注释后执行 |
| 执行面 | Bash 工具默认关闭且自带危险文件/目录黑名单；QwenPaw 插件默认关闭且只读模式跳过 |
| 监听面 | 默认仅 127.0.0.1；`LoopbackBindingGuardMiddleware` 在非回环监听且未开 `ALLOW_NON_LOOPBACK=1` 时，除 `GET/HEAD /api/health` 外 `/api` 一律 **503** 中文拒绝（判定用 `scope["server"]` 实际监听地址，不依赖 `.env` 的 HOST）。开启 `ALLOW_NON_LOOPBACK=1` 不等于有鉴权，必须自行加 VPN/反向代理鉴权与防火墙 |
| 数据边界 | `gitignore` 分层：`data/`、`.env`、`backups/`、现金流引擎产出/真实数据；提交前无真实业务值 |
| PII 门禁 | `dev/check_pii.py` 扫路径/tracked/staged/HEAD archive 与 xlsx 单元格（只输出规则与位置，不回显命中值，命中退出 1）；`dev/hooks/pre-push` + `dev/install_hooks.bat` 在推送前阻断 |

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
| `FINANCE_EXPENSE_ENGINE_PATH` / `FINANCE_INVOICE_ENGINE_PATH` / `FINANCE_POS_ENGINE_PATH` / `FINANCE_CASHFLOW_ENGINE_PATH` / `FINANCE_BOARD_PIPELINE_PATH` | 空（按示例主体22相对位置推导） | 跨项目引擎/管线路径；**接受目录或单一文件**（文件取父目录，`utils.resolve_engine_root`）；缺失时对应接口 503 中文，不影响其他模块 |
| `FINANCE_POS_STATEMENT_DIR` / `FINANCE_POS_OUTPUT_DIR` | 空 | POS 刷卡凭证：对账单+回单归档基目录（下含 `YYYY年/MM` 月度目录）与输出目录（空落 `finance_data_dir/finance_pos/vouchers`）；生成时可按批次人工指定覆盖 |
| `FINANCE_POS_PROJECT_MAP` / `FINANCE_POS_RECEIPT_GLOB` | 空（内置默认） | POS 刷卡凭证：终端尾号→项目名映射（`0002=项目A,0005=项目B,…`）与回单 PDF 文件名通配（逗号分隔多个）；空用内置默认 |
| `FINANCE_RECEIPT_SOURCE_DIR` / `FINANCE_RECEIPT_OUTPUT_DIR` | 空 | 银行回单整理：回单源目录与输出目录（空则页面手选；输出空落 `finance_data_dir/finance_receipt`）；两者均纳入目录浏览白名单 |
| `FINANCE_POS_MATCH_DATE_TOLERANCE` / `FINANCE_POS_MATCH_AMOUNT_TOLERANCE` | 2 / `0.01` | POS 刷卡凭证：回单↔对账单匹配的日期容差天数与金额容差（元） |

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
  `package.json`、`package-lock.json` 的**内容哈希**与 `dist/.build-stamp.json` 构建印章（由
  `postbuild` 的 `write-build-stamp.js` 写入）——产物缺失、印章缺失或源码自构建后变化即判定
  需要构建（退出码 1，打印原因）；`启动工作台.bat` 每次启动先调用它，按需才执行 `npm run build`。
  刻意不用 mtime 比较：ZIP 解包不保留文件时间戳（Python zipfile 即如此），分发包解包后
  mtime 语义会把「产物较新」误判成「源码较新」，导致收件人端每次启动都重建前端。
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
- **MCP env 保护**：逐值 DPAPI 密封 / 还原，输出掩码；存量明文由 `backend/scripts/migrate.py secrets` 一次性迁移（启动只做只读统计与告警）。
- **路径与压缩包安全**：快照 HTML 限制在快照根内；ZIP 逐段校验 Windows 合法字符、设备保留名、穿越路径，并限制解压总量。
- **只读 SQL**：白名单 + 禁词 + 单语句 + 强制 LIMIT；LIMIT 匹配跳过字符串字面量。
- **CSV 公式注入防护**：用户可控文本以 `=`、`+`、`-`、`@` 开头时前置单引号。
- **前端竞态守卫**：列表/预览/详情统一请求序号，迟到响应丢弃，失败清理与 loading 复位只对最新请求生效。
- **概览截断**：今日日程有界读取，截断时返回总数与布尔截断标记，不静默丢数据。
- **BIP 表头定位**（`_locate_header_row`）：前 25 行内按「关键词类别数」评分（≥2 类候选），不用总命中数——用友元数据行「对方科目×2」与余额表两级表头都会骗过总命中评分（2026-10-23 外部验证修复）。
- **BIP 列签名识别**（`detect_file_type`）：分录摘要/制单人 → voucher（凭证列表独有，按账簿分 sheet 无账簿列）；对方科目 → aux；期初/本期/期末 → balance；unknown 进人工映射。多 sheet 以 sheet 名合成 `__sheet_ledger__` 账簿列；续行按账簿分组 ffill（凭证日期/凭证号不重复）。
- **引擎路径解析**（`utils.resolve_engine_root`）：目录原样 / 文件取父目录 / 不存在原样返回交由调用方 503；覆盖五模块。
- **引擎子进程环境**（`utils.engine_subprocess_env`）：TEMP/TMP/TMPDIR 显式钉到应用自建临时目录，修 Git Bash 继承破损 TEMP；上传文件名 mojibake 修复（`repair_upload_mojibake`，latin-1→gbk 回转，回转含中文才采纳）。
- **探测 429 退避**（`model_probe._post_with_retry`）：429 等 30s 重试至多 2 次，避免厂商 TPM 窗口把探测判成部分完成。
- **显式 null 能力字段恢复**（`routers/assistant.save_provider_model`，Task 7）：前端把「思考可关闭」选回「未知」时显式发送 `thinking_can_disable: null`；路由在 `exclude_none` 后仅对显式出现在 `payload.model_fields_set` 的该字段补回 None（其余 None 继续排除），使 `config_store` 的显式 null 恢复分支生效——不扩大为全局 `exclude_unset` 改动。
- **思考参数按能力生成**（`llm.reasoning_request`）：档位不写死表——openai 侧 AgentScope 不认识的 `max` 经 `extra_body` 原样发送且不与顶层 `reasoning_effort` 重复；模型已声明 `thinking_levels` 时声明集之外的档位（含 `max`）视为不支持并回退声明默认/跟随（未声明集时保持兼容）；未知/非法已存档档位与 Anthropic 无法表达的档位一律回退「跟随模型」（不擅改 medium/xhigh）；`thinking_can_disable=False` 的强制思考模型不可被关成 off；风格三形态：effort（`reasoning_effort`）、toggle（`thinking{type}`，anthropic 走 `thinking_mode`）、budget（`enable_thinking+thinking_budget` 数值档——映射为保守约定值 1024~65536 tokens，实际以供应商文档为准，不伪装 effort）；anthropic 原生 `output_config.effort` 含 max。
- **会话头供应商**（`llm.provider_headers`）：仅预设声明 `session_header` 的套餐（OpenCode Go）生成 `User-Agent` + 会话头；取值对同一 session 恒定（sha256 派生，缺省用进程稳定默认值），密钥只走 SDK 鉴权。
- **实测三态落库**（`config_store.record_probe_verdict`）：工具/图片只有明确结论才写 `supports_tools`/`supports_vision`（成功→1、400/422 参数拒绝→0，来源 `probe`；401/403/404/408/429/5xx 与网络错误保持原值——「没测到」不等于「不支持」）；probe 结论记入 `manual_fields` 作为本地覆盖，默认同步不覆盖，`override_manual`/reset-overrides 可恢复同步值。工具侧参数拒绝按「SDK 异常类型 > status_code > 明确的 Error code: 400|422」判定，消息偶含 400 不算。**思考探测同口径**（`model_probe._mark_probe_override`）：`probe_reasoning` 的明确结论（supported/accepted/unsupported）回写 `supports_thinking`/`thinking_levels` 时同样记 `manual_fields` + 来源 `probe`，默认同步不得把实测「不支持」翻回目录「支持」；全部请求失败不写结论也不记覆盖。
- **会话模型缓存 LRU**（`orchestrator._session_cache_put`）：要求会话头的供应商按 (模型, 会话) 缓存模型实例，上限 64（与 Agent 缓存同口径），淘汰最久未用会话即释放实例（含 SDK 连接池）；`invalidate`/`session_delete` 完整清理。
- **财务数据脱敏**（`dev/desensitize_finance_data.py`）：金额随机 + 勾稽重算（凭证借=贷、明细账余额链、余额表配平、报表合计行），泛化命名避开 BIP 表头关键词（防污染表头定位评分）；`dev/verify_desensitized.py` V1–V5 断言。

### 15.2 模块功能需求实现情况

| 模块 | 实现状态 | 验证状态 |
|---|---|---|
| 任务 / 日程 / 笔记 / 概览 / 搜索 | 已实现 | 后端 + 前端 + E2E + 可用性代行通过 |
| AI 助手 / Agent 团队 / 模型探测 | 已实现 | 真实 LLM 实测（硅基三模型；L8/L9 完全体经 Step5 适配补上）；GLM/Qwen 受硅基 TPM 429 截断记录在案 |
| 知识与记忆 / MCP / 技能包 / 专家团文件 | 已实现 | 真实 MCP（kimi-datasource）+ 本地 mock 实测通过 |
| 定时任务 / 调试中心 | 已实现 | E2E 通过 |
| 费用 / 发票 / POS / 现金流 / 报表看板 | 已实现 | 真实引擎 + 脱敏数据实测（对拍一致、E4 边界、E9 ±20% 复跑、W1–W9 全过） |
| 数据池清洗 / BIP / 个人记账 / 企业所得税 | 已实现 | BIP 原稿 13001 行实测入库；其余本地数据库与导入导出边界通过 |
| 租赁核算（finance_lease，工作树在途） | 已实现首期：22 路由 / 6 表（含 BIP 只读对账）/ 简化现值模型 / 三组恒等式与滚动校验 / 五页签导出 | 规则矩阵与合成黄金用例见 `tests/test_finance_lease_calculation.py`、`tests/test_finance_lease_io.py`, `tests/test_module_finance_lease.py` |
| 可用性（第 6 章） | — | U1–U10 自动化代行完成；真人阈值（≥80% 无协助）待招募复测 |

### 15.3 已验证运行情况

- 后端 pytest：`1106/1106`（2026-10-26 Task 7 收尾轮，含 thinking_can_disable 显式 null 恢复与 `_is_non_chat_id` 厂商段过滤用例；首轮 1104/1104，上一轮 909/909）。
- 前端 vitest：`47 文件、745/745`；生产构建通过（13.18s）。
- E2E：17 区探针 `656/656`（2026-10-26 深测轮）；Task 7 隔离 E2E（2026-10-26，临时 SQLite + 本地 mock OpenRouter 形状目录 + 内置浏览器）：新增供应商→同步模型（新增 2/跳过 2/能力三态徽章）→激活→动态思考档位→路由预览命中 security（关键词/高置信/零推理），证据 `.superpowers/sdd/doctor-mid-nite-black-bolt-maria-hill/task-7-e2e/`。
- 公开目录只读 smoke（2026-10-26，临时库+合成 Key）：OpenRouter 在线同步 466 条全成功；OpenCode 在线 401（合成 Key）→ 能力目录兜底 partial，符合「不假成功」契约。
- 迁移演练（2026-10-26，复制库）：应用迁移函数首次仅 workbench.db 变化、二次零变更（幂等）、行数稳定；真实 `backend/data` 115 文件哈希零改动。
- 性能：最慢 p95 `76.68ms`；`/api/health` p95 `21.81ms` 越 20ms 预算，未改阈值。
- 真实浏览器：临时数据库下完成主要菜单与新增入口验收。
- 外部验证（2026-10-23，隔离服务 + 脱敏数据，真实 `backend/data` 字节级零改动）：第 0 章 G1–G9、第 1 章 LLM、第 2 章 MCP、第 3 章 BIP 11/11、第 4 章五管线 + E4/E9、第 5 章 W1–W9、第 6 章 U1–U10 代理走查。
- 完整记录见 `20261022-修复与验收报告.md`。
## 16. 2026-11-04 as-built 追平（银行流水 / 数据质量 / 看板校验 / 滚动摘要 / 路由缓存）

> 本节只记录**当前实现**口径；历史测试数字保持快照语义，不在此重写。版本以 `backend/app/config.py` 为准（v1.18.0）。

### 16.1 银行流水 → BIP 模板（finance_bankflow）

- **零新表**：`prefix=/api/finance/bankflow`，只有 `POST /preview` 与 `POST /template`；上传 20MB。
- **解析**：遍历工作簿全部 sheet 的前 10 行，定位同时含「交易日期」与「账号」的表头行；按**列名**映射，与列顺序无关。核心必需列 `交易日期/账号/收入/支出/用途`；流水号候选链 `银行交易流水号→交易流水号→对账批号→资金系统单据号→单证号码`，全空时用「日期+账号+金额+余额+对方+用途」SHA-256 合成稳定 `SYN<20位>`（幂等）。
- **账号映射**：模块 `config.local.yaml`（gitignore）的 `bank_accounts`；全号精确优先，未命中按 ≥4 位尾号最长后缀匹配；未映射账号预览高亮、导出禁用。
- **格式**：官方骨架 `template_skeleton.xlsx`（清空 r10 起数据行，保留表头样式/列宽/数据验证/文本格式 @/5 sheet）；r10 起追加数据，每列复制 r9 示例行 `_style`；骨架缺失中文 400 拒绝，不降级为无格式模板。
- **硬性行校验**：收入与支出同时有值、均为空、用途为空、交易日期无法解析（坏日期）→ 预览标出，**导出返回 400（不生成模板）**；不是跳过坏行继续导出。
- **下载名**：`{银行简称}{尾4}_银行流水导入模板_{YYYYMMDD}-{YYYYMMDD}.xlsx`；ASCII 兜底 + `filename*` 双头。

### 16.2 数据质量巡检（business_dq）

- **只读、零新表**：`GET /api/business/dq/summary?as_of=YYYY-MM-DD`；`as_of` 用 `Query(default=None, pattern=...)`，空=今天；形状错 422，日历不存在日期（2026-02-30）也 422（否则 SQLite `date()` 返回 NULL 会让未来/过期检查静默通过，输出 100 分假健康）。
- **七域**：leasing / operations / hr / legal / planning / facility / finance；**五维**：空值/重复/孤儿引用/日期有效性/金额异常。
- **计分**：每域 `score=100−Σ(error 10 / warning 3 / info 1)`，下限 0；`overall_score` 为可用域均分；单域异常只 `available=false + gap_reasons`，不拖垮总览。
- **计数**：`issue_count` = Σ各检查 `count`（命中行数），与 `top_issues` 只取 20 条无关；`dq_summary` Agent 工具只返回前 10 条与每域 error/warning/info 计数。
- **跨表勾稽**：营运超收/状态金额不一致/履约中合同过期；人事薪资无考勤；企划预算超支；工程合规逾期；财务 BIP 借贷不平；税务申报无财务数据/财务数据无申报/有税无所得/税额超 25%。
- **财务域附加**：数据池清洗质量画像（行数/整行重复/高缺失列/空表，单表失败不拖垮）、看板陈旧快照（>7 天 warning）、连续失败（最近两条 failed→error）、看板产物一致性（`verify_snapshot` 失败项）、看板 KPI vs 业务库双源对照（带空库守卫）、税务勾稽。
- **空库防误报**：只有对应业务表有数据时才执行相关检查；真实空库只报表缺失/不可用，不产生假问题。

### 16.3 看板：计划任务通道 + 重试 + 产物校验

- **执行通道**：`run_pipeline` 优先 `schtasks /run /tn 微信报表监控`，轮询 `logs/last_run.json`（started/finished/ok/steps），超时 600s；无此计划任务的机器回退直接子进程（`_run_steps_directly`）。这是为了绕开终端安全软件按**进程树**拦截跨目录写盘（venv python 派生的子进程不能写示例主体22区域）。
- **重试**：`_PIPELINE_ATTEMPTS=2`（最多尝试 2 次，即失败后重试 1 次）、`_RETRY_DELAY_SECONDS=5`；逐次失败不落快照，最终失败统一落 failed 快照并 `_notify_failure` 到「调试→问题记录」P2（同标题未关闭去重）。
- **同秒防撞**：`_unique_stamp()` 在 DB/目录已存在时追加 `_2/_3`，避免 `mod_board_snapshot.stamp` 唯一约束冲突。
- **产物校验**：`GET /api/finance/board/verify?snapshot_id=` 提取 HTML 内嵌 `const R = {...}` 与管线 `data/report_data.json` 对照——文件规模、JSON 可解析、敏感 `map` 剔除、顶层键一致（meta 不参与）、递归数值容差 0.01（差异限 20 条）、关键 KPI 齐全；按（快照 id、HTML 大小、数据 mtime）缓存。**仅提供接口，页面无按钮**；失败项在 DQ 财务域显示 warning。

### 16.4 发票按项目归类与拆分打包

- 规则源：引擎 `invoice_processor/config.local.yaml` 的 `address_keywords` + `address_force_mapping`，运行时只读；长词优先；规则为空时**入库/创建阶段**返回 unchanged（不把发票清成未归类）。
- 判定顺序：引擎项目名 → 备注 → PDF 全文（懒加载）→ 同批同购方唯一项目继承 → 未归类。
- 入库即归类；`POST /batches/{id}/reclassify` 可重算（`asyncio.to_thread`，未归类才补取全文），**该接口在规则不可用时返回 400 且不改任何数据**；**人工修正（corrected）永不被覆盖**；已导出批次重新归类会回落 parsed 并清 `export_file`（旧包下载 409）。
- 导出：整批 ZIP + 按项目 ZIP（`outbox/batch_{id}_projects/` + `batch_{id}_projects.json`）；ZIP 名「项目+月份.zip」/「发票汇总+月份.zip」，跨月为区间；下载由前端 Blob + `a.download` 显式落名，响应头同时给 ASCII 兜底与 `filename*`。
- 项目 PDF（即时合并，**无需先导出**）：`GET /api/finance/invoice/batches/{batch_id}/projects/pdf?project=<项目名>`；`project` 必填 1–100 字（与项目 ZIP 同口径），同步 `def` 路由由 FastAPI 线程池执行，合并期间不阻塞事件循环。合并范围 = 本批次 + 本项目的 `parsed/incomplete` 明细（含红冲票与「未归类」，排除 `duplicate/failed`），顺序沿用导出明细的明细 ID 升序（`InvoiceItem.id`），每张原件全部页面按原页序直拼，不插封面/清单/分隔页。只读 `stored_path`：文件须存在、可读，且 `resolve()` 后位于本批 `inbox/batch_{id}`，不读其他批次或任意本机路径；产物是 `finance_invoice/tmp` 下唯一临时文件，正常响应结束由 `FileResponse` 后台任务删除，客户端中断等异常遗留由后续合并时的 1 小时陈旧文件清理兜底，失败清理半成品。
- 项目 PDF 失败即整体失败：缺件、零页、损坏、加密且无法读取、路径越界统一汇总为中文错误（**409**）并列出票号/源文件，**禁止跳过**生成少票 PDF，且不留半成品临时文件；批次不存在 404、项目无有效明细 404；`uploaded/failed` 批次给「可重试/需重新上传」提示（409）；`project` 缺失、空白或超 100 字 422；合并不修改批次状态、明细与导出产物，也不受「未导出下载 ZIP 409」限制。
- 项目 PDF 命名与响应：复用 `_project_safe_name()` + `_months_suffix()`，文件名 `{项目名}{月份}发票.pdf`（单月 `2026年10月`，跨月 `2026年10-11月` / `2026年12月-2027年1月` 区间）；`ProjectSummary` 增 `pdf_name`，列表展示与下载共用同一后端命名来源。响应 `application/pdf`，`Content-Disposition` 同时给 ASCII 兜底 `project_invoices.pdf` 与 UTF-8 `filename*`；`_attachment()` 泛化为可选媒体类型 + 响应后清理回调，既有两个 ZIP 下载行为不变。
- 前端项目级操作：项目拆分表新增「项目 PDF」列（「下载 PDF」/「打印」），未导出 ZIP 不影响这两个操作；打印在点击的同步阶段 `window.open('', '_blank')` 预开新标签页，被拦截时提示允许弹窗或改用下载且**不发请求**；成功后 Blob 转 object URL 导航预开窗口，短延时 `focus()/print()`，并提示可在 PDF 阅读器按 Ctrl+P；object URL 用较长延时回收（不复用下载的 10 秒清理，避免打印前被回收），请求失败关闭空白页、由拦截器统一展示后端中文错误。

### 16.5 长会话滚动摘要、路由缓存与 trace 治理

- **滚动摘要**：`agent_sessions.summary` + `summary_until`（游标）；未压缩历史超过注入窗口 2 倍时，把窗口外更早消息压缩进摘要（复用路由模型或全局模型），注入时 `【此前对话摘要】` 置顶 + 最近窗口；失败只告警沿用旧摘要，不阻塞聊天。`Agent.observe(history)` 必须 `await`（历史注入与摘要依赖它）。
- **路由轻量化**：`agent_settings.routing_model_id` 可选轻量模型做 LLM 仲裁（None=跟随全局激活模型）；`_route_cache` 键=名册文本+问题前 200 字，TTL 600s、上限 128，只缓存成功仲裁；`invalidate()` 同步清空。
- **trace 治理**：`_govern_trace` 单条 result 截 4k 字符、总量 100KB 超限丢弃最旧并留说明标记，保持 `{tool,args,result}` 列表结构（前端折叠渲染依赖）。
- **参数契约**：模块 manifest 的 `agent_tools.params` 必须是三元组 `(type, desc, required)`；`build_tools._param_schema` 兼容 2 元组/dict 并告警，单个工具参数格式错误不再炸掉整个专家。

### 16.6 数据与备份（Task 9 口径）

- **WAL + busy_timeout**：连接监听设置 `journal_mode=WAL`、`busy_timeout=5000`；备份走 `Connection.backup`，ZIP 跳过 `.wal/.shm`。
- **数据目录派生**：`WORKBENCH_DATA_DIR` 在 config 导入期预读进程环境或 `WORKBENCH_ENV_FILE` 指向的 `.env`，派生 `DATA_DIR/UPLOAD_DIR/默认 DATABASE_URL`；默认仍是 `backend/data`。
- **备份双产物 + 敏感配置**：`.db` 与 `workbench_data_*.zip`；本机配置四项（`.env`、银行映射、脱敏钥匙、PII 词表）只由 CLI 收集，ZIP 注释 + `.SENSITIVE.txt` 标记，发布扫描拒绝同名文件。
- **恢复原子化**：按实际写入字节限额；解压到同卷临时目录 → `PRAGMA integrity_check` → 旧目录整目录快照 → 一次换入；第二次 rename 失败把快照改回；快照保留 3 份；本机配置先写 `backups/restore-local/local_*`，任一步失败整体回滚；拒绝 zip-slip/设备名/ADS/0 字节/无业务表库。
- **安全边界**：非回环 503（`ALLOW_NON_LOOPBACK=1` 才放行）、Origin 403、`CORS_ORIGINS`、无鉴权；DPAPI 缺 pywin32 拒绝启动。

### 16.7 AI 助手可靠性强化（Agent Autoloop 借鉴，2026-11-04）

- **运行事实 + 投影 + 回放**：`scheduled_job_run_facts` 只追加记录 `run.started/finished/unknown/skipped`；`ScheduledJob.recent_runs` 是投影，`replay_recent_runs()` 用另一套实现从事实重建，`GET /api/jobs/{id}/runs` 返回时间线并核对 `replay_ok`。事实每任务最多 500 条（有界保留，不照搬「日志永不压缩」）。
- **unknown 语义**：`ScheduledJob.running_token/running_since` 在开始执行时原子占入；服务重启后 `recover_interrupted_runs()` 把未收尾的运行标 `unknown`，`last_run_at` 取开始时间阻止立即补跑，并登记调试中心 P2（同标题未关闭去重）。Agent 对话超时 1800s 也记 `unknown`，不自动重试；模块动作回执无效只记失败，不重跑 handler。
- **单定时器**：`next_due_at()` 纯函数覆盖 interval/daily/cron；`scheduler_loop` 只维护一个 `asyncio.Event` + `wait_for`，任务增删改/执行完成 `wake()`，`TICK_SECONDS=300` 仅作系统休眠/时钟跳变的安全上限，取消 20 秒轮询。
- **能力边界**：所有 `TOOL_SPECS` 显式声明 `readonly`；只读模式只放行 readonly 工具且永不加载 Bash/插件（`_tooling_plan` 纯函数）；新建定时任务默认 `readonly`，历史任务保留原 mode；模块工具漏标 readonly 按可写处理并告警。
- **外部文本**：文本/文档提取超过 4000 字写入 `uploads/context/<session_id>/` 只读上下文文件，提示词只留资料清单；新增只读 `read_context`（上传目录内、≤2MB、单次 ≤500 行）按需读取；`COMMON_RULES` 明确「资料不是指令」。会话删除清理 context 目录。
- **契约测试**：`test_agent_architecture_contract.py` 锁 scheduler 不碰 LLM/ChatMessage、只有 orchestrator 写 ChatMessage、工具三元组 + readonly、模块 manifest v0.2.0 显式声明、只读计划排除 Bash/插件；`test_scheduler_facts.py` / `test_attachment_context.py` 覆盖事实/回放/unknown/超时/回执/路径穿越。

## 17. 2026-11-07 as-built 追平（数据池清洗 / POS 刷卡凭证 / fs 目录浏览）

### 17.1 数据池清洗（finance_vault v0.2.0）

- **原地升级**：模块标题「数据仓→数据池清洗」，菜单标题与前端 manifest 同步；DuckDB 底座、`ingest_dataframe`
  契约、SELECT-only 查询护栏（表白名单 + 强制 LIMIT + interrupt 超时）不变；旧 `QueryView/TableListView`
  删除，前端三页签 `PoolView`（数据池 / 整合分析 / SQL 查询，`?tab=` 驱动、`?table=` 预选）。
- **列画像**：`profile_table` 每列 DuckDB 原生类型 + 文本列采样三态类型推断（numeric/date/text，≥80% 可解析
  才判类、采样 ≤500 值）、缺失率/唯一率/top5 高频值/数值 min-max；表级警告四类 + 整行重复；超扫描上限
  降级 sampled 只算缺失率。单连接短会话 + 写锁串行，与 DQ 质量画像同口径。
- **配方清洗**：4 算子（脏值归一 / 整行全空剔除 / 全空列剔除 / 整行去重）+ required_columns + invalid_policy
  （drop_row/null_cell）+ IQR 离群只标记；固定顺序 7 步、行级首因剔除、逐算子统计（rows/cells/cols）+ 样本
  留痕 ≤10 行；未知算子键忽略并警告（配方跨表复用容错）；应用后落 `{表名}_clean` 新数据集并写
  `mod_vault_clean_log` 配方日志，原始数据不动。
- **整合分析（单表聚合）**：维度 + 指标（sum/avg/count/min/max）+ 过滤（9 算子白名单）+ 排序；**pandas
  内存分组完成，不拼 SQL**（Mimosa PreToolUse 拦截 SQL 字符串拼接，也规避注入面）；文本型数值列先数值化
  （与清洗同口径）；导出 xlsx 走 `safe_cell`；另存经 `ingest_dataframe` 登记后可再查询、可再清洗。
- **清洗日志**：配方 + 逐算子报告 + 样本 + 双侧缺失率对照，按 id 倒序，支持配方复用与重放。

### 17.2 POS 刷卡凭证（finance_pos，v1.19.0）

- **端点**：`GET /api/finance/pos/vouchers/config`（源/输出目录回退值）、`POST /api/finance/pos/vouchers/generate`
  （月份 + 可选源/输出目录；400=输入问题、503=运行期失败）、`GET /api/finance/pos/vouchers`（批次列表）、
  `GET .../files/{name}`（单文件下载）、`POST .../merge`（选中凭证合并 PDF 下载）、`DELETE .../{batch_id}`
  （删批次，目录一并清理）、`DELETE /api/finance/pos/runs/{run_id}`（删运行，running 拒绝）。
- **管线概要**（算法细节见《算法实现逻辑》§11）：月度目录发现（配置基目录或人工指定，归档布局
  `{基目录}/{YYYY}年/{MM}` 或直接当月目录二选一）→ 解析 SHOP 对账单「终端小计」行与回单 PDF 文本层
  「单位客户专用回单」分块 → 双键匹配 → 对账单打印补丁（隐藏其他终端数据行，行号与 sheet XML `r=` 对齐）
  → LibreOffice 分块转 A4 横排 PDF → 回单按 Y 边界裁切 → 每笔交易「对账单页+回单页」合并凭证 +
  月度手续费凭证（汇总首页 + 全部对账单页）→ 中间产物清理 + `manifest.json` 留痕（含匹配/未匹配/金额不符清单）。
- **匹配口径**：终端一致 + 报表日期 ≤ 清算日 ≤ 报表日期 + 容差天（默认 2）；金额命中优先、日期最近兜底；
  同一小计可承接多笔回单（分批清算）；金额未命中不剔除、进 manifest 的 amount_mismatch 清单。交易凭证直接按当前匹配小计的
  `report_date + terminal` 取对账单 PDF，不再按回单号暂存关联，跨来源文件同号回单不会互相覆盖。
- **配置**：六项 `finance_pos_*`（源目录 / 输出目录 / 终端→项目映射 / 回单通配 / 日期容差 / 金额容差），
  见 §12；前端凭证页签配「浏览」弹窗（`DirectoryPicker.vue`）逐层选择本机目录。
- **前端打印**：单个凭证可「打印」，勾选多份可「合并打印选中」；复用现有单文件/合并接口与共享 PDF 打印工具，
  点击同步预开标签页，Blob 导航后尝试自动打印；弹窗拦截不发请求，请求/导航失败关闭空白页，可 Ctrl+P 或改用下载。
- **目录浏览**：`GET /api/fs/drives`（允许浏览的根目录，非 Windows 不返回盘符列表）+ `GET /api/fs/browse`（列子目录，
  不读文件内容；resolve 后按白名单判包含、跳过 `$`/`.` 条目、单层上限 500 项）；只读，普通 GET 不在 OriginGuard 拦截范围，跨站读取依赖 CORS 与回环监听。

### 17.3 依赖与环境（v1.19.0）

- `requirements.txt` 显式声明 `pypdf>=5.0,<7` 与 `pypdfium2>=4.30,<6`（vouchers 裁切/合并与 OCR/回单解析
  直用，原先仅靠 pdfplumber 传递安装，全新机器会 `ModuleNotFoundError`）。
- 干净 venv 冒烟：全新虚拟环境 `pip install -r requirements.txt` → 四域环境变量下导入应用 OK（v1.19.0）
  → 聚焦测试 111 passed / 1 个既有失败（`test_execute_project_inference_failure_marks_error_for_retry`，
  与本轮无关）。跨平台可用性评估见根目录 `2026-11-07_跨平台可用性评估.md`。

## 18. 2026-11-11/12 全量审计整改 as-built

- **数据池清洗**：新增 `GET /tables/{table}/usage`（登记/视图/物理修订表/清洗日志引用/一致性问题，含 `requires_force`）、`DELETE /tables/{table}?confirm=<数据集名>&force=<bool>`（缺失 `confirm` 422、名称不符 400、被引用未 `force` 409）、`POST /tables/{table}/rename`（视图/物理修订表/登记/清洗日志引用同锁同步）；`clean/apply` 与 `aggregate/save` 默认拒绝目标=源表或已存在（409），`allow_overwrite=true` 才覆盖；详见 §17.1。
- **税务**：企业存在已提交申报时删除 409；财报导入能识别税号/名称才真实落库（否则 `persisted=false` 仅预览），`/import/{enterprise_id}` 可指定企业；四税种（印花税/增值税/个税/残保金）同纳税人同期间重复 409，`PUT /filings/{id}` 受控更正（已导出/已提交 409、不换纳税人/期间），已提交删除 409；CIT 与四税种计税入口统一 `require_tax_scope`；个税专项附加标准见 §4.4。
- **银行流水**：预览只返回掩码与 `account_token`；导出只接受令牌（或唯一掩码，歧义 400），完整账号不出后端。
- **POS / 现金流**：`execute` 并发 409、0 字节产物 503 且不归档；现金流运行记录 `actual_year`，失败/重跑分支清空产物。
- **知识库**：自定义 embedding 地址必须单独填写专用 Key（绝不复用聊天模型 Key）；`rebuild_vectors` 默认全量、显式 `limit` 返回 `remaining`；`GET /settings` 返回 `resolved_detail` 中文原因。
- **MCP**：请求已发出后的超时/断流不重放，返回 `result_unknown`；入站单行 ≤1,048,576 字符（≈4MB UTF-8 最坏）；自动重连有界退避，断开/禁用不被自动复活。
- **定时任务**：`mode` 入参白名单（历史非法值读出后执行侧回退 `readonly`）；interval 手动运行不顺延下次到点；`/api/fs` 默认根含 `FINANCE_DATA_DIR` 与 POS 源/输出目录，`FS_BROWSE_ROOTS` 可覆盖。
- **看板**：计划任务通道单次尝试 660s 估算（2×15s 查询 + 30s 触发 + `max(600s, 600s)`），直接回退每次尝试 600s 总预算，前端 1500s；`logs/run.lock` 是「管线在途」的唯一信号，启动 60s 宽限内未出现即快速失败。
- **发布与门禁**：`dev/package_release.py` 版本取 `backend/app/config.py` 单一源并校验前端 `package.json`/`package-lock.json` 两处；预检调用 `dev/check_pii.py --archive HEAD`（含本地词表，只输出规则/位置）；`backend/requirements-lock.txt` 由全新 Windows/Py3.13 venv 重新生成，含 `pytest-xdist`/OCR/向量依赖；PII 门禁 `dev/check_pii.py` + `dev/hooks/pre-push` + `dev/install_hooks.bat`。
- **CI**：`.github/workflows/ci.yml` 产出 pytest/vitest JUnit，失败也上传 artifact（`if: always()`），安装/测试/构建分步超时；远端历史失败日志因未登录 GitHub CLI 且分支未推送**未能取得**，不能据此宣称 Actions 已通过。

## 19. 银行回单整理 as-built（finance_receipt）

- **模块**：`backend/app/modules/finance_receipt/`（`parsers.py` 文件发现与页内切分、`pdf_builder.py` 固定版式排版与 XLSX 清单、`service.py` 批次/复核/生成/下载、`router.py` 接口），表 `mod_receipt_batch`、`mod_receipt_item`，前缀 `/api/finance/receipt`，菜单「财务 / 账务处理 / 银行回单整理」order 96，前端 `frontend/src/modules/finance-receipt/`。
- **账期**：由用户显式给出起止日期（含端点）；选择月份仅给出可编辑建议（上月 25 日 ~ 当月末），不自动推断 23/24/25 日波动或季度、年末结账日。文件按日期区间交集发现，账期外的记录不纳入。
- **识别**：建行表头「中国建设银行单位客户专用回单」，中行「国内支付业务收款/付款回单、客户付费回单、利息收入回单」，平安「收付款业务回单、收费回单(付款通知)、协定存款利息回单」。页内按表头 Y 坐标切分，相邻块上边界不越过上一张下边界。五类优先级：待清算（付款方或用途含收单清算款）→ 结息 → 手续费 → 按收付款方向归正常收入/转账支出；无法判断记 `unclassified`。ZIP 内成员与散放文件按账户+回单号去重。
- **复核与生成**：`unclassified` 且纳入的记录称「待确认」，存在即 `POST …/generate` 返回 409；生成只含纳入项，按账户×类别分册，产物先写临时目录再原子替换，失败清理临时目录并把批次标为 `failed`，源文件缺失时中文 400 列出文件名。
- **版式**：A4 纵向；建行、平安每页 3 槽，中行每页 2 槽，按固定槽位顺序分页（3、3、…、余数），末页空槽留白；超大横版单独成页；页眉标明银行、完整账号、类别、本页笔数、类别总笔数、账期与页码，字体为 PyMuPDF 内置 `china-s`。
- **产物与下载**：分册 `账户目录/{银行}{尾号}_{类别}_{账期}.pdf`，清单 `回单分类明细_{账期}.xlsx`（分类明细 + 分类汇总，文本经 `safe_cell` 防公式注入）；`GET …/files/{file_id}` 支持 `disposition=inline|attachment`（中文名 `filename*=utf-8''`），`POST …/merge` 仅合并 PDF 分册，`GET …/download` 打包 ZIP 保留账户目录；`GET …/items/{id}/preview` 返回单笔矢量裁切 PDF，源文件经批次登记路径读取，不接受任意路径。
- **配置**：`finance_receipt_source_dir`、`finance_receipt_output_dir`（均纳入 `get_fs_browse_roots()` 默认白名单）。
- **依赖**：新增 `PyMuPDF>=1.28,<2`（`backend/requirements.txt`）。
- **已知限制**：仅支持带文字层的三家银行版式；OFD、扫描件、加密文件不支持；全部真实数据只读，测试使用合成 PDF 与临时目录。
## 20. 租赁核算 as-built（finance_lease，2026-11-20 工作树）

- **定位**：**承租人**使用权资产 / 租赁负债的初始计量 + 正常后续计量；不替代 BIP 账簿，不做租赁总账，不回写凭证。
- **模块**：`backend/app/modules/finance_lease/`（`calculation.py`、`checks.py`、`importer.py`、`exporter.py`、`entries.py`、`reconciliation.py`、`service.py`、`router.py`），前缀 `/api/finance/lease`（22 路由），菜单「财务 / 账务处理 / 租赁核算」order 98，前端 `frontend/src/modules/finance-lease/`。
- **表（6）**：`mod_lease_contract`（合同，合同号唯一）、`mod_lease_payment`（付款期，`(contract_id, sequence)` 唯一）、`mod_lease_calculation`（测算批次，`(contract_id, input_hash, rule_version)` 唯一）、`mod_lease_assessment`（付款期级现值/折现因子/U 列利息/负债滚动）、`mod_lease_period`（月份级折旧/应付/利息）、`mod_lease_bip_map`（BIP 对账科目映射，科目位 + 科目编码 + 方向）。
- **状态**：合同 `draft/archived`（archived 只读）；测算 `calculated/confirmed`，仅 `calculated → confirmed`；已确认快照不可改写，有测算历史的合同删除 409，重复提交同输入同规则版本幂等返回既有批次。
- **计算规则**（`RULE_VERSION = "lease-mvp-1"` 参与幂等键）：`months = DATEDIF(起-1天, 止+1天, "M")` 逐期自算；`period_rate = ROUND_HALF_UP(年利率 × months / 12, 4)`；`d_1 = 1`、`d_i = ROUND_HALF_UP(d_{i-1} × (1 + Q_{i-1}), 9)`；首行不折现，其余 `ROUND(amount_i / d_i, 2)`；`U_i = ROUND(B_i × Q_i, 2)`（付款后余额计息）并**在实际计租期内按月均摊**；月折旧 = 使用权资产原值 ÷ 月期总数；金额 `Decimal(ROUND_HALF_UP)` 两位、禁止 `round()`，不吸收尾差。
- **简化口径**：**使用权资产 = 租赁负债现值**；不含预付租金、初始直接费用、复原成本、租赁激励；不支持可变租赁付款、担保余值、购买选择权、短期/低价值豁免。
- **校验**（`checks.py`）：三组恒等式（Σ月折旧 = 使用权资产原值、Σ应付 = 租赁付款额、Σ月利息 = 未确认融资费用）+ 负债期末归零、剩余计划付款、月折旧/月利息规则、合同区间、源表月数与 DATEDIF、分录汇总借贷平衡；状态只有 `ok/failed/unknown`，缺数据一律 `unknown`（不按 0 通过）；容差合计 1 元 / 单项 0.01 元，只认模块常量。
- **导入**（`importer.py`）：表头标签锚点（前 50 行），免租期与 0 月期按行保留，期数必须从 1 连续；**预览只解析不落库，apply 无行级错误才整表替换**；10MB / 20,000 行；合计行即停止解析。
- **导出**（`exporter.py`）：5 页签（数据源 / 评估表 / 期间表 / 校验 / 分录），`safe_cell` 防公式注入；当前结果 `stale` 或含 `failed/unknown` 时 409，历史快照须 `allow_stale=true`。分录页为**逐笔底稿分录**：初始确认 1 张凭证（`LEASE-{id}-INIT`）+ 每月折旧/利息分别 1 张（`LEASE-{id}-DEP-{YYYY-MM}` / `LEASE-{id}-INT-{YYYY-MM}`，金额为 0 不出），导出前 `entries.py::validate_entries` 按凭证号逐张校验借贷相等（不平衡拒绝导出）；范围仅初始确认与月度折旧/利息，不生成付款实收/变更/终止分录、不回写 BIP。校验页的 `entry_recognition`/`entry_monthly` 是**汇总级检查**（合计容差 1 元），与逐凭证平衡不可混为一谈。
- **期间表展示口径**：导出期间表列名为「本行月数（每月=1）」（按月展开，每期月数看评估表「月期数」，**不是**源底稿 R 列月期总数）；导出「使用权资产净值」= 快照现值 − 累计已存折旧（含本月）的派生展示值，逐月滚动、不重算折旧规则、不吸收尾差，差异仍按既有容差判定；页面「租赁付款（整期）」为所属付款期金额，期内每月重复展示、**不可逐月求和**，按月金额只有折旧/利息/应付。
- **BIP 只读对账（工作树新增，`reconciliation.py` + `reconciliation_schemas.py`）**：粒度 = 承租人 + BIP 账簿 + 期间 + 科目位（五科目位：使用权资产原值 / 使用权资产累计折旧 / 租赁付款额（剩余付款）/ 未确认融资费用余额 / 租赁负债净额）；映射存 `mod_lease_bip_map`（科目位 + BIP 科目编码 + 方向 debit/credit），`PUT /reconciliation/mappings` 只替换给定承租人+账簿范围，**绝不写 BIP**；余额读取经 `finance_ledger_bip.bip_balance` 公共只读适配（manifest `requires` 已声明），不 import 其 models、不写凭证/余额/批次；模块侧只取**当前 input_hash 一致 + confirmed + 期间行覆盖期间**的最新批次，归档/未确认/stale/无测算/未覆盖一律 `unknown`；**任一名下合同不可用则整批科目 `unknown`**，不做部分求和与整额 BIP 比较；BIP 实际数按映射方向取期末借方−贷方（方向金额全空才按方向取期末余额绝对值并标注来源），冲突/多行/缺行/缺映射均 `unknown`；容差固定 **0.01**；结果实时计算不落库（聚合按 failed > unknown > ok）。
- **不做**：租金变更、主体变更、终止与调整分录、合并抵销、BIP 凭证/余额**回写**（只读对账是唯一例外）、印花税重复实现、自动推断折现率或同一控制；长期待摊与工会经费由独立模块 `finance_deferred` / `finance_union` 承担。

## 21. 长期待摊 as-built（finance_deferred，2026-11-20 工作树）

- **定位**：仅**租入资产改良支出**的直线摊销底稿；不做非租赁类长期待摊，不做自动转销、不做凭证/BIP 回写。
- **表（3）**：`mod_deferred_item`（项目参数，项目编码唯一）、`mod_deferred_run`（测算批次，`(item_id, input_hash, rule_version)` 唯一）、`mod_deferred_period`（月份级摊销行）。前缀 `/api/finance/deferred`（9 路径 / 13 操作），菜单「财务 / 账务处理 / 长期待摊」order 99。
- **计算**（`calculation.py`，`RULE_VERSION = "deferred-mvp-1"`）：净额 = ROUND_HALF_UP(含税 ÷ (1+税率), 2)、税额 = 含税 − 净额、月摊 = ROUND_HALF_UP(净额 ÷ 月数, 2)、每月等额；**末期尾差 = 净额 − 月摊 × 月数 如实留在末期余额**，不吸收/不倒挤/不自动转销。
- **风险与确认**：摊销到期月 = 起摊月 + 月数 − 1 的月末，晚于租赁到期日只产出 `warning`（不缩短年限）；末期尾差同样 `warning`；`failed`/`unknown` 阻断确认与导出；存在 `warning` 时确认必须 `risk_acknowledged=true` 且 `risk_note` 非空。
- **状态**：项目 `draft/archived`（归档只读，有测算历史删除 409）；测算 `calculated/confirmed` 单向；输入或规则版本变化后旧批次 `stale`，确认/当前导出 409，历史快照导出须 `allow_stale=true`。
- **导出**：三页签（项目参数 / 月度摊销 / 校验），`safe_cell` 防公式注入；`warning` 已披露可导出。

## 22. 工会经费计提 as-built（finance_union，2026-11-20 工作树）

- **定位**：手工工资基数 + 批次费率的工会经费计提底稿；**不读 HR**、不做 60/40 分成、缴纳凭据、税前扣除判断、支付与 BIP 回写。
- **表（2）**：`mod_union_batch`（主体 + 期间唯一索引）、`mod_union_line`（`(batch_id, department)` 唯一）；前缀 `/api/finance/union`（8 路径 / 12 操作），菜单「财务 / 账务处理 / 工会经费计提」order 100。
- **计算**（`calculation.py`，`RULE_VERSION = "union-v1"`）：本期计提 = ROUND_HALF_UP(工资基数 × 批次费率, 2)；上期差异 = 上期实际 − 上期已计提（负数 = 应冲回）；本期建议入账 = 本期计提 + 上期差异。费率**唯一来源是批次**，行级无覆盖字段；金额两位 HALF_UP、禁止 `round()`。
- **缺失即 unknown**：本期工资基数 / 上期已计提 / 上期实际任一 `None` → 该行 `line_status=unknown` 且三个派生值为 `None`；合计只汇总 `ok` 行，缺数据行数单列；显式 0 与缺失严格区分。
- **状态**：批次 `draft/confirmed`；PATCH/DELETE 仅草稿；确认要求无缺数据行、无差异、至少一行；确认后整体只读（行、费率、备注、删除全拒）。无快照表，预览/导出均按当前数据实时计算。
- **导出**：三页签（批次信息 / 部门明细 / 校验），真实值完整（含未通过/未知结论），不重算。
