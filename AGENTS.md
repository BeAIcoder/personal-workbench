# AGENTS.md — personal-workbench 项目记忆

> 本文件只放跨会话必须记住的稳定事实、约定、硬约束与待办；按日期的工作史与验证数字在 `progress.md`（2026-11-04 起含压缩前全文归档），机制细节见 `算法实现逻辑.md` / `spec.md`。Hub 级记忆（脱敏钥匙等）见 Agent Hub，不在此重复。

## 项目速览

- Personal Workbench 个人财税自动化工作台：FastAPI + SQLAlchemy + SQLite（WAL）/ Vue 3 + Vite + Element Plus；版本 v1.16.1。
- **24 个模块 / 43 张模块表**；侧栏：基础 5、AI 助手、AI 配置 4、定时任务、财务 14（直挂数据仓 + 账务处理/记账/税务填报/报表分析四个子分组）、经营职能 8（GM 70、招商 71、营运 72、企划 73、工程 74、法务 81、人事 82、DQ）、调试 3；共 25 个可见菜单，另有隐藏模块示例 `finance-hello`（可直达 `/#/finance/hello`）。
- 数据边界：真实数据在 `backend/data`（gitignore）；测试一律临时 SQLite + 合成数据；真实 Key 只经界面/环境变量录入，禁止写入文档、日志、提交、截图。

## 文档地图

- 用户手册：`使用说明.md`（唯一权威，版本 v1.16.1）、`数据收传使用说明.md`、`数据模板/README.md`。
- 开发文档：`spec.md`、`API.md`（258 路径 / 343 操作，全量索引由 `dev/gen_api_index.py` 生成）、`算法实现逻辑.md`、`功能模块拓扑图.md`、`注意事项.md`、`新增业务模块需求与设计.md`、`BUGfix.md`（缺陷台账）、`20261012New/adr/`。
- 历史与证据：`progress.md`（本地 gitignore）、根目录 dated 报告、`2026-11-03_检查结果.md`、`task-11-report.md`、`backend/.pytest_tmp/*`。

## 全局工程约定

- git 提交经 clean/smudge 脱敏过滤器（本机路径/公司名/昵称 → 占位符）；`dev/sanitize-map.local.json` 是唯一反向钥匙（不入库，丢失无法自动还原）；过滤器按 UTF-8 + GB18030 双编码做字节级替换，覆盖范围见 `.gitattributes`，机制见 `dev/SANITIZE.md`。本地历史已用 `git filter-repo` 重写（HEAD `5e08c6b`，镜像在 `backups/history-mirror-20261104*.git`），**仍未 push**。
- 提交信息用中文 conventional commits（`fix(assistant): …` / `docs: …` / `test: …`）。
- 环境相关默认值统一走 `backend/.env`（字段定义 `backend/app/config.py`，清单 `backend/.env.example`）；新增默认值必须先加配置字段。`WORKBENCH_DATA_DIR` 在 config 导入期派生 `DATA_DIR/UPLOAD_DIR/DATABASE_URL`；探针必须同时设 `WORKBENCH_DATA_DIR / DATABASE_URL / FINANCE_DATA_DIR / DEBUG_DATA_DIR` 四域并在启动前重置自有数据根（`test_swarm_e2e_isolation.py` 的 AST 契约覆盖 20 个探针）。
- 文档契约（`test_docs_contract.py` 58 例）：新增/删除路由后必须重跑 `cd backend && PYTHONPATH=. venv/Scripts/python.exe ../dev/gen_api_index.py` 并替换 API.md 索引；新增菜单必须在《使用说明》有对应八段式章节；版本号以 `backend/app/config.py` 为单一源；文档内所有 `/api` 路径必须真实存在。
- Node 运行时 ≥ v24.22.1（安全更新）；`frontend/.nvmrc` 与 `package.json` 的 `engines` 为权威约束。PHP 本项目零使用。
- Windows 路径：环境变量与命令行用 `D:/…` 风格——Git Bash 的 `/d/…` 会被 SQLAlchemy 误解析成 `D:\d\…`。

## 测试与环境

- 后端：`cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q -n 12`（xdist，全量约 70–115s）；前端：`cd frontend && npm test` + `npm run build`。
- SQLite 连接监听启用 `journal_mode=WAL` + `busy_timeout=5000` + `temp_store=2`；备份走 backup API，ZIP 跳过 `-wal/-shm`。
- conftest 对 client 用例 teardown **只 drop 不重建**：新增非 client 测试文件必须配 `autouse _ensure_tables` 夹具（每测试前 create_all），否则排在 client 重文件之后会撞 `no such table`。
- 改执行通道必须同步改测试的 patch 接缝，并防测试误触真实外部任务：看板测试 fixture 固定 `_scheduled_task_available=False` 或显式替换任务名（曾误触发真实计划任务，整套拖慢 14 倍）。
- 外部 HTTP 全走 mock/本地夹具，密钥全合成；不写真实 `backend/data`。需要迁移演练时在复制库上做，并做两次启动幂等校验。
- 提交前基线（2026-11-04）：后端全量 2011 passed（并行约 116s / 串行约 323s）、前端 75 文件 1082 passed、构建通过（stamp `a79a9b5c9b0c`）。

## 模块开发规范

- 业务/职能模块前缀一律 `/api/business/<domain>`（`/api/other/` 拒绝），财务模块 `/api/finance/<domain>`；模块 = 后端 `app/modules/<name>/`（manifest/service/router/__init__）+ 前端 `src/modules/<name>/`，注册中心自动挂载。
- manifest 校验失败会**静默跳过该模块并级联跳过 requires 依赖模块**——新增模块后必须跑 `test_manifest_loaded` / 聚焦测试兜底。
- Agent 工具参数必须是 `(type, description, required)` **三元组**（内置 `TOOL_SPECS` 契约；`build_tools` 有容错但契约测试锁死）；每个业务模块 1 个只读 summary 工具，并预置到对口专家（`module_tool_preset.py` 启动时幂等并入）。
- 金额 `Decimal(ROUND_HALF_UP)` 两位；日期 `YYYY-MM-DD`、期间 `YYYY-MM` 严格校验；状态 `Literal` + 服务层白名单双保险；删除按业务级联（合同→账单、线索→跟进、员工→考勤/薪资）；导出 xlsx 一律 `app.utils_xlsx.safe_cell`（`= + - @` 前缀加 `'` 防公式注入）。
- 跨模块勾稽：`OpsContract.unit_id` 校验铺位存在 + 同铺位唯一生效合同；出账读租决（免租期/年递增/抽成与保底抽成合同拒绝按固定租金出账 409）；HR `special_additional` 进累计预扣（与 `finance_iit` 同函数同口径）。
- DQ（数据质量巡检）：`issue_count` = Σ 命中行数（与 top_issues 20 条无关）；总分 = 可用域平均；新增检查必须配空库断言防存量误报；跨表/聚合查询优先 SQLAlchemy ORM 分组或 `COUNT+WHERE`（Mimosa 对非 COUNT 聚合、无 LIMIT 裸列查询误报）；路由 `as_of` 用 `Query(default=None, pattern=...)`（`default=""` 会撞 pattern 422）。
- 新增填报类模块样板：manifest requires `finance_tax` + 计算落库 + openpyxl 底稿导出 + 中文 400；计税前必须查 `policy_validity.policy_status` 与税率 `effective_date`。
- 准入制 `finance_tax/config/tax_scope.py`：9 税种 5 admitted / 4 blocked（房产税、城镇土地使用税、土增税、契税）；无地方口径与脱敏样本不得解禁，不编税率。

## 前端约定

- 视图请求必须过 `no-unhandled-request` 静态守卫（一律 try/catch；业务 catch 不重复弹 `ElMessage.error`，由拦截器统一提示）。
- 菜单：manifest `menu.subgroup` + `src/modules/index.js::getModuleMenuGroups()` + AppLayout 嵌套 `el-sub-menu`；模块示例 `menu: null` 不进侧栏。
- Vite 拆包：`vendor-core`（vue + Element Plus + icons + basic）必须同 chunk——EP 与 vue 跨包初始化顺序耦合，分开会触发 `Cannot access before initialization`；echarts / ag-grid 可独立。
- EP 按需引入：`ElementPlusResolver({ importStyle: false })`（全局 CSS 保留）；`v-loading` 必须单独 `app.use(ElLoading)`，否则静默失效；字符串名图标经 `<component :is>` 渲染、resolver 覆盖不到，`main.js` 维护 31 个图标的精确注册表；locale 由 App.vue `el-config-provider` 提供。
- 下载：响应同时给 ASCII `filename` 与 `filename*=utf-8''`；前端用 axios `responseType:'blob'` + `a.download` 显式落名（中文名经代理链路不可靠）。
- 分页上限：后端 leasing units `page_size ≤ 200`，前端不要传 500（会 422）。
- 上传页接入 `TemplateLinks`（`GET /api/templates` 白名单下载）；上传限额 128MB 全局 / 20·10·5·50MB 分项 / 200 张 / 200,000 行。

## AI 栈约定

- 路由优先级：显式指定 > 高置信关键词 > **续聊保持**（同会话当前 Agent）> LLM 仲裁 > 关键词回退 > 默认；`job-*` / `memory-extract` 会话不参与续聊。中断的流式回复在 `CancelledError/GeneratorExit` 时补存 assistant（正文为空写「（已停止生成）」）。
- `agent_settings.routing_model_id` 可指定轻量仲裁模型（None=跟随全局激活模型）；LLM 仲裁结果 TTL 缓存 600s / 上限 128，只缓存成功，`invalidate()` 清空。
- 长会话滚动摘要：`agent_sessions.summary/summary_until` 游标防重复压缩；未压缩历史超过注入窗口 2 倍才压缩窗口外内容；失败保留旧摘要并退避 10 分钟，绝不阻塞聊天。
- trace 治理：单条 result 截 4k 字符、总额 100KB 超限丢弃最旧并留说明标记，保持 `{tool,args,result}` 列表结构。
- 模型同步：`fetch_remote_models` 在线失败抛 `RemoteFetchError`，**200+空列表或结构不可识别一律报错，不假成功**；目录兜底 + `partial/truncated` 如实上报；只发目录/管控请求，绝不推理；列表地址走 `_check_http_url`（20s 超时、4MB 上限、不跟随重定向）；写库单事务、二次同步全 unchanged、远端消失不删本地；manual/probe 覆盖保留，`override_manual=True` 才覆盖。
- 模型探测：工具/图片三态，只有 400/422（SDK `BadRequestError` / `status_code` / 明确 `Error code: 400|422`）判 rejected，401/403/404/408/429/5xx/网络一律 unknown；probe 明确结论写 `sources=probe` + `manual_fields`（本地覆盖），unknown 不改值；`accepted ≠ supported`。
- 思考参数：档位不写死表；模型声明 `thinking_levels` 时声明集外档位回退（`none/off` 不受门控）；`thinking_can_disable=False` 不可关；Anthropic effort→`output_config.effort`、toggle→`thinking{type}`；`supports_thinking=0` 一律不发思考参数。
- 会话头：仅预设声明 `session_header/user_agent` 的套餐（OpenCode Go）生成；密钥只走 SDK 鉴权，绝不进自定义头；按 (模型, 会话) LRU 缓存上限 64；`invalidate()`/`session_delete()` 清理。
- `job-*` 定时任务选中 `usage_scope=plan_interactive_only` 套餐模型时中文拒绝、不发起推理（预路由只在自动路由时预检全局模型）。
- `_ensure_agent` 里 `agent.observe(history)` 必须 `await`（曾漏 await 导致历史注入与摘要长期静默失效）；注入失败不阻断对话。

## 数据导入与外部引擎

- BIP：凭证列表/辅助明细账/辅助余额表**三份独立文件**（整文件识别单一类型，三合一识别混乱）；判重=账簿+期间+凭证号；辅助明细账按金额签名合并；余额表按账簿+期间+科目 upsert。
- POS 引擎按文件名关键词发现文件（认领记录/辅助明细账/凭证列表，模板名即钥匙，填完别改名）；「引擎要求 --project」时后端把可用项目名并进 detail，前端弹窗补填 `?project=` 重试。
- 发票：归类单一规则源=引擎 `config.local.yaml` 的 `address_keywords/address_force_mapping`（长词优先；规则为空返回 unchanged）；判定链 引擎项目名→备注→PDF 全文→同批同购方继承→未归类；人工修正永不覆盖；导出按项目拆 ZIP，命名「项目+月份.zip」。
- 银行流水（bankflow）：建行/中行表头按「交易日期+账号」定位；流水号候选链，全空按 SHA-256 合成 `SYN<20位>`；账号支持尾号 key（≥4 位后缀）；**生成官方 Excel 模板一律以官方样例为骨架**（`template_skeleton.xlsx`，勿手改），从零建表会丢样式/数据验证/@ 文本格式导致导入失败。
- 费用/现金流/POS/发票/看板属外部引擎管线：路径支持目录或单文件（`utils.resolve_engine_root`）；TEMP 环境变量与上传文件名乱码有专门修复（`utils.engine_subprocess_env` / `repair_upload_mojibake`）；POS/现金流执行互斥 + 2h 陈旧 running 自动失败。

## 看板与定时任务

- 报表看板 `run_pipeline` **优先计划任务通道**：`schtasks /run /tn 微信报表监控`，轮询其 `logs/last_run.json`（600s，2s 间隔），四步 exit 全 0 才归档快照；运行中 409、失败/超时 503 并落 failed 快照；无该任务的机器自动回退直接子进程。前端 run 调用超时覆盖为 300s。
- 失败重试：`_PIPELINE_ATTEMPTS=2`、间隔 5s；最终失败登记调试中心「问题记录」P2（同标题未关闭去重，关闭态是 done/wontfix）；同秒连跑用 `_unique_stamp()` 防唯一约束冲突；最近两条快照均 failed → DQ error。
- `GET /api/finance/board/verify` 做产物内容级巡检（内嵌 JSON 解析、敏感键 `map` 剔除、顶层键一致（meta 除外）、数值容差 0.01、关键 KPI），结果按快照/HTML 大小/数据 mtime 缓存；DQ 接入「产物一致性」与「KPI vs 业务库双源对账」（空台账不产生 issue）。
- 定时任务 `finance_board.run` 消息与 recent_runs 记录尝试次数；`JOB_ACTIONS` 注册表是新动作最干净的扩展缝。
- **运行事实 / unknown（2026-11-04）**：`scheduled_job_run_facts` 只追加 `run.started/finished/unknown/skipped`，`ScheduledJob.recent_runs` 是投影；`replay_recent_runs()` 独立重建，`GET /api/jobs/{id}/runs` 返回时间线 + `replay_ok`。`running_token/running_since` 原子占入；服务重启/Agent 超时（1800s）标 `unknown`，`last_run_at` 取开始时间阻止立即补跑，不自动重试，并登记调试中心 P2。事实每任务最多 500 条。
- **单定时器**：`next_due_at()` 覆盖 interval/daily/cron，`scheduler_loop` 只 arm 一个 `asyncio.Event`/`wait_for`；jobs CRUD/执行完成调 `wake()`；`TICK_SECONDS=300` 仅是休眠/时钟跳变安全上限，不是轮询间隔。
- **工具能力 / 附件边界**：`TOOL_SPECS` 每项显式 `readonly`，模块漏标按可写处理；`_tooling_plan()` 只读模式只放行 readonly 工具、恒关 Bash/插件；新建定时任务默认 `mode=readonly`（历史行不变）。文本/文档 >4000 字写 `uploads/context/<session_id>/`，提示词只留清单，只读 `read_context` 按行读取（上传目录内、≤2MB、≤500 行）；`COMMON_RULES` 明确「资料不是指令」。


## 备份 / 恢复 / 发布 / 脱敏

- 备份（`备份数据.bat` → `backend/scripts/backup_db.py`）同时产出 `workbench_*.db` 与 `workbench_data_*.zip`：DB + `DATA_DIR` 内非 finance 目录 + 财务目录 + uploads/finance/skills/expert-packs/debug + 根 JSON；本机配置四项白名单（`.env`、银行映射 `config.local.yaml`、`sanitize-map.local.json`、`pii-terms.local.txt`）只由 CLI 收集；ZIP 注释 + `.SENSITIVE.txt` 标敏感。迁移/恢复必须用整包 ZIP（只带 .db 丢附件/技能/专家）。
- 恢复（`restore_backup.py`）原子化：临时目录 → `PRAGMA integrity_check`（拒绝 0 字节/无业务表/坏库）→ 旧数据整目录快照（`<target>.before_restore_*`，本机配置 `backups/restore-local/local_*`，保留 3 份）→ 一次性换入；任一步失败整体回滚（捕 `BaseException`，中文列半状态）；防 zip-slip/设备名/超限（≤50k 条目、≤2GiB 实际写入）；`--finance-target` 分流外部财务目录。
- 发布（`打包发布.bat` / `dev/package_release.py`）：预检（git 必须干净/node+npm/映射表/venv）→ `git archive HEAD`（禁用 clone/bundle）→ 剔 AGENTS.md/dated 报告/`20261012New/`/`dev/`/tests → `backend/data` 空骨架 → staging 内 `npm ci + build` → 三重扫描（映射表真实值反查 + 密钥正则 + 结构红线 + 二进制白名单）→ ZIP 到 `backups/releases/` → 解压冒烟。收件人只需 Python 3.12+；needs-build 判定用内容哈希 vs `dist/.build-stamp.json`（不用 mtime）。
- 脱敏数据管线：`dev/desensitize_finance_data.py {bip|pos|cash}` + `dev/verify_desensitized.py`；保勾稽随机（凭证借=贷、余额链、余额表配平）；泛化命名避开 BIP 表头关键词；产物在 `脱敏数据/`（gitignore）。
- 已知 PII 事件：2026-11-04 批量 PII 脚本误改本机 `sanitize-map.local.json` 与 `pii-terms.local.txt`，已按上下文重建（非原钥匙）；有离线备份时必须核对恢复；未进入分发包，历史扫描 0 hits 结论不受影响。

## 硬约束与环境坑位

- **进程树写拦截**：本机终端安全软件（`QQSysMonX64` / `FileCrypt`）按进程树禁止从 venv python 派生的进程写 `D:\AssetLib`，换解释器无效；只有任务计划服务进程树可写。报表看板跨目录写**必须**保留计划任务通道；新增跨目录写功能先验证此约束。
- 服务启停：启动用 `开机自启动.vbs`（注册在启动文件夹 `个人工作台.lnk`，单一来源；计划任务 `\PersonalWorkbench` 已删除防双跑）；重启用 PowerShell `Stop-Process` 按**监听 PID** 精确停止 + `Start-Process wscript.exe`（`cmd /c start "" wscript …` 起不来）；uvicorn 先跑 lifespan 才绑端口，启动等待 60s，超时文案「可能仍在启动」。按命令行批量 `taskkill` 会误杀自身。
- 脚本编码：VBScript（`.vbs`）/`.bat` 必须 **CRLF + ANSI（GBK）**；MSYS 工具（sed 等）会剥 CR，LF 的 bat 在 cmd 括号块会错乱；Bash 工具命令行里的 `>nul` 会被改写，重定向内容尽量从文件读取；`timeout` 包 cscript 会连带杀死异步 uvicorn。
- Mimosa：历史 6 条 findings 全为已知误报/设计取舍（`dev/package_release.py` 冒烟 127.0.0.1 与自建 ZIP ×3、`20261012New/scripts/migrate_tax_data.py` 历史 SQL ×2、`dev/desensitize_finance_data.py` 保勾稽固定种子 ×1），新扫描出现这 6 条不算回归；PreToolUse 对非 COUNT 聚合 SQL、无 LIMIT 裸列查询误报。
- Playwright：会话级 console 是累计视图，**验证必须开新标签页隔离读取**；上传只吃 workspace 内文件（外部文件先复制进来，用后删除）；`el-upload :limit=1` 换文件先 on-remove 复位；`api` 实例 baseURL 含 `/api`。
- 写文件噪声：本项目 Write/Edit 高频注入占位噪声（长文件、甚至短脚本），**每次写后立即回读或跑聚焦测试**，`py_compile` / vitest / build 是最后防线；grep `占位|filingError|manifests_源` 可快速自检。
- 其他：`openpyxl.Workbook` 不支持 with；`_locate_header_row` 必须按行枚举（cells.index 是列位置）；uvicorn 只监听 127.0.0.1，非回环绑定需 `ALLOW_NON_LOOPBACK` + 登录态（当前无 auth，禁止开放）。

## 当前状态与待办（截至 2026-11-04）

- 基线：后端全量 2011 passed（并行 116s / 串行 323s）、前端 75 文件 1082 passed、构建 stamp `a79a9b5c9b0c`；发布包 `backups/releases/个人工作台-v1.16.1-20261104.zip`（527 文件 / 2.38 MB，sha256 `d14ef14d…`）。
- 本轮增量（AI 助手可靠性强化，2026-11-04）：后端串行全量 **2039 passed / 1 个与本轮无关的既有 POS 测试失败**（`test_execute_project_inference_failure_marks_error_for_retry` 期望 503 实得 200，单独复跑仍失败）；xdist 在本机出现 worker 停顿，回归以串行为准；前端 **75 文件 1084 passed**；构建 stamp `0d4fe18b1bd4`。
- git：HEAD `5e08c6b`（filter-repo 后），工作区含大量交付物**未提交/未推送**；推送需用户在本机完成 GCM 授权；CI 已配置但从未真实首跑。
- 外部阻断：L-056 / L-068 / L-156（历史本机路径是否再处理待用户决定）、L-170（OpenCode 真实 Key）、L-151（CI 首跑）；已知限制 L-028（MCP 自动重连）。
- 待办：看板重跑生成合规快照（verify 发现 20 处 KPI 差异 + 旧 HTML 含 map）；U10 真人可用性复测（≥80% 无协助）；2 个依赖外部 SDD `server.log` 的探针未独立运行；准入制四税种待地方口径；13 周现金流待业务口径；政策版本化需逐条官方日期复核。
- 演进 P1/P2：长文件拆分（orchestrator / config_store / invoice service、SettingsDrawer / ChatPanel）；路由轻量化 UI；EP 迁移已完成；PostgreSQL + Alembic（触发：多设备/多用户/多 worker，且 auth 先行）；pinia、向量检索、MCP 长结果落盘按触发条件；agentscope 精确锁定，升级单独评估。
- 历史细节：本次压缩前的逐轮记录见 `progress.md` 2026-11-04 归档；更早的验证证据见根目录 dated 报告与 `backend/.pytest_tmp/`。
