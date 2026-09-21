# API 接口说明

- Base URL：`http://127.0.0.1:8000/api`
- 数据格式：JSON（UTF-8）；时间一律 ISO 8601（如 `2026-09-08T09:30:00`，也接受 `2026-09-08` 日期形式）
- 在线调试：启动后访问 <http://127.0.0.1:8000/docs>（Swagger UI）
- 错误约定：参数校验失败返回 `422`（body 含 `detail` 数组）；资源不存在返回 `404`（`{"detail": "..."}`）；删除成功返回 `204` 无内容
- 分页约定：列表接口统一返回 `{"items": [...], "total": 总数, "page": 页码, "page_size": 每页条数}`

## 系统

### GET /api/health
健康检查。
```json
{ "status": "ok", "app": "个人工作台", "version": "1.0.0" }
```

## 任务管理 /api/tasks

任务字段：`title` 标题（必填）、`description` 描述、`status` 状态（`todo` 待办 / `in_progress` 进行中 / `done` 已完成）、`priority` 优先级（`low` / `medium` / `high` / `urgent`）、`due_date` 截止时间（可空）、`category` 分类、`completed_at` 完成时间（系统维护：状态改为 done 时写入，改回时清空）、`created_at` / `updated_at`。

### GET /api/tasks
| 参数 | 说明 |
| --- | --- |
| `status` / `priority` / `category` | 按状态 / 优先级 / 分类过滤 |
| `overdue` | `true` 时只返回已逾期（未完成且截止时间已过） |
| `q` | 关键词，匹配标题或描述 |
| `due_before` / `due_after` | 截止时间区间（含边界），如 `2026-09-07` |
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
| `start` / `end` | 时间范围（含边界），按「日程与区间有交集」匹配，适合日历整月拉取 |
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
  "today_schedules": [ /* 今天有交集的日程，按开始时间升序 */ ],
  "recent_notes":    [ /* 最近更新的 5 条笔记 */ ],
  "trend_7d": [ { "date": "2026-09-01", "count": 0 }, … ]
}
```

## 前端接入说明

- axios 实例：`frontend/src/api/index.js`，`baseURL` 取 `VITE_API_BASE`（默认 `/api`）。
- 开发模式：Vite 将 `/api` 代理到 `http://127.0.0.1:8000`；生产模式：前端构建产物由后端在 8000 端口直接托管，天然同源、无跨域问题。
- 所有 SQL 通过 SQLAlchemy 参数绑定执行，外部输入不会拼接进语句。
