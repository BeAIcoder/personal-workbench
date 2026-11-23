# API 接口说明

- Base URL：`http://127.0.0.1:8000/api`
- 数据格式：JSON（UTF-8）；时间一律 ISO 8601（如 `2026-09-08T09:30:00`，也接受 `2026-09-08` 日期形式）
- 在线调试：启动后访问 <http://127.0.0.1:8000/docs>（Swagger UI）
- 错误约定：参数校验失败返回 `422`（body 含 `detail`；**日期查询参数格式错误也返回 422**，不穿透成 500）；**路径 `{id}` 超过 SQLite int64 范围同样返回 422**（`utils.DbId` 入口拦截 + 全局 `OverflowError` 兜底，不出 500 堆栈）；资源不存在返回 `404`（`{"detail": "..."}`）；删除成功返回 `204` 无内容（例外：AI 助手的 sessions/agents、知识与记忆的 memories、MCP 的 servers 删除返回 200 + JSON，如 `{"ok": true}`）
- 安全护栏（无鉴权本机工作台的统一口径，详见下文「安全护栏」一节）：全局请求体上限 `128MB` 超限返回 `413`；`/api` 状态变更方法与 discover 类 GET 的 Origin 不可信返回 `403`；上传超限/行数超限统一返回 `400`，均带中文 `detail`
- 分页约定：列表接口统一返回 `{"items": [...], "total": 总数, "page": 页码, "page_size": 每页条数}`

## 系统

### GET /api/health
健康检查。同时支持 `HEAD`（供容器/负载均衡探活，无响应体）。
```json
{ "status": "ok", "app": "个人工作台", "version": "1.16.1" }
```

## 安全护栏（全局中间件，先于路由生效）

| 护栏 | 触发条件 | 行为 |
|------|----------|------|
| 全局请求体上限 | 任何请求体超过 `128MB`（`MAX_REQUEST_BODY_BYTES`） | 声明超大 `Content-Length` 的请求进路由前直接 `413`；chunked 请求体在 receive 侧累计，超限即 `413` 并中断读取（`{"detail": "请求体超过 … 字节上限"}`） |
| Origin 校验 | `/api` 下状态变更方法（`POST`/`PUT`/`PATCH`/`DELETE`）与路径含 `discover` 段的 GET，且带了 Origin | Origin 属于 `settings.cors_origins` 配置 / 回环开发来源（localhost·127.0.0.1·::1 任意端口）/ 运维配置 `HOST`（端口与 `PORT` 一致）的同源来源 → 放行；其余 `403` `{"detail": "请求来源不被信任"}`。不再用「Origin 与请求 Host 自证同源」放行（避免 DNS rebinding 绕过）；无 Origin 的 CLI/探针放行；CORS 预选（OPTIONS）不拦 |
| 上传限额 | 各上传路由经共享 `read_upload_limited` 分块读取，超端点限额即中断 | `400`（`UploadTooLargeError` 全局收口），落盘前拒绝、不留半成品 |
| 导入行数护栏 | Excel/CSV 类导入的数据行数超过 `MAX_IMPORT_ROWS = 200000` | `400`，解析阶段即中止，不落库、不留半成品 |
| int64 ID 兜底 | 任意 `/{id}` 路径越界 | `422` `{"detail": "ID 超出可接受范围"}` |
| 非回环绑定守卫 | 服务用非回环地址（如 `--host 0.0.0.0`）启动，且 `ALLOW_NON_LOOPBACK` 未开启 | 除 `GET/HEAD /api/health` 外所有 `/api` 请求返回 **503** `{"detail": "服务绑定在非回环地址（无鉴权），已默认拒绝 API 访问；如确需局域网访问，请在 backend/.env 设置 ALLOW_NON_LOOPBACK=1"}`；`scope["server"]` 是监听套接字地址，与 `.env` 的 `HOST` 可以不同 |
| CORS | 浏览器跨源读取 | 默认只允许本机开发来源；局域网前端必须把完整来源（含协议/端口）写入 `CORS_ORIGINS`（逗号分隔），否则读取被浏览器拦截（写操作另受 Origin 校验 403） |

> 安全模型：本应用面向**本机单人**设计，**没有登录鉴权**。`ALLOW_NON_LOOPBACK=1` 只是关闭 503 守卫，不会增加鉴权；开启前必须自行加 VPN/反向代理鉴权与防火墙。
>
> 模型调用（openai / anthropic 双协议）统一显式 `120` 秒 SDK 超时（`LLM_TIMEOUT_SECONDS`），单用户工作台下避免一次挂起长时间占住请求。

## 任务管理 /api/tasks

任务字段：`title` 标题（必填）、`description` 描述、`status` 状态（`todo` 待办 / `in_progress` 进行中 / `done` 已完成）、`priority` 优先级（`low` / `medium` / `high` / `urgent`）、`due_date` 截止时间（可空）、`category` 分类、`completed_at` 完成时间（系统维护：状态改为 done 时写入，改回时清空）、`created_at` / `updated_at`。

### GET /api/tasks
| 参数 | 说明 |
| --- | --- |
| `status` / `priority` / `category` | 按状态 / 优先级 / 分类过滤 |
| `overdue` | `true` 时只返回已逾期（未完成且截止时间已过） |
| `q` | 关键词，匹配标题或描述 |
| `due_before` / `due_after` | 截止时间区间（含边界），如 `2026-09-07`；非法格式返回 422 |
| `sort` | `created_desc`（默认）/ `created_asc` / `due_asc` / `due_desc` / `priority_desc`（紧急在前） |
| `page` / `page_size` | 分页，默认 1 / 10，最大 100 |

```
GET /api/tasks?status=todo&priority=urgent&sort=due_asc&page=1&page_size=10
```
```json
{
  "items": [
    { "id": 1, "title": "整理本月增值税进项发票并认证", "description": "…",
      "status": "in_progress", "priority": "urgent", "due_date": "2026-09-08T17:00:00",
      "category": "税务申报", "completed_at": null,
      "created_at": "2026-09-07T12:00:00", "updated_at": "2026-09-07T12:00:00" }
  ],
  "total": 1, "page": 1, "page_size": 10
}
```

### POST /api/tasks（返回 201）
```json
{ "title": "核销备用金", "priority": "high", "due_date": "2026-09-12T17:00:00", "category": "日常核算" }
```

### GET /api/tasks/{id}
任务详情，不存在返回 404。

### PUT /api/tasks/{id}
部分更新：只传需要修改的字段。
```json
{ "status": "done" }
```

### DELETE /api/tasks/{id}（返回 204）

## 日程管理 /api/schedules

日程字段：`title`（必填）、`description` 备注、`location` 地点、`start_time` / `end_time`（必填，结束必须晚于开始）、`all_day` 是否全天、`color` 标记颜色（如 `#409EFF`）、`created_at` / `updated_at`。

### GET /api/schedules
| 参数 | 说明 |
| --- | --- |
| `start` / `end` | 时间范围（含边界），按「日程与区间有交集」匹配，适合日历整月拉取；非法格式返回 422 |
| `q` | 关键词，匹配标题 / 地点 / 备注 |
| `page` / `page_size` | 分页，默认 1 / 100，最大 200 |

```
GET /api/schedules?start=2026-09-01&end=2026-09-30
```

### POST /api/schedules（返回 201）
```json
{ "title": "月度经营分析会议", "start_time": "2026-09-07T09:30:00", "end_time": "2026-09-07T11:00:00", "location": "三楼会议室", "color": "#409EFF" }
```

### GET / PUT / DELETE /api/schedules/{id}
同任务模块；PUT 只传需要修改的字段，结束时若 `end_time <= start_time` 返回 422。

## 笔记管理 /api/notes

笔记字段：`title`（必填）、`content` 内容、`tags` 标签数组（自动去空格去重，最多 10 个、单个最长 20 字）、`pinned` 是否置顶、`created_at` / `updated_at`。列表默认置顶在前、更新时间倒序。

### GET /api/notes
| 参数 | 说明 |
| --- | --- |
| `q` | 关键词，匹配标题或内容 |
| `tag` | 按标签过滤 |
| `pinned` | `true` / `false` |
| `page` / `page_size` | 分页，默认 1 / 20，最大 100 |

### GET /api/notes/tags
全部标签去重列表：`{ "tags": ["税务", "报销", "结账"] }`

### POST /api/notes（返回 201）
```json
{ "title": "报销审核要点", "content": "…", "tags": ["报销", "发票"], "pinned": false }
```

### GET / PUT / DELETE /api/notes/{id}
同任务模块；PUT 只传需要修改的字段。

## 全局搜索

### GET /api/search?q=关键词
一次检索任务（标题+描述）、日程（标题+地点+备注）、笔记（标题+内容），各取前 5 条；`snippet` 为关键词上下文片段。
```json
{
  "q": "增值税",
  "total": 3,
  "tasks":    [ { "id": 1, "type": "task", "title": "增值税申报准备", "snippet": "…", "time": "2026-09-08T17:00:00" } ],
  "schedules":[ { "id": 1, "type": "schedule", "title": "增值税申报会议", "snippet": "…", "time": "2026-09-08T09:00:00" } ],
  "notes":    [ { "id": 3, "type": "note", "title": "申报注意事项", "snippet": "…增值税申报截止 15 日…", "time": "2026-09-07T12:00:00" } ]
}
```

## 工作台统计

### GET /api/dashboard/summary
概览页一次取全：
```json
{
  "task":   { "total": 10, "todo": 5, "in_progress": 2, "done": 3, "overdue": 1, "today_due": 2, "completion_rate": 30.0 },
  "schedule": { "today_count": 2, "upcoming_7d": 5 },
  "note":   { "total": 5, "week_added": 5 },
  "upcoming_tasks":  [ /* 未完成任务，按截止时间升序，最多 6 条，结构同 TaskOut */ ],
  "today_schedules": [ /* 今天有交集的日程，按开始时间升序；默认最多 100 条 */ ],
  "schedule": { "today_count": 2, "today_schedule_total": 138, "today_schedules_truncated": true, "upcoming_7d": 5 },
  "recent_notes":    [ /* 最近更新的 5 条笔记 */ ],
  "trend_7d": [ { "date": "2026-09-01", "count": 0 }, … ]
}
```

## AI 助手 /api/assistant

### 对话

#### POST /api/assistant/chat（非流式）
```json
{
  "session_id": "s_abc123",           // 前端生成并保持稳定，6~64 位
  "message": "工作台有多少逾期任务？",
  "agent": "it_finance",               // 可选：指定专家标识；留空自动路由
  "mode": "standard",                  // 可选：standard / readonly / deep
  "attachments": [ { "name": "报表.csv", "path": "<上传接口返回的 path>" } ]
}
```
成功：
```json
{
  "ok": true, "session_id": "s_abc123", "reply": "…", "thinking": "",
  "agent": { "name": "it_finance", "label": "信息化财务分析师", "emoji": "💼", "color": "#E6A23C" },
  "routed_by": "关键词", "mode": "standard",
  "trace": [ { "tool": "query_tasks", "args": {}, "result": {} } ]
}
```
- 模型未配置：200 返回 `{ "ok": false, "need_llm_config": true, "message": "模型未配置：…" }`
- 带图附件而当前模型未开启视觉能力：返回 `400` `{ "detail": "当前模型不支持图片理解，请在设置中为该模型开启视觉能力或切换视觉模型" }`
- **运行时异常（模型网络/鉴权等）：返回 `502` `{ "detail": "助手服务暂时不可用，请稍后重试" }`**（服务端日志记录完整堆栈，不回传内部细节）

#### POST /api/assistant/chat/stream（SSE 流式）
入参同上。响应 `Content-Type: text/event-stream`，每条 `data: {json}`，流末 `data: [DONE]`。事件类型：

| type | 含义 |
| --- | --- |
| `meta` | 路由结果（agent / routed_by / mode），先于正文 |
| `delta` | 正文增量 `{text}` |
| `thinking_delta` | 思考过程增量 `{text}` |
| `tool_start` | 工具调用开始 `{name}` |
| `done` | 最终消息（reply / thinking / trace，结构同非流式成功体） |
| `error` | 失败（模型未配置时带 `need_llm_config: true`） |

流执行异常时服务端会把用户消息与一条 assistant 侧错误消息都落库，保持历史一问一答成对。前端用 AbortController 可随时中止。

#### GET /api/assistant/history/{session_id}
会话历史（时间正序），`limit` 默认 50（1~200）：`{ "session_id": "…", "messages": [ { "id", "role", "agent_name", "agent_label", "content", "thinking", "trace", "created_at" } ] }`

#### 会话管理
| 接口 | 说明 |
| --- | --- |
| `GET /api/assistant/sessions?limit=50&archived=` | 会话列表（按更新时间倒序，含 msg_count / archived）；`archived` 传 `true`/`false` 按归档状态过滤，不传返回全部 |
| `POST /api/assistant/sessions` | 新建会话 `{session_id, title?}` |
| `PUT /api/assistant/sessions/{id}` | 重命名 `{session_id, title}` |
| `DELETE /api/assistant/sessions/{id}` | 删除会话（含全部消息） |
| `POST /api/assistant/sessions/{id}/archive` | 归档会话 → `{"ok": true}`；不存在返回 404 |
| `POST /api/assistant/sessions/{id}/unarchive` | 恢复会话 → `{"ok": true}`；不存在返回 404 |
| `GET /api/assistant/sessions/{id}/export` | 导出 Markdown 附件（`text/markdown; charset=utf-8`，`Content-Disposition: attachment; filename="session-{id}.md"`）；不存在返回 404 |

### 专家团队配置

专家字段：`name` 英文标识（`^[a-z][a-z0-9_]*$`，唯一）、`display_name`、`emoji`、`color`、`description`、`role_prompt`、`keywords`（最多 50 个）、`tools` / `skills`（数组或 null）、`model_id`（绑定模型，null 跟随全局激活模型）、`enabled`、`sort`（0~9999，路由优先级，小者优先）。响应另含 `id`、`is_builtin`、`created_at` / `updated_at`。

| 接口 | 说明 |
| --- | --- |
| `GET /api/assistant/agents` | 全部专家（含停用，按 sort 排序）：`{ "agents": [...] }` |
| `POST /api/assistant/agents` | 新增专家，返回 201 `{ "agent": {...} }`；name 重复返回 400 |
| `PUT /api/assistant/agents/{id}` | 部分更新；显式传 `null` 可清空 `model_id` / `tools` / `skills`；不存在 404 |
| `DELETE /api/assistant/agents/{id}` | 删除（内置专家也允许删，前端自行警告）：`{ "ok": true, "deleted": "name" }` |
| `POST /api/assistant/agents/route-test` | 关键词路由预览 |
| `POST /api/assistant/agents/reset` | 内置专家恢复出厂（按 name 覆盖/补插，不影响自定义专家）：`{ "ok": true, "restored": [...] }` |

route-test 入参与返回：
```json
// 请求
{ "text": "帮我核对这张凭证分录" }
// 返回
{
  "routed": "it_finance",
  "matched_by": "关键词",                 // 或 "默认"
  "matches": [ { "name": "it_finance", "display_name": "信息化财务分析师", "hits": ["凭证", "分录"], "score": 2 } ]
}
```
专家配置变更后团队缓存与已构建 Agent 立即失效，无需重启。

#### GET /api/assistant/team（兼容端点）
团队名册 + 模型状态 + 配置（脱敏）+ 供应商/模型 + 工作模式。**注意**：只返回启用中的专家、按 sort 排序，专家条目含 `id / model_id / keywords / enabled / sort / is_builtin` 字段。
```json
{
  "llm": { "configured": true, "protocol": "openai", "provider": "…", "source": "provider", "base_url": "…", "model": "…" },
  "config": { /* api_key 已脱敏为 ••••••xxxx，附 api_key_set */ },
  "presets": [ /* 33 家供应商预设（含按量/仅交互/交互+API 套餐） */ ],
  "providers": [ /* 供应商及其模型 */ ],
  "models": [ /* 启用中模型扁平列表（快速切换用） */ ],
  "work_modes": { "standard": {...}, "readonly": {...}, "deep": {...} },
  "agents": [ { "name", "label", "emoji", "color", "description", "tools",
                "id", "model_id", "keywords", "enabled", "sort", "is_builtin" } ]
}
```
`llm.source`：模型来源标识，`provider` = 界面配置的供应商激活模型，`env` = .env 兜底（供应商全关/未配置时生效，该模型不在 `models` 下拉里，界面需明示「env 兜底」）。

### 模型与参数配置

#### GET /api/assistant/config
`{ config（脱敏）, presets, providers, models, llm }`。

#### PUT /api/assistant/config
保存 **Agent 工作参数**（模型配置请走供应商/模型接口）。全部可选、部分更新：
`max_iters`（1~20，工具轮次）、`enable_memory`、`history_inject`（0~100）、`enable_skills` / `skills_dir`、`enable_bash`、`enable_plugins` / `plugins_dir`、`reasoning_effort`（`""` 跟随模型 / `off`（旧版「关闭思考」，兼容保留，语义同 `none`）/ `none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max`；白名单外按跟随模型处理）。

#### POST /api/assistant/config/test
```json
{ "config": { /* 可选候选配置 */ }, "provider_id": 1, "model_id": 2, "include_image": true, "test_tools": true }
```
返回 `{ "text": {...}, "tools": {...}, "image": {...} }`（后两项仅在 `text.ok` 为真且对应开关打开时出现）。

- `provider_id`：测试该供应商第一个启用模型
- `model_id`：测试指定模型（用其完整配置含密钥）；工具/图片实测按该模型执行，**明确结论**回写 `supports_tools` / `supports_vision`（来源 `probe`：成功→1、400/422 参数拒绝→0、401/403/404/408/429/5xx 与网络错误保持原值，不把「没测到」当成「不支持」）；不存在/未启用返回 `400`
- **测试当前激活模型**（不传 `config`）：`api_key` 留空或传掩码哨兵 `********`（`••••••` 开头的回显掩码同）时自动回填现值
- **测试自定义候选配置**（传 `config`）：必须显式填写**新的** API Key——空/掩码不会借用当前激活模型的真实密钥，返回 `200` + `{"text": {"ok": false, "error": "请填写新的 API Key：测试自定义配置不会沿用当前激活模型的密钥"}}`；`base_url` 经共享网关校验，非法返回 `400`；自定义配置不对应模型行，不回写能力结论
- `text` 结构：成功 `{ok: true, latency_ms, reply, usage: {input_tokens, output_tokens}}`；失败 `{ok: false, latency_ms, error}`
- `tools`：`{ok, latency_ms, tool_calls, reply, verdict}`，`verdict` ∈ `supported`（明确调用成功）/ `rejected`（SDK `BadRequestError`、`status_code=400/422` 或明确的 `Error code: 400|422`）/ `unknown`（401/429/5xx、网络错误、消息偶含 400 或模型未调用）
- `image`：`include_image=true` 时按协议走 openai 兼容或 anthropic 图片识别测试（红色图片问答，判定回答含「红/red」）；`verdict` 三态同 `tools`，仅 400/422 判 rejected
- **probe 结论是本地覆盖**：回写时该字段同时记入 `manual_fields`，默认目录同步不覆盖实测值；`override_manual=true` 的同步或 `reset-overrides` 可清除覆盖并按最近同步快照恢复。思考探测（`probe/reasoning`）同口径：明确结论回写 `supports_thinking`/`thinking_levels` 时也记 `manual_fields` + 来源 `probe`，全部请求失败不写结论也不记覆盖

#### 供应商 / 模型（两级管理）
| 接口 | 说明 |
| --- | --- |
| `GET /api/assistant/providers` | `{ providers（api_key 不回传，含 api_key_set）, presets }`；presets 每项含 `vision_models`（已知视觉模型清单，供界面预勾选模型的 supports_vision） |
| `POST /api/assistant/providers` | 新增/更新：`{provider_id?, name, protocol?, base_url?, api_key?, preset_key?, models_url?, sync_access_key?, sync_secret_key?, enabled?}`；api_key 空或 `********` 保留现值。`preset_key` 定位同步适配器与能力目录（适配器只认 preset_key）；`models_url` 模型列表地址（仅 http(s)，空 = 无官方列表仅目录兜底）；`sync_access_key`/`sync_secret_key` 为可选管控面凭证（火山 AK/SK）：密封存储、任何 GET 不回传，空/掩码保留现值 |
| `DELETE /api/assistant/providers/{id}` | 删除供应商（含其模型；若为激活模型则取消激活） |
| `POST /api/assistant/providers/{id}/models` | 新增/更新模型：`{model_id?, model, context_size?(4096~2000000), max_tokens?(64~500000), multimodal?, supports_vision?, enabled?, temperature?, note?, supports_thinking?(-1/0/1), thinking_levels?, max_input_tokens?, supports_tools?(-1/0/1), input_modalities?, output_modalities?, default_thinking_level?, thinking_can_disable?, reasoning_style?, api_family?}`；`supports_vision` 为视觉（图片理解）能力，聊天传图需开启。**提交了的能力字段才记入 `manual_fields`**（同步默认不覆盖）——编辑时未变化的字段不要重复提交。`thinking_can_disable` 例外：显式传 `null`（请求体含该键且值为 null）表示恢复「未知」，落库 None 并清旧判定；**不传该键**才表示保留现值（路由仅对显式出现的该字段补回 null，其余 null 字段继续按缺省排除） |
| `DELETE /api/assistant/providers/{id}/models/{mid}` | 删除模型 |
| `POST /api/assistant/models/{id}/activate` | 激活模型（跨供应商，立即生效）；不存在/未启用返回 404 |
| `POST /api/assistant/providers/{id}/sync-models` | 同步供应商在线模型列表（只读目录/管控请求，不发推理）：`{include_disabled?=false, override_manual?=false}`。返回 `{ok, partial, online, source, added, updated, unchanged, skipped[], warnings[], truncated, errors[], synced_at, providers}`；在线不可达按能力目录兜底并标 `partial`；分页到上限标 `truncated`；一条非法记录（缺模型 ID 或记录不是 JSON 对象——`_require_entries`/阿里 entries 入口统一守卫）或写入异常整批回滚（`ok=false` + `errors`，不裸 500）；fetch 阶段适配器未预期缺陷同样结构化失败（错误只记异常类型）；并发同步同一供应商 409。`override_manual` 默认 false：手动能力字段与实测（probe）结论不被覆盖；界面勾选「覆盖手动/实测」后才传 true（勾选后每次同步前弹确认警告） |
| `POST /api/assistant/models/{id}/capabilities/reset-overrides` | 清除手动能力覆盖（`manual_fields`）并按最近同步快照恢复；无快照仅清标记（`applied=false`，下次同步生效）；不存在返回 404 |
| `GET /api/assistant/models` | 启用中模型扁平列表（`id/label/provider/model/multimodal/supports_vision/active` + 能力契约字段 `max_input_tokens/supports_tools/input_modalities/output_modalities/default_thinking_level/thinking_can_disable/api_family/capability` + 思考字段 `supports_thinking/thinking_levels/reasoning_style`，与 providers 视图同口径） |

### 附件与扩展

#### POST /api/assistant/upload
multipart 表单：`session_id`（白名单校验）+ `file`。上限 10MB（超限 400）。扩展名白名单：文本/数据类 `.txt .md .csv .json .log .py .html .xml .yaml .yml .sql`、Excel `.xlsx/.xls`（自动把第一张工作表转 CSV）、图片 `.png .jpg .jpeg .webp .gif`、文档 `.pdf .doc .docx .ppt .pptx`；白名单外返回 400，无扩展名按普通文件放行。返回 `{ "name", "path", "kind"("file"/"csv_converted"/"image"/"document_text"), "size", "note" }`。
- 读取经共享分块限额（`read_upload_limited`）：超限在落盘前即 400，不留任何文件
- xlsx 首表行数（含表头）超过导入行数上限 `200000` 行：上传整体拒绝并清理 xlsx 与半成品 CSV，返回 `400`
- Excel 转 CSV 失败时降级：保留 xlsx 本体，`note` 说明失败原因（既有契约）
- 文档上传保留原文件，`path` 仍指向原件；提取文本写入同会话 `.extracted/<原文件名>.txt`，`kind="document_text"`，`note` 标明提取字数、旧版转换与截断。聊天只传 `{name,path}`，编排层自动读取 sidecar 注入模型，无需视觉模型或 Bash。
- PDF/DOCX/PPTX 直接提取文字与表格；DOC/PPT 需本机 LibreOffice（可用 `SOFFICE_PATH` 指定）无界面转换为新版后提取。每附件最多 80,000 字符，PDF 最多前 300 页、PPT 最多前 500 页；OOXML 最多 5,000 成员/解压 100MB，旧版转换 60 秒超时，达到提取边界在文本与 `note` 中明示。
- 无文字扫描件、损坏文件、解析或转换失败返回 400 中文原因并清理原件与 sidecar；聊天附件当前不做 OCR（知识库文档库可开启扫描件 OCR），不把“仅保存原件”冒充可分析成功。

#### GET /api/assistant/skills/discover?dir=...
扫描技能池目录（SKILL.md 格式）：`{ "count", "names" }`（names 最多 30 条）。**安全边界：仅允许配置中已保存的 `skills_dir`**——未保存时返回 `{"count": 0, "names": [], "error": "目录未保存：请先在设置中保存该技能池目录再检测"}`；目录不存在返回 `{"count": 0, "names": [], "error": "目录不存在"}`。

#### GET /api/assistant/plugins/discover?dir=...
扫描 QwenPaw 插件目录：`{ "loadable", "plugins": [ {name, ok, tools / error} ] }`（plugins 最多 30 条）。**安全边界：`load_qwenpaw_plugin` 会执行插件后端代码，仅允许配置中已保存的 `plugins_dir`**——未保存时返回 `{"loadable": 0, "plugins": [], "error": "目录未保存：请先在设置中保存该插件目录再检测"}`；目录不存在返回 `{"loadable": 0, "plugins": [], "error": "目录不存在"}`。

## 定时任务 /api/jobs

定时 Agent：到点自动按提示词执行一次助手对话，回复写成笔记（标题以「【定时任务】」开头，标签含「定时任务」）。调度器为**单定时器**：按最近到期时间唤醒，任务增删改/执行完成立即重算；同一任务串行执行（上次未结束则跳过）；LLM 未配置时本轮标记 `skipped`，不发请求。运行事实追加写入 `scheduled_job_run_facts`，`recent_runs` 是可重建投影；服务重启/超时导致未收尾时状态为 `unknown`，不自动重试。

任务字段（ScheduledJob）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 主键 |
| `name` | string | 任务名称（必填，≤100 字） |
| `prompt` | string | 到点执行的提示词（必填，≤5000 字） |
| `agent_name` | string\|null | 指定专家标识；`null` = 智能路由 |
| `mode` | string | 工作模式：`readonly` / `standard` / `deep`（入参白名单）；**新建默认 `readonly`**；历史非法值仍可读出（列表不 500），执行侧一律回退 `readonly` |
| `schedule_type` | string | 调度方式：`interval` / `daily` / `cron` |
| `interval_minutes` | int\|null | 间隔分钟（`interval` 必填，5~10080） |
| `daily_at` | string\|null | 每天执行时间 HH:MM（`daily` 必填） |
| `enabled` | bool | 是否启用 |
| `last_run_at` | datetime\|null | 最近一次执行时间 |
| `last_status` | string | 最近一次状态：`ok` / `error` / `skipped` / `unknown` / 空（未运行） |
| `last_error` | string | 最近一次错误/未知摘要 |
| `last_note_id` | int\|null | 最近一次结果笔记 id |
| `running_token` / `running_since` | string\|null / datetime\|null | **内部占入字段**（运行开始时写入、结束/未知恢复时清空；不在列表响应中返回） |
| `recent_runs` | array | 最近 20 条运行投影，元素 `{at, status, summary, run_id, duration_seconds}`（升级前旧条目可能没有后两项） |
| `created_at` / `updated_at` | datetime | 创建 / 更新时间 |

### GET /api/jobs
全部任务列表（按 id 升序）：`[ { ...ScheduledJob } ]`

### POST /api/jobs（返回 201）
创建任务。`schedule_type=interval` 必须带 `interval_minutes`（5~10080）；`daily` 必须带 `daily_at`（HH:MM）。缺字段或格式错误返回 422。未传 `mode` 时默认 `readonly`。
```json
{
  "name": "每周税务自查", "prompt": "检查未来 7 天到期任务并汇总成清单",
  "agent_name": null, "mode": "readonly",
  "schedule_type": "interval", "interval_minutes": 10080, "enabled": true
}
```

### PUT /api/jobs/{id}
部分更新：只传需要修改的字段；不存在返回 404；调度参数不一致（interval 未带 interval_minutes / daily 未带 daily_at）返回 422。更新后立即唤醒调度器重算。

### DELETE /api/jobs/{id}（返回 204）
删除任务及其运行事实，不存在返回 404。

### POST /api/jobs/{id}/run
立即执行一次（不影响下次到点触发），不存在返回 404。**interval 任务手动运行不顺延下次到点**：只有已到点时才推进 `last_run_at`，提前手动运行不占用调度槽位。
```json
{ "status": "ok", "summary": "……（回复摘要，最长 200 字）", "note_id": 12 }
```
- `status`：`ok`（成功，`note_id` 为结果笔记 id）/ `skipped`（模型未配置，或上一次尚未结束）/ `error`（`summary` 为错误摘要，同时记入任务 `last_error`）/ `unknown`（Agent 超时，可能已产生部分副作用，不自动重试；人工核实后手动补跑）。

### GET /api/jobs/{id}/runs
运行时间线：从运行事实重放得到，并与 `recent_runs` 投影核对。
```json
{
  "job_id": 1,
  "timeline": [{"at": "2026-11-04 10:30", "status": "ok", "summary": "……", "run_id": "…", "duration_seconds": 12.3}],
  "replay_ok": true,
  "mismatch": null
}
```
- `limit`：1~200，默认 50。
- `replay_ok=false` 时 `mismatch` 返回重放结果，页面会提示投影不一致；事实每任务最多保留 500 条，超出裁剪最旧。

## 知识与记忆 /api/knowledge

知识库由两部分组成：**文档库**（公司制度/流程/软件手册，`kb_docs` + `kb_chunks`，FTS5 关键词索引 + 可选 sqlite-vec 向量索引）与**笔记**（原 BM25）。检索默认合并两者，用 RRF 融合并返回出处；长期记忆保持原行为。前端入口为 `/knowledge` 的「制度与手册 / 知识检索 / 长期记忆」三个页签。

### 文档库

核心字段：`title` / `category` / `department` / `doc_no` / `doc_key` / `version` / `status` / `effective_from` / `effective_to` / `tags` / `owner` / `summary` / `source_name` / `source_size` / `page_count` / `char_count` / `chunk_count` / `ocr_pages` / `index_status` / `index_error` / `supersedes_id`。

- `doc_key` = 制度编号（为空时取规范化标题）；`(doc_key, version)` 唯一。同 key 新版本入库时，旧版自动置 `superseded` 并回填 `effective_to = 新版生效日 - 1 天`。
- `status`：`draft` / `effective` / `superseded` / `archived`；检索默认只查 `effective`。
- `index_status`：`ok` / `embedding` / `embedding_failed` / `truncated` / `keyword_only`。
- `ocr_pages`：实际走 OCR 的页数（0 = 纯文本层解析）；开启 OCR 时只对单页文本少于 20 字的无文本层 PDF 页识别，单文档上限 50 页，超出置 `ocr_truncated` 并写入 `index_error`。
- 原件存 `UPLOAD_DIR/knowledge/`，数据库只存相对路径；下载时做目录边界校验。

#### GET /api/knowledge/docs
筛选与分页：`q`（标题/编号/摘要）、`category`、`department`、`status`、`include_inactive`、`page`、`page_size`（≤100）。返回 `{items,total,page,page_size}`；未传 `status` 且 `include_inactive=false` 时只返回 `effective`。

#### GET /api/knowledge/docs/facets
筛选器计数：`{categories:[{value,count}], departments:[...], statuses:[...], total}`。

#### POST /api/knowledge/docs/upload（201，multipart/form-data）
`file` + 可选表单字段 `title/category/department/doc_no/version/status/effective_from/effective_to/tags/owner/summary/supersedes_id`。支持 `.pdf/.docx/.doc/.pptx/.ppt/.md/.txt`，单文件 ≤20MB；SHA-256 相同文件返回 409。解析与切块在线程池执行；启用向量检索时返回 `index_status=embedding`，由后台任务补向量。开启扫描件 OCR 后，无文本层 PDF 页会自动识别并累计 `ocr_pages`；OCR 不可用或未开启时，纯图片 PDF 返回 400「请先进行 OCR」。

#### POST /api/knowledge/docs（201，JSON）
粘贴 Markdown/纯文本建文档（软件手册常用），正文 10~500000 字，落盘为 `.md` 原件后可重解析：
```json
{ "title": "VPN 登录说明", "content": "# 登录步骤\n1. …", "category": "软件手册", "version": "v1.0" }
```

#### GET /api/knowledge/docs/{doc_id}
详情：元数据 + `outline`（块级章节大纲：chunk_id/heading/page_no/preview）+ `versions`（同 doc_key 版本链）。

#### PUT /api/knowledge/docs/{doc_id}
仅改元数据（标题/分类/部门/编号/版本/状态/生效期/标签/责任人/摘要）；同 key 同版本冲突返回 409。改状态为 `effective` 会把同 key 其它生效版本置为 `superseded`。

#### DELETE /api/knowledge/docs/{doc_id}（204）
级联删除块、FTS/向量索引与原件；引用该文档的记忆条目**引用置空**（`source_doc_id` 置 null，记忆本体保留，不出孤儿 doc id）。

#### POST /api/knowledge/docs/{doc_id}/reindex
重解析原件并重建该文档的块与 FTS 索引；启用向量时转 `embedding` 后台补齐。

#### POST /api/knowledge/docs/{doc_id}/embed
手动重算该文档向量；未启用/未配置 embedding 返回 400。

#### GET /api/knowledge/docs/{doc_id}/file
下载原件（ASCII `filename` + `filename*=utf-8''` 双文件名）。

#### GET /api/knowledge/docs/{doc_id}/chunks/{chunk_id}?before=1&after=1
返回命中块及前后各 N 块（0~5），用于「小检索、大上下文」。

### 知识库设置（OCR / 向量）

`GET /api/knowledge/settings` 返回 `{ocr_enabled, ocr_available, embedding_enabled, embedding_model, embedding_dim, embedding_base_url, has_api_key, resolved, resolved_detail, vector_ready}`；`resolved_detail` 是 `resolved` 的中文原因（为何启用/禁用、跟随哪个供应商），界面直接展示，不再自行拼接文案。
`PUT /api/knowledge/settings` 保存；`ocr_enabled` 控制扫描件 OCR（默认 false，仅对无文本层 PDF 页生效）；`embedding_api_key` 传空保留旧值（DPAPI 加密），`embedding_base_url` 为空表示跟随当前激活模型供应商。
`ocr_available=false` 表示当前环境未安装 rapidocr/onnxruntime，OCR 开关不可用，上传扫描件仍返回原「请先进行 OCR」提示。
`POST /api/knowledge/settings/test` 用一个样例文本调 `/embeddings`，返回 `{ok, dim, vectors}`；失败 400 并带中文原因。body 可选 `embedding_model / embedding_base_url / embedding_api_key / embedding_dim` 覆盖已保存配置（用于测试弹窗里尚未保存的值）。**自定义 embedding 地址必须单独填写该地址的 API Key，绝不复用聊天模型 Key**（缺失时 `resolved=false`，原因「自定义向量接口地址必须单独填写该地址的 API Key（不会使用聊天模型 Key）」）；地址变更且未填新 Key 会清空旧 Key。sqlite-vec 不可用时 `vector_ready=false`，检索自动只用关键词。

### 检索与索引

#### GET /api/knowledge/rag/stats
```json
{ "notes_indexed": 5, "chunks": 12, "vocabulary": 486,
  "documents": { "docs": 3, "effective_docs": 2, "chunks": 18, "fts": true, "vector": true, "vector_dim": 1024 } }
```
顶层 `notes_indexed/chunks/vocabulary` 是笔记 BM25 口径；文档库在 `documents` 内。

#### POST /api/knowledge/rag/rebuild?with_vectors=false
重建笔记索引 + 文档 FTS 索引；`with_vectors=true` 时再重建向量（换 embedding 模型/维度后使用，同步执行、可能较慢）。**默认处理全部文档**；显式传 `limit` 时未处理的文档标 `embedding_failed` 并在返回值给出 `remaining`（避免第 201 篇起静默失去向量）。返回 `{notes:{...}, documents:{docs,chunks,fts}, vectors?:{processed,done,failed,remaining}}`。

#### POST /api/knowledge/rag/search
```json
{ "query": "住宿标准", "top_k": 5, "scope": "all", "category": "", "status": "", "include_inactive": false }
```
`scope`：`all` / `docs` / `notes`；`top_k` 1~20。返回：
```json
{ "items": [ { "source": "doc", "id": 1, "title": "差旅管理制度", "snippet": "…",
  "score": 1.0, "citation": "《差旅管理制度》 · v1.0 · 第三章 · 第 7 页",
  "doc": { "id": 1, "version": "v1.0", "status": "effective", "category": "制度规范",
           "heading": "第三章 差旅管理 > 3.2 住宿标准", "page_no": 7, "chunk_id": 4 } } ] }
```
制度与笔记用 RRF（`1/(60+rank)`）融合，**每通道每文档只取最佳 rank 参与累加**（同一文档多块命中不重复加分）；展示的命中块在关键词/向量两通道最佳候选中按 rank 择优（并列时关键词优先），保证引用块就是最合适的证据；`score` 归一化到 0~1；向量不可用/失败时 `items[*].vector_note` 会带降级原因。单字查询无 bigram 时自动回退 `LIKE`。

### 长期记忆

记忆字段：`content` 内容（必填，≤2000 字）、`source` 来源（`manual` 手动 / `auto` 对话抽取）、`tags` 标签数组、`pinned` 是否置顶、`hit_count` 命中次数（召回命中自动累计）、`created_at`；另含可选**来源引用**：`source_doc_id`（显式引用的 `kb_docs.id`，可空；非法值或文档不存在返回 400）与 `source_doc_title`（响应字段，由 `source_doc_id` 解析出的文档标题；无引用/旧记录为空字符串，**不自动生成记忆来源**）。与既有记忆近重复（前 80 字互相包含）时拒存，返回 400。

#### GET /api/knowledge/memories?keyword=&type=
记忆列表（置顶在前、id 倒序，最多 100 条；`keyword` 按内容过滤，`type` 按类型过滤）：`{ "items": [ { "id", "content", "source", "type", "session_id", "tags", "pinned", "hit_count", "source_doc_id", "source_doc_title", "created_at" } ] }`；批量按 `source_doc_id` 一次取标题，旧记录（无引用）两项为 `null`/空串，不受影响。

#### POST /api/knowledge/memories（返回 201）
```json
{ "content": "企业所得税汇算清缴截止 5 月 31 日", "tags": ["税务"], "pinned": false, "type": "reference", "source_doc_id": 1 }
```
`type` 可选：`user`（用户画像）/`feedback`（纠正偏好）/`project`（项目背景）/`reference`（参考线索）/空（未分类）；检索时 user/feedback 类加权 +0.5。`source_doc_id` 可选：引用的知识库文档 id，**不存在或非法（非正整数/布尔）返回 400**，不静默忽略。

#### GET /api/knowledge/memories/stats（未发版，随 v1.16.1）
统计：`{ "total", "by_source", "by_type", "pinned", "top_hits": [{id, content, hit_count}] }`。

#### GET /api/knowledge/memories/export.md（未发版，随 v1.16.1）
导出全部记忆为 MEMORY.md 风格 Markdown（`## 类型` 分节 + `- 条目`，置顶带标记；zcode/WorkBuddy 兼容）。

#### POST /api/knowledge/memories/import.md（未发版，随 v1.16.1）
```json
{ "text": "## project\n- 条目一\n- 条目二" }
```
`## 类型节` 解析为 type（仅四类白名单生效），`- 行` 为条目；近重复自动跳过。返回 `{added, skipped}`。

#### PUT /api/knowledge/memories/{id}
部分更新 `content` / `tags` / `pinned` / `type` / `source_doc_id`；不存在返回 404，内容为空返回 400。`source_doc_id` 语义：**显式传 `null` = 清空引用**；传正整数 = 换绑文档（不存在/非法 400）；不传该字段 = 既有引用保持原样，`source_doc_title` 按最终生效的 `source_doc_id` 输出。

#### DELETE /api/knowledge/memories/{id}
返回 `{"ok": true}`；不存在返回 404。

#### POST /api/knowledge/memories/recall
记忆检索（与 Agent 工具 `recall_memories` 同一实现：token 与记忆统一 casefold + bigram 重叠打分 + 置顶/命中数加权，泛词按有限噪声阈值过滤）：
```json
{ "query": "申报截止", "top_k": 5 }
```
`top_k` 默认 5（1~10）。返回 `{ "items": [ /* 同记忆字段（含 source_doc_id / source_doc_title） */ ] }`。

#### POST /api/knowledge/memories/extract
从对话文本一次性抽取记忆（走 LLM，近重复去重后入库）：
```json
{ "conversation": "……（对话全文或最近若干轮，至少 20 字）" }
```
返回 `{ "extracted": 3, "saved": 2 }`；模型未配置或对话太短时返回 `{ "extracted": 0, "saved": 0, "error": "…" }`。抽取**不会自动**生成记忆来源引用（不自动关联 `kb_docs`），需要来源时在列表/详情里显式挂 `source_doc_id`。

## MCP 工具 /api/mcp

MCP（Model Context Protocol）服务器管理：仅支持 **stdio** 传输（本地命令起的 MCP Server；HTTP/SSE 预留）。连接成功后服务器工具自动注册为 `mcp_<服务器名>__<工具名>` 进入 Agent 工具白名单（是否只读以工具自带 `annotations.readOnlyHint` 为唯一依据，缺省视为可写）；服务启动时自动拉起全部启用中的服务器，单服务器失败不影响宿主，自动重连有界退避。服务器 `env` 落库前经 DPAPI 密封，接口只回显键名与 `***`；存量明文会在启动时自动迁移。

入站报文单行上限 **1,048,576 字符**（`MAX_MESSAGE_BYTES // 4`，按 UTF-8 最坏 4 字节/字符折算，等价单行 4MB），超限拒绝该行；**请求已发出后的超时/断流不自动重放**，工具返回 `{ok:false, result_unknown:true, error}`，由调用方先核实服务器端结果再决定是否重试。

服务器字段：`name` 服务器名（必填唯一，≤50 字，即工具前缀）、`command` 启动命令（如 `npx` / `python` / `uv`）、`args` 命令参数数组、`env` 环境变量（敏感值只存本机库，**回显一律脱敏为 `***`**）、`enabled`、`status`（`stopped` / `connected` / `error`）、`last_error`、`tools`（已发现的工具清单）。

### GET /api/mcp/servers
服务器列表（按 id 倒序，含工具清单，env 脱敏）：
```json
{ "items": [ { "id": 1, "name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/path/to/docs"],
  "env": { "API_KEY": "***" }, "enabled": true, "status": "connected", "last_error": "",
  "tools": [ { "name": "read_file", "description": "…" } ], "created_at": "2026-10-12 10:00:00" } ] }
```

### POST /api/mcp/servers（返回 201）
```json
{ "name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/path/to/docs"], "env": {}, "enabled": true }
```
name 重复或必填缺失返回 400。

### PUT /api/mcp/servers/{id}
部分更新 `command` / `args` / `env` / `enabled`（保存后 status 重置为 `stopped`，需重新连接）；不存在返回 404。

### DELETE /api/mcp/servers/{id}
删除并断开连接，返回 `{"ok": true}`；不存在返回 404。

### POST /api/mcp/servers/{id}/connect
连接（或重连）服务器并注册其工具，返回服务器详情（`status` 变为 `connected`，含工具清单）；命令不存在或握手失败返回 503 中文提示（`status` 记为 `error`）。

### POST /api/mcp/servers/{id}/disconnect
断开连接（`status` 变为 `stopped`），返回服务器详情；不存在返回 404。

### POST /api/mcp/servers/{id}/test-call
对已连接的服务器发一次 `tools/call` 测试调用：
```json
{ "tool": "read_file", "arguments": { "path": "D:/path/to/docs/a.txt" } }
```
返回 `{ "text": "…（拼接的文本块，≤8000 字）", "is_error": false, "raw": { /* 原始 JSON-RPC result */ } }`；服务器未连接或调用失败返回 503。

## 模块机制（Module Registry）/api/finance/*

工作台支持按目录加载业务模块（当前用于财务域）。模块机制约定：

- **发现与加载**：后端扫描 `backend/app/modules/*/manifest.py`（`_` 开头目录如 `_template` 跳过）；manifest 校验失败或挂载异常仅记日志跳过该模块，不影响宿主与其它模块。
- **路由挂载**：模块 router 按 manifest.prefix 挂载（财务模块必须 `/api/finance/` 开头，调试类模块以 `/api/debug/` 开头），如 `/api/finance/hello/ping`。
- **建表**：模块 models 随 manifest 导入注册进 `Base.metadata`（`load_modules(app)` 在 lifespan 之前执行），启动时 `create_all` 建出模块表；表名统一 `mod_` 前缀。
- **轻量迁移**：manifest.column_patches（`{表名: {列名: DDL}}`）并入启动时的列补齐逻辑。
- **Agent 工具**：manifest.agent_tools 经 `register_module_tools()` 并入工具注册表（与内置工具重名不覆盖）；专家白名单引用已禁用模块的工具时自动跳过不崩。
- **前端**：`frontend/src/modules/*/manifest.js` 声明路由（`/finance/<name>/...`，懒加载）与菜单（group/icon/order），自动进侧边栏对应分组。

### GET /api/finance/hello/ping
示例模块 finance_hello 的连通性自检（机制实证）：
```json
{ "module": "finance_hello", "ok": true }
```

模块自带样例表 `mod_hello_entry`（id/name/created_at）与条目接口：`GET /api/finance/hello/entries`、`POST /api/finance/hello/entries`（`{"name": "..."}`，返回 201）。模块还注册只读 Agent 工具 `hello_ping`（财税专员白名单内）。

新建模块：复制 `backend/app/modules/_template` 为 `finance_<name>` 并按目录内注释改造；前端对应建 `frontend/src/modules/finance-<name>/`（manifest.js + views）。重启生效。

## 财务模块接口（/api/finance/*）

| 模块 | 前缀 | 主要接口 |
|------|------|---------|
| 数据模板（通用） | `/api/templates` | `GET /`（15 份导入模板清单，返回 `{total, items:[{name, module, size, available}]}`）、`GET /{name}`（白名单模板下载，中文文件名 RFC5987；不在白名单一律 404） |
| finance_expense 费用分类 | `/api/finance/expense` | `POST /classify`（上传 xlsx 分类）、`GET /runs`、`GET /runs/{id}`、`GET /runs/{id}/download`、`GET /rules/summary`、`POST /rules/reload` |
| finance_invoice 发票处理 | `/api/finance/invoice` | `GET /engine/status`、`POST /batches`（上传 PDF/ZIP）、`GET /batches`、`GET /batches/{id}`、`PATCH /items/{id}`、`POST /batches/{id}/reclassify`（按项目规则重新归类）、`GET /batches/{id}/projects`（项目汇总）、`POST /batches/{id}/export`（重命名 + 清单 + 整批/分项目 ZIP）、`GET /batches/{id}/projects/download?project=`（单项目 ZIP）、`GET /batches/{id}/download`（整批 ZIP） |
| finance_vault 数据池清洗 | `/api/finance/vault` | `GET /tables`、`POST /ingest/file`、`POST /query`（只读 SQL）、`GET /tables/{table}/preview`（前 100 行）、`GET /tables/{table}/profile`（列画像）、`POST /tables/{table}/clean/*`（清洗预览/应用/日志）、`POST /tables/{table}/aggregate*`（聚合/导出/另存）、`GET /tables/{table}/usage`（删除/重命名前预检）、`DELETE /tables/{table}?confirm=&force=`（二次确认删除）、`POST /tables/{table}/rename`（重命名）；清洗/聚合另存的结果表默认**不得与源表同名、且不得已存在**，否则 409，需显式 `allow_overwrite=true` |
| finance_ledger_personal 个人记账 | `/api/finance/ledger/personal` | 账户/分类/交易 CRUD、`GET /stats/monthly`、`GET /stats/trend`、`GET/PUT /budgets`、`POST /import/csv`、`GET /export/csv` |
| finance_ledger_bip 企业记账 | `/api/finance/ledger/bip` | `POST /imports/preview`、`POST /imports/confirm`、`GET /imports`、`GET /vouchers`、`GET /vouchers/{id}`、`GET /entries`、`GET /balances/summary`、`POST /vouchers/export-to-vault` |
| finance_tax 企业所得税 | `/api/finance/tax` | `/enterprises`（CRUD；存在已提交申报时删除 409，禁止抹掉申报留痕）、`/financial-import`（导入+模板下载；能识别税号或企业名称才真实落库，无身份仅预览 `persisted=false`；`/import/{enterprise_id}` 指定企业落库）、`/policies`（政策库）、`/tax-returns`（申报 CRUD/提交/计算/导出 Excel·PDF；**入参万元、落库与导出口径为元**；草稿可 `PUT` 受控更正并重算，已提交更正/删除 409，不允许换企业或改成同期间同类型已存在记录）、`/scope`（税种准入清单：admitted/blocked 及原因；CIT 计税入口同样过 `require_tax_scope`）、`/textbook-reference`（教材对照） |
| finance_board 报表看板 | `/api/finance/board` | `GET /snapshots`（快照列表，最多 50 条，时间倒序）、`POST /run`（触发管线）、`GET /latest`、`GET /snapshots/{id}/html`（iframe 嵌入） |
| finance_cashflow 现金流填报 | `/api/finance/cashflow` | `POST /runs`（上传建运行，必填 `actual_month` 1~12，可选 `actual_year` 2000~2100 缺省当前年）、`POST /runs/{id}/execute`、`GET /runs`（分页）、`GET /runs/{id}`（运行详情；历史 `actual_year=0` 按创建年兜底展示）、`GET /runs/{id}/artifacts/{name}`、`GET /runs/{id}/download`（ZIP）；**任何失败/重跑分支清空 artifacts**，不会给出上一轮产物 |
| finance_pos POS 对账 | `/api/finance/pos` | 同上（无 actual_month 参数），含 `GET /runs/{id}` 运行详情；同一时刻只允许一个执行，`POST /runs/{id}/execute` 并发 409；**只有 0 字节产物判失败 503**（不归档、不可下载）；刷卡凭证报表日期容差 = `max(10, date_tolerance+7)` 天 |
| finance_lease 租赁核算 | `/api/finance/lease` | `GET/POST /contracts`、`GET/PATCH/DELETE /contracts/{id}`（归档后只读；有测算历史删除 409）、`GET/PUT /contracts/{id}/payments`（整表替换仅草稿）、`POST /contracts/{id}/payments/import/preview|apply`（字段名 `file`，10MB；预览只解析不落库，apply 无行级错误才整表替换）、`POST|GET /contracts/{id}/calculations`（同输入同规则版本幂等返回，不重复堆积）、`GET /calculations/{id}`（含 `stale`）、`GET /calculations/{id}/assessment|periods|checks`、`POST /calculations/{id}/confirm`（须当前输入 + 规则版本未变 + 全部检查 `ok`）、`GET /calculations/{id}/export.xlsx?allow_stale=`（五页签；当前结果过期或存在 failed/unknown 时 409，历史快照须显式 `allow_stale=true`）、`GET /summary`、**BIP 只读对账**：`GET/PUT /reconciliation/mappings`（只写本模块 `mod_lease_bip_map`，绝不写 BIP）、`GET /reconciliation/context`（承租人取合同、账簿/期间取 BIP 余额表）、`POST /reconciliation/check`（承租人+账簿+期间五科目，容差 0.01，任一合同不可用整批 `unknown`）；合同状态 `draft/archived`，测算状态 `calculated/confirmed` 且只允许单向确认 |
| finance_deferred 长期待摊 | `/api/finance/deferred` | `POST/GET /items`、`GET/PATCH/DELETE /items/{id}`（归档只读；有测算历史删除 409）、`POST/GET /items/{id}/runs`（同参数同规则版本幂等）、`GET /runs/{id}`（含 `stale`/风险标记）、`GET /runs/{id}/periods|checks`、`POST /runs/{id}/confirm`（须当前输入 + 无 failed/unknown；存在 `warning` 时必须 `risk_acknowledged=true` 且 `risk_note` 非空）、`GET /runs/{id}/export.xlsx`（三页签，过期/差异/风险未确认 409）、`GET /summary`；口径：净额 = 含税 ÷ (1+税率)、月摊 = 净额 ÷ 月数（均 HALF_UP 两位）、末期尾差如实留在末期余额（不吸收/不转销）；仅租入资产改良支出 |
| finance_union 工会经费计提 | `/api/finance/union` | `POST/GET /batches`（同主体同期间唯一，重复 409）、`GET/PATCH/DELETE /batches/{id}`（PATCH/DELETE 仅草稿）、`GET/PUT /batches/{id}/lines`（部门行三数：本期工资基数/上期已计提/上期实际）、`GET /batches/{id}/preview`（本期计提 / 上期差异 / 本期建议入账）、`GET /batches/{id}/checks`、`POST /batches/{id}/confirm`（缺数据或差异拒绝；确认后只读）、`GET /batches/{id}/export.xlsx`（三页签）、`GET /summary`；费率只来自批次，行级无覆盖；缺输入 `unknown` 不按 0；不读 HR、不做 60/40、凭据、税前扣除判断、支付与 BIP 回写 |
| finance_stamp_duty 印花税填报（未发版，随 v1.16.1） | `/api/finance/stamp-duty` | `GET /tax-items`（税目税率表）、`POST /filings`（period/taxpayer_name/half_discount/items[{tax_item,amount,exempt}] 创建并计算；同纳税人同期间重复 409）、`GET /filings`、`GET /filings/{id}`、`PUT /filings/{id}`（受控更正，已导出/已提交 409）、`DELETE /filings/{id}`（已提交 409）、`POST /filings/{id}/export`、`GET /filings/{id}/download`；计税入口过 `require_tax_scope` |
| finance_vat 增值税填报（未发版，随 v1.16.1） | `/api/finance/vat` | `POST /filings`（taxpayer_type=general：output_items[{business_type,amount,rate}] + input_vat；small_scale：sales_amount + levy_rate，季度 30 万免征与 1% 征收率按 19号公告有效期校验；同纳税人同期间重复 409）、`PUT /filings/{id}` 受控更正（已导出/已提交 409、不换纳税人/期间）、其余同上 CRUD/导出/下载（已提交删除 409）；计税入口过 `require_tax_scope` |
| finance_iit 个人所得税填报（未发版，随 v1.16.1） | `/api/finance/iit` | `GET /standards`（专项附加扣除标准，国发〔2023〕13号：住房贷款利息 1000 元/月最长 240 个月、住房租金 1500/1100/800 元/月按城市档位、学历继续教育 400 元/月×最长 48 个月、职业资格继续教育 3600 元/年）、`POST /filings`（year + rows[{month,name,salary,social_insurance,special_additional,other_deductions}]，累计预扣法逐月模拟）、`PUT /filings/{id}` 受控更正、其余同上 CRUD/导出/下载；计税入口过 `require_tax_scope` |
| finance_dsf 残保金填报（未发版，随 v1.16.1） | `/api/finance/dsf` | `POST /filings`（year/total_employees/total_salary/disabled_employees/ratio 默认 1.5%，30 人免征 + 分档减收按 8号公告校验；同纳税人同期间重复 409）、`PUT /filings/{id}` 受控更正、其余同上 CRUD/导出/下载（已提交删除 409）；计税入口过 `require_tax_scope` |

### 财务模块上传与行数护栏

各财务导入路由共用两道路栏：上传大小（分块限额读取，超限 `400`）与导入行数（超过 `MAX_IMPORT_ROWS = 200000` 行即 `400` 中止，不落库、不留半成品）。

| 模块 | 上传上限 | 备注 |
|------|----------|------|
| finance_expense `POST /classify` | 20MB | 仅 `.xlsx/.xls`，空文件 400；引擎不可用 503 |
| finance_invoice `POST /batches` | 单批次 100MB（ZIP 按解压后口径） | PDF/ZIP 批次上传 |
| finance_vault `POST /ingest/file` | 20MB | 入仓数据行数超 200000 行 400 |
| finance_ledger_bip `POST /imports/preview` | 10MB | xlsx/xls；解析阶段即做行数护栏 |
| finance_cashflow `POST /runs` | 50MB | ZIP 解压后总量上限 200MB（防解压炸弹） |
| finance_lease `POST /contracts/{id}/payments/import/*` | 10MB | 仅 xlsx；单文件 ≤20,000 行；表头在前 50 行内按标签文本定位（不锁工作表顺序） |

- **现金流 `GET /runs` 分页**：`page`（默认 1）与 `page_size`（默认 20，1~100），返回 `{total, items, page, page_size}`，`total` 为 COUNT(*) 全量计数；`GET /runs/{id}/download` 无产物时返回 `409`
- **租赁核算检查口径**：`GET /checks` 返回 `{items, counts:{ok,failed,unknown}, all_passed, has_unknown}`，状态只有 `ok/failed/unknown`；缺合同区间/月期等数据一律 `unknown`（不按 0 记 `ok`，也不显示为通过）。金额字段两位小数、序列化为字符串，比率/折现因子单独精度；日期只收 `YYYY-MM-DD`（带时间即 422/400）
- **租赁核算幂等与快照**：测算唯一键为 `(contract_id, input_hash, rule_version)`，重复提交返回既有批次而非新增；失败计算不落任何结果行；`confirm` 与当前底稿导出在「输入已变化（`stale`）/规则版本变化/存在 failed 或 unknown」时返回 **409**，已确认批次不可再确认或改写
- **看板快照 `log_tail`**：入库截断至 4000 字符，列表/详情读取时仅返回**尾部 500 字符**（有界暴露，不整文件无界返回）；`POST /run` 管线繁忙返回 `409`、引擎不可用返回 `503`；`GET /latest` 无成功快照返回 `404`；`GET /snapshots/{id}/html` 仅允许读取看板快照目录内文件（越界 `403`），响应带 `Content-Security-Policy: frame-ancestors 'self'` 与 `X-Content-Type-Options: nosniff`

### 定时任务扩展（v1.11.0）

- `GET /api/jobs/actions`：已注册模块动作列表（如 `finance_board.run`）
- 定时任务（`POST /api/jobs`）新增字段：`cron_expr`（schedule_type=cron 时必填，五段表达式）、`action`（模块动作名，空 = Agent 对话任务）
- 调度语义：cron 同自然分钟不重复触发；模块动作任务走线程池执行、不写笔记

> 跨项目引擎路径（费用分类 / 发票工具）经 `finance_expense_engine_path` / `finance_invoice_engine_path` 配置项解析，默认按示例主体22相对位置推导；引擎目录缺失时对应接口返回 503 中文提示，不影响其他模块。BIP 导入的 `export-to-vault` 在数据池清洗模块不可用时同样 503 降级；入仓取数阶段即受 200,000 行上限约束，超限返回 400，不会先全量组装数据。

## 调试中心 /api/debug（v1.16.0）

调试中心模块 `debug_center`：问题记录 + E2E 测试运行归档 + 前端报错采集。**纯 JSON 文件存储**
（根目录默认 `backend/data/debug/`，`DEBUG_DATA_DIR` 可覆盖），不建表、不开放给 Agent。

### 问题记录 /api/debug/issues

问题字段：`{id, title, severity(P0-P3), status(open/in_progress/done/wontfix), source(manual/e2e/analysis), page, description, evidence, created_at, updated_at}`。
首次使用自动写入 11 条 analysis 种子问题（幂等）。

| 端点 | 说明 |
|------|------|
| `GET /api/debug/issues?status=&severity=` | 问题列表（可选筛选），返回 `{total, items}`（按严重度+创建时间排序） |
| `POST /api/debug/issues` | 新建（返回 201）；body 仅需 `title`，其余字段有默认（severity=P2/status=open/source=manual） |
| `PUT /api/debug/issues/{id}` | 部分更新（未传字段不变），自动刷新 `updated_at`；不存在 404，非法枚举 422 |
| `DELETE /api/debug/issues/{id}` | 删除，返回 `{"ok": true}`；不存在 404 |

### 测试运行 /api/debug/runs

`dev/e2e/*` 脚本跑完经 `dev/e2e/debug_reporter.py` 落库（`runs/<run_id>/result.json` + `shots/*.png`），
run_id 形如 `20261013-153000_recon`；有错误的页面/失败用例自动建/更新问题（source=e2e、P1）。

| 端点 | 说明 |
|------|------|
| `GET /api/debug/runs` | 运行列表（时间倒序），每项 `{id, kind, base, started_at, finished_at, totals{pages,bad_pages,tests,failed_tests}, shots}` |
| `GET /api/debug/runs/{run_id}` | 运行详情（result.json 全文 + `shots` 截图文件名列表）；不存在 404，非法 run_id 400 |
| `GET /api/debug/runs/{run_id}/shots/{filename}` | 截图（image/png，FileResponse）；路径穿越 400、文件缺失 404 |
| `DELETE /api/debug/runs/{run_id}` | 整目录删除该次运行，返回 `{"ok": true}` |

### 前端报错 /api/debug/client-errors

| 端点 | 说明 |
|------|------|
| `GET /api/debug/client-errors?limit=200` | 尾部读取最近 limit 条（1-1000），返回 `{total, items}`（按写入顺序） |
| `POST /api/debug/client-errors` | 追加一条（返回 201）；**请求体上限 1MB**（`CLIENT_ERROR_MAX_BODY_BYTES`，全局 128MB 之外的端点级护栏），超限即 `400`；**宽容解析**——非 JSON body/数组/字符串/缺字段一律归一记录为 `{ts, page, message, stack, ua}`，绝不 500。文件**有界**：超过 10MB 自动裁剪为仅保留最近 1000 条（逐行仍是合法 JSON）。前端 `main.js` 全局 errorHandler/error/unhandledrejection 自动上报（裸 axios、同消息 5 秒去重） |
| `DELETE /api/debug/client-errors` | 清空，返回 `{"ok": true}` |

### GET /api/debug/summary

调试中心总览：
```json
{
  "open_issues_by_severity": {"P0": 4, "P1": 3, "P2": 4, "P3": 0},
  "total_issues": 11,
  "runs_count": 1,
  "last_run": {"id": "20261013-153000_recon", "kind": "recon", "finished_at": "2026-10-13 15:30:42", "bad_pages": 0, "failed_tests": 0},
  "client_errors_count": 0
}
```
无运行时 `last_run` 为 `null`；未决计数含 status 为 open 与 in_progress 的问题。

## 前端接入说明

- axios 实例：`frontend/src/api/index.js`，`baseURL` 取 `VITE_API_BASE`（默认 `/api`）；响应拦截器统一弹出中文错误提示。
- SSE 流式对话走原生 fetch（与 axios 共用同一 baseURL），用 AbortController 中止。
- 开发模式：Vite 将 `/api` 代理到 `http://127.0.0.1:8000`；生产模式：前端构建产物由后端在 8000 端口直接托管，天然同源、无跨域问题。
- 所有 SQL 通过 SQLAlchemy 参数绑定执行，外部输入不会拼接进语句。

### 技能包与专家团文件（v1.13.0）

| 模块 | 端点 |
|------|------|
| 技能包 | `GET /api/skills/packs`、`POST /api/skills/packs`、`DELETE /api/skills/packs/{name}`、`PUT /api/skills/packs/{name}/enabled`、`GET /api/skills/packs/{name}/files/{filename}`、`POST /api/skills/rescan` |
| 专家团文件 | `GET /api/experts/packs`、`POST /api/experts/packs/import-all`、`POST /api/experts/packs/import/{name}`、`POST /api/experts/packs/reset`、`POST /api/experts/packs/export/{expert_name}`、`GET /api/experts/packs/{name}/content`、`DELETE /api/experts/packs/{name}` |

- 技能包 store：`backend/data/skills/<name>/SKILL.md`（zcode/WorkBuddy 同款格式），首次启动从 `backend/seed_skills/` 播种；AI 助手设置的「技能池目录」指向该目录即注入 Agent，**已停用的包（`_store.json` 的 disabled 列表）在注入侧过滤，停用开关对 Agent 真实生效**
- 专家团文件：`backend/data/expert-packs/<name>.md`（frontmatter: name/label/emoji/color/description/keywords/tools/sort + 正文角色提示词），导入为 upsert（工具白名单取并集，导入不缩减），导出把数据库专家写回文件
- `GET /api/experts/packs` 每项含 `diff`（差异简述）与 `diff_fields`（差异字段名列表，如 `["显示名","角色提示词"]`，供界面列出具体差异项）
- 模块工具预置：模块 manifest 声明 `preset_experts`，加载时把模块工具幂等合并进指定专家白名单（如财税专员自动获得全部财务模块工具）

### WorkBuddy 同步导入（未发版，随 v1.16.1）

| 端点 | 说明 |
|---|---|
| `GET /api/assistant/workbuddy/scan?home=` | 只读扫描本机 WorkBuddy 数据目录（默认 `~/.workbuddy`，可被设置/env `WORKBUDDY_HOME` 覆盖）：`{home, available, skills[], memories[], identity_files[]}` |
| `POST /api/assistant/workbuddy/import` | `{home, skills, memories, identity, dry_run}` 执行幂等导入：技能包整目录进技能池（**新包默认停用**）、MEMORY.md 分节进 memories（source=import，agent-hub 托管块剔除）、SOUL/IDENTITY/USER/BOOTSTRAP → 专家包草稿 `workbuddy-*.md`；返回三组 added/updated/skipped/failed |
| `GET/PUT /api/assistant/workbuddy/settings` | 导入设置（home 路径）与上次导入结果（`backend/data/workbuddy_import.json`） |

CLI 等价：`backend/scripts/import_workbuddy.py [--dry-run] [--from <目录>] [--skills-only]`。

### 模型能力探测与厂商预设（v1.14.0）

| 端点 | 说明 |
|------|------|
| `POST /api/models/{id}/probe/reasoning` | 思考级别探测（none/low/medium/high 各一发，判定 supported/accepted/unsupported） |
| `POST /api/models/{id}/probe/context` | 上下文长度二分探测（≤6 次小请求，max_tokens=1） |
| `POST /api/models/{id}/probe/output` | 最大输出长度阶梯探测（1024→131072） |
| `POST /api/models/{id}/probe/all` | 三连探测，汇总写 probe_result + probed_at |
| `GET /api/models/{id}/probe` | 当前探测状态（只读） |
| `GET /api/providers/presets` · `POST /api/providers/presets/reload` | 厂商预设（provider_presets.json，33 家；含按量/仅交互/交互+API 套餐）；另挂 `/api/models/providers/presets` 别名（兼容「router prefix=/models」的路径解读）。v3 起每项可含 `session_header`/`user_agent`（官方要求会话头的套餐，如 OpenCode Go `x-opencode-session`）与 `usage_scope`（`plan_interactive_only` = 仅限交互式使用，`job-*` 定时任务会话选中该套餐模型时中文拒绝、不发起推理） |

- 探测直连厂商 `/chat/completions`（openai 兼容）；anthropic 协议走 `/v1/messages`（effort→`output_config.effort`、budget→`thinking.budget_tokens`、off→`thinking{type:disabled}`），失败=200+error（不可达≠不支持，supports_thinking 保持 -1）；429 等 30s 退避重试至多 2 次
- 模型级自定义配置：temperature（空=跟随）、note、supports_thinking/thinking_levels 手动覆盖；temperature 已接入 build_model 运行时（openai/anthropic 双协议）

---

## 经营/职能模块接口（2026-10-31 as-built）

> 统一约定：列表 `{total, items, page, page_size}`；创建 201；参数非法 400/422（中文）；不存在 404；唯一键/状态冲突 409；金额两位小数；日期 `YYYY-MM-DD`、月份 `YYYY-MM`；导出为 xlsx（`Content-Disposition` 含 ASCII 回退名 + RFC5987 中文名）。

### 招商管理 `/api/business/leasing`

| 端点 | 说明 |
|---|---|
| `GET /units/summary` | 总数/总面积/空置率/出租率/开业率/在谈面积/各状态与楼栋 |
| `GET/POST /units` · `PUT/DELETE /units/{id}` | 铺位 CRUD（同楼栋+楼层+铺位号唯一）；任一线索引用时删除 409；状态与有效签约冲突 409 |
| `GET /funnel` | 线索漏斗：各阶段数量/需求面积/预算 + 转化率 |
| `GET /leads/due` | 下次跟进 ≤ 今天且未签约/未流失；最多 100 条（计数走 summary） |
| `GET/POST /leads` · `PUT/DELETE /leads/{id}` | 线索 CRUD；`stage=signed` 必须有可用 `unit_id` 并原子置铺位 leased；换铺/取消同事务；已签线索直接删除 409 |
| `GET/POST /leads/{id}/followups` | 跟进记录（渠道：电话/微信/面谈/邮件/其他） |
| `GET /export/units.xlsx` · `/export/leads.xlsx` | 导出 |

### 营运管理 `/api/business/operations`

| 端点 | 说明 |
|---|---|
| `GET /summary?period=` | 履约合同/出租面积/应收/实收/收缴率/欠费/欠费合同/已过期履约/30 天到期/未结工单 |
| `GET /contracts/expiring?days=30` | 到期合同（days 可 30/60/90） |
| `GET/POST /contracts` · `PUT/DELETE /contracts/{id}` | 合同 CRUD（编号唯一、起止校验；active→terminated 单向；删除级联账单） |
| `POST /contracts/{id}/bills` | active 且期间与租期相交才可出账（同合同同期间唯一，重复 409） |
| `GET /bills?period=&status=&contract_id=` | 账单列表（含欠费） |
| `GET /bills/aging?as_of=YYYY-MM-DD` | 四桶 not_due/1_30/31_90/over_90（金额/笔数/合同数）与按逾期倒序未清清单 |
| `POST /bills/{id}/payments` | 登记收款（累计；超应收 400；收清后 paid） |
| `GET/POST /work-orders` · `PUT/DELETE /work-orders/{id}` | 工单 CRUD；优先级含 urgent，due_at/parent_id/equipment_id 可空；迁移矩阵；有子单时删除 409 |
| `GET/POST /work-orders/{id}/followups` · `PUT/DELETE /work-orders/{id}/followups/{fid}` | 处理留痕：时间/内容/结果/满意度 1-5，删工单级联 |
| `GET /export/contracts.xlsx` · `/export/bills.xlsx` · `/export/work-orders.xlsx` | 导出（工单与筛选列表一致，含设备/SLA/父单） |

### 企划管理 `/api/business/planning`

| 端点 | 说明 |
|---|---|
| `GET /summary?year=` | 年度预算/实际/差异/超支/进行中/已结束/calculated 平均 ROI/手填计数 |
| `GET /campaigns/calendar?year=` | 年度日历（跨月活动展开到每个覆盖月份） |
| `GET/POST /campaigns` · `PUT/DELETE /campaigns/{id}` | 活动 CRUD；预算明细自动合计；`grade` + `sales_baseline/sales_during/merchant_share/roi_target`；三项金额齐全时服务端 Decimal 计算 ROI，零投入 null+warning，手填不混算；档期冲突 409，显式 `allow_conflict=true` 才覆盖 |
| `GET/POST /vendors` · `PUT/DELETE /vendors/{id}` | 供应商 CRUD（服务类型 物料/美陈/演艺/投放/执行；评分 1-5） |
| `GET /export/campaigns.xlsx?year=` | 导出 |

### 法务管理 `/api/business/legal`

| 端点 | 说明 |
|---|---|
| `GET /summary` | 待审/高风险/临期与逾期合同/未结案件/7 天内与逾期开庭/未结标的额 |
| `GET/POST /contracts` · `PUT/DELETE /contracts/{id}` | 合同审查 CRUD；pending→reviewing→approved/rejected→archived，禁止回跳；可空日期支持显式 null 清空 |
| `GET/POST /cases` · `PUT/DELETE /cases/{id}` | 案件 CRUD；filed→hearing→judgment→appeal/closed，closed 终态；开庭逾期可见 |
| `GET/POST /seals` · `PUT/DELETE /seals/{id}` | 用印台账 CRUD（份数 ≥1） |
| `GET /export/contracts.xlsx` · `/export/cases.xlsx` | 导出 |

### 行政人事 `/api/business/hr`

| 端点 | 说明 |
|---|---|
| `GET /summary?month=` | 在职/试用/离职/30 天到期/当月薪资人数与实发/个税/草稿数 |
| `GET /employees/expiring?days=30` | 试用或合同到期员工 |
| `GET/POST /employees` · `PUT/DELETE /employees/{id}` | 员工 CRUD（工号唯一；删除级联考勤/薪资） |
| `GET /attendance?month=` · `PUT /attendance/{emp_id}/{month}` | 月度考勤查询/upsert（同员工同月唯一） |
| `POST /payroll/generate` | 按月份生成薪资（`month/allowance/social_rate/housing_rate`；同月重复 409）；响应 `warnings[]` 逐人提示缺考勤；减除费用按入职月份数 |
| `GET /payroll?month=` | 月度薪资列表（含应发/社保/公积金/个税/实发） |
| `POST /payroll/{id}/confirm` · `DELETE /payroll/{id}` | 确认薪资（确认后不可删/不可重复确认）；删除仅草稿 |
| `GET /export/employees.xlsx` · `/export/payroll.xlsx?month=` | 导出 |

### 工程物业 `/api/business/facility`（2026-10-31 新增）

| 端点 | 说明 |
|---|---|
| `GET /summary?as_of=YYYY-MM-DD` | 设备总数/ABC 分布/运行维修报废/30 天内到期/逾期/楼宇级合规数 |
| `GET/POST /equipment` · `PUT/DELETE /equipment/{id}` | 设备一机一档（code 唯一 409）；仍有关联合规记录时删除 409；导出设备台账 |
| `GET/POST /compliance` · `PUT/DELETE /compliance/{id}` | 合规到期 CRUD（`equipment_id` 可空=楼宇级；kind annual_inspection/fire/maintenance/other；status pending/done/overdue 派生） |
| `GET /export/equipment.xlsx` · `/export/compliance.xlsx` | xlsx 导出（筛选一致、公式注入防护） |

### 总经理经营聚合 `/api/business/gm`（2026-10-31 新增）

| 端点 | 说明 |
|---|---|
| `GET /summary?period=YYYY-MM` | 六域 `{available,data,gap_reasons}` + 指标字典 + 缺源说明 + 高优先提醒；失败域不填 0；含 12 个月到期面积集中度（严格 >25% 才 warning） |

`business_gm` 无表、只读；Agent 工具 `gm_summary` 预置 `realestate`/存在的 `cre_gm`。签名与错误码：非法 period 422，缺指标时 value=null 且 reason 非空。

### 经营管理 `/api/business/manage`（2026-10-31 新增，v1.19.0 补齐）

| 端点 | 说明 |
|---|---|
| `GET /meta` | 字段字典：报表类型 / 项目 / section / 字段 / 指标键目录 |
| `POST /imports/preview` | multipart `file`（仅 `.xlsx`，≤10MB）：返回识别出的报表类型/项目/期间、各小节计数与预览 `token`；格式不识别 400 中文，token 有 TTL |
| `POST /imports/confirm` | `{token, project?, period?}` 确认导入；粒度 = 报表类型+项目+期间，**同粒度整批覆盖**（旧批次的指标/明细/文本一并替换）；token 过期或已用 400「预览已过期，请重新上传」 |
| `GET/POST /reports` · `GET/PUT/DELETE /reports/{id}` | 报表批次 CRUD（可新建空批次手工填报；删除级联指标/明细/文本） |
| `GET/PUT /reports/{id}/metrics` | 批量写入/修正指标（键必须在规格内，越界 400） |
| `POST /reports/{id}/records` · `PUT/DELETE /records/{id}` | 明细行新增 / 修改 / 删除 |
| `PUT /reports/{id}/texts` | 批量写入叙述小节 |
| `GET /overview?project=&months=` · `GET /overview/export.xlsx` | 总览指标卡 + 图表序列（出租率/收缴/招商/责任书/诉讼/预警）；`months` 1~36 默认 12；**有源才画、不补 0**；导出 xlsx |
| `GET /reports/{id}/export.xlsx` | 单份报表导出（指标/明细/文本三表） |

四份模板（内控管理 6 表 / 云南周报 / 曲靖周报 / 月度工作情况）按**标签文本锚点**解析、不锁行号；模板工作表名与标签行不要改。

## 模板下载 /api/templates

| 端点 | 说明 |
|---|---|
| `GET /api/templates` | 返回 `{total, items: [{name, module, size, available}]}`（共 14 份），清单来自仓库 `数据模板/`（固定白名单，不接受用户路径）；`size=0`/`available=false` 表示文件缺失 |
| `GET /api/templates/{name}` | 下载模板；文件名必须在白名单内，未命中返回 404；响应头同时给 ASCII 兜底 `filename="..."` 与 `filename*=utf-8''...` |

## 本机目录浏览 /api/fs

只读目录浏览，供前端「浏览选择目录」弹窗（`DirectoryPicker.vue`）逐层选取财务引擎 / POS 目录；不读文件内容，不提供写入。

| 端点 | 说明 |
|---|---|
| `GET /api/fs/drives` | 返回 `{drives: ["D:/…", …]}`：允许浏览的根目录（目录浏览起点）。默认根 = 用户主目录 + 数据根 `DATA_DIR` + 财务数据目录（`FINANCE_DATA_DIR`，留空为数据根下 `finance`）+ 已配置的 POS 对账单源目录 / 凭证输出目录，同路径去重；显式配置 `FS_BROWSE_ROOTS`（逗号或分号分隔）后以配置值为准 |
| `GET /api/fs/browse?path=` | 列出该目录下的**子目录**（不含文件）；`path` 为空返回根目录列表。返回 `{path, parent, entries:[{name, path, is_dir}]}`；单层最多 500 条，跳过 `.` / `$` 开头的隐藏项，指向白名单外的符号链接/目录联接不列出 |

语义要点：

- 绝对路径先 `resolve` 再判白名单，`..` 与符号链接按真实路径判定；越界 **403**，非绝对路径 / 不存在 / 不是目录 **400**（中文）。
- 该接口是普通 GET，**不在 OriginGuard 拦截范围**（只拦状态变更方法与含 `discover` 的路径）；跨站读取依赖 CORS 与默认仅监听 `127.0.0.1`。本项目无登录鉴权，不要把整个盘符加入 `FS_BROWSE_ROOTS`，也不要绑定 `0.0.0.0`。
- 非 Windows 平台同样按白名单工作，默认根为用户主目录与数据根，不返回盘符列表。

## 技能市场 /api/skills/market

| 端点 | 说明 |
|---|---|
| `GET /api/skills/market/scan?keyword=` | 聚合本机技能源（QwenPaw 技能池、WorkBuddy、平台种子）返回 `{items, sources}`；`keyword` 对名称/描述做子串过滤；单个来源不可用只标 `available=false`，不影响其他来源 |
| `POST /api/skills/market/install` | `{name}` 从市场一键安装进平台技能池；**默认停用**；已存在则更新；不存在返回 400 中文 |

## 专家团文件 /api/experts

| 端点 | 说明 |
|---|---|
| `GET /api/experts/packs` | 列出 `backend/data/expert-packs/*.md` 与数据库一致性差异 |
| `POST /api/experts/packs/import/{name}` · `POST /api/experts/packs/import-all` | 单文件/全部导入（upsert；工具白名单取并集） |
| `POST /api/experts/packs/export/{expert_name}` | 数据库专家导出为 `.md`（覆盖同名文件） |
| `POST /api/experts/packs/reset` | 从 `backend/seed_experts/` 重置同名文件 |
| `GET /api/experts/packs/{name}/content` · `DELETE /api/experts/packs/{name}` | 查看原文 / 仅删文件（不影响数据库专家） |

## 银行流水模板 /api/finance/bankflow

| 端点 | 说明 |
|---|---|
| `POST /preview` | multipart `file`（xlsx，≤20MB）：解析全部 sheet 的流水表头，返回 `{total, accounts:[{account, account_token, tail, name, ent, rows, date_min, date_max, mapped}], problems, warnings, unmapped_count}`；**`account` 只返回掩码**（如 `**** **** **** 1234`），完整账号留在服务端，导出凭 `account_token` 反查；表头缺失/文件损坏返回 400 中文 |
| `POST /template` | multipart `file` + `account_token`（首选）/`account`（仅兼容预览掩码）+ `date_start?` + `date_end?`（`YYYY-MM-DD`）：按账户与日期区间生成 BIP 银行对账单模板并直接下载 xlsx；令牌只在本批上传流水中反查，**掩码必须在流水中唯一匹配**（同尾号多账户返回 400 要求改用令牌）；账号未映射 400，日期格式错 400，骨架缺失 400；响应头 ASCII 兜底 + `filename*` |

**语义要点**：账号映射来自模块 `config.local.yaml`（全号精确优先、≥4 位尾号兜底）；无流水号时合成稳定 `SYN<20位>`；收入/支出同时有值、均为空、用途为空、坏日期在预览标出并在导出时拒绝；模板格式来自官方骨架 `template_skeleton.xlsx`（表头样式/列宽/数据验证/文本格式），不手改骨架。

## 看板产物校验 /api/finance/board

| 端点 | 说明 |
|---|---|
| `POST /run` | 刷新管线（优先计划任务通道）：单次尝试 = 可用性查询 15s + 等待状态查询 15s + 触发 30s + `max(计划任务通道 600s, 直接回退总预算 600s)` = **660s**；最多尝试 2 次（重试 1 次、间隔 5s、残留 `run.lock` 等待上限 60s），最坏 1385s，前端超时 1500s；成功归档快照并返回 `{status, stamp, attempts, steps, log_tail}`；并发 409；触发/步骤失败/超时 503 并落 failed 快照 |
| `GET /snapshots` · `GET /snapshots/{id}/html` | 快照列表 / 下载 HTML（仅看板目录内文件，带 `nosniff`） |
| `GET /verify?snapshot_id=` | **看板产物内容级巡检（仅接口，页面无按钮）**：提取 HTML 内嵌 `const R = {...}` 与管线 `data/report_data.json` 对照，返回 `{ok, issues, kpi, checked_at, cached}`；检查文件规模、JSON 可解析、敏感 `map` 剔除、顶层键一致（meta 不参与）、递归数值容差 0.01、关键 KPI 齐全 |

## 数据质量 /api/business/dq

| 端点 | 说明 |
|---|---|
| `GET /summary?as_of=YYYY-MM-DD` | 七域×五维只读巡检。`as_of` 用 `Query(default=None, pattern=...)`：空=今天，形状错 422，日历不存在日期（如 2026-02-30）也 422。返回 `{as_of, overall_score, issue_count, domains:{leasing,operations,hr,legal,planning,facility,finance}, top_issues}`；`issue_count`=Σ各检查命中行数，`top_issues` 最多 20 条；单域异常只该域 `available=false`；空库不误报 |

---

## 全量路由索引（由 `dev/gen_api_index.py` 从 OpenAPI 生成）

> 本表以运行时 `app.openapi()` 为准，覆盖全部 `/api` 路径与 HTTP 方法；手写章节负责语义，本表负责**不遗漏**。新增路由后运行 `cd backend && PYTHONPATH=. venv/Scripts/python.exe ../dev/gen_api_index.py` 重新生成并替换本节表格。`scripts/test_docs_contract.py` 会逐条比对，缺一条即失败。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/assistant/agents` | 专家团队列表（含停用，按 sort 排序） |
| `POST` | `/api/assistant/agents` | 新增专家 |
| `POST` | `/api/assistant/agents/reset` | 内置专家恢复出厂定义（按 name 覆盖/补插，不影响自定义专家） |
| `POST` | `/api/assistant/agents/route-test` | 路由预览：默认离线，可选低置信 LLM 仲裁 |
| `PUT` | `/api/assistant/agents/{agent_id}` | 更新专家（显式传 null 可清空 model_id/tools/skills） |
| `DELETE` | `/api/assistant/agents/{agent_id}` | 删除专家（内置专家允许删除，前端需自行警告） |
| `POST` | `/api/assistant/chat` | 与 Agent 团队对话（非流式） |
| `POST` | `/api/assistant/chat/stream` | 与 Agent 团队对话（SSE 流式） |
| `GET` | `/api/assistant/config` | 读取 AI 助手配置（模型状态/供应商/激活模型） |
| `PUT` | `/api/assistant/config` | 保存 Agent 工作参数（工具轮次/记忆管理） |
| `POST` | `/api/assistant/config/test` | 测试模型连通性（可选多模态/工具，仅 openai 协议） |
| `GET` | `/api/assistant/history/{session_id}` | 会话历史（时间正序） |
| `GET` | `/api/assistant/models` | 启用中的模型扁平列表（快速切换用） |
| `POST` | `/api/assistant/models/{model_id}/activate` | 激活模型（跨供应商，立即生效） |
| `POST` | `/api/assistant/models/{model_id}/capabilities/reset-overrides` | 清除模型手动能力覆盖并按最近同步快照恢复 |
| `GET` | `/api/assistant/plugins/discover` | 扫描 QwenPaw 插件目录（工具型插件兼容层；仅限已保存目录） |
| `GET` | `/api/assistant/providers` | 供应商列表（含各自模型，api_key 脱敏） |
| `POST` | `/api/assistant/providers` | 新增/更新供应商 |
| `DELETE` | `/api/assistant/providers/{provider_id}` | 删除供应商（含其模型） |
| `POST` | `/api/assistant/providers/{provider_id}/models` | 新增/更新供应商下的模型 |
| `DELETE` | `/api/assistant/providers/{provider_id}/models/{model_id}` | 删除模型 |
| `POST` | `/api/assistant/providers/{provider_id}/sync-models` | 同步供应商在线模型列表（只读目录/管控请求，不发推理请求） |
| `GET` | `/api/assistant/sessions` | 会话列表（按更新时间倒序；archived 参数按归档状态过滤） |
| `POST` | `/api/assistant/sessions` | 新建会话 |
| `PUT` | `/api/assistant/sessions/{session_id}` | 会话重命名 |
| `DELETE` | `/api/assistant/sessions/{session_id}` | 删除会话（含全部消息） |
| `POST` | `/api/assistant/sessions/{session_id}/archive` | 归档会话（不存在返回 404） |
| `GET` | `/api/assistant/sessions/{session_id}/export` | 导出会话为 Markdown 附件（不存在返回 404） |
| `POST` | `/api/assistant/sessions/{session_id}/unarchive` | 恢复会话（不存在返回 404） |
| `GET` | `/api/assistant/skills/discover` | 扫描技能池目录（SKILL.md 格式；仅限已保存目录） |
| `GET` | `/api/assistant/team` | Agent 团队名册、模型状态、配置与工作模式 |
| `POST` | `/api/assistant/upload` | 上传聊天附件（图片/文本/Excel/Office/PDF；上限 10MB） |
| `POST` | `/api/assistant/workbuddy/import` | 执行导入（幂等 upsert；dry_run 只预览不写） |
| `GET` | `/api/assistant/workbuddy/scan` | 扫描本机 WorkBuddy 数据（只读预览） |
| `GET` | `/api/assistant/workbuddy/settings` | 查看导入设置与上次导入结果 |
| `PUT` | `/api/assistant/workbuddy/settings` | 保存导入设置（WorkBuddy 数据目录） |
| `GET` | `/api/business/dq/summary` | 跨模块数据质量巡检总览 |
| `GET` | `/api/business/facility/compliance` | 合规到期台账（分页，状态派生） |
| `POST` | `/api/business/facility/compliance` | 新增合规事项 |
| `PUT` | `/api/business/facility/compliance/{compliance_id}` | 更新合规事项 |
| `DELETE` | `/api/business/facility/compliance/{compliance_id}` | 删除合规事项 |
| `GET` | `/api/business/facility/equipment` | 设备台账（分页） |
| `POST` | `/api/business/facility/equipment` | 新建设备 |
| `PUT` | `/api/business/facility/equipment/{equipment_id}` | 更新设备 |
| `DELETE` | `/api/business/facility/equipment/{equipment_id}` | 删除设备（有合规记录时拒绝） |
| `GET` | `/api/business/facility/export/compliance.xlsx` | 导出合规到期台账 |
| `GET` | `/api/business/facility/export/equipment.xlsx` | 导出设备台账 |
| `GET` | `/api/business/facility/summary` | 工程看板（设备等级/30 天到期/逾期） |
| `GET` | `/api/business/gm/summary` | 总经理只读经营聚合 |
| `GET` | `/api/business/hr/attendance` | 月度考勤列表 |
| `PUT` | `/api/business/hr/attendance/{emp_id}/{month}` | 录入/更新月度考勤 |
| `GET` | `/api/business/hr/employees` | 员工花名册（分页） |
| `POST` | `/api/business/hr/employees` | 新建员工 |
| `GET` | `/api/business/hr/employees/expiring` | N 天内试用/合同到期员工 |
| `PUT` | `/api/business/hr/employees/{employee_id}` | 更新员工 |
| `DELETE` | `/api/business/hr/employees/{employee_id}` | 删除员工（有已确认薪资时拒绝；否则连同考勤/草稿薪资） |
| `GET` | `/api/business/hr/export/employees.xlsx` | 导出员工花名册 |
| `GET` | `/api/business/hr/export/payroll.xlsx` | 导出月度薪资表 |
| `GET` | `/api/business/hr/payroll` | 月度薪资列表 |
| `POST` | `/api/business/hr/payroll/generate` | 按月份生成薪资（累计预扣个税） |
| `DELETE` | `/api/business/hr/payroll/{payroll_id}` | 删除薪资（仅草稿） |
| `POST` | `/api/business/hr/payroll/{payroll_id}/confirm` | 确认薪资 |
| `GET` | `/api/business/hr/summary` | 人事看板（在职/试用/到期/当月人力成本） |
| `GET` | `/api/business/leasing/export/leads.xlsx` | 导出招商线索 |
| `GET` | `/api/business/leasing/export/units.xlsx` | 导出铺位台账 |
| `GET` | `/api/business/leasing/funnel` | 招商漏斗（各阶段数量/面积/转化率） |
| `GET` | `/api/business/leasing/leads` | 线索列表（分页，可按阶段/负责人/公司筛选） |
| `POST` | `/api/business/leasing/leads` | 新建线索 |
| `GET` | `/api/business/leasing/leads/due` | 下次跟进已到期/今日的线索 |
| `PUT` | `/api/business/leasing/leads/{lead_id}` | 更新线索 |
| `DELETE` | `/api/business/leasing/leads/{lead_id}` | 删除线索（已签约须先把阶段改为已流失，连同跟进记录） |
| `GET` | `/api/business/leasing/leads/{lead_id}/followups` | 线索跟进记录 |
| `POST` | `/api/business/leasing/leads/{lead_id}/followups` | 新增跟进记录 |
| `GET` | `/api/business/leasing/units` | 铺位列表（分页，可按楼栋/状态/铺位号筛选） |
| `POST` | `/api/business/leasing/units` | 新建铺位 |
| `GET` | `/api/business/leasing/units/summary` | 铺位看板（空置率/各状态数量） |
| `PUT` | `/api/business/leasing/units/{unit_id}` | 更新铺位 |
| `DELETE` | `/api/business/leasing/units/{unit_id}` | 删除铺位（有效签约引用时拒绝） |
| `GET` | `/api/business/legal/cases` | 案件列表 |
| `POST` | `/api/business/legal/cases` | 新建案件 |
| `PUT` | `/api/business/legal/cases/{case_id}` | 更新案件 |
| `DELETE` | `/api/business/legal/cases/{case_id}` | 删除案件 |
| `GET` | `/api/business/legal/contracts` | 合同审查列表 |
| `POST` | `/api/business/legal/contracts` | 新建合同审查 |
| `PUT` | `/api/business/legal/contracts/{review_id}` | 更新合同审查 |
| `DELETE` | `/api/business/legal/contracts/{review_id}` | 删除合同审查 |
| `GET` | `/api/business/legal/export/cases.xlsx` | 导出案件台账 |
| `GET` | `/api/business/legal/export/contracts.xlsx` | 导出合同审查台账 |
| `GET` | `/api/business/legal/seals` | 用印列表 |
| `POST` | `/api/business/legal/seals` | 新建用印记录 |
| `PUT` | `/api/business/legal/seals/{seal_id}` | 更新用印记录 |
| `DELETE` | `/api/business/legal/seals/{seal_id}` | 删除用印记录 |
| `GET` | `/api/business/legal/summary` | 法务看板（待审/高风险/到期/开庭） |
| `POST` | `/api/business/manage/imports/confirm` | 确认导入（同类型+项目+期间整批替换） |
| `POST` | `/api/business/manage/imports/preview` | 上传报表并预览（识别类型/期间/小节计数，10MB） |
| `GET` | `/api/business/manage/meta` | 字段字典（报表类型/项目/section/字段/指标键目录） |
| `GET` | `/api/business/manage/overview` | 总览：指标卡 + 图表序列（出租率/收缴/招商/责任书/诉讼/预警） |
| `GET` | `/api/business/manage/overview/export.xlsx` | 导出总览（指标卡与各图表序列） |
| `PUT` | `/api/business/manage/records/{record_id}` | 修改明细行 |
| `DELETE` | `/api/business/manage/records/{record_id}` | 删除明细行 |
| `GET` | `/api/business/manage/reports` | 报表批次列表（按期间倒序，含指标/明细/文本条数） |
| `POST` | `/api/business/manage/reports` | 新建空批次（手工填报） |
| `GET` | `/api/business/manage/reports/{report_id}` | 报表详情（指标 + 明细 + 叙述） |
| `PUT` | `/api/business/manage/reports/{report_id}` | 更新批次表头（标题/备注/期间） |
| `DELETE` | `/api/business/manage/reports/{report_id}` | 删除批次（级联明细/指标/文本） |
| `GET` | `/api/business/manage/reports/{report_id}/export.xlsx` | 导出单份报表（指标/明细/文本三表） |
| `PUT` | `/api/business/manage/reports/{report_id}/metrics` | 批量写入/修正指标（键必须在规格内） |
| `POST` | `/api/business/manage/reports/{report_id}/records` | 新增明细行 |
| `PUT` | `/api/business/manage/reports/{report_id}/texts` | 批量写入叙述小节 |
| `GET` | `/api/business/operations/bills` | 账单列表（按期间/状态/合同筛选） |
| `GET` | `/api/business/operations/bills/aging` | 欠费账龄（固定截止日四桶与未清明细） |
| `POST` | `/api/business/operations/bills/{bill_id}/payments` | 登记收款（累计后自动改状态） |
| `GET` | `/api/business/operations/contracts` | 合同列表（分页） |
| `POST` | `/api/business/operations/contracts` | 新建合同 |
| `GET` | `/api/business/operations/contracts/expiring` | N 天内到期合同（默认 30 天） |
| `PUT` | `/api/business/operations/contracts/{contract_id}` | 更新合同 |
| `DELETE` | `/api/business/operations/contracts/{contract_id}` | 删除合同（连同未收款账单；已收款需先处理） |
| `POST` | `/api/business/operations/contracts/{contract_id}/bills` | 按期间生成账单 |
| `GET` | `/api/business/operations/export/bills.xlsx` | 导出账单明细 |
| `GET` | `/api/business/operations/export/contracts.xlsx` | 导出合同台账 |
| `GET` | `/api/business/operations/export/work-orders.xlsx` | 导出工单（与列表同筛选） |
| `GET` | `/api/business/operations/summary` | 营运看板（合同/应收/实收/收缴率/欠费/工单） |
| `GET` | `/api/business/operations/work-orders` | 工单列表（按状态/类型/描述筛选） |
| `POST` | `/api/business/operations/work-orders` | 新建工单 |
| `PUT` | `/api/business/operations/work-orders/{order_id}` | 更新工单（含状态流转） |
| `DELETE` | `/api/business/operations/work-orders/{order_id}` | 删除工单 |
| `GET` | `/api/business/operations/work-orders/{order_id}/followups` | 工单跟进记录 |
| `POST` | `/api/business/operations/work-orders/{order_id}/followups` | 新增工单跟进 |
| `PUT` | `/api/business/operations/work-orders/{order_id}/followups/{followup_id}` | 更新工单跟进 |
| `DELETE` | `/api/business/operations/work-orders/{order_id}/followups/{followup_id}` | 删除工单跟进 |
| `GET` | `/api/business/planning/campaigns` | 活动列表（按年度/状态/类型/名称筛选） |
| `POST` | `/api/business/planning/campaigns` | 新建活动 |
| `GET` | `/api/business/planning/campaigns/calendar` | 年度企划日历（按月分组） |
| `PUT` | `/api/business/planning/campaigns/{campaign_id}` | 更新活动（含预算/复盘） |
| `DELETE` | `/api/business/planning/campaigns/{campaign_id}` | 删除活动 |
| `GET` | `/api/business/planning/export/campaigns.xlsx` | 导出企划活动与预算 |
| `GET` | `/api/business/planning/summary` | 企划看板（年度预算/实际/超支/平均 ROI） |
| `GET` | `/api/business/planning/vendors` | 供应商列表（按服务类型/名称筛选） |
| `POST` | `/api/business/planning/vendors` | 新建供应商 |
| `PUT` | `/api/business/planning/vendors/{vendor_id}` | 更新供应商 |
| `DELETE` | `/api/business/planning/vendors/{vendor_id}` | 删除供应商 |
| `GET` | `/api/dashboard/summary` | 工作台统计数据（概览页一次取全） |
| `GET` | `/api/debug/client-errors` | 前端报错列表（尾部 limit 条） |
| `POST` | `/api/debug/client-errors` | 追加一条前端报错（有界宽容解析） |
| `DELETE` | `/api/debug/client-errors` | 清空前端报错 |
| `GET` | `/api/debug/issues` | 问题列表（可按状态/严重度筛选） |
| `POST` | `/api/debug/issues` | 新建问题 |
| `PUT` | `/api/debug/issues/{issue_id}` | 更新问题（自动刷新 updated_at） |
| `DELETE` | `/api/debug/issues/{issue_id}` | 删除问题 |
| `GET` | `/api/debug/runs` | 测试运行列表（时间倒序） |
| `GET` | `/api/debug/runs/{run_id}` | 测试运行详情（含截图文件名列表） |
| `DELETE` | `/api/debug/runs/{run_id}` | 删除一次测试运行（整目录） |
| `GET` | `/api/debug/runs/{run_id}/shots/{filename}` | 运行截图（防路径穿越） |
| `GET` | `/api/debug/summary` | 调试中心总览 |
| `GET` | `/api/experts/packs` | 专家包文件清单（含与数据库的一致性比对） |
| `POST` | `/api/experts/packs/export/{expert_name}` | 把数据库专家导出为 .md 文件（覆盖同名文件） |
| `POST` | `/api/experts/packs/import-all` | 导入全部有效专家包（upsert） |
| `POST` | `/api/experts/packs/import/{name}` | 导入指定专家包（upsert 进 agent_specs） |
| `POST` | `/api/experts/packs/reset` | 从内置种子重置专家包目录（覆盖同名文件） |
| `DELETE` | `/api/experts/packs/{name}` | 删除专家包文件（不影响数据库中的专家） |
| `GET` | `/api/experts/packs/{name}/content` | 查看专家包 .md 原文 |
| `GET` | `/api/finance/bankflow/accounts` | 已登记的账号映射（账号仅展示尾 4 位，防止完整卡号外泄） |
| `POST` | `/api/finance/bankflow/preview` | 解析流水文件并按账号汇总预览（异常行计数 + 中文提示） |
| `POST` | `/api/finance/bankflow/template` | 按账号令牌与日期区间生成 BIP 银行流水导入模板（直接下载 xlsx） |
| `GET` | `/api/finance/board/latest` | 最新成功快照（无则 404） |
| `POST` | `/api/finance/board/run` | 运行一次报表监控管线并归档快照 |
| `POST` | `/api/finance/board/runs` | 异步运行报表监控管线（202 + run_id，轮询 GET /runs/{run_id}） |
| `GET` | `/api/finance/board/runs/{run_id}` | 查询异步运行状态 |
| `GET` | `/api/finance/board/snapshots` | 看板快照列表（按时间倒序） |
| `GET` | `/api/finance/board/snapshots/{snapshot_id}/html` | 看板 HTML（自包含单文件，可直接 iframe 嵌入） |
| `GET` | `/api/finance/board/verify` | 看板产物内容级巡检（HTML 内嵌数据 vs 管线数据源） |
| `GET` | `/api/finance/cashflow/runs` | 运行记录列表（分页，按 id 倒序） |
| `POST` | `/api/finance/cashflow/runs` | 上传输入文件建运行（银行辅助明细账+模板等，或一个 ZIP） |
| `GET` | `/api/finance/cashflow/runs/{run_id}` | 运行记录详情 |
| `GET` | `/api/finance/cashflow/runs/{run_id}/artifacts/{name}` | 下载单个产物（支持子目录相对名） |
| `GET` | `/api/finance/cashflow/runs/{run_id}/download` | 打包下载全部产物（ZIP） |
| `POST` | `/api/finance/cashflow/runs/{run_id}/execute` | 执行运行（子进程调引擎，约 1-3 分钟） |
| `GET` | `/api/finance/deferred/items` | 项目列表（分页/状态/关键词） |
| `POST` | `/api/finance/deferred/items` | 新建长期待摊项目（草稿） |
| `GET` | `/api/finance/deferred/items/{item_id}` | 项目详情与计数 |
| `PATCH` | `/api/finance/deferred/items/{item_id}` | 维护项目参数（归档后只读） |
| `DELETE` | `/api/finance/deferred/items/{item_id}` | 删除项目（有测算历史 409） |
| `GET` | `/api/finance/deferred/items/{item_id}/runs` | 测算列表（最新在前） |
| `POST` | `/api/finance/deferred/items/{item_id}/runs` | 创建/幂等返回摊销测算 |
| `GET` | `/api/finance/deferred/runs/{run_id}` | 测算详情（含过期标记） |
| `GET` | `/api/finance/deferred/runs/{run_id}/checks` | 统一校验结果与计数 |
| `POST` | `/api/finance/deferred/runs/{run_id}/confirm` | 确认测算（风险警告需显式风险说明） |
| `GET` | `/api/finance/deferred/runs/{run_id}/export.xlsx` | 导出三页签 xlsx 底稿 |
| `GET` | `/api/finance/deferred/runs/{run_id}/periods` | 月份级摊销行 |
| `GET` | `/api/finance/deferred/summary` | 长期待摊摘要（按项目或全部） |
| `GET` | `/api/finance/dsf/filings` | 填报记录列表 |
| `POST` | `/api/finance/dsf/filings` | 创建并计算残保金填报 |
| `GET` | `/api/finance/dsf/filings/{filing_id}` | 填报详情 |
| `PUT` | `/api/finance/dsf/filings/{filing_id}` | 更正残保金填报（重算；已导出/已提交不可覆盖） |
| `DELETE` | `/api/finance/dsf/filings/{filing_id}` | 删除填报记录 |
| `GET` | `/api/finance/dsf/filings/{filing_id}/download` | 下载残保金申报底稿 |
| `POST` | `/api/finance/dsf/filings/{filing_id}/export` | 生成残保金申报底稿 xlsx |
| `POST` | `/api/finance/expense/classify` | 上传银行存款辅助明细账 xlsx 并执行费用分类 |
| `POST` | `/api/finance/expense/rules/reload` | 重读规则 JSON 并返回最新摘要 |
| `GET` | `/api/finance/expense/rules/summary` | 分类规则摘要（条数/分类数/来源路径） |
| `GET` | `/api/finance/expense/runs` | 分类运行记录分页列表 |
| `GET` | `/api/finance/expense/runs/{run_id}` | 运行详情（含分类汇总统计与明细行） |
| `GET` | `/api/finance/expense/runs/{run_id}/download` | 下载分类结果 xlsx（中文文件名，RFC5987） |
| `GET` | `/api/finance/hello/entries` | 样例条目列表（按 id 倒序） |
| `POST` | `/api/finance/hello/entries` | 新建样例条目 |
| `GET` | `/api/finance/hello/ping` | 模块连通性自检（机制实证） |
| `GET` | `/api/finance/iit/filings` | 填报记录列表 |
| `POST` | `/api/finance/iit/filings` | 创建并计算个税扣缴填报 |
| `GET` | `/api/finance/iit/filings/{filing_id}` | 填报详情 |
| `PUT` | `/api/finance/iit/filings/{filing_id}` | 更正个税扣缴填报（重算；已导出/已提交不可覆盖） |
| `DELETE` | `/api/finance/iit/filings/{filing_id}` | 删除填报记录 |
| `GET` | `/api/finance/iit/filings/{filing_id}/download` | 下载扣缴申报底稿 |
| `POST` | `/api/finance/iit/filings/{filing_id}/export` | 生成扣缴申报底稿 xlsx |
| `GET` | `/api/finance/iit/standards` | 专项附加扣除标准表（国发〔2018〕41号 + 国发〔2023〕13号） |
| `GET` | `/api/finance/invoice/batches` | 批次列表（分页，按 id 倒序） |
| `POST` | `/api/finance/invoice/batches` | 上传发票建批次并解析（多个 PDF 或一个 ZIP；引擎缺失时 503 降级） |
| `GET` | `/api/finance/invoice/batches/{batch_id}` | 批次详情（含明细） |
| `DELETE` | `/api/finance/invoice/batches/{batch_id}` | 删除批次（明细 + inbox 文件 + 导出 ZIP） |
| `GET` | `/api/finance/invoice/batches/{batch_id}/download` | 下载批次导出 ZIP（未导出返回 409） |
| `POST` | `/api/finance/invoice/batches/{batch_id}/export` | 导出批次（重命名复制 + 清单 xlsx + ZIP 打包） |
| `GET` | `/api/finance/invoice/batches/{batch_id}/projects` | 项目汇总（张数/净额/单项目 ZIP/PDF 文件名） |
| `GET` | `/api/finance/invoice/batches/{batch_id}/projects/download` | 下载单个项目 ZIP（未导出 409、项目无包 404） |
| `GET` | `/api/finance/invoice/batches/{batch_id}/projects/pdf` | 下载单个项目合并 PDF（按当前归类即时合并，无需先导出；缺件整次失败） |
| `POST` | `/api/finance/invoice/batches/{batch_id}/reclassify` | 按项目归类规则重新归类（人工修正不覆盖） |
| `GET` | `/api/finance/invoice/engine/status` | 发票解析引擎可用性自检（降级探测，永不 503） |
| `PATCH` | `/api/finance/invoice/items/{item_id}` | 人工修正发票明细（项目归类/备注） |
| `GET` | `/api/finance/lease/calculations/{calculation_id}` | 测算详情（含过期标记） |
| `GET` | `/api/finance/lease/calculations/{calculation_id}/assessment` | 付款期级评估行 |
| `GET` | `/api/finance/lease/calculations/{calculation_id}/checks` | 统一校验结果与计数 |
| `POST` | `/api/finance/lease/calculations/{calculation_id}/confirm` | 确认测算（不可改写快照） |
| `GET` | `/api/finance/lease/calculations/{calculation_id}/export.xlsx` | 导出五页签 xlsx 底稿 |
| `GET` | `/api/finance/lease/calculations/{calculation_id}/periods` | 月份级期间行 |
| `GET` | `/api/finance/lease/contracts` | 合同列表（分页/状态/关键词） |
| `POST` | `/api/finance/lease/contracts` | 新建租赁合同（draft） |
| `GET` | `/api/finance/lease/contracts/{contract_id}` | 合同详情与计数 |
| `PATCH` | `/api/finance/lease/contracts/{contract_id}` | 维护合同（归档后只读） |
| `DELETE` | `/api/finance/lease/contracts/{contract_id}` | 删除合同（有测算历史 409） |
| `GET` | `/api/finance/lease/contracts/{contract_id}/calculations` | 测算列表（最新在前） |
| `POST` | `/api/finance/lease/contracts/{contract_id}/calculations` | 创建/幂等返回测算 |
| `GET` | `/api/finance/lease/contracts/{contract_id}/payments` | 付款计划列表（按期数升序） |
| `PUT` | `/api/finance/lease/contracts/{contract_id}/payments` | 整表替换付款计划（仅草稿） |
| `POST` | `/api/finance/lease/contracts/{contract_id}/payments/import/apply` | 确认导入付款计划 xlsx（无错误才整表替换） |
| `POST` | `/api/finance/lease/contracts/{contract_id}/payments/import/preview` | 预览付款计划 xlsx（只解析，不落库） |
| `POST` | `/api/finance/lease/reconciliation/check` | BIP 只读对账（承租人/账簿/期间五科目勾稽） |
| `GET` | `/api/finance/lease/reconciliation/context` | BIP 对账下拉上下文（合同承租人 + BIP 账簿/期间） |
| `GET` | `/api/finance/lease/reconciliation/mappings` | BIP 对账科目映射列表（本模块映射表） |
| `PUT` | `/api/finance/lease/reconciliation/mappings` | 整批替换 BIP 对账科目映射（绝不写 BIP） |
| `GET` | `/api/finance/lease/summary` | 租赁核算摘要（按合同或全部） |
| `GET` | `/api/finance/ledger/bip/balances` | 科目余额表行查询（期间 + 科目前缀 / 账簿，含全量合计） |
| `GET` | `/api/finance/ledger/bip/balances/summary` | 科目发生额汇总（凭证分录优先，仅余额表时回退余额表） |
| `GET` | `/api/finance/ledger/bip/entries` | 分录查询（科目编码前缀/期间等组合筛选） |
| `GET` | `/api/finance/ledger/bip/imports` | 导入批次列表（按时间倒序分页） |
| `POST` | `/api/finance/ledger/bip/imports/confirm` | 确认导入（同来源重导幂等、跨来源合并补写、并发冲突 409） |
| `POST` | `/api/finance/ledger/bip/imports/preview` | 上传 BIP 导出文件并预览（类型识别 + 列映射建议 + 前 50 行；xlsx/xls，上限 10MB） |
| `GET` | `/api/finance/ledger/bip/vouchers` | 凭证组合筛选（期间/账簿/凭证号/摘要关键词）+ 分页 |
| `POST` | `/api/finance/ledger/bip/vouchers/export-to-vault` | 凭证数据入仓（依赖 finance_vault 模块；不可用时返回 503） |
| `GET` | `/api/finance/ledger/bip/vouchers/{voucher_id}` | 凭证详情（分录借贷两栏对称） |
| `GET` | `/api/finance/ledger/personal/accounts` | 账户列表 |
| `POST` | `/api/finance/ledger/personal/accounts` | 新建账户 |
| `PUT` | `/api/finance/ledger/personal/accounts/{account_id}` | 更新账户 |
| `DELETE` | `/api/finance/ledger/personal/accounts/{account_id}` | 删除账户（有交易引用的拒绝） |
| `GET` | `/api/finance/ledger/personal/budgets` | 某月预算视图（总预算 + 各一级支出分类） |
| `PUT` | `/api/finance/ledger/personal/budgets` | 整月预算整体保存（幂等，amount<=0 清除该项） |
| `GET` | `/api/finance/ledger/personal/categories` | 分类列表（flat，可按类型过滤） |
| `POST` | `/api/finance/ledger/personal/categories` | 新建分类（最多两级） |
| `PUT` | `/api/finance/ledger/personal/categories/{category_id}` | 更新分类名称 / 排序 |
| `DELETE` | `/api/finance/ledger/personal/categories/{category_id}` | 删除分类（有子分类或交易的拒绝） |
| `GET` | `/api/finance/ledger/personal/export/csv` | 导出 CSV（utf-8-sig，中文列名，可带筛选条件） |
| `POST` | `/api/finance/ledger/personal/import/csv` | 导入 CSV（中文列名，上限 5MB） |
| `GET` | `/api/finance/ledger/personal/stats/monthly` | 月度汇总（收入/支出/结余 + 分类占比） |
| `GET` | `/api/finance/ledger/personal/stats/trend` | 最近 N 个月收支趋势 |
| `GET` | `/api/finance/ledger/personal/transactions` | 交易列表（月份/分类/账户/关键词筛选 + 分页） |
| `POST` | `/api/finance/ledger/personal/transactions` | 记一笔 |
| `PUT` | `/api/finance/ledger/personal/transactions/{tx_id}` | 更新交易 |
| `DELETE` | `/api/finance/ledger/personal/transactions/{tx_id}` | 删除交易 |
| `GET` | `/api/finance/pos/runs` | 运行记录列表（按 id 倒序） |
| `POST` | `/api/finance/pos/runs` | 上传输入文件建运行（SHOP 对账单 + 认领记录 + 辅助明细账 + 凭证列表，或一个 ZIP） |
| `GET` | `/api/finance/pos/runs/{run_id}` | 运行记录详情 |
| `DELETE` | `/api/finance/pos/runs/{run_id}` | 删除运行（running 中拒绝；运行目录一并清理） |
| `GET` | `/api/finance/pos/runs/{run_id}/artifacts/{name}` | 下载单个产物 |
| `GET` | `/api/finance/pos/runs/{run_id}/download` | 打包下载全部产物（ZIP） |
| `POST` | `/api/finance/pos/runs/{run_id}/execute` | 执行运行（台账生成 + 四源差异分析） |
| `GET` | `/api/finance/pos/vouchers` | 凭证批次列表 |
| `GET` | `/api/finance/pos/vouchers/config` | 凭证目录默认值（源目录/输出目录回退项） |
| `POST` | `/api/finance/pos/vouchers/generate` | 生成月度刷卡凭证（回单×对账单匹配合并；源目录/输出目录可人工指定） |
| `DELETE` | `/api/finance/pos/vouchers/{batch_id}` | 删除凭证批次 |
| `GET` | `/api/finance/pos/vouchers/{batch_id}/files/{name}` | 下载单个凭证文件 |
| `POST` | `/api/finance/pos/vouchers/{batch_id}/merge` | 合并选中的凭证 PDF 下载 |
| `GET` | `/api/finance/receipt/batches` | 回单整理批次列表（不含明细） |
| `GET` | `/api/finance/receipt/batches/{batch_id}` | 批次详情（含逐笔明细） |
| `GET` | `/api/finance/receipt/batches/{batch_id}/download` | 整批产物打包下载（ZIP，保留账户目录） |
| `GET` | `/api/finance/receipt/batches/{batch_id}/files/{file_id}` | 下载或预览单个生成文件 |
| `POST` | `/api/finance/receipt/batches/{batch_id}/generate` | 按已复核的纳入项生成账户×类别 PDF 与清单（原子替换） |
| `PATCH` | `/api/finance/receipt/batches/{batch_id}/items/{item_id}` | 逐笔复核：修改分类或是否纳入 |
| `GET` | `/api/finance/receipt/batches/{batch_id}/items/{item_id}/preview` | 单笔原回单预览（PDF） |
| `POST` | `/api/finance/receipt/batches/{batch_id}/merge` | 合并选中的 PDF 分册并下载 |
| `GET` | `/api/finance/receipt/config` | 银行回单整理默认目录与五类口径 |
| `POST` | `/api/finance/receipt/scan` | 按账期扫描回单源目录，建立待复核批次（不生成 PDF） |
| `GET` | `/api/finance/recon/batches` | 批次列表（最新在前，精简视图） |
| `POST` | `/api/finance/recon/batches` | 新建商户核销对账批次（draft） |
| `GET` | `/api/finance/recon/batches/{batch_id}` | 批次详情（含合同范围与子表计数） |
| `PATCH` | `/api/finance/recon/batches/{batch_id}` | 维护批次（仅 draft/failed：名称、对账日、容差、合同范围） |
| `DELETE` | `/api/finance/recon/batches/{batch_id}` | 删除批次（仅 draft/failed，级联清理五张子表；ready 为审计留痕） |
| `POST` | `/api/finance/recon/batches/{batch_id}/check` | 执行勾稽检查（precheck→checking→ready\|failed） |
| `GET` | `/api/finance/recon/batches/{batch_id}/export/report.docx` | 导出对账报告 Word（通用排版；未决事项显著标注草稿；needs_recheck 409） |
| `GET` | `/api/finance/recon/batches/{batch_id}/export/report.md` | 导出对账报告 Markdown（与 xlsx/docx 同源；未决事项显著标注草稿） |
| `GET` | `/api/finance/recon/batches/{batch_id}/export/xlsx` | 导出对账工作底稿 xlsx（五类工作表；口径参数改动未重跑时 409） |
| `GET` | `/api/finance/recon/batches/{batch_id}/issues` | 问题清单（机检+人工，origin/status/active 过滤） |
| `POST` | `/api/finance/recon/batches/{batch_id}/issues` | 人工补记问题/待补资料（origin=human；机检永不触碰） |
| `GET` | `/api/finance/recon/batches/{batch_id}/matches` | 匹配清单（机检建议 + 人工裁定） |
| `POST` | `/api/finance/recon/batches/{batch_id}/matches` | 人工新增认领↔核销匹配建议（唯一三元组；确认时才校验分摊超额） |
| `GET` | `/api/finance/recon/batches/{batch_id}/records` | 事实记录分页查询（kind/合同/单据过滤；page_size≤200） |
| `GET` | `/api/finance/recon/batches/{batch_id}/sources` | 源文件列表（含解析状态/失败原因/检测到的合同；绝不返回存储路径） |
| `POST` | `/api/finance/recon/batches/{batch_id}/sources/{kind}` | 上传四类源文件之一（仅 draft/failed；重复内容返回 skipped_duplicates 不重复导入） |
| `DELETE` | `/api/finance/recon/batches/{batch_id}/sources/{source_id}` | 删除源文件（仅 draft/failed；级联 records+suggested matches；人工引用 409） |
| `GET` | `/api/finance/recon/batches/{batch_id}/summary` | 批次勾稽摘要（统一报告视图，含合计/检查状态/待确认数） |
| `GET` | `/api/finance/recon/batches/{batch_id}/terms` | 口径参数清单（逐合同完整性与缺失项） |
| `PUT` | `/api/finance/recon/batches/{batch_id}/terms` | 保存单合同口径参数（ready 批次修改置 needs_recheck） |
| `PATCH` | `/api/finance/recon/issues/{issue_id}` | 人工裁定问题：状态/级别/说明（确认或排除必须给说明） |
| `PATCH` | `/api/finance/recon/matches/{match_id}` | 人工裁定匹配：状态/分摊/说明（confirmed/rejected 分摊冻结） |
| `GET` | `/api/finance/stamp-duty/filings` | 填报记录列表（分页，按 id 倒序） |
| `POST` | `/api/finance/stamp-duty/filings` | 创建并计算印花税填报 |
| `GET` | `/api/finance/stamp-duty/filings/{filing_id}` | 填报记录详情 |
| `PUT` | `/api/finance/stamp-duty/filings/{filing_id}` | 更正印花税填报（重算；已导出/已提交不可覆盖） |
| `DELETE` | `/api/finance/stamp-duty/filings/{filing_id}` | 删除填报记录 |
| `GET` | `/api/finance/stamp-duty/filings/{filing_id}/download` | 下载申报表底稿 |
| `POST` | `/api/finance/stamp-duty/filings/{filing_id}/export` | 生成申报表底稿 xlsx |
| `GET` | `/api/finance/stamp-duty/tax-items` | 印花税税目税率表（印花税法） |
| `GET` | `/api/finance/tax/enterprises/` | Get Enterprises |
| `POST` | `/api/finance/tax/enterprises/` | Create Enterprise |
| `GET` | `/api/finance/tax/enterprises/{enterprise_id}` | Get Enterprise |
| `PUT` | `/api/finance/tax/enterprises/{enterprise_id}` | Update Enterprise |
| `DELETE` | `/api/finance/tax/enterprises/{enterprise_id}` | Delete Enterprise |
| `POST` | `/api/finance/tax/financial-import/import` | 导入财务报表 |
| `POST` | `/api/finance/tax/financial-import/import/{enterprise_id}` | 导入财务报表到指定企业 |
| `GET` | `/api/finance/tax/financial-import/template` | 下载导入模板 |
| `GET` | `/api/finance/tax/financial-import/template/balance-sheet` | 下载资产负债表模板 |
| `GET` | `/api/finance/tax/financial-import/template/income-statement` | 下载利润表模板 |
| `GET` | `/api/finance/tax/policies/` | Get Policies |
| `POST` | `/api/finance/tax/policies/` | Create Policy |
| `GET` | `/api/finance/tax/policies/active` | Get Active Policies |
| `GET` | `/api/finance/tax/policies/types` | Get Policy Types |
| `GET` | `/api/finance/tax/policies/{policy_id}` | Get Policy |
| `PUT` | `/api/finance/tax/policies/{policy_id}` | Update Policy |
| `DELETE` | `/api/finance/tax/policies/{policy_id}` | Delete Policy |
| `POST` | `/api/finance/tax/policies/{policy_id}/activate` | Activate Policy |
| `POST` | `/api/finance/tax/policies/{policy_id}/clone` | Clone Policy |
| `POST` | `/api/finance/tax/policies/{policy_id}/deactivate` | Deactivate Policy |
| `GET` | `/api/finance/tax/scope` | 税种准入清单 |
| `GET` | `/api/finance/tax/scope/{tax_key}` | 单个税种准入状态 |
| `GET` | `/api/finance/tax/tax-returns/` | Get Tax Returns |
| `POST` | `/api/finance/tax/tax-returns/` | Create Tax Return |
| `POST` | `/api/finance/tax/tax-returns/calculate` | Calculate Tax Detail |
| `GET` | `/api/finance/tax/tax-returns/{return_id}` | Get Tax Return |
| `PUT` | `/api/finance/tax/tax-returns/{return_id}` | Update Tax Return |
| `DELETE` | `/api/finance/tax/tax-returns/{return_id}` | Delete Tax Return |
| `GET` | `/api/finance/tax/tax-returns/{return_id}/export/excel` | Export Tax Return Excel |
| `GET` | `/api/finance/tax/tax-returns/{return_id}/export/pdf` | Export Tax Return Pdf |
| `PUT` | `/api/finance/tax/tax-returns/{return_id}/submit` | Submit Tax Return |
| `GET` | `/api/finance/tax/textbook-reference/` | Get All Textbooks |
| `GET` | `/api/finance/tax/textbook-reference/full-reference` | Get Full Reference |
| `GET` | `/api/finance/tax/textbook-reference/knowledge-points/{kp_id}` | Get Knowledge Point Detail |
| `GET` | `/api/finance/tax/textbook-reference/mappings` | Get System Function Mappings |
| `GET` | `/api/finance/tax/textbook-reference/{textbook_name}` | Get Textbook Detail |
| `GET` | `/api/finance/union/batches` | 批次列表（分页/主体/期间/状态） |
| `POST` | `/api/finance/union/batches` | 新建计提批次（draft，主体+期间唯一） |
| `GET` | `/api/finance/union/batches/{batch_id}` | 批次详情（批次头 + 部门行 + 合计 + 校验） |
| `PATCH` | `/api/finance/union/batches/{batch_id}` | 维护批次（仅草稿） |
| `DELETE` | `/api/finance/union/batches/{batch_id}` | 删除批次（仅草稿，连带部门行） |
| `GET` | `/api/finance/union/batches/{batch_id}/checks` | 校验结论与计数（ok/failed/unknown） |
| `POST` | `/api/finance/union/batches/{batch_id}/confirm` | 确认批次（draft → confirmed，缺数据拒绝） |
| `GET` | `/api/finance/union/batches/{batch_id}/export.xlsx` | 导出三页签 xlsx 底稿 |
| `GET` | `/api/finance/union/batches/{batch_id}/lines` | 部门行列表（含实时派生金额） |
| `PUT` | `/api/finance/union/batches/{batch_id}/lines` | 整批保存部门行（仅草稿，部门唯一） |
| `GET` | `/api/finance/union/batches/{batch_id}/preview` | 计算预览（只读，不落库） |
| `GET` | `/api/finance/union/summary` | 工会经费计提摘要（按主体/期间） |
| `GET` | `/api/finance/vat/filings` | 填报记录列表 |
| `POST` | `/api/finance/vat/filings` | 创建并计算增值税填报 |
| `GET` | `/api/finance/vat/filings/{filing_id}` | 填报详情 |
| `PUT` | `/api/finance/vat/filings/{filing_id}` | 更正增值税填报（重算；已导出/已提交不可覆盖） |
| `DELETE` | `/api/finance/vat/filings/{filing_id}` | 删除填报记录 |
| `GET` | `/api/finance/vat/filings/{filing_id}/download` | 下载申报底稿 |
| `POST` | `/api/finance/vat/filings/{filing_id}/export` | 生成申报底稿 xlsx |
| `POST` | `/api/finance/vault/ingest/file` | 上传 xlsx/csv 入仓（扩展名白名单 + 大小限制 + 留档） |
| `POST` | `/api/finance/vault/query` | 只读 SQL 查询（SELECT-only 校验 + 表名白名单 + 强制限流 + 超时中断） |
| `GET` | `/api/finance/vault/tables` | 数据集清单（表名/行数/来源/注册时间） |
| `DELETE` | `/api/finance/vault/tables/{table}` | 安全删除数据集（confirm=数据集名二次确认；被引用需 force=true） |
| `POST` | `/api/finance/vault/tables/{table}/aggregate` | 单表聚合：维度分组 + 指标汇总 + 过滤 + 排序 |
| `POST` | `/api/finance/vault/tables/{table}/aggregate/export` | 聚合结果导出 xlsx（safe_cell 防公式注入） |
| `POST` | `/api/finance/vault/tables/{table}/aggregate/save` | 聚合结果另存为数据池数据集 |
| `POST` | `/api/finance/vault/tables/{table}/clean/apply` | 应用清洗：结果落新数据集 + 写配方日志（可复用重放） |
| `GET` | `/api/finance/vault/tables/{table}/clean/log` | 清洗日志（配方+逐算子报告，按 id 倒序） |
| `POST` | `/api/finance/vault/tables/{table}/clean/preview` | 清洗预览（dry-run，不写库） |
| `GET` | `/api/finance/vault/tables/{table}/preview` | 已注册表数据预览（前 100 行） |
| `GET` | `/api/finance/vault/tables/{table}/profile` | 数据集列画像（类型/缺失率/唯一率/高频值 + 清洗建议） |
| `POST` | `/api/finance/vault/tables/{table}/rename` | 重命名数据集（视图+物理修订表+登记+清洗日志引用同步） |
| `GET` | `/api/finance/vault/tables/{table}/usage` | 数据集使用情况预检（物理修订表/清洗日志引用/一致性，供删除二次确认） |
| `GET` | `/api/fs/browse` | 浏览目录（仅子目录，不含文件） |
| `GET` | `/api/fs/drives` | 允许浏览的根目录（目录浏览起点） |
| `GET` | `/api/health` | 健康检查 |
| `HEAD` | `/api/health` | 健康检查 |
| `GET` | `/api/jobs` | 定时任务列表 |
| `POST` | `/api/jobs` | 新建定时任务 |
| `GET` | `/api/jobs/actions` | 已注册的模块动作（供定时任务选择，如 finance_board.run） |
| `PUT` | `/api/jobs/{job_id}` | 更新定时任务（仅传需要修改的字段） |
| `DELETE` | `/api/jobs/{job_id}` | 删除定时任务 |
| `POST` | `/api/jobs/{job_id}/run` | 立即执行一次（不影响下次到点触发） |
| `GET` | `/api/jobs/{job_id}/runs` | 定时任务运行时间线（事实重放 + 投影核对） |
| `GET` | `/api/knowledge/docs` | 知识库文档列表（关键词/分类/部门/状态筛选） |
| `POST` | `/api/knowledge/docs` | 粘贴 Markdown/纯文本创建知识库文档 |
| `GET` | `/api/knowledge/docs/facets` | 知识库筛选项计数（分类/部门/状态） |
| `POST` | `/api/knowledge/docs/upload` | 上传制度/手册文件并建索引 |
| `GET` | `/api/knowledge/docs/{doc_id}` | 知识库文档详情（大纲/版本链） |
| `PUT` | `/api/knowledge/docs/{doc_id}` | 编辑知识库文档元数据 |
| `DELETE` | `/api/knowledge/docs/{doc_id}` | 删除知识库文档（级联块/索引/原件） |
| `GET` | `/api/knowledge/docs/{doc_id}/chunks/{chunk_id}` | 读取命中块的前后文 |
| `POST` | `/api/knowledge/docs/{doc_id}/embed` | 手动重算该文档的向量索引 |
| `GET` | `/api/knowledge/docs/{doc_id}/file` | 下载知识库原件 |
| `POST` | `/api/knowledge/docs/{doc_id}/reindex` | 重解析原件并重建索引 |
| `GET` | `/api/knowledge/memories` | 记忆列表（关键词/类型过滤，置顶优先） |
| `POST` | `/api/knowledge/memories` | 新建记忆 |
| `GET` | `/api/knowledge/memories/export.md` | 导出记忆为 MEMORY.md 风格 Markdown |
| `POST` | `/api/knowledge/memories/extract` | 从对话文本抽取记忆（一次性，LLM） |
| `POST` | `/api/knowledge/memories/import.md` | 导入 MEMORY.md 风格 Markdown（## 节=类型，- 行=条目） |
| `POST` | `/api/knowledge/memories/recall` | 记忆检索（与 Agent 工具同一实现） |
| `GET` | `/api/knowledge/memories/stats` | 记忆统计（总数/来源/类型分布/命中Top） |
| `PUT` | `/api/knowledge/memories/{memory_id}` | 更新记忆（内容/标签/置顶/类型） |
| `DELETE` | `/api/knowledge/memories/{memory_id}` | 删除记忆 |
| `POST` | `/api/knowledge/rag/rebuild` | 重建知识库索引（笔记 + 文档；可选重算向量） |
| `POST` | `/api/knowledge/rag/search` | 知识库检索（关键词 + 向量混合） |
| `GET` | `/api/knowledge/rag/stats` | 知识库索引状态（笔记/文档/块/向量） |
| `GET` | `/api/knowledge/settings` | 知识库设置（向量检索开关/模型/维度） |
| `PUT` | `/api/knowledge/settings` | 保存知识库设置 |
| `POST` | `/api/knowledge/settings/test` | 测试 embedding 端点连通性（可用未保存的当前表单值） |
| `GET` | `/api/mcp/catalog` | MCP 服务器目录（内置常用服务器模板，一键添加） |
| `POST` | `/api/mcp/catalog/{catalog_id}/add` | 按目录模板一键添加 MCP 服务器（默认停用，可先编辑再启用） |
| `GET` | `/api/mcp/servers` | MCP 服务器列表（含工具清单，env 脱敏） |
| `POST` | `/api/mcp/servers` | 新增 MCP 服务器 |
| `PUT` | `/api/mcp/servers/{server_id}` | 更新 MCP 服务器 |
| `DELETE` | `/api/mcp/servers/{server_id}` | 删除 MCP 服务器 |
| `POST` | `/api/mcp/servers/{server_id}/connect` | 连接服务器并注册其工具 |
| `POST` | `/api/mcp/servers/{server_id}/disconnect` | 断开服务器 |
| `POST` | `/api/mcp/servers/{server_id}/test-call` | 测试调用服务器工具 |
| `GET` | `/api/models/providers/presets` | LLM 厂商预设列表（纯数据，无密钥概念） |
| `POST` | `/api/models/providers/presets/reload` | 重新加载 provider_presets.json 预设 |
| `GET` | `/api/models/{model_id}/probe` | 读取模型当前探测状态（不触发探测） |
| `POST` | `/api/models/{model_id}/probe/all` | 全量探测（思考→上下文→输出，单步失败不中断） |
| `POST` | `/api/models/{model_id}/probe/context` | 探测有效上下文 tokens（二分查找，最多 6 次请求） |
| `POST` | `/api/models/{model_id}/probe/output` | 探测最大输出 tokens（阶梯 1024→131072） |
| `POST` | `/api/models/{model_id}/probe/reasoning` | 探测思考能力（reasoning_effort 分级，仅 openai 协议） |
| `GET` | `/api/notes` | 笔记列表（搜索 / 标签过滤 / 分页） |
| `POST` | `/api/notes` | 新建笔记 |
| `GET` | `/api/notes/tags` | 全部标签（去重） |
| `GET` | `/api/notes/{note_id}` | 笔记详情 |
| `PUT` | `/api/notes/{note_id}` | 更新笔记（仅传需要修改的字段） |
| `DELETE` | `/api/notes/{note_id}` | 删除笔记 |
| `GET` | `/api/providers/presets` | LLM 厂商预设列表（纯数据，无密钥概念） |
| `POST` | `/api/providers/presets/reload` | 重新加载 provider_presets.json 预设 |
| `GET` | `/api/schedules` | 日程列表（按时间范围 / 关键词查询） |
| `POST` | `/api/schedules` | 新建日程 |
| `GET` | `/api/schedules/{schedule_id}` | 日程详情 |
| `PUT` | `/api/schedules/{schedule_id}` | 更新日程（仅传需要修改的字段） |
| `DELETE` | `/api/schedules/{schedule_id}` | 删除日程 |
| `GET` | `/api/search` | 全局搜索（任务 / 日程 / 笔记） |
| `POST` | `/api/skills/market/install` | 从市场一键安装技能包进平台技能池（默认停用） |
| `GET` | `/api/skills/market/scan` | 技能市场扫描（QwenPaw 技能池 + WorkBuddy + 平台种子） |
| `GET` | `/api/skills/packs` | 技能包清单（含启停与校验状态） |
| `POST` | `/api/skills/packs` | 新建技能包（内置模板生成 SKILL.md） |
| `DELETE` | `/api/skills/packs/{name}` | 删除技能包（整目录） |
| `PUT` | `/api/skills/packs/{name}/enabled` | 启用/停用技能包 |
| `GET` | `/api/skills/packs/{name}/files/{filename}` | 读取技能包内文件内容 |
| `POST` | `/api/skills/rescan` | 重新扫描（并补齐种子技能包） |
| `GET` | `/api/tasks` | 任务列表（筛选 / 搜索 / 排序 / 分页） |
| `POST` | `/api/tasks` | 新建任务 |
| `GET` | `/api/tasks/{task_id}` | 任务详情 |
| `PUT` | `/api/tasks/{task_id}` | 更新任务（仅传需要修改的字段） |
| `DELETE` | `/api/tasks/{task_id}` | 删除任务 |
| `GET` | `/api/templates` | 数据模板清单（名称/所属模块/大小/是否就位） |
| `GET` | `/api/templates/{name}` | 下载数据模板（白名单文件，中文文件名 RFC5987） |
