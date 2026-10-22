# E2E 深度探测轮 · 最终存档

> 更新时间：2026-10-22（swarm2 文档轮：补充旧 E2E 三脚本与 AI 走查口径；历史数字全部降级为历史实测）
> 状态：**历史存档（非当前结论）** —— 一～八节为 2026-10-20 及更早轮次的实测记录：
> 16 区 647/647（含当轮补齐 5 区）→ 同日加固轮 17 区 656/656；后端 pytest 469→505、
> 前端 vitest 251→352（34 文件）、`npm run build` 通过（均为 2026-10-20 实测值）。
> ⚠️ 2026-10-22 加固轮（swarm2）**未复跑**任何探针与旧 E2E 脚本：上述所有「全绿 / N/N」
> 均为**历史实测快照**，不代表当前工作区状态，复跑后才能刷新。
> 旧 E2E 三脚本（8123/8124）与 AI 走查的隔离/互斥/归档口径见第九节。

---

## 一、总体结果

| # | 区域 | 探针 | 结果 | 本轮要点 |
|---|---|---|---|---|
| 1 | tasks | `tasks.py` | **36/36** | 筛选参数同步、逾期排序/筛选、AI 简报跳转、列表异常态 |
| 2 | schedules | `schedules.py` | **22/22** | AI 备会按钮事件冒泡修复复验 |
| 3 | knowledge | `knowledge.py` | **19/19** | 11 项失败逐条甄别（选择器/时序为主，含真实修复） |
| 4 | jobs | `jobs.py` | **25/25** | 时间选择器/严格模式/轮询时序；后端 schedule_type 两个真 bug |
| 5 | assistant-settings | `assistant-settings.py` | **38/38** | 预设填充、专家表单模型下拉刷新 |
| 6 | mcp | `mcp.py` | **24/24** | 只读注解默认拒绝 + 工具注销；控制台 400/404/422/503 为负例预期 |
| 7 | debug-center | `debug-center.py` | **33/33** | 空白标题 422、内联改状态刷新；同 URL 重载隔离 |
| 8 | finance-tax-pos-hello | `finance-tax-pos-hello.py` | **32/32** | PDF 导出补 reportlab；响应头大小写修正 |
| 9 | finance-ledger | `finance-ledger.py` | **27/27** | 新增 TC-FL-PL-015：支出构成不再混入收入分类 |
| 10 | finance-invoice-vault | `finance-invoice-vault.py` | **42/42** | 批次 failed 态、SQL 白名单、file input 重选复验 |
| 11 | api-contract | `api-contract.py` | **153/153** | 此前 10 条 report-only 全部转绿；3 条 observation 为预期变体 |
| 12 | dashboard-search ★ | `dashboard-search.py` | **29/29** | 搜索竞态/占位项/日程跳转/任务排序/空白关键词/00:00 边界 |
| 13 | notes ★ | `notes.py` | **38/38** | 草稿保护、null/空白校验、标签转义、失败反馈、任务悬挂引用 |
| 14 | assistant-chat ★ | `assistant-chat.py` | **32/32** | 会话故障保留状态、上传失败反馈、session_id 校验、首条消息标题 |
| 15 | skills-experts ★ | `skills-experts.py` | **51/51** | YAML 特殊字符、目录名 URL 编码、关键词逗号往返、恢复出厂工具池 |
| 16 | finance-board-cashflow-expense ★ | `finance-board-cashflow-expense.py` | **46/46** | 非法文件名/损坏 ZIP 改 400、failed 快照即时刷新 |

★ = 本轮新增探针（5 区共 196 例）。**合计：647/647 通过。**

环境事实：前端 dist 构建于 2026-10-20 11:27（含全部前端修复）；探针端口 8201-8221 跑完即释放；所有探针使用独立 `DATABASE_URL` / `FINANCE_DATA_DIR` / `DEBUG_DATA_DIR`，不触碰真实 `backend/data/`。

---

## 二、本轮（五区补探 + 记账口径）修复的缺陷

### A. dashboard-search（6 项）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 搜索慢响应晚到后覆盖新关键词的建议 | `utils/search.js` 新增 `createLatestGuard()`；`GlobalSearch` 按请求序号丢弃过期回调 |
| 2 | 日程搜索建议跳转后不按 `q` 过滤、不定位命中日期（`date` 丢失） | `ScheduleView` 消费 `?q&date`：定位日期、过滤月历/当日列表、可关闭搜索标签；`parseSearchDate()` 防御非法值 |
| 3 | 最近列表 `/tasks?sort=due_asc` 未生效 | `utils/tasks.js` 补齐 `created_desc/due_asc/priority_desc` 并解析 `sort` 参数 |
| 4 | 无结果占位项被选中后把输入框改写成「未找到相关内容」 | `keywordAfterSuggestion()` 恢复用户实际输入，占位项不跳转 |
| 5 | `/api/search?q=" "` 会命中含空格记录 | 后端 `strip()` 后为空返回 422，提示关键词不能为空 |
| 6 | 今日日程包含「结束时间恰为 00:00」的跨日事件 | 今日判断由 `end_time >= day_start` 改为 `> day_start` |

未解决（记录）：缺参/超长关键词的 422 `detail` 仍为 FastAPI 英文框架文案。

### B. notes（6 项）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | `PUT` 显式传 null 或纯空白标题 → 500 且脏数据入库，列表接口随后 500 | `NoteUpdate` 四个非空列字段增加 null/空白校验 → 422；标题 strip 后写入 |
| 2 | 标签含引号/反斜杠时筛选无结果（JSON 转义与 LIKE 模式不一致） | `utils.tag_like_pattern()` 先 `json.dumps` 再转义 LIKE 元字符 |
| 3 | 未保存正文时点置顶，接口返回值覆盖草稿（数据丢失） | `planPinToggle()` 脏态直接阻止并提示先保存 |
| 4 | 新建时重复弹「放弃修改」；筛选/翻页态确认放弃后旧草稿仍留在编辑器，继续保存会改错笔记 | `needsDiscardConfirm()` 支持 force；`rolledBackDraft()` 回滚到已保存快照；新笔记不在当前列表时不再乱选第一条 |
| 5 | 列表/标签/保存失败产生未捕获 rejection | 各入口补 catch（拦截器已提示），保持 current/dirty 供重试 |
| 6 | 删除笔记后定时任务仍持有 `last_note_id` 悬挂引用 | 删除事务内清空 `ScheduledJob.last_note_id` |

### C. assistant-chat（4 项）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 删除当前会话失败时，聊天区与选中状态先被清空（状态损坏） | 先请求删除，成功后才清空并打断流；失败保留现状 |
| 2 | 上传失败时 `onFilePicked` 抛未捕获 `Network Error` | 增加 catch（拦截器提示），`finally` 复位 uploading |
| 3 | `POST /sessions`、`/chat`、`/chat/stream` 未校验 `session_id`，非法 ID 可落库且无法管理 | 统一 `_check_session_id()` → 400 |
| 4 | 界面「新建」会话的首条消息不更新标题，永远显示「新会话」 | `_touch_session()` 在标题为空/「新会话」时用首条消息补标题；用户手动改名后不覆盖 |

附带：归档/恢复/重命名失败均增加 catch（失败不改本地状态）。未解决（记录）：无 LLM 环境仅验证降级分支，真实模型流式与多模态路径未覆盖。

### D. skills-experts（4 项）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 新建技能包描述含「: 」/换行/「 #」时 frontmatter 非法，包立即失效 | `skill_store` 用 JSON 双引号标量序列化 description（合法 YAML），可逐字往返 |
| 2 | 目录名含 `# ? %` 时启停/读文件/删除请求被 URL 截断 | 新增 `utils/skillPack.js`，`SkillPackView` 对目录名与文件路径逐段 `encodeURIComponent` |
| 3 | 关键词本身含英文逗号时，导出→导入被判差异并拆成多个词 | 导出改为 JSON 数组，解析优先 JSON 行内列表并兼容旧逗号格式/手写 `[a, b]` |
| 4 | `/api/assistant/agents/reset` 按静态 TEAM 覆盖工具，`finance_specialist` 丢失模块预置财务工具，专家包出现伪差异 | `registry.apply_loaded_presets()` 按已加载 manifest 幂等重放预置；恢复出厂后调用 |

### E. finance-board-cashflow-expense（3 项）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 用户文件名含 Windows 非法字符或超长 → 落盘 `OSError` 500 | `finance_cashflow._safe_name()` 清洗；ZIP 条目同样清洗；落盘异常改 400 |
| 2 | 损坏 ZIP 触发 500 | 捕获 `BadZipFile` 等 → 400，且不留下半成品运行记录 |
| 3 | 看板管线失败会归档 failed 快照，但前端 catch 不刷新列表，失败记录不可见 | `shouldReloadAfterRun()`：有 HTTP 响应即重拉；网络中断保留旧列表 |

已知限制（未扩展）：看板 API 无 `log_tail`；现金流列表固定 50 条上限、`total` 不实且无分页。

### F. finance-ledger 统计口径（1 项）

| # | 问题 | 结论与修复 |
|---|---|---|
| 1 | 「当月支出分类构成」表格与饼图混入收入分类，而 `percent` 分母是支出，数字对不上 | 口径定为**支出构成只含支出**：前端新增 `expenseCategories(list)` 过滤 `kind === 'expense'`，表格/饼图统一使用；后端 `by_category` 保持同时返回两类供其他口径使用。补前端单测与 E2E `TC-FL-PL-015` |

---

## 三、前一轮（11 区）确认并修复的缺陷

### A. 结构性审计（5 项，均有 pytest 回归）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | MCP 工具只读判定靠名字白名单，任意第三方工具名可绕过；连接/断开/更新/删除后 `TOOL_SPECS` 残留旧工具 | 以 `annotations.readOnlyHint` 为唯一放行依据（缺省拒绝）；新增 `_unregister_server_tools()`；`_readonly_tool_names()` 快照迭代 |
| 2 | cron 错过补偿会回填任务创建之前的时段 | 补偿下界取 `max(now-24h, last_run_at, created_at)` |
| 3 | 专家包导入时 NULL 工具（默认查询工具池）被降级为 `[]` | 区分新建/既有行：新建按文件精确落库；既有 NULL 行保持 NULL 并与文件工具并集 |
| 4 | 导出文件名直接拼中文导致 latin-1 500；ASCII 回退未清洗 CR/LF 与引号 | 仅保留 32-126 可打印字符并排除 `"`/`\`，空回退按扩展名生成 |
| 5 | 未知 `schedule_type` 被 201 接受后按 interval 兜底执行 | Pydantic `Literal` → 422；`_check_schedule` 归一化跨类型字段 |

### B. 探针新挖出的真实缺陷

| # | 缺陷 | 修复 |
|---|---|---|
| 6 | PDF 导出依赖 reportlab 未声明，真实环境 500 | `requirements*.txt` 声明并安装；新增真实渲染回归 |
| 7 | 调试中心纯空白标题通过 `min_length` | strip 后非空校验 → 422 |
| 8 | 调试中心筛选态内联改状态后列表不刷新；摘要类型显示英文 | 改后 `Promise.all([loadIssues(), loadSummary()])`；补 `kindLabel()` |
| 9 | 任务页 4 项：URL 筛选未生效、逾期排序/筛选项失效、AI 简报未跳转、加载失败无异常态 | `utils/tasks.js` + `TasksView.vue`；`e2e-bug-tasks.test.js`（12 例） |
| 10 | 助手供应商「预设填充」不写 base_url/候选模型 | `fillProviderFromPreset()` 两处入口共用 |
| 11 | 专家表单打开时模型下拉数据陈旧 | 打开表单前刷新模型列表 |

### C. 探针基础设施修正（非应用缺陷，避免误报）

- `debug-center.py`：仅 hash 变化时 `page.goto` 不重载 → 同 URL 强制 `reload`；汇总卡片取末尾数字。
- `tasks.py`：Element Plus 2.14 选中值位于 `.el-select__placeholder span`。
- `jobs.py`：`.page-title` 严格模式重复；`.el-date-editor--time`；弹窗残留级联；toast 轮询。
- `finance-tax-pos-hello.py`：`ROOT` 层级（parents[3]）；响应头大小写不敏感。
- 本轮补探又修：`dashboard-search.py` 占位项 URL 断言 `rstrip('/')` 自反；`notes.py` 选择器/时序 3 处；`assistant-chat.py` 标题超 30 字截断选择器、专家标签、链式比较、按用例窗口统计异常；`skills-experts.py` 对话框/loading 过渡、role_prompt 断言、reset 基线工具、描述引号断言；`finance-board-cashflow-expense.py` 同 URL/networkidle、XSS 目录、log_tail 查库。

---

## 四、遗留与已知限制

1. **真实 LLM 路径未覆盖**：测试与探针均离线；聊天仅验证「未配置」降级、附件与状态机，真实模型流式/多模态未跑。
2. **无 LLM 下的标题生成**：现按首条用户消息截断补标题；未接模型摘要。
3. **现金流/看板限制**：看板 API 无 `log_tail`；现金流列表固定 50 条、`total` 不实、无分页（记录待后续产品化）。
4. **api-contract observations（3 条，非缺陷）**：技能包名非法 400/超长描述 422、调试中心缺 title 422，均为预期校验形态。
5. **mcp 控制台 4 条 error**：负例用例触发的 400/404/422/503 资源加载错误，非未捕获异常。
6. **FastAPI 框架文案**：搜索缺参/超长 422 的 `detail` 仍为英文，前端已按状态码提示。

---

## 五、复跑方式

```bash
# 后端全量（项目 venv）
cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q

# 前端单测 + 构建
cd frontend && npm test && npm run build

# 单区域探针（系统 python，含 Playwright；脚本自起独立服务）
PYTHONIOENCODING=utf-8 /d/DevEnv/runtime/python/python.exe dev/e2e/probes/<area>.py
```

- 后端服务用 `backend/venv/Scripts/python.exe`；探针用系统 python（Playwright 安装在那里）。
- 每个探针自选端口（8201-8221）并自建/自清测试库；`debug-center` 必须使用独立 `DEBUG_DATA_DIR`。
- 控制台中文乱码是 cp936/UTF-8 显示差异，落盘 JSON 为 UTF-8，不影响结果。
- 旧 E2E 三脚本（固定端口 8123/8124）与 AI 走查是另一套资产，运行/隔离口径见第九节。

---

## 六、文件索引

### 探针脚本（16 个，`dev/e2e/probes/`）

`api-contract.py`、`assistant-chat.py`、`assistant-settings.py`、`dashboard-search.py`、`debug-center.py`、
`finance-board-cashflow-expense.py`、`finance-invoice-vault.py`、`finance-ledger.py`、`finance-tax-pos-hello.py`、
`jobs.py`、`knowledge.py`、`mcp.py`、`notes.py`、`schedules.py`、`skills-experts.py`、`tasks.py`

### 结果 JSON（16 份）

`api-contract.json`、`assistant-chat.json`、`assistant-settings.json`、`dashboard-search.json`、`debug-center.json`、
`finance-board-cashflow-expense.json`、`finance-invoice-vault.json`、`finance-ledger/finance-ledger.json`、
`finance-tax-pos-hello.json`、`jobs.json`、`knowledge/knowledge.json`、`mcp.json`、`notes.json`、
`schedules/schedules.json`、`skills-experts.json`、`tasks.probe.json`

### 前端回归测试（新增/改动）

- 本轮：`e2e-bug-dashboard-search.test.js`（14）、`notes.test.js`（8）、`e2e-bug-assistant-chat.test.js`（5）、
  `skill-experts.test.js`（6）、`finance-board.test.js`（5）、`e2e-bug-finance-ledger.test.js`（9）
- 前轮：`e2e-bug-tasks.test.js`（12）、`e2e-bug-assistant-settings.test.js`（6）、`e2e-bug-mcp.test.js`（19）、
  `e2e-bug-schedules.test.js`（2）、`e2e-bug-finance-invoice-vault.test.js`（7）、`bugfix-frontend-*.test.js` 等
- 全量：**32 文件 / 251 例**

### 后端回归测试（新增/改动）

`test_dashboard_search.py`、`test_notes.py`、`test_assistant.py`、`test_skill_store.py`、`test_expert_packs.py`、
`test_agent_specs.py`（恢复出厂模块工具）、`test_module_finance_cashflow.py`（2026-10-22 起随模块入库）等；
当轮全量 **469 例**（后被第八节 505 例取代，均为历史值）。

### 旧 E2E 资产（不在上表内）

`dev/e2e/e2e_recon.py`、`e2e_interactions.py`、`e2e_edge.py`、`debug_reporter.py`（归档 + 运行锁）、
`ai_walkthrough.py` + `AI-WALKTHROUGH.md`、`QA-REPORT.md`（旧脚本首轮记录）——口径见第九节。

---

## 七、环境与 git 状态

- **git**：本轮未做任何提交/推送；`git status` 约 180 项改动仍全部在工作区。提交前注意脱敏过滤器
  （`dev/SANITIZE.md`），不要提交 `sanitize-map.local.json`、密钥、本机路径。
- **服务**：探针自起自停；8000 端口工作台服务未由本轮改动启动。
- **数据**：探针产物仅位于 `dev/e2e/probe_results/`；真实 `backend/data/` 未被写入。
- **测试残留**：`.tmp-diag/`、`.tmp-tasks-diag/` 等临时诊断目录已清理。

---

## 八、2026-10-20 加固轮复跑（17 区 656/656 + 性能基线）

上文一～七节为「16 区 647」那一轮的存档，本节记录其后的诊断—修复—验证轮在同一天
的全部改动与复跑结果（**探针资产本身也被修改，故必须复跑全部区域**）。

### 复跑结果（逐区，全部 2026-10-20 实测）

| 区域 | 结果 | 区域 | 结果 |
|---|---|---|---|
| tasks | **36/36** | finance-invoice-vault | **42/42**（复跑，见下 TC-FIV-114） |
| schedules | **22/22** | finance-ledger | **27/27** |
| knowledge | **19/19** | finance-tax-pos-hello | **32/32** |
| jobs | **25/25** | finance-board-cashflow-expense | **46/46** |
| notes | **38/38** | api-contract | **153/153** |
| dashboard-search | **29/29** | debug-center | **33/33**（含新增样式生效断言） |
| assistant-chat | **32/32** | assistant-settings | **38/38** |
| skills-experts | **51/51** | mcp | **24/24** |
| interaction-ux ★新增 | **9/9** | **合计 17 区** | **656/656** |

★ `interaction-ux.py`（端口 8240）是**自动化交互走查**：9 项 INTX 断言覆盖连点单次提交、
失败后对话框保留 + 提交锁复位 + 重试即成功、切月失败清空旧月份、loading 禁用态反馈、
全程零未捕获 rejection。**它不是真人可用性测试**（无参与者、无原话、无主观评分），
探针 docstring 与本节口径一致锁定。

### 本轮对探针资产的修改

- `debug-center.py` TC-DC-RUN-003 改名「页面行展开错误清单（含样式生效）」，新增
  **computed style 断言**（背景 `rgb(254,240,240)` + `max-height:180px`）与
  `data-v-*` 属性采集。这一断言正是用来钉死「Vue scoped 属性不落函数式子组件非根元素，
  选择器静默失配、样式其实没生效」这类肉眼易漏的缺陷。
- `finance-invoice-vault.py` TC-FIV-114 **假失败修复**：`_run_sql` 以「结果卡文本必须变化」
  判定刷新，而上一条用例（`SELECT * FROM t_big` 无 LIMIT）与它（`LIMIT 999999`）渲染出
  完全相同的「结果：1000 行（已截断）」文本，导致误判超时失败——产品行为其实正确
  （后端把顶层 LIMIT 改写为上限+1 并置 `truncated`）。改为先 `goto` 回查询页清空结果卡
  再执行，并补「1000 行」断言，复跑 42/42。

### 性能基线（`dev/e2e/perf_probe.py`）

- 播种规模：tasks 5000 / notes 2000 / schedules 1000 / sessions 200 / messages 4000 /
  transactions 20000；每接口预热 5 次后取 30 次；本机 loopback 单请求，**不是并发压测**。
- API：18 个接口 p95 最慢 67.0ms（`stats/trend?months=24`）；预算 health 20 / list 120 /
  aggregate 250，唯一越线 `/api/health` p95 21.67ms（静态字典端点、两次实测 19.25/21.67ms，
  属阈值落在本机噪声带，**不通过改阈值消音**，已记入 progress.md 待办）。
- 真实浏览器首屏：DOMContentLoaded 201ms、load 211ms、资源 2.70MB、dashboard 往返 13ms、
  0 page error；大头为两个 index chunk（1.28MB + 1.03MB，gzip 413KB + 343KB）。
- 执行计划抽查全部命中索引（`ix_tasks_completed_at` / `ix_notes_pinned_updated` /
  `ix_mod_pl_transaction_occurred_on` / `ix_agent_sessions_updated_at`）；
  notes 的 LIKE 搜索为全表扫描 + TEMP B-TREE 排序（单人数据量下 p95 32ms，可接受）。
- 报告与机器可读结果：`dev/e2e/probe_results/perf/report.md`、`result.json`（改索引或查询后
  复跑比较即可量化收益）。

### 本轮由探针/审查驱动的真实修复

1. `RunsView.vue` 错误清单样式静默失效 → `:deep()` 穿透（探针计算样式断言锁定）。
2. 调试三页失败后计数残留 → `catch` 内 `total` 同步归零。
3. `client_errors.jsonl` 无界增长 → 超 10MB 裁剪保留末 1000 行 + 回归用例。
4. 14 处函数顶层请求缺 `catch`（含 finance-tax 两页 3 处）→ 全部补齐；
   `frontend/tests/no-unhandled-request.test.js` 由「固定清单 20 例」升级为
   **「扫描 `src` 全部 86 个 .vue/.js」**，新页面自动纳管，当前未捕获点为 0。
5. `main.js` Vue errorHandler 仅 DEV 打印 console（上报不变），避免走查噪音。
6. `backend/tmpXXXXXXXX` 0 字节散件：根因是 **pytest capture 在 conftest 之前**用 stdlib
   tempfile，而 Git Bash 的 TEMP 不可写使其退化到 CWD → conftest 钉 `tempfile.tempdir`
   到已验证可写的系统临时根，`.gitignore` 补 `backend/tmp*`、`/tmp*`（原 `tmp*/` 只匹配目录）。

### 已知观察项（不掩盖）

- schedules TC-SCH-019 快速切路由时控制台出现 `Failed to fetch dynamically imported module`：
  三次运行一致（历史同款、hash 随构建变化）、用例通过，判定为在途模块请求被中止的
  走查噪音，非产品回归。
- api-contract 服务端日志 1 条 h11 `LocalProtocolError` traceback：来自探针故意发送的畸形
  请求体在 uvicorn `send_400_response` 路径上的框架产物，非应用代码。
- mcp / api-contract 结果里的 `console_errors` 400/404/422/503 为负例预期。
- tasks 页报告里的 5 条控制台错误（2×422 + 3×`net::ERR_FAILED`）同样是**探针自己造的**：
  `tasks.py` 用 `page.route(...)` 对 DELETE/PUT/GET 显式 `r.abort()` 来验证失败反馈与
  提交锁复位，属于负例设计，不是页面回归。

### 测试与 git 状态

- 后端 pytest **505/505**（3:30，4 条上游弃用警告）；前端 vitest **352/352**（34 个文件）；
  `npm run build` 通过（8.56s，dist 与源码同步）。
- **本轮未做任何提交/推送**；`git status` 约 190 项改动全部留在工作区。提交前注意脱敏
  过滤器（`dev/SANITIZE.md`），不要提交 `sanitize-map.local.json`、密钥、本机路径。
- 真实 `backend/data/` 未被探针写入（全部走 `probe_results/` 内的独立库与数据目录）；
  但调试中心真实 `backend/data/debug/client_errors.jsonl` 存有 **45 条本轮之前的历史报错**
  （gitignored 用户数据，未删除，待决定）。

---

## 九、旧 E2E 三脚本与 AI 走查（固定端口 8123/8124，2026-10-22 隔离加固后口径）

第八节之外，`dev/e2e/` 下还有一套早于 probes 的旧 E2E 资产。2026-10-22 swarm 任务
`e2e-isolation` 对其完成隔离加固（修复前 recon 缺 `FINANCE_DATA_DIR`/`DEBUG_DATA_DIR`
会直写真实 `backend/data`，三脚本归档均落真实调试中心），契约由
`backend/tests/test_swarm_e2e_isolation.py`（19 项）静态 + 行为守护。

### 9.1 三脚本与隔离三件套

| 脚本 | 端口 | 测试库（DATABASE_URL） | 财务数据目录 | 调试归档目录 | 归档 kind |
|---|---|---|---|---|---|
| `e2e_recon.py`（20 页加载侦察 + 截图） | 8123 | `dev/e2e/e2e_recon.db` | `dev/e2e/finance_data_recon/` | `dev/e2e/debug_data_recon/` | `recon` |
| `e2e_interactions.py`（12 条核心交互） | 8123 | `dev/e2e/e2e.db` | `dev/e2e/finance_data/` | `dev/e2e/debug_data_interactions/` | `interactions` |
| `e2e_edge.py`（5 条边界场景） | 8124 | `dev/e2e/e2e_edge.db` | `dev/e2e/finance_data_edge/` | `dev/e2e/debug_data_edge/` | `edge` |

- **独立隔离**：每个脚本在启动服务前各自设置 `DATABASE_URL` / `FINANCE_DATA_DIR` /
  `DEBUG_DATA_DIR`，路径全部在 `dev/e2e/` 下且两两不同，**绝不指向 `backend/data/` 真实数据**。
- **归档器同目录**：`DEBUG_DATA_DIR` 同时写入父进程 `os.environ`（归档器 `save_run` 在本进程读）
  与子进程 env（服务端 `service.data_dir()` 读），两侧同值，保证归档与服务看到同一调试目录。
- **每次运行重置**：拿锁后先删除自有库文件、rmtree 自有财务/调试目录，再起服务，运行环境独立。

### 9.2 运行互斥锁

- 三脚本共用 `debug_reporter.acquire_run_lock()`：OS 级排他文件锁（Windows `msvcrt` /
  POSIX `fcntl`，锁文件 `dev/e2e/.e2e-run.lock`），**先拿锁 → 再重置目录 → 再启动服务**，
  `finally` 中释放；进程退出自动释放、无残留锁；锁已被持有时抛 `RuntimeError` 明确拒绝，
  不会静默互踩端口/目录。

### 9.3 运行顺序

- recon 与 interactions 共用端口 8123、edge 用 8124；三脚本无共享数据，**顺序自由但必须串行**
  （互斥锁强制，任一时刻只能跑一个）。推荐 recon → interactions → edge（先侦察后交互），
  仅为约定而非硬依赖。
- 每个脚本自起自停后端（`backend/venv` 的 uvicorn，绑 `127.0.0.1`），跑完即释放端口与锁；
  控制台中文乱码是 cp936/UTF-8 显示差异，归档 JSON 为 UTF-8，不影响结果。

### 9.4 产物归档

- 跑完经 `debug_reporter.save_run()` 落**各自** `debug_data_*/runs/<run_id>/`：
  `result.json`（totals / pages / tests / extra）；recon 另把 `dev/e2e/shots/` 的截图复制进
  `runs/<run_id>/shots/`。run_id 形如 `20261022-153000_recon`（同日重跑自动追加 `_n`）。
- 有错误页面 / 失败用例 → 自动建/更新问题记录（`issues.json`，source=e2e、P1）；
  **全绿不写 issues.json**，不自动关闭既有 open 问题。归档失败只打印「调试中心归档跳过」，
  不阻断脚本自身输出。
- 归档位置从此是 `dev/e2e/debug_data_*`，**不再进入真实调试中心** `backend/data/debug`
  （历史污染残留见 9.6）。

### 9.5 AI 走查隔离口径（默认隔离归档）

- `ai_walkthrough.py` 是人工验收层：**面向真实服务 `http://127.0.0.1:8000`**
  （`启动工作台.bat` 起的实例），不起服务、不占 8123/8124、**不持互斥锁**；
  与三脚本默认归档位置不同，互不干扰。
- 以 `kind=ai-verify` 归档到**默认隔离目录** `dev/e2e/debug_data_ai_verify/`
  （每次运行前重置）；需要归档到别处时用 `--debug-dir` 或环境变量 `DEBUG_DATA_DIR`
  显式指定。未设置环境变量时**不再落真实 `backend/data/debug`**。
- 结果记录：全绿 `python ai_walkthrough.py`；失败
  `python ai_walkthrough.py --fail AI-VERIFY-004:原因`（可重复、可加 `--note`）；
  失败项自动建/更新问题（source=e2e、P1）。流程与 12 项验收集见 `dev/e2e/AI-WALKTHROUGH.md`。

### 9.6 当前状态（2026-10-22 复跑后）

- 三旧脚本在隔离加固后**已复跑并通过**：recon / interactions / edge 全部 0 失败，归档分别落在
  `dev/e2e/debug_data_recon|interactions|edge`，未写真实 `backend/data/debug`。
- `backend/data/debug/runs/` 中 2026-10-13 / 10-19 的 recon / interactions / edge 归档是
  修复前写入的真实调试数据（历史记录，未删除）；真实 `client_errors.jsonl` 的历史报错
  同待使用方决定。

---

## 十、2026-10-22 Swarm 第三轮（已完成自动化复跑）

> 本节记录 2026-10-22 第三轮的静态加固、实跑结果与探针口径修正；历史 656/656 与本节当前 656/656
> 是不同轮次的实测，不互相替代。

1. **17 区探针三目录隔离静态补齐**：原先 12/17 缺 `DEBUG_DATA_DIR`，已在启动服务前全部补齐
   `DATABASE_URL` / `FINANCE_DATA_DIR` / `DEBUG_DATA_DIR`；由
   `backend/tests/test_swarm3_probes_isolation.py` 与既有
   `backend/tests/test_swarm_probe_contracts.py` 守护（合计 **78 passed**，17 个探针语法编译通过）。
2. **17 区探针实跑**：合计 **656/656 通过**（api-contract 153、skills-experts 51、
   finance-board-cashflow-expense 46、finance-invoice-vault 42、assistant-chat 32、
   finance-tax-pos-hello 32、debug-center 33、dashboard-search 29、finance-ledger 27、jobs 25、
   mcp 24、schedules 22、knowledge 19、interaction-ux 9、notes 38、tasks 36、assistant-settings 38）。
3. **首轮实跑失败与处置（均为探针口径滞后，非产品安全回退）**：
   - assistant-chat：发送失败现行契约是回滚乐观气泡并恢复草稿，探针旧断言等待错误气泡 → 改为断言 toast/草稿恢复；
   - assistant-settings / finance-ledger：删除模型/账户/分类已有可取消确认，探针旧口径点 X 即删 → 补确认步骤；
   - finance-board-cashflow-expense：非法 ZIP 名按审计要求拒绝而非清洗接受；非法 xlsx 在行数/内容护栏前拒绝且不写运行记录 → 按现行契约改写；
   - finance-invoice-vault：批次 404 现为 `el-result` 错误态 → 改断言错误态；
   - mcp：env 落库已 DPAPI 密封 → 断言密封与键名，不再断言明文；
   - 费用路径穿越：原断言要求 inbox 恰好 1 个文件，受长时服务进程文件句柄/清理竞争影响 → 改为断言“有落盘、无 `..`、无明文 evil 名”，不降低穿越防护。
4. **旧 E2E 三脚本**：隔离复跑全部通过（recon / interactions / edge），归档在各自 `debug_data_*`。
5. **性能探针**：18 接口 p95 最慢 76.68ms（`stats/trend?months=24`）；`/api/health` p95 21.81ms
   越 20ms 预算（静态端点，未改阈值）；首屏 DOMContentLoaded 207ms、load 220ms、资源 2.705MB、0 page error。
6. **本轮已确认的数据边界根因**：`backend/tests/test_swarm_personal_memory.py` 直接上传
   `poison.csv` / `many.csv`，而 pytest 公共夹具此前未隔离 `FINANCE_DATA_DIR`，会写真实 inbox。
   已先在 `backend/tests/test_swarm3_data_isolation.py` 复现红，再修
   `backend/tests/conftest.py`（临时财务目录 + 结束清理），相关测试 **24 passed**。
7. **前端竞态加固**：新增 `frontend/tests/swarm3-request-races.test.js`（11 项，先红后绿），
   覆盖数据集预览、BIP 导入向导、凭证列表、费用运行列表、现金流运行列表五处慢旧响应竞态。
8. **后端加固**：新增 `backend/tests/test_swarm3_backend_hardening.py`（14 项，先红后绿），
   覆盖 Origin 不再用 Host 互证、MCP env DPAPI 密封与迁移、看板快照路径边界、BIP 导出行数护栏前移、
   税务分页/LIKE/`sys.path`、个人 CSV 公式注入、概览今日日程有界截断、数据仓 LIMIT 不改字符串字面量。
