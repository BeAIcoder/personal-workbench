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
  "presets": [ /* 18 家供应商预设 */ ],
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
`max_iters`（1~20，工具轮次）、`enable_memory`、`history_inject`（0~100）、`enable_skills` / `skills_dir`、`enable_bash`、`enable_plugins` / `plugins_dir`、`reasoning_effort`（`""` 跟随模型 / `off` / `low` / `medium` / `high` / `max`）。

#### POST /api/assistant/config/test
```json
{ "config": { /* 可选候选配置 */ }, "provider_id": 1, "include_image": true, "test_tools": true }
```
返回 `{ "text": {...}, "tools": {...}, "image": {...} }`（后两项仅在 `text.ok` 为真且对应开关打开时出现）。

- `provider_id`：测试该供应商第一个启用模型
- **测试当前激活模型**（不传 `config`）：`api_key` 留空或传掩码哨兵 `********`（`••••••` 开头的回显掩码同）时自动回填现值
- **测试自定义候选配置**（传 `config`）：必须显式填写**新的** API Key——空/掩码不会借用当前激活模型的真实密钥，返回 `200` + `{"text": {"ok": false, "error": "请填写新的 API Key：测试自定义配置不会沿用当前激活模型的密钥"}}`；`base_url` 经共享网关校验，非法返回 `400`
- `text` 结构：成功 `{ok: true, latency_ms, reply, usage: {input_tokens, output_tokens}}`；失败 `{ok: false, latency_ms, error}`
- `image`：`include_image=true` 时按协议走 openai 兼容或 anthropic 图片识别测试（红色图片问答，判定回答含「红/red」）

#### 供应商 / 模型（两级管理）
| 接口 | 说明 |
| --- | --- |
| `GET /api/assistant/providers` | `{ providers（api_key 不回传，含 api_key_set）, presets }`；presets 每项含 `vision_models`（已知视觉模型清单，供界面预勾选模型的 supports_vision） |
| `POST /api/assistant/providers` | 新增/更新：`{provider_id?, name, protocol?, base_url?, api_key?, enabled?}`；api_key 空或 `********` 保留现值 |
| `DELETE /api/assistant/providers/{id}` | 删除供应商（含其模型；若为激活模型则取消激活） |
| `POST /api/assistant/providers/{id}/models` | 新增/更新模型：`{model_id?, model, context_size?(4096~2000000), max_tokens?(64~500000), multimodal?, supports_vision?, enabled?}`；`supports_vision` 为视觉（图片理解）能力，聊天传图需开启 |
| `DELETE /api/assistant/providers/{id}/models/{mid}` | 删除模型 |
| `POST /api/assistant/models/{id}/activate` | 激活模型（跨供应商，立即生效）；不存在/未启用返回 404 |
| `GET /api/assistant/models` | 启用中模型扁平列表 |

### 附件与扩展

#### POST /api/assistant/upload
multipart 表单：`session_id`（白名单校验）+ `file`。上限 10MB（超限 400）。扩展名白名单：文本/数据类 `.txt .md .csv .json .log .py .html .xml .yaml .yml .sql`、Excel `.xlsx/.xls`（自动把第一张工作表转 CSV）、图片 `.png .jpg .jpeg .webp .gif`；白名单外返回 400，无扩展名按普通文件放行。返回 `{ "name", "path", "kind"("file"/"csv_converted"/"image"), "size", "note" }`。
- 读取经共享分块限额（`read_upload_limited`）：超限在落盘前即 400，不留任何文件
- xlsx 首表行数（含表头）超过导入行数上限 `200000` 行：上传整体拒绝并清理 xlsx 与半成品 CSV，返回 `400`
- Excel 转 CSV 失败时降级：保留 xlsx 本体，`note` 说明失败原因（既有契约）

#### GET /api/assistant/skills/discover?dir=...
扫描技能池目录（SKILL.md 格式）：`{ "count", "names" }`（names 最多 30 条）。**安全边界：仅允许配置中已保存的 `skills_dir`**——未保存时返回 `{"count": 0, "names": [], "error": "目录未保存：请先在设置中保存该技能池目录再检测"}`；目录不存在返回 `{"count": 0, "names": [], "error": "目录不存在"}`。

#### GET /api/assistant/plugins/discover?dir=...
扫描 QwenPaw 插件目录：`{ "loadable", "plugins": [ {name, ok, tools / error} ] }`（plugins 最多 30 条）。**安全边界：`load_qwenpaw_plugin` 会执行插件后端代码，仅允许配置中已保存的 `plugins_dir`**——未保存时返回 `{"loadable": 0, "plugins": [], "error": "目录未保存：请先在设置中保存该插件目录再检测"}`；目录不存在返回 `{"loadable": 0, "plugins": [], "error": "目录不存在"}`。

## 定时任务 /api/jobs

定时 Agent：到点自动按提示词执行一次助手对话，回复写成笔记（标题以「【定时任务】」开头，标签含「定时任务」）。调度器每 20 秒检查一次到期任务；同一任务串行执行（上次未结束则跳过）；LLM 未配置时本轮标记 `skipped`，不发请求。

任务字段（ScheduledJob）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 主键 |
| `name` | string | 任务名称（必填，≤100 字） |
| `prompt` | string | 到点执行的提示词（必填，≤5000 字） |
| `agent_name` | string\|null | 指定专家标识；`null` = 智能路由 |
| `mode` | string | 工作模式：`standard` / `readonly` / `deep` |
| `schedule_type` | string | 调度方式：`interval` / `daily` / `cron` |
| `interval_minutes` | int\|null | 间隔分钟（`interval` 必填，5~10080） |
| `daily_at` | string\|null | 每天执行时间 HH:MM（`daily` 必填） |
| `enabled` | bool | 是否启用 |
| `last_run_at` | datetime\|null | 最近一次执行时间 |
| `last_status` | string | 最近一次状态：`ok` / `error` / `skipped` / 空（未运行） |
| `last_error` | string | 最近一次错误摘要 |
| `last_note_id` | int\|null | 最近一次结果笔记 id |
| `recent_runs` | array | 最近 20 条运行记录，元素 `{at, status, summary}` |
| `created_at` / `updated_at` | datetime | 创建 / 更新时间 |

### GET /api/jobs
全部任务列表（按 id 升序）：`[ { ...ScheduledJob } ]`

### POST /api/jobs（返回 201）
创建任务。`schedule_type=interval` 必须带 `interval_minutes`（5~10080）；`daily` 必须带 `daily_at`（HH:MM）。缺字段或格式错误返回 422。
```json
{
  "name": "每周税务自查", "prompt": "检查未来 7 天到期任务并汇总成清单",
  "agent_name": null, "mode": "standard",
  "schedule_type": "interval", "interval_minutes": 10080, "enabled": true
}
```

### PUT /api/jobs/{id}
部分更新：只传需要修改的字段；不存在返回 404；调度参数不一致（interval 未带 interval_minutes / daily 未带 daily_at）返回 422。

### DELETE /api/jobs/{id}（返回 204）
删除任务，不存在返回 404。

### POST /api/jobs/{id}/run
立即执行一次（不影响下次到点触发），不存在返回 404。
```json
{ "status": "ok", "summary": "……（回复摘要，最长 200 字）", "note_id": 12 }
```
- `status`：`ok`（成功，`note_id` 为结果笔记 id）/ `skipped`（模型未配置，或上一次尚未结束）/ `error`（`summary` 为错误摘要，同时记入任务 `last_error`）。

## 知识与记忆 /api/knowledge

RAG 笔记检索（BM25 + 中文 bigram 分词，零外部依赖离线可用；索引常驻内存，按笔记更新时间失效自动重建）与长期记忆（跨会话事实/偏好存取；每轮对话前自动召回 Top-3 相关记忆注入上下文）。

### RAG 知识库

#### GET /api/knowledge/rag/stats
索引状态（笔记数 / 块数 / 词表大小）：
```json
{ "notes_indexed": 5, "chunks": 12, "vocabulary": 486 }
```

#### POST /api/knowledge/rag/rebuild
手动重建索引（通常无需——索引随笔记变更自动失效重建），返回结构同 stats。

#### POST /api/knowledge/rag/search
```json
{ "query": "增值税申报", "top_k": 5 }
```
`top_k` 默认 5（1~20）。返回 `{ "items": [ { "note_id": 3, "title": "申报注意事项", "snippet": "…", "score": 2.317 } ] }`；同一笔记只保留最高分块，`snippet` 截断至 220 字。

### 长期记忆

记忆字段：`content` 内容（必填，≤2000 字）、`source` 来源（`manual` 手动 / `auto` 对话抽取）、`tags` 标签数组、`pinned` 是否置顶、`hit_count` 命中次数（召回命中自动累计）、`created_at`。与既有记忆近重复（前 80 字互相包含）时拒存，返回 400。

#### GET /api/knowledge/memories?keyword=
记忆列表（置顶在前、id 倒序，最多 100 条；`keyword` 按内容过滤）：`{ "items": [ { "id", "content", "source", "session_id", "tags", "pinned", "hit_count", "created_at" } ] }`

#### POST /api/knowledge/memories（返回 201）
```json
{ "content": "企业所得税汇算清缴截止 5 月 31 日", "tags": ["税务"], "pinned": false }
```

#### PUT /api/knowledge/memories/{id}
部分更新 `content` / `tags` / `pinned`；不存在返回 404，内容为空返回 400。

#### DELETE /api/knowledge/memories/{id}
返回 `{"ok": true}`；不存在返回 404。

#### POST /api/knowledge/memories/recall
记忆检索（与 Agent 工具 `recall_memories` 同一实现：bigram 重叠打分 + 置顶/命中数加权）：
```json
{ "query": "申报截止", "top_k": 5 }
```
`top_k` 默认 5（1~10）。返回 `{ "items": [ /* 同记忆字段 */ ] }`。

#### POST /api/knowledge/memories/extract
从对话文本一次性抽取记忆（走 LLM，近重复去重后入库）：
```json
{ "conversation": "……（对话全文或最近若干轮，至少 20 字）" }
```
返回 `{ "extracted": 3, "saved": 2 }`；模型未配置或对话太短时返回 `{ "extracted": 0, "saved": 0, "error": "…" }`。

## MCP 工具 /api/mcp

MCP（Model Context Protocol）服务器管理：仅支持 **stdio** 传输（本地命令起的 MCP Server；HTTP/SSE 预留）。连接成功后服务器工具自动注册为 `mcp_<服务器名>__<工具名>` 进入 Agent 工具白名单（是否只读以工具自带 `annotations.readOnlyHint` 为唯一依据，缺省视为可写）；服务启动时自动拉起全部启用中的服务器，单服务器失败不影响宿主。服务器 `env` 落库前经 DPAPI 密封，接口只回显键名与 `***`；存量明文会在启动时自动迁移。

服务器字段：`name` 服务器名（必填唯一，≤50 字，即工具前缀）、`command` 启动命令（如 `npx` / `python` / `uv`）、`args` 命令参数数组、`env` 环境变量（敏感值只存本机库，**回显一律脱敏为 `***`**）、`enabled`、`status`（`stopped` / `connected` / `error`）、`last_error`、`tools`（已发现的工具清单）。

### GET /api/mcp/servers
服务器列表（按 id 倒序，含工具清单，env 脱敏）：
```json
{ "items": [ { "id": 1, "name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/docs"],
  "env": { "API_KEY": "***" }, "enabled": true, "status": "connected", "last_error": "",
  "tools": [ { "name": "read_file", "description": "…" } ], "created_at": "2026-10-12 10:00:00" } ] }
```

### POST /api/mcp/servers（返回 201）
```json
{ "name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/docs"], "env": {}, "enabled": true }
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
{ "tool": "read_file", "arguments": { "path": "D:/docs/a.txt" } }
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
| finance_expense 费用分类 | `/api/finance/expense` | `POST /classify`（上传 xlsx 分类）、`GET /runs`、`GET /runs/{id}`、`GET /runs/{id}/download`、`GET /rules/summary`、`POST /rules/reload` |
| finance_invoice 发票处理 | `/api/finance/invoice` | `GET /engine/status`、`POST /batches`（上传 PDF/ZIP）、`GET /batches`、`GET /batches/{id}`、`PATCH /items/{id}`、`POST /batches/{id}/export`、`GET /batches/{id}/download` |
| finance_vault 数据仓 | `/api/finance/vault` | `GET /tables`、`POST /ingest/file`、`POST /query`（只读 SQL）、`GET /tables/{name}/preview` |
| finance_ledger_personal 个人记账 | `/api/finance/ledger/personal` | 账户/分类/交易 CRUD、`GET /stats/monthly`、`GET /stats/trend`、`GET/PUT /budgets`、`POST /import/csv`、`GET /export/csv` |
| finance_ledger_bip 企业记账 | `/api/finance/ledger/bip` | `POST /imports/preview`、`POST /imports/confirm`、`GET /imports`、`GET /vouchers`、`GET /vouchers/{id}`、`GET /entries`、`GET /balances/summary`、`POST /vouchers/export-to-vault` |
| finance_tax 企业所得税 | `/api/finance/tax` | `/enterprises`（CRUD）、`/financial-import`（导入+模板下载）、`/policies`（政策库）、`/tax-returns`（申报 CRUD/提交/计算/导出 Excel·PDF）、`/textbook-reference`（教材对照） |
| finance_board 报表看板 | `/api/finance/board` | `GET /snapshots`（快照列表，最多 50 条，时间倒序）、`POST /run`（触发管线）、`GET /latest`、`GET /snapshots/{id}/html`（iframe 嵌入） |
| finance_cashflow 现金流填报 | `/api/finance/cashflow` | `POST /runs`（上传建运行，表单必填 `actual_month` 1~12）、`POST /runs/{id}/execute`、`GET /runs`（分页）、`GET /runs/{id}`（运行详情）、`GET /runs/{id}/artifacts/{name}`、`GET /runs/{id}/download`（ZIP） |
| finance_pos POS 对账 | `/api/finance/pos` | 同上（无 actual_month 参数），含 `GET /runs/{id}` 运行详情 |

### 财务模块上传与行数护栏

各财务导入路由共用两道路栏：上传大小（分块限额读取，超限 `400`）与导入行数（超过 `MAX_IMPORT_ROWS = 200000` 行即 `400` 中止，不落库、不留半成品）。

| 模块 | 上传上限 | 备注 |
|------|----------|------|
| finance_expense `POST /classify` | 20MB | 仅 `.xlsx/.xls`，空文件 400；引擎不可用 503 |
| finance_invoice `POST /batches` | 单批次 100MB（ZIP 按解压后口径） | PDF/ZIP 批次上传 |
| finance_vault `POST /ingest/file` | 20MB | 入仓数据行数超 200000 行 400 |
| finance_ledger_bip `POST /imports/preview` | 10MB | xlsx/xls；解析阶段即做行数护栏 |
| finance_cashflow `POST /runs` | 50MB | ZIP 解压后总量上限 200MB（防解压炸弹） |

- **现金流 `GET /runs` 分页**：`page`（默认 1）与 `page_size`（默认 20，1~100），返回 `{total, items, page, page_size}`，`total` 为 COUNT(*) 全量计数；`GET /runs/{id}/download` 无产物时返回 `409`
- **看板快照 `log_tail`**：入库截断至 4000 字符，列表/详情读取时仅返回**尾部 500 字符**（有界暴露，不整文件无界返回）；`POST /run` 管线繁忙返回 `409`、引擎不可用返回 `503`；`GET /latest` 无成功快照返回 `404`；`GET /snapshots/{id}/html` 仅允许读取看板快照目录内文件（越界 `403`），响应带 `Content-Security-Policy: frame-ancestors 'self'` 与 `X-Content-Type-Options: nosniff`

### 定时任务扩展（v1.11.0）

- `GET /api/jobs/actions`：已注册模块动作列表（如 `finance_board.run`）
- 定时任务（`POST /api/jobs`）新增字段：`cron_expr`（schedule_type=cron 时必填，五段表达式）、`action`（模块动作名，空 = Agent 对话任务）
- 调度语义：cron 同自然分钟不重复触发；模块动作任务走线程池执行、不写笔记

> 跨项目引擎路径（费用分类 / 发票工具）经 `finance_expense_engine_path` / `finance_invoice_engine_path` 配置项解析，默认按AssetLib相对位置推导；引擎目录缺失时对应接口返回 503 中文提示，不影响其他模块。BIP 导入的 `export-to-vault` 在数据仓模块不可用时同样 503 降级；入仓取数阶段即受 200,000 行上限约束，超限返回 400，不会先全量组装数据。

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

### 模型能力探测与厂商预设（v1.14.0）

| 端点 | 说明 |
|------|------|
| `POST /api/models/{id}/probe/reasoning` | 思考级别探测（none/low/medium/high 各一发，判定 supported/accepted/unsupported） |
| `POST /api/models/{id}/probe/context` | 上下文长度二分探测（≤6 次小请求，max_tokens=1） |
| `POST /api/models/{id}/probe/output` | 最大输出长度阶梯探测（1024→131072） |
| `POST /api/models/{id}/probe/all` | 三连探测，汇总写 probe_result + probed_at |
| `GET /api/models/{id}/probe` | 当前探测状态（只读） |
| `GET /api/providers/presets` · `POST /api/providers/presets/reload` | 厂商预设（provider_presets.json，18 家）；另挂 `/api/models/providers/presets` 别名（兼容「router prefix=/models」的路径解读） |

- 探测直连厂商 `/chat/completions`（openai 兼容），失败=200+error（不可达≠不支持，supports_thinking 保持 -1）
- 模型级自定义配置：temperature（空=跟随）、note、supports_thinking/thinking_levels 手动覆盖；temperature 已接入 build_model 运行时（openai/anthropic 双协议）
