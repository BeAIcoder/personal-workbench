# 个人工作台 · 需求与设计规格（spec）

> 本文档描述系统当前（v1.3.0）的需求与设计。历次演进：v1.0.0 基础工作台 → v1.1.0 Agent 团队 → v1.1.1 真实模型接入 → v1.2.0 供应商/模型两级管理 → **v1.3.0 Agent 团队配置化 + SSE 流式对话**。

## 1. 背景与目标

面向个人日常办公的本地 Web 工作台：任务、日程、笔记三大事项载体 + 概览与全局搜索；
外加一个**可配置的 AI 助手 Agent 团队**（内置商业地产领域 7 位专家，可在界面上增删改、改关键词、独立绑定模型），
让自然语言可以直接操作工作台数据。

定位与硬约束：

- **本机单人使用**：服务仅监听 `127.0.0.1`，无登录鉴权；数据不出本机。
- **SQLite 单文件**：`backend/data/workbench.db`，零外部数据库依赖；重启不丢数据。
- Windows 11 · Python 3.12+ · Node 18+；不允许 Docker 与外部独立数据库。
- 交付达到可继续开发的软件工程标准（测试、文档、双击 bat 启动）。

## 2. 功能规格

### 2.1 任务管理（`routers/tasks.py` + `TasksView.vue`）

- 字段：标题（必填）、描述、状态（todo/in_progress/done）、优先级（low/medium/high/urgent）、截止时间（可空）、分类、完成时间。
- 行为规则：`completed_at` 由后端维护——状态改为 `done` 时写入、改回时清空，供 7 天趋势统计使用；删除有前端二次确认。
- 列表支持状态/优先级/分类/逾期过滤、标题与描述关键词搜索、截止时间区间（含边界）、五种排序、分页。

### 2.2 日程管理（`routers/schedules.py` + `ScheduleView.vue`）

- 月历视图 + 按天列表；有日程的日期显示彩色圆点（最多 3 个，多出显示 +N）。
- 校验规则：结束时间必须晚于开始时间（违反返回 422）；全天日程落库为 00:00:00–23:59:59。
- 范围查询按「日程与区间有交集」匹配，适合整月拉取。

### 2.3 笔记管理（`routers/notes.py` + `NotesView.vue`）

- 字段：标题、正文、标签数组（自动去空格去重，最多 10 个、单个最长 20 字）、置顶。
- 标签以 JSON 存储且 `ensure_ascii=False`（中文原文），用 LIKE 过滤；列表置顶在前、更新时间倒序。

### 2.4 概览与全局搜索

- `/api/dashboard/summary` 一次取全：统计卡片（待办/进行中/逾期/今日日程）、任务状态饼图、近 7 天完成趋势、即将到期任务/今日日程/最近笔记三栏。
- `/api/search?q=` 一次检索任务/日程/笔记三类，各取前 5 条并返回关键词上下文片段；前端 `Ctrl+K` 唤起，点击结果跳转并带入筛选。

### 2.5 AI 助手（`routers/assistant.py` + `app/agents/`）

- **专家团队配置化**：专家定义存 `agent_specs` 表，界面可增删改、启停、编辑关键词与提示词、绑定模型、调整路由优先级（sort）；首次启动自动 seed 7 位内置专家（幂等，仅空表执行）。
- **对话**：支持非流式 `POST /chat` 与 SSE 流式 `POST /chat/stream`；三种工作模式——standard 标准执行 / readonly 只读咨询（仅查询工具）/ deep 深度研究（更高工具轮次）。
- **会话**：多会话管理（新建/重命名/删除），消息持久化，刷新与重启后可回看。
- **供应商/模型两级管理**：一家供应商一张卡片（协议 openai/anthropic、base_url、api_key、启停），下挂多个模型（上下文/输出上限/多模态标记）；跨供应商激活一个模型作为全局默认；连通性/工具调用/多模态三类测试。
- **附件**：聊天可上传附件（10MB 上限；xlsx 自动转 CSV），文本类附件注入消息、其余提示 Agent 用 Bash 处理。
- **扩展能力（默认关闭）**：本地技能池（SKILL.md）、Bash 工具、QwenPaw 插件兼容层，均有独立开关。

## 3. 实现方法与算法说明

### 3.1 关键词路由算法（`agents/team.py`）

路由顺序：**用户指定 > 关键词规则 > LLM 结构化调度 > 默认专家**。

1. **关键词打分**：对每位启用中的专家，统计其 `keywords` 在消息（小写化）中的出现次数（子串包含），得 `{name, display_name, hits, score}` 列表；按命中数降序排列，**同分时 sort 小者胜**（sort 即路由优先级，先专后宽：security 0 → leasing 1 → … → realestate 6 兜底管家）。命中即由得分最高专家应答，`routed_by="关键词"`。
2. **LLM 回退**：关键词未命中且模型可用时，调度员 Agent 以名册文本 + 用户消息做结构化输出 `{agent, reason}`；输出不在名册内或任何异常都视为失败，`routed_by="AI 调度"`。
3. **默认专家**：优先 `realestate`（商业地产管家）；被删除/停用时取 sort 最小者，`routed_by="默认"`。
4. **缓存与失效**：`load_team()` 进程内 60 秒 TTL 缓存；任何专家/模型/供应商配置变更由路由层调用 `invalidate_team_cache()` + `orchestrator.invalidate()` 立即生效；表为空或数据库不可用时回退代码内置 TEAM 定义，保证离线可用。
5. **路由预览**：`POST /api/assistant/agents/route-test` 输入 `{text}`，强制刷新团队后返回 `{routed, matched_by, matches}`，供界面调试关键词。

### 3.2 SSE 流式对话（`agents/orchestrator.py` 的 `chat_stream`）

- 事件协议（每条 `data: {json}\n\n`，流末 `data: [DONE]`）：
  - `meta`：路由结果（agent 信息、routed_by、mode），先于正文；
  - `delta`：正文增量；`thinking_delta`：思考过程增量（思考型模型）；
  - `tool_start`：一次工具调用开始（含工具名）；
  - `done`：最终消息（完整 reply、thinking、trace）；
  - `error`：失败事件（模型未配置时带 `need_llm_config: true`）。
- **中止**：前端 `ChatPanel` 用 AbortController 持有当前流；发送新消息、切换/删除会话、组件卸载时 `abort()` 打断旧流。
- **错误落库保持历史成对**：用户消息先落库，流执行异常时补写一条 assistant 侧错误消息（`抱歉，本次回复生成失败：<异常类型>`），保证历史里一问一答成对出现。
- **非流式 `/chat`**：成功返回 `{ok: true, reply, thinking, agent, routed_by, mode, trace}`；模型未配置返回 `{ok: false, need_llm_config: true, message}`；**运行时异常返回 502**（日志记录完整堆栈，对外不回传内部细节）。
- 正文中的 `<think>…</think>` 标签会被剥离到 thinking 字段，思考块独立于正文展示。

### 3.3 数据模型（9 张表，`models.py`）

```
tasks            id, title, description, status, priority, due_date?, category, completed_at?, created_at, updated_at
schedules        id, title, description, location, start_time, end_time, all_day, color, created_at, updated_at
notes            id, title, content, tags(JSON), pinned, created_at, updated_at
chat_messages    id, session_id→agent_sessions, role(user/assistant), agent_name, content, trace(JSON), thinking, created_at, updated_at
agent_sessions   session_id(PK), title, created_at, updated_at
model_providers  id, name, protocol(openai/anthropic), base_url, api_key, enabled, created_at, updated_at
provider_models  id, provider_id→model_providers, model, context_size, max_tokens, multimodal, enabled, created_at, updated_at
agent_settings   id=1 单行：max_iters, enable_memory, history_inject, active_model_id→provider_models,
                 enable_skills/skills_dir, enable_bash, enable_plugins/plugins_dir, reasoning_effort, updated_at
agent_specs      id, name(唯一, ^[a-z][a-z0-9_]*$), display_name, emoji, color, description, role_prompt,
                 keywords(JSON), tools(JSON 可空), skills(JSON 可空), model_id(可空, 逻辑引用 provider_models.id),
                 enabled, sort(=路由优先级，小者优先), is_builtin, created_at, updated_at
```

要点：状态/优先级用 Pydantic Literal 校验；时间统一本地 naive datetime；`model_id` 为逻辑引用（无数据库外键），绑定的模型被删除/停用时自动回退全局激活模型。

### 3.4 轻量列迁移机制（`main.py` 的 `_ensure_sqlite_columns`）

SQLite 的 `create_all` 不会修改已有表。启动时对历史库做补丁式迁移：`PRAGMA table_info(表)` 读现有列，缺哪列补哪条 `ALTER TABLE ... ADD COLUMN`（当前覆盖 `chat_messages.thinking` 与 `agent_settings` 的后加列）。表不存在则跳过，整体异常仅告警不阻断启动。专家团队 seed（`seed_agent_specs`）仅空表执行一次，属配置数据，不受 `SEED_ON_STARTUP` 控制。

### 3.5 热备份（`backend/scripts/backup_db.py`）

用 `sqlite3 Connection.backup` API 在线热备份：源库以只读 URI 打开、逐页拷贝，服务运行中也可安全执行，不会拷出半成品文件。产物带时间戳存到项目根 `backups/`，自动滚动清理只保留最近 30 份。`备份数据.bat` 调用该脚本。

### 3.6 附件安全（`routers/assistant.py` 的 `/upload` 与编排器 `_attachments_block`）

- 上传：`session_id` 白名单正则校验（防路径穿越）；文件名取 `Path(name).name` 去目录；10MB 上限（超限 400）；落盘到 `data/uploads/{session_id}/`；xlsx/xls 用 openpyxl 读第一张工作表转 CSV（`utf-8-sig`）。
- 注入：对话时对每个附件路径做 `resolve() + relative_to(UPLOAD_DIR)` 校验，越界路径直接忽略；仅白名单文本后缀（txt/md/csv/json/log/py/html/xml/yaml/sql）且 ≤300KB 的附件按文本注入消息（截断至 8 万字符）；其余仅告知路径，提示 Agent 用 Bash 工具处理（Bash 默认关闭）。

### 3.7 前端架构

- **无状态库的组件通信**：不使用 Pinia，页面内用 props/emit + ref 组合。`AssistantView.vue` 拆为容器（约 235 行，持有会话/团队/设置状态）+ `src/components/assistant/` 五个子组件：`SessionList`（会话列表）、`AgentQuickList`（专家快选）、`ChatPanel`（对话区，SSE/中止/工具轨迹折叠）、`SettingsDrawer`（设置抽屉）、`AgentTeamPanel`（专家团队管理）。
- **列表请求序号防竞态**：任务/日程/笔记列表请求带自增序号（`++loadSeq`），响应返回时序号不是最新则丢弃，避免快速切换筛选时旧响应覆盖新数据。
- **统一 axios 拦截器**：`src/api/index.js` 把 422 detail 数组/超时/断网统一转成中文 ElMessage 提示；SSE 走原生 fetch 与 axios 共用 `VITE_API_BASE`（默认 `/api`）。

### 3.8 模型接入与配置存储（`agents/llm.py` + `config_store.py`）

- 配置优先级：激活模型（`model_providers` + `provider_models`，界面管理）> `.env` 的 `LLM_*` 兜底。
- openai 协议 → `OpenAIChatModel`（覆盖 ModelScope/硅基流动/DeepSeek/火山方舟/DashScope 兼容/Ollama）；anthropic 协议 → `AnthropicChatModel`（强制流式聚合）。
- 思考深度 `reasoning_effort`（''/off/low/medium/high/max）映射到两协议各自的 thinking 参数；温度默认 0.3。
- API Key 存 `model_providers.api_key`（明文，本机）；回传前端脱敏为 `••••••xxxx`，掩码哨兵 `********`（8 个星号）传回表示保留现值。
- 专家可独立绑定模型（`agent_specs.model_id`），编排器按 `model_cfg_by_id` 构建并缓存；不可用（删除/停用）时回退全局激活模型。

### 3.9 QwenPaw Agent 批量导入（`scripts/import_qwenpaw_agents.py`）

管道：**source files → sanitize → merge/create → DB**。

1. **source files**：读取 QwenPaw 工作区下每个 Agent 的角色定义文件——SOUL.md / PROFILE.md / AGENTS.md / 00_系统总则.md / 06_协同规则.md（存在才读，按序拼接）。工作区默认 `D:/AI_Service/QwenPaw/data/workspaces`，可用 `--from` 或环境变量 `QWENPAW_WORKSPACES` 指定；都没有则友好退出。
2. **sanitize**：脱敏——「老头子」等称呼替换为「用户」，本机绝对路径替换为 `[本地路径]`。
3. **merge/create**：领域重叠的合并进内置专家（cre-leasing→leasing、cre-ops→operations、cre-mkt→marketing、cre-fm→property、cre-finance→it_finance、cre-it→security，追加角色定义 + 关键词取并集）；全新职责的新建专家（cre_gm 商业地产总经理、cloud_orchestrator CloudPaw 主控编排、cloud_executor CloudPaw 执行器、cloud_verifier CloudPaw 验证器、datapaw DataPaw 数据分析），`sort=7..11` 排在内置专家 0–6 之后，同分优先路由到内置。
4. **DB**：写入 `agent_specs` 表，只进本机数据库、不进 git 仓库。`--dry-run` 只预览不写入；`--overwrite` 剥掉旧导入内容后重导；默认幂等——角色提示词带【QwenPaw 整合】标记，已导入则自动跳过不叠加。导入后 60 秒内团队缓存自动刷新，或重启服务立即生效。

`AgentSpecBase/Update.role_prompt` 上限从 8000 放宽到 60000 字符（QwenPaw 角色定义约 1.4 万字符），脚本对拼接结果另做 60000 硬截断。配套测试 `backend/tests/test_import_qwenpaw_agents.py` 4 例，静态校验、不需要本机装有 QwenPaw。

## 4. 总体架构

```
前端（Vue 3 + Element Plus + ECharts）
  五页：概览 / 任务 / 日程 / 笔记 / AI 助手（容器 + 5 个子组件）
        │  HTTP /api/*（SSE 走 fetch 流）
        ▼
FastAPI（routers/）
  ├─ tasks / schedules / notes / search / dashboard  → SQLAlchemy → SQLite
  └─ assistant                                        → app/agents/
        ├─ llm.py           模型工厂（openai/anthropic 协议分流）
        ├─ config_store.py  供应商/模型两级存储 + 预设 + 连通性/工具/多模态测试
        ├─ tools.py         8 个业务工具（显式 schema、contextvar 轨迹）
        ├─ team.py          专家团队：内置定义 + agent_specs 表加载 + 关键词路由（60s 缓存）
        ├─ orchestrator.py  路由 → 专家执行（SSE/非流式）→ 轨迹/会话持久化
        └─ qwenpaw_compat.py QwenPaw 插件兼容层（实验性，默认关闭）
```

## 5. 接口设计

统一前缀 `/api`，业务接口详见 API.md。要点：

- 列表统一分页 `{items, total, page, page_size}`；PUT 均为部分更新（`exclude_unset`）。
- 删除返回 204、不存在 404、校验失败 422（含非法日期参数——`utils.parse_dt` 统一把日期解析错误转成 422 而非 500）。
- `/api/assistant/chat` 运行时异常返回 502；`/chat/stream` 保持 SSE 协议、在流内发 `error` 事件。

## 6. 配置与环境变量

对齐 `backend/.env.example`（全部有默认值，可不改直接运行）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 监听地址与端口（不要改成 `0.0.0.0`） |
| `DATABASE_URL` | `sqlite:///backend/data/workbench.db` | 数据库连接，默认 SQLite |
| `SEED_ON_STARTUP` | `1` | 空库时是否写入示例数据（0 关闭；专家团队 seed 不受此控制） |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 跨域来源（仅开发模式需要） |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | AI 助手模型兜底配置；填齐后三项即激活。界面配置优先于它 |

## 7. 测试与验证

- 后端 pytest **92 例**（`backend/tests/`）：业务 CRUD/筛选/统计/搜索 + Agent 路由/工具/配置脱敏/供应商模型 CRUD/专家团队 CRUD 与恢复出厂/连通性测试 + QwenPaw 导入脚本静态校验（4 例）。全部离线，独立临时库，conftest 显式清空 LLM 环境变量。
- 前端 vitest 11 例（`frontend/tests/` 3 个文件：格式化/日历范围/Agent 相关工具函数）。
- 手动验收：五页 + 设置抽屉 + 专家团队 tab + SSE 对话流 + 路由测试工具。

## 8. 交付边界与后续方向

- 单用户、监听 127.0.0.1；删除类数据操作不开放给 Agent（仅页面物理删除，任务可由 Agent 标记完成）。
- 路线图：LLM 路由 few-shot 增强/会话归档 → 长期记忆与笔记 RAG → MCP 工具接入、定时 Agent → 多渠道（团队共享再考虑多租户）。
