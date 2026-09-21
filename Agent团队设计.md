# Agent 团队设计（AgentScope 演进）

> 目标：把「个人工作台」从工具型系统演进为**商业地产领域多 Agent 协同团队**，
> 覆盖招商、运营、企划、工程物业、网络安全、信息化财务六大业务域 + 综合协调管家。
> 基于 AgentScope 2.0（`agentscope==2.0.7.post1`）构建，嵌入现有 FastAPI 后端，无需重构。

## 1. 总体架构

```
前端 AI 助手页（Vue）                    现有四大页面（任务/日程/笔记/概览）
      │  POST /api/assistant/chat               │
      ▼                                       │
┌───────────────────────────────────────────┐ │
│ 编排器 orchestrator.py                      │ │
│  ① 路由：指定专家 > 关键词规则 > LLM 结构化调度 > 默认管家 │ │
│  ② 按 (会话, 专家) 缓存 Agent 实例           │ │
│  ③ 工具轨迹 contextvar 收集 → 会话持久化      │ │
└──────────────┬────────────────────────────┘ │
               ▼                              ▼
┌─────────────────────────────┐   ┌─────────────────────┐
│ 7 位领域专家（ReActAgent）     │   │ 共享业务工具 tools.py  │
│ · 商业地产管家（默认/协调）      │──▶│ query/create/complete │
│ · 招商专员 · 运营专员          │   │ task · schedule ·    │
│ · 企划策划 · 工程物业          │   │ note · stats          │
│ · 网络安全 · 信息化财务         │   └─────────┬───────────┘
└──────────────┬──────────────┘             ▼
               ▼                       SQLite（SQLAlchemy 参数绑定）
        AgentScope 模型层（OpenAI 兼容）
        ModelScope / 硅基流动 / DeepSeek / 火山 / Ollama
```

- **AgentScope 作为库嵌入**，不是平台替代：Agent 模型、工具注册、ReAct 循环、结构化输出由框架提供；路由与持久化由我们自己编排，保持轻量、可控、可测。
- 生产形态不变：仍由 FastAPI 在 8000 端口单端口托管，双击 `启动工作台.bat` 启动。

## 2. 团队名册（7 位专家）

| 标识 | 中文名 | 职责 | 工具范围 |
| --- | --- | --- | --- |
| `realestate` | 商业地产管家 | 资产管理全景分析、租约/收缴/坪效解读；团队总协调与日程任务安排入口（默认接洽人） | 全查询 + 建任务/日程/笔记/完成任务 |
| `leasing` | 招商专员 | 品牌引进、业态组合、租决条件（租金/免租/装补/递增）、招商跟进 | 查询 + 建任务/日程/笔记 |
| `operations` | 运营专员 | 客流/销售额/收缴率/坪效解读、商户经营辅导、营运安排 | 查询 + 建任务/日程/完成任务 |
| `marketing` | 企划策划 | 营销活动策划、档期排期、物料预算清单、会员营销 | 查询 + 建任务/日程/笔记 |
| `property` | 工程物业工程师 | 设备设施维保、能耗管理、巡检整改、外包管理 | 查询 + 建任务/日程 |
| `security` | 网络安全工程师 | 漏洞整改跟踪、等保合规、安全检查清单、办公终端防护 | 查询 + 建任务/笔记 |
| `it_finance` | 信息化财务分析师 | **核心专家**：报表解读、凭证核对、预算费用分析、税务提醒、信息化建议 | 全查询 + 全部写入 |

每位专家拥有：
- **角色提示词**（`team.py` 中 AgentSpec.system_prompt）：中文、结论先行、金额日期严谨、不越界；
- **公共工作规范**（COMMON_RULES）：先查数据再作答、口头安排落成任务/日程/笔记、创建后告知用户；
- **收敛后的工具集**：写入类工具按职责裁剪（如网络安全不建日程、工程物业不写笔记），控制操作面。

## 3. 路由策略（先专后宽，四级兜底）

1. **用户指定**：前端可选择某位专家或「智能路由」；
2. **关键词规则**（离线可用，`ROUTE_KEYWORDS`）：如「租约」→ 招商、「凭证」→ 信息化财务、「漏洞」→ 网络安全；
3. **LLM 结构化调度**：关键词未命中时，调度员 Agent 用 `structured_schema` 输出 `{agent, reason}`；
4. **默认管家**：以上均失败 → 商业地产管家（综合协调，可处理跨域请求）。

策略取舍：显式两步「路由→执行」而非嵌套多 Agent（leader 再调 worker），更省 token、延迟更低、执行轨迹清晰可展示；`routed_by` 字段回传前端展示本次由谁应答。

## 4. 共享业务工具（tools.py）

8 个工具直接操作工作台数据，全部经 SQLAlchemy 参数绑定：

| 工具 | 说明 |
| --- | --- |
| `query_tasks` / `create_task` / `complete_task` | 任务查询（状态/优先级/关键词/逾期过滤）、新建、完成 |
| `query_schedules` / `create_schedule` | 日程按日期范围查询、新建（校验结束>开始） |
| `search_notes` / `create_note` | 笔记关键词/标签搜索、新建 |
| `workbench_stats` | 概览统计（各状态任务数、逾期、今日日程、笔记数） |

要点：
- 工具签名带中文参数说明，注册时生成显式 JSON Schema（`build_tools`），对模型友好、可离线测试；
- 出错返回 `{"ok": False, "error": "..."}` 而非抛异常，便于模型自我纠错重试；
- 每轮执行轨迹通过 contextvar 收集（`tool_trace`），持久化到会话记录并回传前端「🔧 工具调用」折叠区展示，做到可审计。

## 5. 会话与记忆

- 会话消息存 `chat_messages` 表（session_id / role / agent_name / content / trace）；
- 进程内按 `(session_id, agent_name)` 缓存 Agent 实例，多轮对话上下文连续；
- 重启后按历史最近 12 条重建会话上下文（`observe` 注入），聊天记录在界面可回看；
- 记忆策略当前为「最近 N 条注入」+ 模型上下文，未启用长期记忆后端（AgentScope 2.0 提供 ReMe/Mem0，列为后续增强）。

## 6. 模型接入（一次性配置）

统一走 **OpenAI 兼容协议**。**推荐在「AI 助手」页点「⚙ 设置」直接配置**（运行时配置，存 `agent_settings` 表、重启不丢、无需手改文件）；也可写 `backend/.env` 作为兜底：

```ini
LLM_BASE_URL=https://api-inference.modelscope.cn/v1
LLM_API_KEY=sk-xxxxxxxx
LLM_MODEL=Qwen/Qwen3.5-122B-A10B
```

设置页已内置 7 个供应商预设（ModelScope / 硅基流动 / DeepSeek / 火山方舟 / DashScope 兼容 / Ollama / 自定义），选供应商自动填充 Base URL 与候选模型，并提供：
- **上下文长度 / 最大回复 Token / 温度**（生成参数，映射到 OpenAIChatModel.Parameters）
- **多模态开关**：开启后「测试连通性」附带发送一张红色图片，验证模型视觉能力（AgentScope 2.0.7 块模型无图片输入块，多模态测试走 OpenAI 兼容直连）
- **Agent 工作模型**：最大工具轮次（ReAct max_iters，默认 5）
- **记忆管理**：启用会话记忆 + 注入历史条数（默认 12）

配置变更会自动清空模型/会话缓存，下次请求即生效（无需重启服务）。

| 渠道 | LLM_BASE_URL | 示例模型 |
| --- | --- | --- |
| ModelScope 推理 | `https://api-inference.modelscope.cn/v1` | `Qwen/Qwen3.5-122B-A10B` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3.2` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-xxx`（控制台开通） |
| DashScope 兼容模式 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b` |

未配置时：`GET /api/assistant/team` 正常返回名册（`llm.configured=false`），聊天接口返回指引信息，**主工作台功能完全不受影响**。

## 7. 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/assistant/team` | 团队名册 + 模型状态 + 运行时配置（脱敏）+ 供应商预设 |
| `GET /api/assistant/config` | 读取 AI 助手配置（api_key 已脱敏） |
| `PUT /api/assistant/config` | 保存配置（部分更新合并；api_key 留空/掩码保留现值），自动清缓存生效 |
| `POST /api/assistant/config/test` | 测试连通性（可选 `include_image` 附带多模态图片识别） |
| `POST /api/assistant/chat` | `{session_id, message, agent?}` → `{ok, reply, agent, routed_by, trace}` |
| `GET /api/assistant/history/{session_id}` | 会话历史（时间正序） |

## 8. 测试

后端 pytest（`tests/test_assistant.py`，14 例）覆盖：7 专家名册、关键词路由、模型工厂（未配置返回 None / 假配置可构造）、无密钥聊天分支、业务工具增删改查往返、工具越界裁剪、历史持久化。全部离线运行，不消耗模型额度。

## 9. 演进路线图

- [x] **v1（本期）**：7 专家 + 路由 + 8 工具 + 对话页 + 会话持久化
- [ ] v2：LLM 路由效果增强（few-shot 调度）、工具结果摘要压缩、会话标题与归档
- [ ] v3：长期记忆（ReMe/Mem0）、笔记 RAG 问答（AgentScope RAG Service）
- [ ] v4：接入企业 ERP/业务系统浏览器自动化为团队工具（凭证核对自动取数、报表自动拉取）；定时 Agent（每日工作日报、申报提醒）
- [ ] v5：AgentScope Agent Service 多租户/渠道（飞书/钉钉）——仅当需要团队共享时考虑

## 10. 风险与对策

- **版本漂移**：`requirements.txt` 锁定 `agentscope==2.0.7.post1`；AgentScope 0.x→1.0→2.0 经历破坏性演进，升级前先跑 pytest。
- **无密钥/断网**：模型功能降级为「未配置提示」，主功能不受影响。
- **工具执行权限**：Agent 构造时以 `AgentState(permission_context=PermissionContext(mode=PermissionMode.BYPASS))` 自动放行工具调用（个人助手 + 工具白名单非破坏性场景）；若未来开放删除类工具，应改回逐次确认（HITL）。
- **LLM 直接写数据**：写入工具参数白名单校验 + 返回可审计轨迹；删除类操作未开放给 Agent（仅任务可 complete，物理删除仍需在页面操作）。
- **Windows 嵌套事件循环**：全部走 async 路由 await，不在同步上下文 `asyncio.run`。
