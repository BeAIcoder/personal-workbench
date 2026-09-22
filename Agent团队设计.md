# Agent 团队设计（配置化版）

> 目标：把「个人工作台」从工具型系统演进为**可配置的多 Agent 协同团队**。
> 内置商业地产领域 7 位专家（出厂定义），全部定义存数据库 `agent_specs` 表，
> 界面上可增删改、启停、改关键词、绑定模型、调路由优先级，保存即生效。
> 基于 AgentScope 2.0（`agentscope==2.0.7.post1`）构建，嵌入现有 FastAPI 后端。

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
└──────────────┬──────────────┘   │ note · stats          │
               ▼                  └─────────┬───────────┘
        AgentScope 模型层                     ▼
        openai / anthropic 协议        SQLite（SQLAlchemy 参数绑定）
        （专家可绑定各自模型，空则全局激活模型）
```

- **AgentScope 作为库嵌入**，不是平台替代：Agent 模型、工具注册、ReAct 循环、结构化输出由框架提供；路由与持久化由我们自己编排。
- 生产形态不变：仍由 FastAPI 在 8000 端口单端口托管，双击 `启动工作台.bat` 启动。

## 2. 数据模型（agent_specs 表）

| 字段 | 说明 |
| --- | --- |
| `name` | 英文标识，唯一，格式 `^[a-z][a-z0-9_]*$`（路由/存储用） |
| `display_name` / `emoji` / `color` | 界面展示名与头像配色 |
| `description` | 一句话职责（供 LLM 路由名册与前端展示） |
| `role_prompt` | 角色系统提示词（编排器统一拼接 COMMON_RULES 公共工作规范） |
| `keywords` | 路由关键词数组（JSON），最多 50 个 |
| `tools` | 工具白名单（JSON）；NULL = 默认查询工具池 |
| `skills` | 专家级技能白名单（JSON）；NULL = 跟随全局技能池，空数组 = 显式关闭 |
| `model_id` | 绑定模型（逻辑引用 `provider_models.id`）；NULL = 跟随全局激活模型 |
| `enabled` | 停用后不参与路由、不出现在名册 |
| `sort` | 排序，**兼作关键词同分时的路由优先级，小者优先** |
| `is_builtin` | 内置专家标记（恢复出厂用） |

**seed 机制**：首次启动（`agent_specs` 为空）由 `seed_agent_specs()` 灌入内置 7 专家，幂等、只在空表执行一次，之后不再覆盖用户改动；该 seed 属配置数据，不受 `SEED_ON_STARTUP` 控制。表被清空或数据库不可用时，`load_team()` 回退代码内置 TEAM 定义，保证离线/异常场景可用。

## 3. 内置团队名册（7 位专家）

| 标识 | 中文名 | sort | 职责 | 工具范围 |
| --- | --- | --- | --- | --- |
| `security` | 网络安全工程师 | 0 | 漏洞整改跟踪、等保合规、安全检查清单、办公终端防护 | 查询 + 建任务/笔记 |
| `leasing` | 招商专员 | 1 | 品牌引进、业态组合、租决条件（租金/免租/装补/递增）、招商跟进 | 查询 + 建任务/日程/笔记 |
| `operations` | 运营专员 | 2 | 客流/销售额/收缴率/坪效解读、商户经营辅导、营运安排 | 查询 + 建任务/日程/完成任务 |
| `marketing` | 企划策划 | 3 | 营销活动策划、档期排期、物料预算清单、会员营销 | 查询 + 建任务/日程/笔记 |
| `property` | 工程物业工程师 | 4 | 设备设施维保、能耗管理、巡检整改、外包管理 | 查询 + 建任务/日程 |
| `it_finance` | 信息化财务分析师 | 5 | **核心专家**：报表解读、凭证核对、预算费用分析、税务提醒、信息化建议 | 全查询 + 全部写入 |
| `realestate` | 商业地产管家 | 6 | 资产管理全景分析；团队总协调与默认接洽人 | 全查询 + 建任务/日程/笔记/完成任务 |

每位专家拥有：角色提示词（中文、结论先行、金额日期严谨、不越界）+ 公共工作规范 COMMON_RULES（先查数据再作答、口头安排落成任务/日程/笔记、创建后告知用户）+ 收敛后的工具集（写入工具按职责裁剪）。

## 4. 路由策略（先专后宽，四级兜底）

1. **用户指定**：前端点选某位专家或「智能路由」，`routed_by="指定"`；
2. **关键词规则**（离线可用）：统计每位启用专家的关键词在消息中的命中数，得分最高者胜出；**同分时 sort 小者胜**（稳定排序，先专后宽），`routed_by="关键词"`；
3. **LLM 结构化调度**：关键词未命中且模型可用时，调度员 Agent 读名册文本做结构化输出 `{agent, reason}`；输出不在名册或任何异常都回退，`routed_by="AI 调度"`；
4. **默认专家**：以上均失败 → `realestate`（被删/停用则取 sort 最小者），`routed_by="默认"`。

策略取舍：显式两步「路由→执行」而非嵌套多 Agent，更省 token、延迟更低、轨迹清晰；`routed_by` 回传前端展示本次由谁应答。

## 5. 缓存与失效

- `load_team()`：进程内 60 秒 TTL 缓存；`force=True` 强制刷新（路由预览用）。
- 任何专家增删改、供应商/模型变更、工作参数保存，路由层统一调用 `invalidate_team_cache()` + `orchestrator.invalidate()`（清模型实例与各会话 Agent 缓存），**保存即生效，无需重启**。
- Agent 实例缓存 key 为 `(session_id, 专家, 模式, model_id)`：专家绑定模型变化时自动重建。

## 6. 工作模式

| 模式 | 说明 |
| --- | --- |
| `standard` 标准执行 | 专家全部工具，自动放行（默认） |
| `readonly` 只读咨询 | 仅查询工具，提示词声明不修改数据（插件也不加载） |
| `deep` 深度研究 | 全部工具 + 工具轮次提到至少 10 + 深度分析提示词 |

## 7. 共享业务工具（tools.py）

8 个工具直接操作工作台数据，全部经 SQLAlchemy 参数绑定：
`query_tasks / create_task / complete_task / query_schedules / create_schedule / search_notes / create_note / workbench_stats`。

要点：显式 JSON Schema 含中文参数说明；出错返回 `{"ok": False, "error": ...}` 便于模型自我纠错；轨迹经 contextvar 收集持久化，前端折叠展示可审计；**删除类不开放**。

## 8. 前端管理界面

`AssistantView.vue` 容器（约 235 行）+ `src/components/assistant/` 五个子组件：SessionList / AgentQuickList / ChatPanel / SettingsDrawer / **AgentTeamPanel**。

设置抽屉新增「专家团队」tab（AgentTeamPanel，约 513 行）：卡片式增删改、启用开关、关键词编辑、绑定模型下拉（跟随全局/各已配置模型）、**路由测试工具**（输入一句话预览命中专家与命中词）、**恢复出厂**按钮（按 name 覆盖/补插内置专家，不动自定义专家）。

## 9. 与旧版（写死定义）的差异

| 维度 | 旧版 | 现版 |
| --- | --- | --- |
| 专家定义 | 写死在 `team.py` TEAM | 存 `agent_specs` 表，TEAM 仅作 seed 数据源与兜底 |
| 增删改 | 改代码重启 | 界面操作，保存即生效（缓存失效） |
| 路由关键词 | 同上写死 | 每专家可编辑；sort 可调优先级；route-test 可预览 |
| 模型 | 全团队共用一个激活模型 | 每专家可绑定独立模型（`model_id`），空则跟随全局 |
| 技能 | 全局开关 | 专家级白名单：NULL 跟随全局 / 空数组显式关闭 |
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

- 会话元数据存 `agent_sessions`（标题/时间），消息存 `chat_messages`（role / agent_name / content / trace / thinking）；
- 多会话管理：新建/重命名/删除（删除同时清理消息与进程内 Agent 缓存）；
- 重启后按历史最近 N 条（`history_inject`，默认 12）`observe` 重建上下文；记忆可整体关闭。

## 12. 模型接入

统一走 OpenAI 兼容协议（另支持 anthropic 协议）。**推荐在「AI 助手 → ⚙ 设置 → 模型」配置**（供应商/模型两级，存库，重启不丢）；也可写 `backend/.env` 的 `LLM_*` 兜底。内置 7 个供应商预设，选预设自动填充 base_url 与候选模型；连通性 / 工具调用 / 多模态（小红图识色）三类测试内联展示。配置变更自动清缓存，下次请求即生效。

| 渠道 | LLM_BASE_URL | 示例模型 |
| --- | --- | --- |
| ModelScope 推理 | `https://api-inference.modelscope.cn/v1` | `Qwen/Qwen3.5-122B-A10B` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3.2` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-xxx`（控制台开通） |
| DashScope 兼容模式 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b` |

未配置时：`GET /api/assistant/team` 正常返回（`llm.configured=false`），聊天返回未配置指引，**主工作台功能完全不受影响**。

## 13. 测试

后端 pytest 中 Agent 相关覆盖（`test_assistant*.py` / `test_agent_specs.py` 等）：专家 CRUD 与标识校验、关键词路由打分与同分优先级、route-test、恢复出厂、seed 幂等、团队缓存失效、模型工厂、无密钥聊天分支、工具往返、历史持久化。全部离线运行，不消耗模型额度。

## 14. 风险与对策

- **版本漂移**：`requirements.txt` 锁定 `agentscope==2.0.7.post1`；升级前先跑 pytest。
- **无密钥/断网**：模型功能降级为「未配置提示」，主功能不受影响。
- **工具执行权限**：Agent 以 `PermissionMode.BYPASS` 自动放行（个人助手 + 白名单非破坏性工具）；若未来开放删除类工具，应改回逐次确认（HITL）。
- **LLM 直接写数据**：写入工具参数校验 + 可审计轨迹；删除类操作未开放（物理删除仅在页面操作）。
- **Bash / 插件**：默认关闭的实验能力，开启前知悉本机执行风险（详见 注意事项.md）。
- **Windows 嵌套事件循环**：全部走 async 路由 await，不在同步上下文 `asyncio.run`。
