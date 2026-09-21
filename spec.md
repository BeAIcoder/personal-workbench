# 个人工作台 · 需求与设计规格（spec）

> 本文档描述系统当前（v1.2.0）的需求与设计。历次演进：v1.0.0 基础工作台 → v1.1.0 Agent 团队 → v1.1.1 真实模型接入 → v1.2.0 参数配置界面。

## 1. 背景与目标

面向个人日常办公的本地 Web 工作台：任务、日程、笔记三大事项载体 + 概览与全局搜索；
并演进为**商业地产领域多 Agent 团队**（招商/运营/企划/工程物业/网络安全/信息化财务 + 综合管家），
让自然语言可以直接操作工作台数据。

硬约束：Windows 11 · Python 3.12+ · Node 22+；不允许 Docker 与外部独立数据库（MySQL/PostgreSQL/Redis/MongoDB）；
数据真实持久化（重启不丢）；交付达到可继续开发的软件工程标准。

## 2. 版本能力清单

| 版本 | 内容 |
| --- | --- |
| v1.0.0 | 工作台五大模块（任务/日程/笔记/概览/全局搜索）+ 测试 + 一键 bat 启动 |
| v1.1.0 | Agent 团队基座（AgentScope 2.0.7）：7 专家、智能路由、8 业务工具、会话持久化 |
| v1.1.1 | 真实模型接入（用户授权同步 QWENPAW 配置 → ModelScope Qwen3.5-122B-A10B），三类场景真实联调 |
| v1.2.0 | AI 助手参数配置界面：运行时配置存储、供应商预设、上下文/多模态/Agent 工作/记忆管理 |

## 3. 需求清单

| 编号 | 需求 | 实现 |
| --- | --- | --- |
| R1 | 任务管理：CRUD、状态/优先级/分类、截止与逾期、筛选/搜索/排序/分页 | `routers/tasks.py` + `TasksView.vue` |
| R2 | 日程管理：CRUD、月历、全天、时间范围查询 | `routers/schedules.py` + `ScheduleView.vue` |
| R3 | 笔记管理：CRUD、多标签、置顶、搜索/标签筛选 | `routers/notes.py` + `NotesView.vue` |
| R4 | 概览：统计卡片、状态分布、7 天趋势、快捷列表 | `routers/dashboard.py` + `DashboardView.vue` |
| R5 | 全局搜索：一次检索三类内容并跳转带入筛选 | `routers/search.py` + `GlobalSearch.vue` |
| R6 | 持久化：SQLite 单文件、启动自动建表、重启不丢 | SQLAlchemy + `data/workbench.db` |
| R7 | 数据初始化：空库自动写示例数据 + `python -m app.init_db [--reset]` | `seed.py` / `init_db.py` |
| R8 | 接口说明：Swagger 自文档 + API.md | `/docs`、`API.md` |
| R9 | 工程交付：结构、依赖、.env 示例、测试、示例数据、README/四件套 | 项目根 |
| R10 | AI 助手：7 专家团队、四级路由、工具调用（增/查，删除类不开放）、轨迹审计、会话持久化 | `app/agents/` + `AssistantView.vue` |
| R11 | 参数配置界面：模型/供应商、上下文长度、生成参数、多模态开关、Agent 最大工具轮次、记忆管理 | `agent_settings` 表 + 设置抽屉 |
| R12 | 多模态能力测试：向模型发图片验证视觉能力 | `config_store.test_multimodal` |
| R13 | 会话记忆：持久化 + 注入历史条数可配置、可关闭 | `chat_messages` 表 + 编排器 |

## 4. 技术选型与理由

- **SQLite + SQLAlchemy 2.x**：零部署、参数绑定防注入、可平滑换 PostgreSQL（换连接串即可）。
- **FastAPI + Pydantic v2**：自动校验（422）、Swagger、类型安全。
- **AgentScope 2.0.7**（锁定版本）：ReAct 多 Agent 编排、FunctionTool 显式 JSON Schema 工具注册、
  Permission/HITL、结构化输出、`Msg` 块模型；纯 Python 库，**嵌入**现有 FastAPI 而非替代平台。
- **OpenAI 兼容协议统一模型接入**：一套代码覆盖 ModelScope/硅基流动/DeepSeek/火山方舟/DashScope 兼容/Ollama，
  规避各家 SDK 差异；AgentScope 的 `OpenAIChatModel(credential, model, parameters, context_size)` 直连。
- **Vue 3 + Vite + Element Plus + ECharts**：组件覆盖表格/表单/日历/抽屉；hash 路由免 SPA 回退配置，
  前端构建产物由后端 8000 端口托管，单端口交付 + 双击 bat 启动。

## 5. 总体架构

```
前端五页（概览/任务/日程/笔记/AI 助手+设置抽屉）
        │  HTTP /api/*
        ▼
FastAPI（routers/）
  ├─ tasks / schedules / notes / search / dashboard   → SQLAlchemy → SQLite
  └─ assistant                                          → app/agents/ 编排器
        ├─ llm.py            模型工厂（运行时配置 → OpenAIChatModel）
        ├─ config_store.py   运行时配置存储 + 供应商预设 + 连通性/多模态测试
        ├─ tools.py          8 个业务工具（显式 schema、contextvar 轨迹）
        ├─ team.py           7 专家定义 + 关键词路由表
        └─ orchestrator.py   四级路由 → Agent 执行 → 轨迹/会话持久化
```

配置优先级：运行时配置（`agent_settings` 表，界面可改）> `backend/.env` 兜底。

## 6. 数据模型

```
tasks         id, title, description, status, priority, due_date?, category, completed_at?, created_at, updated_at
schedules     id, title, description, location, start_time, end_time, all_day, color, created_at, updated_at
notes         id, title, content, tags(JSON), pinned, created_at, updated_at
chat_messages id, session_id, role(user/assistant), agent_name, content, trace(JSON), created_at, updated_at
agent_settings id=1, provider, base_url, api_key, model,
               context_size, max_tokens, temperature, multimodal,
               max_iters, enable_memory, history_inject, updated_at
```

约束：状态/优先级用 Literal 校验；`completed_at` 由后端维护（进 done 写入、退出清空，支撑趋势统计）；
标签 JSON `ensure_ascii=False` 存储以便 LIKE 过滤；时间统一本地 naive datetime；日程 `end > start`。

## 7. 接口设计

统一前缀 `/api`。业务接口详见 API.md。要点：

- 列表统一分页 `{items, total, page, page_size}`；更新接口 PUT 均为**部分更新**（`exclude_unset`）。
- 删除返回 204、不存在 404、校验失败 422。
- 概览 `/api/dashboard/summary` 一次取全；搜索 `/api/search` 三类各 Top5 + snippet。

AI 助手接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/assistant/team` | 名册 + 模型状态 + 配置（脱敏）+ 预设 |
| `GET /api/assistant/config` | 读配置（api_key 脱敏） |
| `PUT /api/assistant/config` | 保存配置（**部分更新合并**；api_key 留空/掩码保留现值）；清缓存生效 |
| `POST /api/assistant/config/test` | 连通性测试（可选 `include_image` 多模态） |
| `POST /api/assistant/chat` | 对话：`{session_id, message, agent?}` → `{ok, reply, agent, routed_by, trace}` |
| `GET /api/assistant/history/{id}` | 会话历史 |

## 8. Agent 团队设计（详见 Agent团队设计.md）

- **7 专家**：商业地产管家（默认/协调）、招商、运营、企划、工程物业、网络安全、信息化财务（核心：报表解读/凭证核对）。
- **四级路由**：用户指定 > 关键词规则（离线）> LLM 结构化调度 > 默认管家。
- **工具矩阵**：8 工具（任务/日程/笔记/统计）直接操作工作台数据，全参数绑定；写入工具按职责收敛；**删除类不开放**。
- **权限**：`PermissionMode.BYPASS` 自动放行（个人助手 + 白名单非破坏性工具）。
- **工作模型**：`ReActConfig(max_iters)` 控制最大工具轮次。
- **轨迹审计**：contextvar 收集工具调用 → 随会话持久化 → 前端折叠展示。
- **记忆**：会话消息存 `chat_messages`；重启后按 `history_inject` 条数 `observe` 重建上下文；可关闭。

## 9. 前端结构

- `AppLayout`：深色侧边栏（五入口：概览/任务/日程/笔记/AI 助手）+ 顶栏（标题、全局搜索 Ctrl+K、日期）。
- `AssistantView`：左团队名册（点选专家/智能路由）+ 右对话区（气泡、工具轨迹折叠、建议问题）+
  「⚙ 设置」抽屉（供应商/模型/Key、上下文/生成参数、多模态、Agent 轮次、记忆管理，测试结果内联展示）。
- 四个业务页 + `BaseChart`（ECharts 封装）；axios 拦截器统一中文报错。

## 10. 配置与记忆管理

- 运行时配置存 `agent_settings` 单行表；保存为部分更新合并；api_key 掩码哨兵 `******` 保留现值，回显 `••••••xxxx`。
- 供应商预设 7 个，选择自动填充 base_url + 候选模型。
- 上下文长度映射 `OpenAIChatModel(context_size=)`；最大回复/温度映射 `Parameters(max_tokens, temperature)`。
- 多模态：AgentScope 2.0.7 块模型无图片输入块 → 测试走 OpenAI 兼容直连发送小红图（PNG 运行时生成），
  由模型回答颜色判定视觉能力；聊天传图列为后续。
- 记忆：`enable_memory` 开关 + `history_inject` 注入条数（0~100，默认 12）。

## 11. 质量保障

- 后端 pytest **62 例**（业务 CRUD/筛选/统计/搜索 + Agent 路由/工具/配置 CRUD/脱敏/部分更新/测试接口/PNG 工具），
  全部离线，独立临时库，conftest 显式清空 LLM 环境变量与本地 .env 解耦。
- 前端 vitest 6 例（格式化/逾期判断/日历范围）。
- 真实联调（ModelScope Qwen3.5-122B-A10B）：查询（工具调用+精确数据）、写入（自然语言建日程）、
  财务凭证核对、连通性（3.5s）、多模态（识别"红色"）均通过。
- Playwright 页面验收：五页 + 设置抽屉 + 对话流，无控制台错误。

## 12. 交付边界与后续方向

- 单用户、监听 127.0.0.1；删除类数据操作由 Agent 不接触（仅页面物理删除）。
- 路线图：LLM 路由 few-shot 增强/会话归档 → 长期记忆（ReMe/Mem0）与笔记 RAG → 接入企业 ERP/业务系统
  浏览器自动化 + 定时 Agent（日报/申报提醒）→ AgentScope Agent Service 多租户/飞书钉钉（团队共享再考虑）。
