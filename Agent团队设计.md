# Agent 团队设计（配置化版）

> 目标：把「个人工作台」从工具型系统演进为**可配置的多 Agent 协同团队**。
> 内置出厂专家 8 位（`team.py` 的 `TEAM`，seed 数据源），全部定义存数据库 `agent_specs` 表，
> 界面上可增删改、启停、改关键词、绑定模型、调路由优先级，保存即生效；
> 经 QwenPaw 批量导入还可扩充 5 位专家（合计 13 位，见 §15）。
> 基于 AgentScope 2.0（`agentscope==2.0.7.post1`）构建，嵌入现有 FastAPI 后端。
>
> **权威口径**：本文为设计文档，已随实现追平至 v1.16.1；接口细节以 `API.md` 为准，
> 权威 as-built 现状以 `spec.md`（v1.16.1，2026-10-19）为准，两处冲突时以 `spec.md` 为准。
> 文中标注「历史」的段落为旧设计快照，仅作演进对照，不代表当前实现。

## 1. 总体架构

```
前端 AI 助手页（Vue 容器 + 5 子组件）         现有四大页面（任务/日程/笔记/概览）
      │  POST /api/assistant/chat(/stream)          │
      ▼                                           │
┌───────────────────────────────────────────┐   │
│ 编排器 orchestrator.py                      │   │
│  ① 路由：指定专家 > 关键词打分 > LLM 结构化调度 > 默认 │   │
│  ② 按 (会话, 专家, 模式, 绑定模型) 缓存 Agent 实例   │   │
│  ③ 工具轨迹 contextvar 收集 → 会话持久化          │   │
└──────────────┬────────────────────────────┘   │
               ▼                                ▼
┌─────────────────────────────┐   ┌─────────────────────┐
│ 专家团队（读 agent_specs 表）   │   │ 共享业务工具 tools.py  │
│ load_team() 60s 缓存         │──▶│ query/create/complete │
│ 空表/异常回退内置 TEAM        │   │ task · schedule ·    │
│ 全停用 = 空团队（明确报错）     │   │ note · stats · 知识/记忆 │
└──────────────┬──────────────┘   └─────────┬───────────┘
               ▼                  ┌─────────┴───────────┐
        AgentScope 模型层          │ 技能池（SKILL.md 包）  │
        openai / anthropic 协议    │ MCP stdio 服务器工具   │
        （专家可绑定各自模型，      │ 动态注入 toolkit        │
         空则全局激活模型）         └─────────────────────┘
               ▼
        SQLite（SQLAlchemy 参数绑定）
```

- **AgentScope 作为库嵌入**，不是平台替代：Agent 模型、工具注册、ReAct 循环、结构化输出由框架提供；路由与持久化由我们自己编排。
- **技能池与 MCP 旁路注入**：本地技能包（见 §16）经 AgentScope `LocalSkillLoader` 注入，MCP stdio 服务器工具（见 §17）按连接状态动态注册——与内置工具共用同一套白名单与只读约束。
- 生产形态不变：仍由 FastAPI 在 8000 端口单端口托管，双击 `启动工作台.bat` 启动。

## 2. 数据模型（agent_specs 表）

| 字段 | 说明 |
| --- | --- |
| `name` | 英文标识，唯一，格式 `^[a-z][a-z0-9_]*$`（路由/存储用） |
| `display_name` / `emoji` / `color` | 界面展示名与头像配色 |
| `description` | 一句话职责（供 LLM 路由名册与前端展示） |
| `role_prompt` | 角色系统提示词（编排器统一拼接 COMMON_RULES 公共工作规范） |
| `keywords` | 路由关键词数组（JSON），最多 50 个 |
| `tools` | 工具白名单（JSON）；NULL = 默认查询工具池，空数组 = 真零工具 |
| `skills` | 专家级技能白名单（JSON）；NULL = 跟随全局技能池，空数组 = 显式关闭 |
| `model_id` | 绑定模型（逻辑引用 `provider_models.id`）；NULL = 跟随全局激活模型 |
| `enabled` | 停用后不参与路由、不出现在名册 |
| `sort` | 排序，**兼作关键词同分时的路由优先级，小者优先** |
| `is_builtin` | 内置专家标记（恢复出厂用） |

**seed 机制**：`seed_agent_specs()` 按 **增量 upsert**（缺谁补谁，ADR-005）灌入内置专家——表为空时全量灌入；升级后新增的内置专家（如财税专员）在下次启动自动补齐；已存在行的用户修改/删除不被覆盖。该 seed 属配置数据，不受 `SEED_ON_STARTUP` 控制。表被清空或数据库不可用时，`load_team()` 回退代码内置 TEAM 定义，保证离线/异常场景可用；表中有行但全部停用时返回空团队——这是有效的用户配置，聊天路径据此给出明确错误而不是静默回退。

## 3. 内置团队名册（8 位专家）

| 标识 | 中文名 | sort | 职责 | 工具范围 |
| --- | --- | --- | --- | --- |
| `security` | 网络安全工程师 | 0 | 漏洞整改跟踪、等保合规、安全检查清单、办公终端防护 | 查询 + 建任务/笔记 |
| `leasing` | 招商专员 | 1 | 品牌引进、业态组合、租决条件（租金/免租/装补/递增）、招商跟进 | 查询 + 建任务/日程/笔记 |
| `operations` | 运营专员 | 2 | 客流/销售额/收缴率/坪效解读、商户经营辅导、营运安排 | 查询 + 建任务/日程/完成任务 |
| `marketing` | 企划策划 | 3 | 营销活动策划、档期排期、物料预算清单、会员营销 | 查询 + 建任务/日程/笔记 |
| `property` | 工程物业工程师 | 4 | 设备设施维保、能耗管理、巡检整改、外包管理 | 查询 + 建任务/日程 |
| `it_finance` | 信息化财务分析师 | 5 | **核心专家**：报表解读、凭证核对、预算费用分析、税务提醒、信息化建议 | 全查询 + 全部写入 |
| `realestate` | 商业地产管家 | 6 | 资产管理全景分析；团队总协调与默认接洽人 | 全查询 + 建任务/日程/笔记/完成任务 |
| `finance_specialist` | 财税专员 | 7 | 财税事务专员：发票/台账/凭证/记账/报销/费用的口径咨询与事项落地 | 查询 + 建任务/日程/笔记 + 预置全部财务模块工具 |

- 每位专家拥有：角色提示词（中文、结论先行、金额日期严谨、不越界）+ 公共工作规范 COMMON_RULES（先查数据再作答、口头安排落成任务/日程/笔记、创建后告知用户）+ 收敛后的工具集（写入工具按职责裁剪）。
- `finance_specialist` 的财务模块工具由模块 manifest 的 `preset_experts` 自动并入白名单（见 `agents/module_tool_preset.py` 的 `FINANCE_TOOL_NAMES`），运行时权威来源是 Module Registry。
- 经 QwenPaw 批量导入（§15）还可新增 5 位专家，与内置合计最多 13 位（`spec.md` §5.1）。

## 4. 路由策略（先专后宽，四级兜底）

1. **用户指定**：前端点选某位专家或「智能路由」，`routed_by="指定"`；
2. **关键词规则**（离线可用）：统计每位启用专家的关键词（消息小写化后按子串包含）命中数，得分最高者胜出；**同分时 sort 小者胜**（稳定排序，先专后宽），`routed_by="关键词"`；
3. **LLM 结构化调度**：关键词未命中且模型可用时，调度员 Agent 读名册文本做结构化输出 `{agent, reason}`；输出不在名册或任何异常都回退，`routed_by="AI 调度"`；
4. **默认专家**：以上均失败 → `realestate`（被删/停用则取 sort 最小者），`routed_by="默认"`。

策略取舍：显式两步「路由→执行」而非嵌套多 Agent，更省 token、延迟更低、轨迹清晰；`routed_by` 回传前端展示本次由谁应答。权威口径见 `spec.md` §5.2。

## 5. 缓存与失效

- `load_team()`：进程内 60 秒 TTL 缓存；`force=True` 强制刷新（路由预览用）。
- 任何专家增删改、供应商/模型变更、工作参数保存，路由层统一调用 `invalidate_team_cache()` + `orchestrator.invalidate()`（清模型实例与各会话 Agent 缓存），**保存即生效，无需重启**。
- Agent 实例缓存 key 为 `(session_id, 专家, 模式, model_id)`：专家绑定模型变化时自动重建；缓存为 LRU，上限 64 个，超出淘汰最久未用（构建锁随淘汰一并清理）。

## 6. 工作模式

| 模式 | 说明 |
| --- | --- |
| `standard` 标准执行 | 专家全部工具，自动放行（默认） |
| `readonly` 只读咨询 | 仅放行内置查询工具 + 注册表中 `readonly=True` 标记的工具（模块/MCP 只读工具同样放行），提示词声明不修改数据（插件也不加载） |
| `deep` 深度研究 | 全部工具 + 工具轮次提到至少 10（`max_iters` 取配置值与 10 之大者）+ 深度分析提示词 |

只读放行名单在构建时实时汇总（`_readonly_tool_names()`）：内置查询工具 + `TOOL_SPECS` 中带 `readonly` 标记的工具——该标记由模块 manifest（`modules/spec.py` 契约）与 MCP 工具注册（`mcp.py`）写入，模块/MCP 注册变化即时生效。

## 7. 共享业务工具（tools.py）

工具全部经 SQLAlchemy 参数绑定，按来源分四类：

- **内置业务工具 8 个**，直接操作工作台数据：`query_tasks / create_task / complete_task / query_schedules / create_schedule / search_notes / create_note / workbench_stats`；
- **知识/记忆只读工具 2 个**：`search_knowledge`（BM25 知识检索）/ `recall_memories`（长期记忆召回），均带 `readonly=True` 标记；
- **财务模块工具**：由 Module Registry 的 `preset_experts` 幂等合并进目标专家白名单（如 `finance_specialist`），工具名清单见 `module_tool_preset.FINANCE_TOOL_NAMES`，运行时权威来源是注册表；
- **MCP 工具**：`mcp_<服务器名>__<工具名>`，按服务器连接状态动态注册（见 §17）。

要点：显式 JSON Schema 含中文参数说明；出错返回 `{"ok": False, "error": ...}` 便于模型自我纠错；轨迹经 contextvar 收集持久化，前端折叠展示可审计；**删除类不开放**。

## 8. 前端管理界面

`AssistantView.vue` 容器（约 315 行）+ `src/components/assistant/` 五个子组件：SessionList / AgentQuickList / ChatPanel / SettingsDrawer / **AgentTeamPanel**。

设置抽屉新增「专家团队」tab（AgentTeamPanel，约 515 行）：卡片式增删改、启用开关、关键词编辑、绑定模型下拉（跟随全局/各已配置模型）、**路由测试工具**（输入一句话预览命中专家与命中词）、**恢复出厂**按钮（按 name 覆盖/补插内置专家，不动自定义专家）。

另有独立静态页：**MCP 工具**、**技能包管理**、**专家团文件**（导入/导出/一致性比对），入口见 `spec.md` §9 前端清单。

## 9. 与旧版（写死定义）的差异

| 维度 | 旧版 | 现版 |
| --- | --- | --- |
| 专家定义 | 写死在 `team.py` TEAM | 存 `agent_specs` 表，TEAM 仅作 seed 数据源与兜底 |
| seed 行为 | 仅空表灌入一次 | 增量 upsert：缺谁补谁，升级自动补齐新内置专家，不覆盖用户改动（ADR-005） |
| 增删改 | 改代码重启 | 界面操作，保存即生效（缓存失效） |
| 路由关键词 | 同上写死 | 每专家可编辑；sort 可调优先级；route-test 可预览 |
| 模型 | 全团队共用一个激活模型 | 每专家可绑定独立模型（`model_id`），空则跟随全局 |
| 技能 | 全局开关 | 专家级白名单：NULL 跟随全局 / 空数组显式关闭；技能池停用对 Agent 注入侧生效（v1.16.1） |
| MCP / 技能池 / 专家团文件 | 无 | stdio MCP 客户端 + SKILL.md 技能包管理 + 专家包 .md 导入导出（§16 / §17） |
| 名册接口 | 只返回静态字段 | 只返回启用专家、按 sort 排序，新增 id/model_id/keywords/enabled/sort/is_builtin |

## 10. 接口（详见 API.md）

| 接口 | 说明 |
| --- | --- |
| `GET /api/assistant/agents` | 专家团队列表（含停用，按 sort 排序） |
| `POST /api/assistant/agents` | 新增专家（201；name 重复 400） |
| `PUT /api/assistant/agents/{id}` | 更新专家（部分更新；显式传 null 可清空 model_id/tools/skills） |
| `DELETE /api/assistant/agents/{id}` | 删除专家（内置也允许删，前端自行警告） |
| `POST /api/assistant/agents/route-test` | 路由预览：`{text}` → `{routed, matched_by, matches[]}` |
| `POST /api/assistant/agents/reset` | 内置专家恢复出厂（不影响自定义专家） |
| `GET /api/assistant/team` | 兼容端点：名册 + 模型状态 + 配置（脱敏）+ 供应商/模型 + 工作模式 |

## 11. 会话与记忆

- 会话元数据存 `agent_sessions`（标题/时间/归档），消息存 `chat_messages`（role / agent_name / content / trace / thinking）；
- 多会话管理：新建/重命名/删除（删除同时清理消息与进程内 Agent 缓存）/归档/导出 Markdown；
- 重启后按历史最近 N 条（`history_inject`，默认 12，上限 100）`observe` 重建上下文；记忆可整体关闭；
- 相关长期记忆经 BM25 召回后作为上下文前缀注入用户消息（失败静默，不影响对话）；
- 内部会话（定时任务 `job-*`、记忆抽取 `memory-extract`）不进会话列表，不注入历史。

## 12. 模型接入

统一走 OpenAI 兼容协议（另支持 anthropic 协议）。**推荐在「AI 助手 → ⚙ 设置 → 模型」配置**（供应商/模型两级，存库，重启不丢）；也可写 `backend/.env` 的 `LLM_*` 兜底（`llm.source` 标识当前生效来源：界面配置 / env 兜底）。

**33 家厂商预设**数据化存于 `backend/app/agents/provider_presets.json`：`GET /api/providers/presets` 读取、`POST /api/providers/presets/reload` 热加载；文件缺失/解析失败回退内置最小默认集并记警告。每条预设含 `name/label/protocol/base_url/models/reasoning_models/vision_models/note/docs_url`，界面选择后自动填充 base_url、候选模型与视觉模型清单。协议仅 `openai` / `anthropic` 两种。套餐范围：`general`（按量，任意场景）/ `plan_interactive_only`（仅交互式，定时任务选中会中文拒绝）/ `plan_api_allowed`（交互+API 均可）。

| 预设 | base_url | 示例模型 | 协议 | 套餐范围 |
| --- | --- | --- | --- | --- |
| ModelScope 推理 | `https://api-inference.modelscope.cn/v1` | `Qwen/Qwen3-235B-A22B-Instruct-2507` | openai | 按量 |
| 硅基流动 SiliconFlow（国内） | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3.2` | openai | 按量 |
| 硅基流动 SiliconFlow（国际） | `https://api.siliconflow.com/v1` | `deepseek-ai/DeepSeek-V4-Pro` | openai | 按量 |
| DeepSeek 官方 | `https://api.deepseek.com/v1` | `deepseek-flash` | openai | 按量 |
| 火山方舟 Volcengine 按量 | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-2-1-pro-260628` | openai | 按量 |
| 火山方舟 Coding Plan | `https://ark.cn-beijing.volces.com/api/coding/v3` | `deepseek-v4-flash` | openai | 仅交互 |
| 火山方舟 Agent Plan | `https://ark.cn-beijing.volces.com/api/plan/v3` | 手填 | openai | 仅交互 |
| 阿里 DashScope 按量 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.8-max` | openai | 按量 |
| 阿里 DashScope Coding Plan | `https://coding.dashscope.aliyuncs.com/v1` | `qwen3.5-plus` | openai | 仅交互 |
| 阿里 DashScope Token Plan | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` | `qwen3.8-max` | openai | 仅交互 |
| 腾讯 TokenHub 按量 | `https://tokenhub.tencentmaas.com/v1` | `hy3-preview` | openai | 按量 |
| 腾讯 Token Plan | `https://api.lkeap.cloud.tencent.com/plan/v3` | `hy3` | openai | 仅交互 |
| 腾讯 Coding Plan | `https://api.lkeap.cloud.tencent.com/coding/v3` | `tc-code-latest` | openai | 仅交互 |
| 智谱 GLM 官方 | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.3` | openai | 按量 |
| 智谱 GLM Coding Plan | `https://open.bigmodel.cn/api/coding/paas/v4` | `glm-5.3-highspeed` | openai | 仅交互 |
| 智谱 GLM（Anthropic 协议） | `https://open.bigmodel.cn/api/anthropic` | `glm-5.3` | anthropic | 按量 |
| 月之暗面 Kimi 开放平台 | `https://api.moonshot.cn/v1` | `kimi-k3.1-pro` | openai | 按量 |
| Kimi Code | `https://api.kimi.com/coding/v1` | `kimi-for-coding` | openai | 仅交互 |
| StepFun 阶跃星辰 按量 | `https://api.stepfun.com/v1` | `step-5-preview` | openai | 按量 |
| StepFun Step Plan | `https://api.stepfun.com/step_plan/v1` | `step-5-preview` | openai | 交互+API |
| OpenCode 付费推理 | `https://opencode.ai/inference/openai/v1` | `kimi-k3` | openai | 按量 |
| OpenCode Go | `https://opencode.ai/inference/go/openai/v1` | `kimi-k3.1-pro` | openai | 交互+API |
| MiniMax | `https://api.minimax.chat/v1` | `MiniMax-M3` | openai | 按量 |
| 腾讯混元 | `https://api.hunyuan.cloud.tencent.com/v1` | `hunyuan-turbos-latest` | openai | 按量 |
| 讯飞星火 | `https://spark-api-open.xf-yun.com/v1` | `lite` | openai | 按量 |
| OpenAI | `https://api.openai.com/v1` | `gpt-5.1` | openai | 按量 |
| Anthropic Claude | `https://api.anthropic.com` | `claude-opus-4-5` | anthropic | 按量 |
| OpenRouter | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4-flash` | openai | 按量 |
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` | openai | 按量 |
| Together AI | `https://api.together.xyz/v1` | `Qwen/Qwen3-235B-A22B` | openai | 按量 |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` | openai | 按量 |
| 本地 LM Studio | `http://localhost:1234/v1` | `qwen2.5-7b-instruct` | openai | 按量 |
| 自定义 | 手填 | 手填 | openai | — |

> 历史：v1.1.0–v1.13.0 仅内置 7 家预设（ModelScope / 硅基流动 / DeepSeek / 火山方舟 / DashScope / 本地 Ollama + 自定义）；v1.14.0 起预设数据化并扩充至 18 家；v1.16.x 随在线同步（Task 4）与套餐边界（Task 5）扩充至 33 家（新增各家 Coding/Token/Agent Plan、智谱 Anthropic 协议、Kimi Code、StepFun Plan、OpenCode Go、硅基国际等）。上表以 `provider_presets.json` 运行时权威为准。

- **模型超时**：两种协议共用 **120 秒**显式 SDK 超时（`llm.py` 的 `LLM_TIMEOUT_SECONDS`，经 `client_kwargs` 透传给 `openai.AsyncClient` / `anthropic.AsyncAnthropic`；SDK 默认 600 秒，单用户工作台显式收紧，避免一次挂起长时间占住请求）。
- **模型级自定义**：`temperature`（空 = 跟随系统默认，已接入 `build_model` 运行时、双协议注入）、思考档位 `reasoning_effort`、`supports_vision`、备注。
- **能力探测**（v1.14）：连通性 / 工具调用 / 多模态（小红图识色）三类测试内联展示；另有思考能力、上下文（二分查找）、最大输出（阶梯）三连探测，失败一律 200 + error 返回——**不可达 ≠ 不支持**，`supports_thinking` 保持 `-1` 不误判。
- 配置变更自动清缓存，下次请求即生效；未配置时 `GET /api/assistant/team` 正常返回（`llm.configured=false`），聊天返回未配置指引，**主工作台功能完全不受影响**。

## 13. 测试

后端 pytest 中 Agent 相关覆盖（`test_assistant.py` / `test_assistant_v2.py` / `test_assistant_config.py` / `test_agent_specs.py` / `test_chat_image.py` / `test_skill_store.py` / `test_expert_packs.py` / `test_mcp.py` / `test_memory.py` / `test_rag.py` / `test_session_archive.py` / `test_import_qwenpaw_agents.py` 等）：专家 CRUD 与标识校验、关键词路由打分与同分优先级、route-test、恢复出厂、seed 增量 upsert、团队缓存失效、模型工厂、无密钥聊天分支、工具往返、历史持久化、技能包停用注入过滤、专家包导入/导出/一致性比对、MCP 只读判定与工具注册清理。全部离线运行，不消耗模型额度。

## 14. 风险与对策

- **版本漂移**：`requirements.txt` 锁定 `agentscope==2.0.7.post1`；升级前先跑 pytest。
- **无密钥/断网**：模型功能降级为「未配置提示」，主功能不受影响。
- **工具执行权限**：Agent 以 `PermissionMode.BYPASS` 自动放行（个人助手 + 白名单非破坏性工具）；若未来开放删除类工具，应改回逐次确认（HITL）。
- **LLM 直接写数据**：写入工具参数校验 + 可审计轨迹；删除类操作未开放（物理删除仅在页面操作）。
- **MCP 只读边界**：只读模式以 `annotations.readOnlyHint` 为唯一放行依据、缺省视为可写——对不可信的第三方 Server，其自声明注解同样被信任，只读放行不等于绝对安全，谨慎启用陌生 Server；单服务器启动失败不影响宿主。
- **Bash / 插件 / 技能池**：默认关闭的实验能力，开启前知悉本机执行风险（Bash 自带危险文件/目录黑名单，详见 注意事项.md）。
- **Windows 嵌套事件循环**：全部走 async 路由 await，不在同步上下文 `asyncio.run`。

## 15. QwenPaw Agent 整合

除界面手配外，可用 `backend/scripts/import_qwenpaw_agents.py` 把本机 QwenPaw 工作区里的 Agent 定义批量整合进 `agent_specs` 表，一键扩充/刷新团队。`--dry-run` 可只预览不写库。

**数据流**：

```
QwenPaw 工作区文件（SOUL.md / PROFILE.md / AGENTS.md / 00_系统总则.md / 06_协同规则.md）
  → 按序拼接 + 脱敏（「某昵称」等称呼替换为「用户」，本机绝对路径替换为 [本地路径]）
  → 合并 / 新建进 agent_specs 表（role_prompt 带【QwenPaw 整合】标记）
```

- 源目录：默认 `D:/AIApps/QwenPaw/data/workspaces`，可用 `--from` 或环境变量 `QWENPAW_WORKSPACES` 指定；都没有则友好退出，不影响别的功能。

**合并 / 新建策略**：

| QwenPaw 工作区 | 处理方式 | 工作台专家 |
| --- | --- | --- |
| cre-leasing | 合并：追加角色定义 + 关键词并集 | `leasing` 招商专员 |
| cre-ops | 合并 | `operations` 运营专员 |
| cre-mkt | 合并 | `marketing` 企划策划 |
| cre-fm | 合并 | `property` 工程物业工程师 |
| cre-finance | 合并 | `it_finance` 信息化财务分析师 |
| cre-it | 合并 | `security` 网络安全工程师 |
| cre-gm | 新建 | `cre_gm` 商业地产总经理 |
| cloud-orchestrator | 新建 | `cloud_orchestrator` CloudPaw 主控编排 |
| cloud-executor | 新建 | `cloud_executor` CloudPaw 执行器 |
| cloud-verifier | 新建 | `cloud_verifier` CloudPaw 验证器 |
| datapaw | 新建 | `datapaw` DataPaw 数据分析 |

**排序策略**：新建专家 `sort=7..11`（cre_gm=7、cloud_orchestrator=8、cloud_verifier=9、cloud_executor=10、datapaw=11），排在内置专家（0–7）之后；与内置财税专员同占 sort=7 时按入库顺序稳定排序（内置 seed 先行），关键词同分情况下消息优先被内置专家接住。

**幂等标记机制**：导入的角色提示词带【QwenPaw 整合】标记。默认模式下已导入的专家自动跳过、不会叠加；`--overwrite` 先按标记剥掉上次导入的内容再重写。导入只写本机数据库、不进 git 仓库；`agent_specs.role_prompt` 上限从 8000 放宽到 60000 字符（QwenPaw 角色定义约 1.4 万字符），脚本对拼接结果另做 60000 硬截断，防止异常文件撑爆上下文。导入的专家与内置专家共存：seed 为增量 upsert，不会清掉已导入的 5 位专家。

**配套测试**：`backend/tests/test_import_qwenpaw_agents.py` 4 例，静态校验合并/新建映射表、脱敏与幂等剥离逻辑，不需要本机装有 QwenPaw，全部离线运行。

## 16. 技能池与专家团文件（v1.13.0 / v1.16.1）

### 16.1 技能池（skills）

- **术语**：一个「技能包」= 一个目录（`<name>/SKILL.md` + 包内参考文件），沿用 zcode / WorkBuddy 的 SKILL.md 约定（YAML frontmatter `name/description/version` + Markdown 正文）。
- **技能池目录**：默认 `backend/data/skills`（`agent_settings.skills_dir` 可改）；种子包来自 `backend/seed_skills`，首次启动/重扫时幂等复制。
- **启停**：`_store.json` 的 `disabled` 列表；orchestrator 经 `_EnabledSkillLoader` 在注入侧过滤——**停用对 Agent 真实生效**（v1.16.1 修复项，此前仅界面开关）。
- **全局开关**：`enable_skills` 默认 `False`；未启用或目录不存在时不注入技能池。
- **专家级白名单**（`agent_specs.skills`）：`NULL` = 跟随全局技能池；空数组 = 显式关闭；非空 = 只注入名单内的技能包。

### 16.2 专家团文件（Expert Packs）

- 每位专家可导出为 `backend/data/expert-packs/<name>.md`（frontmatter：`name/label/emoji/color/description/keywords/tools/sort`，正文即角色提示词）；目录首次创建时从 `backend/seed_experts` 复制种子（幂等）。
- 导入为 upsert：新建行 `is_builtin=False`；已存在则更新展示字段，`is_builtin/enabled/model_id/skills` 保持原值；**工具白名单取并集**（导入不缩小白名单，模块预置与界面勾选授予的工具不因导入旧文件丢失）；导入/导出后调用 `invalidate_team_cache()` 立即生效。
- 一致性比对：文件与数据库行逐字段 diff（显示名/角色提示词/关键词/工具/图标/颜色/描述/排序），界面列出「差在哪」。
- 「恢复出厂」= `reset_from_seed()` 从 `seed_experts` 重新覆盖复制种子文件（不影响数据库中的自定义专家）。

## 17. MCP 工具接入（v1.12.0）

- **定位**：最小 stdio 客户端（ADR-002 口径）——只做本地命令起的 MCP Server 的 stdio 传输；HTTP/SSE 传输留待需要时再接，避免为一个协议引入 SDK 依赖。
- **协议**：换行分隔的 JSON-RPC 2.0，握手 `initialize` → `notifications/initialized` → `tools/list` → `tools/call`（协议版本 `2024-11-05`）。
- **托管**：每个 Server 一个子进程，同步请求/响应经进程内锁串行化；暴露给 Agent 的工具为 async 包装（`asyncio.to_thread`），不阻塞事件循环。
- **工具命名**：`mcp_<服务器名>__<工具名>`；服务器名禁止包含 `__`（与工具前缀切分冲突，防止误删其他服务器的工具）；断开/重连/删除时按前缀清理旧注册，不在注册表残留陈旧 schema。
- **只读判定（readOnlyHint）**：以工具自带的 `annotations.readOnlyHint` 为**唯一放行依据**——缺省视为可写；只读咨询模式下未声明该注解的工具保守拒绝（宁可少用不可越权）。名称启发式（write/create 之类）可被任意命名绕过，不作放行依据。
- **超时**：单次工具调用 30 秒（`CALL_TIMEOUT`），握手 15 秒（`START_TIMEOUT`）；等待响应超时会终止子进程并报错，避免阻塞在读线程上的资源泄漏。
- **持久化与安全**：Server 清单存 `mcp_servers` 表（env 含敏感值，仅本机库、禁入 git）；管理接口 env 一律脱敏为 `***` 不回显；单个服务器启动失败不拖垮宿主，状态与错误回写数据库供界面排查。
