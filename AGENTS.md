# AGENTS.md — personal-workbench 项目记忆

> 本文件是项目级长期记忆：跨会话保持的状态、约定与待办。Hub 级记忆（脱敏钥匙等）见 Agent Hub，不在此重复。

## AI 打通与数据清洗强化（2026-11-01）

- **OpenCode Go 更新替换**：预设 `opencode_go` 模型目录按官方 Go 文档刷新（26 模型，区域/隐私受限模型不收录）；`dev/import_opencode_go.py` 把真实库供应商 id=2 从旧端点（`https://opencode.ai/zen/go`，anthropic）替换为现行端点（`https://opencode.ai/inference/go/openai/v1`，openai 协议，preset_key/models_url/usage_scope=plan_api_allowed），启用后目录兜底同步 31 模型并激活 **glm-5.3-flash**。**遗留：库中 Key 对新端点 401，需在 AI 助手→设置→供应商→OpenCode Go 粘贴新 Key**；在线同步会自动从 401 兜底到官方目录，不假成功。
- **新模块 business_dq（数据质量巡检，第 23 个模块）**：`/api/business/dq/summary`（七域×五维，全字面量 SQL 只读对账、零新表；表缺失→available=false+中文 gap_reasons；score=100-Σ扣分，error 10/warning 3/info 1）。`dq_summary` 只读工具预置 realestate/cre_gm。**路由 as_of 必须用 `Query(default=None, pattern=...)`——default="" 会撞 pattern 直接 422**。
- **财务域 AI 工具补齐**：finance_tax `tax_returns_summary` / finance_pos `pos_summary` / finance_cashflow `cashflow_summary` 三个只读工具预置 finance_specialist；`module_tool_preset.py` FINANCE_TOOL_NAMES 同步（dq_summary 属业务专家，不在该清单）。manifest 校验失败会**静默跳过模块并级联跳过 requires 依赖模块**——本轮 params 字段被噪声吃掉导致 4 个税种模块消失，靠聚焦测试抓回。
- **前端 AI 入口全覆盖**：`utils/assistantEntry.js` + `components/assistant/AskAiButton.vue`；**15 个模块视图接线**（业务 7 + 财务 8，每视图 3 处：模板按钮/import+aiPrompt）；新视图 DqView（总分/七域卡/issues 表）。Vue 视图加代码时**不能把既有 ref 声明夹删**（PlanningView loading 曾被删，vitest 抓回）。
- **验证**：后端聚焦 29 passed + 全量回归（见下）；前端 **59 文件 885 passed** + 构建通过（stamp `5a4a8d99af0d`）；运行态：服务重启后 dq summary 200（含缺省）、专家白名单含新工具、OpenCode Go 运行态生效。真实 `backend/data` 仅供应商配置写入（预备份 `workbench_20261101_222929.db`+ZIP）。**Mimosa 深度扫描通过**：6 findings 全为历史已知项（package_release 127.0.0.1 冒烟误报×2+路径穿越误报、20261012New 历史存档 SQL×2、保勾稽固定种子×1），本轮新文件 0 findings；seal `7a43f99b…`。

## ZCode 配置同步 + 数据清洗增强（2026-11-02）

- **OpenCode Go 按 ZCode 配置同步**：`dev/sync_opencode_from_zcode.py` 读取 `~/.zcode/v2/config.json` 第一个带 Key 的 OpenCode 供应商，把 apiKey/baseURL/协议写进真实库供应商 id=2（→ **anthropic + `https://opencode.ai/zen/go`**），目录同步 31 模型并**激活 deepseek-v4.1-flash**（默认模型，替换 glm-5.3-flash）。**实测：ZCode 本地 4 个 Key 全部 401**（当前 ZCode 会话模型 `72bd6520…/deepseek-v4.1-flash` 走宿主账号网关，AppData/config 均无本地 Key）——需用户在界面粘贴最新 Key；`/api/assistant/chat` 冒烟返回 502+中文提示（不假成功、不裸栈）。
- **vault append 行级去重**：`ingest_dataframe` append 模式整行重复自动跳过（DuckDB 关系 API anti-join，列名 `_quote_ident` 双引号转义，无值拼接），返回新增 `skipped_duplicates`；批内重复保留、列集合不一致退回 by_name 直追。测试：契约测试改为新语义 + `test_append_dedupe_semantics`（批内重复/部分重叠/全重复）。
- **safe_cell 六份拷贝收敛**：leasing/operations/hr/legal/planning/facility 的本地 `_cell`/内联 `cell` 全部改为 `app.utils_xlsx.safe_cell`（A 类别名导入、B 类直接调用）；finance_ledger_bip 的 `_cell` 是 JSON 序列化用途，不属公式防护，保持不动。
- **验证**：聚焦 **136 passed**（vault 10 + 六业务模块/数据仓相关 126）；运行态重启后 provider2=anthropic/zen-go/31 模型/active=deepseek-v4.1-flash、dq summary 100。**Mimosa deep 复核**：6 findings 仍全为历史已知项（同上轮），本轮改动文件 0 findings；seal `sha256:b3489936…`。

## 财务菜单层级重组（2026-11-01）

- 「财务」侧栏组由 14 项扁平改为**直挂数据仓 + 4 个二级折叠子分组**（账务处理=费用/发票/POS、记账=个人/BIP、税务填报=企所税/印花/增值/个税/残保金、报表分析=看板/现金流）。机制：manifest `menu.subgroup`（可选）+ `src/modules/index.js` 新增 `getModuleMenuGroups()`（纯聚合，`getModuleMenus` 未动）+ AppLayout 动态段嵌套 `el-sub-menu` 渲染 + `defaultOpeneds`（setup 一次性按初始 route 反查父级 index，保证深链/刷新自动展开；不做成响应式以免覆盖用户手动折叠）。
- **模块示例（finance-hello）不进侧栏**：manifest `menu: null`（注册中心按无菜单过滤），路由保留可直达 `/#/finance/hello`，代码保留作新模块开发样板。
- 测试：新建 `tests/menu-groups.test.js`（3 例锁分组结构）+ `modules.test.js` hello 断言反向；全量前端 **58 文件 874 passed**、构建通过（stamp `5b0a5f83fd59`）。浏览器实测（真实服务重启后）：深链 `/finance/stamp-duty` 父级自动展开且激活项高亮、菜单树完整、`/finance/hello` 直达正常、0 console errors。
- **本轮教训（再次印证）**：长文件 Write/Edit 高频混入占位噪声（本轮自纠 6 处：`filingError'增值税填报'`、`每报表看板`、`falsiness`、`B长期`、`AIConfiguration` 等）——**写后必须立即重读该区段或跑聚焦测试**，噪声 grep（filingError|占位|manifests_源）可作快速自检。

## 2026-10-30 五部分工作轮（实测/政策/四填报/WorkBuddy/知识记忆）

- **Part A 实测**：八项现有功能（数据仓/费用/发票/个人记账/BIP/企业所得税/现金流 + MCP/技能/专家团/知识记忆）以脱敏数据隔离实测 **31/31 全绿**（API+浏览器 0 console errors），现金流引擎 0.81–0.85s；发票**跨批次重复识别**与 BIP **幂等导入**为正确设计。证据 `backend/.pytest_tmp/verify-20261030-features/`。
- **Part B 政策**：新 `finance_tax/config/policy_validity.py`——9 条政策统一登记公告号+生效/失效（POLICY_REGISTRY + policy_status 三态），税率项/优惠规则补 policy_basis/expires_date；所得税计算器接 `tax_year`（过期小微 400 中文拒绝，不静默套 5%）；政策管理页可见有效期摘要。**新增填报模块计税前必须查 policy_status。**
- **Part C 四填报模块**：`finance_stamp_duty`（印花税 13 税目+六税两费减半）、`finance_vat`（一般/小规模双模式，1% 与 30 万免征按期校验）、`finance_iit`（累计预扣法逐月模拟+国发13号标准）、`finance_dsf`（残保金 30 人免征+分档减收）；每模块 计算落库+openpyxl 底稿导出+中文 400；菜单 order 81-84。
- **Part D WorkBuddy 同步**：`agents/workbuddy_sync.py` + `/api/assistant/workbuddy/{scan,import,settings}` + CLI `scripts/import_workbuddy.py`；技能包导入**默认停用**、记忆走近重复去重、SOUL/IDENTITY/USER/BOOTSTRAP → 专家包草稿；MEMORY.md 的 **agent-hub 托管块剔除**；设置持久化 `backend/data/workbuddy_import.json`。真实导入 11 技能+6 记忆+4 身份草稿已执行，幂等复跑全 updated/skipped。
- **Part E 知识记忆**：memories `type` 四类（user/feedback/project/reference，检索加权 +0.5）、`/memories/stats`、`export.md`/`import.md`（zcode/WorkBuddy 兼容格式）、`agent_settings.memory_inject_max` 注入预算。**坑：0 是合法预算值，save/recall 两侧必须 `is None` 判定（falsy bug 已修并有测试锁定）。**
- **Part F 三绿**：后端 **1205 passed**、前端 **777 passed**、构建通过。真实数据终检 196 文件全部可归因（导入产物 + 2 处预期状态变化）；新表/新列待服务重启自动迁移。
- **工程约定补充**：① 新前端视图的请求必须过 `no-unhandled-request` 静态守卫（一律 try/catch）；② 新增填报类模块参照四模块样板（manifest requires finance_tax + calc 落库 + xlsx 导出）；③ pre-commit 扫描器（Mimosa）对 dev-CLI 的 argv→文件系统污点链敏感——CLI 参数入口要么经边界收敛要么删除；④ 编辑长文件易损坏，**写后必须 py_compile/vitest/build 验证**（本轮自纠 10+ 处占位符噪声）。

## 项目

Personal Workbench 个人财税自动化工作台（FastAPI + SQLAlchemy + SQLite / Vue 3 + Vite + Element Plus）。开发进度与验证史见 `progress.md`（本地文件，gitignore 管理，不入库）。

## 外部验证计划执行状态（2026-10-26）

依据 `20261022-外部验证计划与Checklist.md`：

- **第 0 章 通用前置 G1–G9**：✅ 完成。代码冻结基线 `6f64572`；`backend/data` 备份在 `backups/verify-20261022-pre/`（115 文件 + sha256，恢复演练通过）；验证期间真实数据零写入（每章结束字节级复核）。
- **第 1 章 真实付费 LLM**：✅ 完成（硅基 SiliconFlow，Key 经界面录入）。L1–L7、L10、L12–L14 通过；**L8/L9 完全体已补上**——硅基 TPM 不限流窗口未等到，改用 Step5 preview 的 KimiCode 参数适配推进（context 984,407 完整建立、output 1024、reasoning 四级 supported；429 自动退避重试 30s×2 上线）。GLM/Qwen 被硅基 TPM 429 截断的记录在案（外部约束）。记录的问题（错误中文化、30s 超时、400 守卫、停止按钮、429 重试）已全部修复并线上复测。
- **第 2 章 真实 MCP**：✅ 完成（M1–M9 全过）。真实 server 用本机 kimi-datasource 插件（`node plugins/managed/kimi-datasource/bin/kimi-datasource.mjs`）；只读判定以 annotations.readOnlyHint 为唯一依据。
- **第 3 章 用友 BIP**：✅ 11/11 完成（凭证缺口 2026-10-23 修复：检测签名「分录摘要/制单人→voucher、对方科目→aux」+ 多 sheet 以 sheet 名为账簿 + 续行 ffill；原稿 13001 行全导入）。脱敏数据在 `脱敏数据/`（生成器 `dev/desensitize_finance_data.py`，校验器 `dev/verify_desensitized.py`）。
- **第 4 章 外部财务引擎/管线**：✅ 五管线全部完成——费用分类（对拍一致）、发票（ZIP+冲红配对）、POS（四源对拍一致）、现金流、报表看板（快照+CSP+nosniff）；E4 超限 400 全过；**E9 ±20% 同数据复跑判定通过**（expense +4~12%、POS +18.8%、现金流 +0.9%、看板 +8.5%，均在噪声带内）。引擎路径支持**目录或单一文件**（`utils.resolve_engine_root`）。修复：TEMP 环境变量、上传文件名 mojibake、POS analyze --pos/--project（`utils.engine_subprocess_env` / `repair_upload_mojibake`）。
- **第 5 章 报表看板 W1–W9**：✅ 全过（多次 run 200 / iframe 真实渲染 27,531.6 万元 / log_tail 有界 500 字符 / failed 快照可见 / 目录改名 503 中文 / 并行双 run A200-B409 / 路径安全单测覆盖 / CSP+nosniff / 脱敏源 14 文件 sha256 零改动）。证据：`backend/.pytest_tmp/verify-20261022-exp/board-w1-w9-e9rerun.json`。
- **第 6 章 真人可用性 U1–U10**：🔶 四档证据——① API 级代行；② **browser-use 轮**（真实点击/文件对话框/确认框，发现并修复缺 Key 文案 P3）；③ **computer-use 真实桌面轮（2026-10-26）**：U8 双击备份 bat，产物完整性与源库一致；U1 启动 bat 后终端与 Tabbit 首页正常，停止后真实 `backend/data` 115 文件 sha256 全部不变。CU 轮修复停止脚本 P1：findstr 正则空格造成全监听行匹配并误选其他 PID，改无空格正则并有真实 findstr 回归；④ U5/U6/U9/U10 引用第 1/4 章实证。U10 ≥80% 无协助阈值**仍待真人复测**（computer-use/browser-use 均不能替代真人迷路/主观评分）。记录表：`backend/.pytest_tmp/verify-20261022-exp/usability-task-script.md`。
- **全功能深测（2026-10-26）**：17 区 Playwright 探针 **656/656** 全绿；后端 **909/909**、前端 **652/652**、生产构建通过。修正 dashboard-search 时间依赖预期；debug-center 首轮异常经截图确认为构建清空 dist 干扰，复跑 33/33。真实 `backend/data` 全程 115 文件零改动。
- 证据归档：`backend/.pytest_tmp/verify-20261022-{general,llm,mcp,bip,exp}/`（result.json + report.md + 日志）。

## 架构与算法文档（2026-10-23 新建）

- `算法实现逻辑.md`：四级路由 / MCP readOnlyHint 门控 / BIP 导入四机制 / 脱敏保勾稽随机 / 探测二分+429 重试 / 引擎路径解析 / TEMP 与乱码修复。
- `功能模块拓扑图.md`：总体拓扑 / 模块注册机制 / 11 模块清单 / 财务五管线数据流 / AI 栈 / 数据边界 / 前端拓扑。
- `spec.md` 第 12/13/15 节已追平 as-built；`API.md` 接口清单不变更时以代码为准。

## 验证环境约定（重要）

- 隔离服务模板见清单 0.1 节；**路径必须用 `D:/…` Windows 风格**——Git Bash 的 `$PWD`（`/d/…`）会被 SQLAlchemy 在 Windows 上误解析成 `D:\d\…`（2026-10-22 踩过，已清理）。
- 真实 Key 只经界面/环境变量输入，禁止写入文档、日志、提交、截图；证据目录内只放合成值与脱敏记录。
- 请求预算：首轮单主题 ≤50 次真实供应商请求（含 SDK 重试），达到即停。
- **Node 运行时 ≥ v24.22.1**（2026-10-21 官方安全更新：CVE-2026-111497 Brotli 解码器越界写 RCE 等共 10 项，22.x/24.x/26.x 低于修复版均受影响）。本机两处 Node 安装（DevEnv 运行时目录与 Program Files）都须 ≥ 该版本；`frontend/.nvmrc` 与 `frontend/package.json` 的 `engines` 为权威约束。PHP 本项目零使用（2026-10-23 排查确认：无 .php 文件、未安装、无容器），无需维护。

## 工程约定

- git 提交经 clean/smudge 脱敏过滤器（本机路径/公司名/昵称 → 占位符）；反向恢复钥匙 `dev/sanitize-map.local.json` 不入库，丢失无法自动还原（机制见 `dev/SANITIZE.md`）。
- 提交信息用中文 conventional commits（`fix(assistant): …` / `docs: …` / `test: …`）。
- 本机路径/端口等环境相关默认值统一走 `backend/.env`（字段定义见 `backend/app/config.py`，完整变量清单与说明见 `backend/.env.example`），新增此类默认值前必须先加配置字段。
- 测试：后端 `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q`；前端 `cd frontend && npm test` + `npm run build`。
- **脱敏数据管线**（外部验证数据准备）：生成 `python dev/desensitize_finance_data.py {bip|pos|cash}`，校验 `python dev/verify_desensitized.py`（需 `VERIFY_ATTACH_DIR` 指向会话附件目录）。产物在 `脱敏数据/`（gitignore）。规则：保勾稽随机数（凭证借=贷、明细账余额链、余额表配平）；泛化命名**避开 BIP 表头关键词**（账簿/科目/日期/凭证号/借方/贷方/期初/期末/本期/摘要），否则污染 `_locate_header_row`。
- **分发包制作（ZIP 脱敏分发）**：双击 `打包发布.bat`（或 `python dev/package_release.py`，`--skip-smoke` 可跳冒烟）。管线：预检（git 必须干净/node+npm/映射表/venv）→ `git archive HEAD` 导出脱敏树（**禁用 clone/bundle**：过滤器 2026-10-13 才引入，更早历史未脱敏）→ 剔内部文档（AGENTS.md、dated 报告、`20261012New/`；保留 README/使用说明/算法/拓扑/spec/API 等）→ `backend/data` 空骨架 → staging 内 `npm ci + build`（脱敏源码构建，`node_modules` 删掉不进包）→ 三重扫描（映射表 13 条真实值反查 + 密钥正则 + 结构红线，失败即中止）→ ZIP 到 `backups/releases/`（gitignore）→ 解压冒烟（仓库 venv 起 uvicorn，`/` 与 `/docs` 200、新库生成）。产物 2.9MB 左右、顶层单目录、**收件人只需 Python 3.12+**：`启动工作台.bat` 见 `frontend/dist/index.html` 存在且无 Node 时直接用预构建产物。needs-build 判定是**内容哈希 vs `dist/.build-stamp.json`**（postbuild 写入），不用 mtime——zip 解包不保留时间戳，mtime 语义会让收件人每次启动误重建。

## Task 4：在线模型同步适配器与同步端点（2026-10-26）

- 新模块 `backend/app/agents/model_sync.py`：`fetch_remote_models(provider, credentials)`（models 为 None = 无在线来源，调用方目录兜底；在线失败抛 `RemoteFetchError`，绝不返回空列表）与 `sync_provider_models(db, provider_id, include_disabled, override_manual)`；`config_store.sync_provider_models(...)` 为端点用包装。
- 适配器（payload→`RemoteModel` 纯映射，过滤策略集中在同步层）：`parse_openai_models / parse_opencode_models / parse_deepseek_models / parse_openrouter_models / parse_alibaba_models / parse_tencent_models / parse_anthropic_models / parse_volcengine_foundation_models / parse_volcengine_plan_models`；OpenCode 未知 api_family 标 `unknown` 并 skipped 说明，火山管控面 `volc_signature` 为官方 HMAC-SHA256（只读 POST + 按实际 body 计算 X-Content-Sha256）。
- 端点 `POST /api/assistant/providers/{id}/sync-models`：provider lock 并发 409、批内去重、单事务、一条非法记录或写入异常整批回滚（结构化 `ok=false`，不裸 500）、第二次同步全 unchanged、远端消失不删本地、manual/probe/note/temperature/enabled/active 全保留；响应 `{ok, partial, online, source, added, updated, unchanged, skipped, warnings, errors, synced_at, truncated, providers}`。同步只发目录/管控请求，绝不推理（唯一例外：火山官方管控面三个只读列表 Action 的只读 POST，body 仅 `{}` 或 PageNumber/PageSize 两个分页字段，发送前强制官方 host + Action 白名单 + 根路径）。
- 安全口径：列表地址发出前经 `config_store._check_http_url`；20s 超时、4MB 响应上限、不跟随重定向、429 仅按有界 Retry-After（≤2s）重试一次、OpenRouter 只跟进同源分页（相对路径先 urljoin，≤5 页）；Anthropic `has_more+after_id`、阿里 `page_no/page_size/total`、火山 `PageNumber/PageSize`+`Result.TotalCount` 有界分页，**到页数上限仍有后续页时 warning + `truncated=true`**（不把截断当完整成功）；**200+空列表或结构不可识别一律 RemoteFetchError**（不假成功），失败即目录兜底 + partial。
- 官方响应形状（fix round 1/2，双评审后）：火山管控面 `https://ark.cn-beijing.volcengineapi.com/` + `Action/Version=2024-01-01` 三个只读列表 Action（`ListFoundationModels`→`Result.Items[].Name/DisplayName`，请求体 `PageNumber/PageSize`、响应 `Result.TotalCount`；Coding/Agent Plan→`Result.Datas[].ModelID`，无明确分页字段按单页处理），只读 POST + 按实际 body 计算的 `X-Content-Sha256` + SignedHeaders `content-type;host;x-content-sha256;x-date`；阿里 `output.models`（ID 字段 `model`、展示名 `name`、`inference_metadata` 模态数组、`model_info` 三项、`features`/`capabilities` 字符串数组、`inference_offline_info` 下线信息）；DeepSeek `name` + `effort.supported_levels/default_level`；Anthropic `lifecycle` + `capabilities` **对象**（`image_input.supported`→视觉、`thinking.supported`→思考、`capabilities.effort.<level>.supported=true`→档位——effort 与 thinking 是兄弟节点、官方推荐顺序 high/low/medium/max/xhigh；官方无 default 字段，仅保守探测且缺失必须 None；无通用工具键→`supports_tools=-1`，不从 `server_tools` 推断）+ `max_input_tokens/max_tokens`；腾讯 `pre-offline/preoffline` 纳入下线过滤。
- 预设适配器改接：deepseek→`deepseek_models`、dashscope→`alibaba_models`、tokenhub→`tencent_models`、opencode(/go)→`opencode_models`；`SYNC_ADAPTERS` 白名单同步扩充。
- Task 3 评审遗留已处理：`model_catalog._catalog_key_for` 区分 `catalog_key is None`（回退 preset_key）与显式空串（无目录，`catalog_for` 返回 None）。
- 测试：`backend/tests/test_model_sync.py`（74 例，含 `test_sync_remote_failure_is_honest`）+ `test_swarm_model_probe_security.py` 新增同步端点非法 URL 用例；外部 HTTP 全走 mock/本地夹具，密钥全合成；火山签名单测用官方 canonical 字段独立重算的测试向量（含分页体）。

## Task 5：按模型能力生成思考参数、模型级实测与运行约束（2026-10-26）

- `llm.reasoning_request(cfg, effort) -> (Parameters kwargs, extra_body)`：档位不写死表。openai effort 风格档位进 `Parameters.reasoning_effort`，AgentScope 不认识的档位（`max`）经 `extra_body` 原样发送且**不与顶层重复**；toggle 风格→`thinking{type}`；budget 风格→`enable_thinking+thinking_budget`（`thinking_budget_for` 数值档，不伪装 effort）；anthropic 原生 `reasoning_effort`（含 max，内部转 `output_config.effort`）。`off/none` 受 `thinking_can_disable=False` 保护（强制思考不可 off，回退跟随模型）；未知/非法已存档档位与 Anthropic 无法表达的档位一律回退「跟随模型」（用声明的 `default_thinking_level`，无声明则不发参数），**不擅改 medium/xhigh**。老模型无元数据时按通用 effort 兼容行为。删除旧 `_apply_reasoning` 与 `EFFORT_*` 常量，旧 `test_b1_openai_max_effort_maps_to_xhigh` 已按新行为改写。
- `llm.provider_headers(cfg, session_id)` / `needs_session_headers` / `session_header_value`：仅预设声明 `session_header`/`user_agent` 的套餐（OpenCode Go：`x-opencode-session` + `personal-workbench/1.0`）生成默认请求头，经 `client_kwargs.default_headers` 透传；会话头取值对同一 session 恒定（sha256 派生）；**无会话上下文（session_id=None）时用稳定值——全局模型取进程稳定默认值，专家绑定模型在首个会话调用先发生时会复用该会话头值（稳定但不保证是默认值，不为此外新增缓存实例）**；密钥只走 SDK 鉴权，绝不进自定义头。`build_model(cfg, session_id=None)` 向后兼容；orchestrator `_get_spec_model/_get_model/_build_agent/_ensure_agent` 透传 session，**只有需要会话头的供应商按 (模型, 会话) 维度缓存**（LRU 上限 64，命中刷新新近度），其余沿用共享缓存；`invalidate()`/`session_delete()` 同步清理。
- 预设 v3：`provider_presets.json` 新增 `session_header`/`user_agent`（`_normalize_preset` 校验头名/UA 合法性）；cfg（`load()`/`model_cfg_by_id()`/`provider_test_cfg()`）新增 `preset_key`/`usage_scope`。
- `model_probe`：reasoning 探测档位取已同步/手动的 `thinking_levels`（缺省 none/low/medium/high，≤6 档），风格取 `capability_meta.reasoning_style`；**accepted ≠ supported**（接受参数但无思考输出不计支持）；anthropic 不再一律 skipped——新增 `_messages_once`（/v1/messages，effort→`output_config.effort`、budget→`thinking.budget_tokens`、off→`thinking{type:disabled}`），context/output 探测同样按 Messages 实测；`_chat_once` 增加 `extra`（思考风格请求体增量），429 退避逻辑抽为 `_post_with_retry` 共用。
- 工具/图片实测落库：`TestRequest` 新增可选 `model_id`（`/config/test` 按该模型执行）；`config_store.record_probe_verdict` 只写**明确结论**（supported→1、rejected→0，来源 `probe` 且字段加入 `manual_fields` 作本地覆盖；unknown 不改动任何值）；`test_tool_calling`/`test_multimodal(_anthropic)` 结果新增 `verdict` 三态（仅 `BadRequestError` 类型 / `status_code=400,422` / 明确的 `Error code: 400|422` 判 rejected，401/403/404/408/429/network/模型未调用=unknown）；`resolve_test_model_id` 定位回写行（显式 model_id > 供应商首个启用模型 > 激活模型；自定义配置不回写）。（rejected 口径与 manual 处理在 fix round 1 精确化，见下。）
- 运行约束：`orchestrator._job_scope_refusal`——`job-*` 会话（定时任务）选中 `usage_scope=plan_interactive_only` 套餐的生效模型（专家绑定或全局）时，chat/chat_stream 同口径返回中文拒绝、不发起推理（scheduler 记 error）；普通聊天会话不受影响。
- 测试：`test_bugfix_agent_store.py`（B1 重写为 10 例：档位原样/max extra_body/强制思考/非法回退/三风格/Anthropic/会话头）、`test_model_probe.py`（同步档位与风格、accepted≠supported、Anthropic Messages 三态、verdict 落库、工具/图片三态）、`test_assistant.py`（model_id 回写、400 守卫、job-* 拒绝、普通聊天不误伤、session 缓存维度、session 透传）；全量后端 1082 passed。

### Task 5 fix round 1（2026-10-26，双评审修复）

- **图片/工具实测状态码精确化**：`_http_multimodal_test` / `_http_anthropic_multimodal_test` 只有 **400/422** 判 `rejected`，401/403/404/408/429/5xx 与网络错误一律 `unknown`（修 401 Key 无效/429 限流被误写「不支持视觉」）；`_is_param_rejection` 按「SDK `BadRequestError` 类型 > `status_code` 属性（400/422）> 消息中明确的 `Error code: 400|422`」判定，消息偶含 400（端口号/请求 ID）不算。
- **probe 结论是本地覆盖**：`record_probe_verdict` 在 `sources[field]='probe'` 的同时把字段加入 `manual_fields`——默认目录同步不覆盖实测值（实测优先），`override_manual=True` 或 `reset-overrides` 可清除覆盖并按最近同步快照恢复（不引入新字段的最小兼容方案）。
- **Anthropic effort 探测边界**：`_ANTHROPIC_MIN_OUTPUT_TOKENS=2048`（effort/toggle 探测 max_tokens，避免 budget>max_tokens 假失败）；verdict 判定先看 max_tokens/output 超限类错误归 `error`，再看 effort 特征判 `unsupported`（修 thinking/budget 错误被误判成「不支持思考」）。
- **会话模型缓存 LRU**：`orchestrator._session_models` 改 `OrderedDict`，键 (模型 id|0, session_id)，上限 `_SESSION_MODEL_CACHE_MAX=64`（与 Agent 缓存同口径），`_session_cache_put` 淘汰最久未用会话即释放实例（含 SDK 连接池）；`invalidate`/`session_delete` 完整清理；共享模型缓存（无会话头供应商）不改为每会话。
- **声明集门控（A）**：`_resolve_thinking_level` 增加 `_level_declared`——模型已声明 `thinking_levels` 非空时，声明集之外的 canonical 档位（含 `max`）视为不支持，回退声明默认档/跟随模型，不原样发给供应商；未声明集保持兼容。cfg 新增 `thinking_levels`（`load()`/`model_cfg_by_id()`/DEFAULTS）。
- **anthropic toggle（B）**：映射 `thinking_enable` + `thinking_mode`（enabled/disabled），不再误走 effort。
- **预路由守卫（C）**：`_preroute_scope_refusal` 只在**自动路由**（未明确指定专家）时预检全局模型（路由本身可能调用全局模型仲裁）；明确指定专家交由路由后 spec 复核，避免「全局交互式套餐 + 专家绑定按量模型」的合法定时任务被误伤。
- **budget 映射（D）**：保留 1024~65536 保守约定值，文档注明实际上限以供应商文档为准，不扩大任务范围。
- 测试：fix round 1 新增 14 例（400/422 口径×2、工具精确拒绝、probe→sync 交互×2、Anthropic max_tokens、budget 错误归类、声明集门控、Step max 回退、anthropic toggle、LRU×3、显式专家守卫）；三文件 137 passed、交互三文件 124 passed。

## Task 6：设置页同步/能力展示/动态思考下拉 + 6A/6B 契约补齐（2026-10-26）

- **6A（仅前端）**：新建 `frontend/src/utils/modelCapabilities.js`（`capabilityBadges/reasoningOptions/syncSummary/isMultimodal` 等纯函数单一实现，三态=支持/不支持/未知，未知不显示红叉）；`SettingsDrawer.vue` 每供应商「同步模型」按钮（`syncingProviderId` 忙态锁 + `aria-live="polite"` 摘要，partial/truncated/ok=false 分级提示，失败保留原列表不假成功）、模型搜索+首批 20 条/展开、首屏外模型卡 `content-visibility:auto`+`contain-intrinsic-size`、preset_key/models_url/火山 AK/SK 密码框保存、每模型工具/图片实测传 `model_id`（verdict unknown 显示「未确认」）、`resetModelOverrides` 恢复同步值；`ChatPanel.vue` 思考下拉由激活模型（AssistantView 传 `teamConfig`）动态生成，提示按各 Agent 生效模型自适应。
- **6B（后端契约 + 历史值兼容）**：
  - `config_store.save_globals` 的 `reasoning_effort` 白名单扩为 `''/off/none/minimal/low/medium/high/xhigh/max`（`_REASONING_EFFORT_VALUES`，与 `llm._THINKING_LEVELS` 同口径；config_store 不 import llm 避免环依赖）；`off` 为旧版「关闭思考」兼容保留，语义同 `none`；`GlobalsPayload` 描述同步。
  - `models_flat()` 纯增量补 `supports_thinking/thinking_levels/reasoning_style`（与 `list_providers` 同口径），其余字段与停用过滤不变。
  - 前端 `normalizeEffort`（ChatPanel，挂载时与 watch 均执行）：旧 `off` → 模型允许关闭（`canClose`：`thinking_can_disable !== false` 且非明确不支持思考，与后端 `_resolve_thinking_level` 的 can_disable 口径一致并多一条保守条件）时迁移为 canonical `none` 并保存，只有强制思考才回「跟随模型」；`none` 与 off 同口径**不受声明集门控**（因此 `reasoningOptions` 在可关闭模型上即使声明集无 none 也提供「关闭」项，避免迁移值无可选项）；其它 canonical 档位仍受声明集门控，非法值回「跟随模型」。
  - **supports_thinking 真实契约（fix round 1）**：`load()`/`model_cfg_by_id()`/`config.get` 的 config 均暴露 `supports_thinking`（与 providers 视图同口径），ChatPanel 的「已知不支持 → 禁用思考下拉」在生产数据上可达；`llm._resolve_thinking_level` 对 `supports_thinking == 0` 兜底——任何档位/风格/协议都不发思考参数（未知 -1/缺省不受阻）。
- 测试：`tests/model-sync.test.js`（新建 54 例，终审修复后 60 例）、`model-probe.test.js`、`assistant.test.js`（ChatPanel 动态下拉与 off 迁移回归）、`swarm-settings-knowledge.test.js`（锁重命名）；后端 `test_assistant_config.py` 新增白名单逐值保存与 models_flat 序列化/默认值用例。

## Task 7：隔离端到端验收、文档与全量回归（2026-10-26）

- **TDD 小修复（thinking_can_disable 显式 null 恢复「未知」）**：`routers/assistant.save_provider_model` 原先 `payload.model_dump(exclude_none=True)` 丢掉前端显式发送的 `thinking_can_disable: null`（「思考可关闭」选回未知），config_store 的 `if "thinking_can_disable" in data` 恢复分支永不触发、旧值残留。修复：exclude_none 后**仅对显式出现在 `payload.model_fields_set` 且值为 None 的该字段**补回 data（其余 None 继续排除），**未扩大为全局 exclude_unset**。测试 `test_assistant_config.py::test_thinking_can_disable_explicit_null_resets_to_unknown`（真实路由往返：true→false→null→GET 为 None；未提交该字段时旧值保留）。
- **文档追平 as-built**：`算法实现逻辑.md` §5 重写（三态探测 + anthropic `/v1/messages` + 工具/图片三态口径），新增 §5b 在线模型同步（partial/truncated/在线失败兜底/安全口径）与 §5c 思考参数生成 + `job-*` 套餐拒绝 + OpenCode Go 会话头与 (模型,会话) LRU；`spec.md` §6 补同步模型/能力三态与来源/动态思考档位/off 兼容，15.1 补显式 null 恢复条目；`使用说明.md` §4.2 补同步模型与模型能力编辑操作、§4.3 追平探测口径、§4.6 动态思考档位、§6.4 套餐边界；`API.md` 补 models 端点 null 契约与 presets v3 字段（session_header/usage_scope）。旧「max 固定 xhigh / anthropic 一律不可探测 / 只发 GET / 仅 openai 协议」等过期描述已清。
- **隔离 E2E**（`.superpowers/sdd/doctor-mid-nite-black-bolt-maria-hill/`）：临时 SQLite + 临时数据目录 + 本地 mock OpenRouter 富字段形状目录服务 + 隔离 FastAPI（8001，托管 `frontend/dist`），内置浏览器真实走通 新增供应商→保存→同步模型（added=2/能力三态徽章）→激活→动态思考档位（6 项含 max）；专家路由预览「服务器中毒了怎么处理」命中 security（关键词/高置信/零推理）。证据（文字/截图 receipt）在 scratch 目录，不在仓库新增报告脚手架；Key 全合成值。
- **公开目录只读 smoke**（真实结果）：OpenRouter 在线同步 **466 条全成功**（online=true/partial=false/errors=[]）；OpenCode 在线 **401**（合成 Key 无效，外部约束）→ 按设计能力目录兜底（online=false/partial=true/中文错误），**未伪造成功**。顺带核实：真实 OpenRouter 目录 466 条全带厂商段、非对话类 0 条——E2E 发现的「`_is_non_chat_id` 前缀过滤对厂商段 ID 不命中」当前无实际影响，作为潜在缺口记录在案（未超出授权范围修复）。
- **数据边界**：迁移在真实 `backend/data` 的**复制库**上演练（不启动指向真实库的服务）：首次仅 `workbench.db` 字节变化、**二次零变更（幂等）**、行数稳定；按既有流程 `scripts/backup_db.py` 热备份；终检真实目录与 `task-7-data-hashes-before.json`（115 文件）**零 mismatch、零新增**。隔离服务已停、浏览器标签已关。
- **全量回归**（2026-10-26 Task 7 轮真实结果）：后端 `pytest tests -q` **1104 passed**（328s）、前端 `npm test` **47 文件 745 passed**、`npm run build` **通过**（12.88s）——三绿。证据与每步细节见 `.superpowers/sdd/doctor-mid-nite-black-bolt-maria-hill/task-7-report.md`。

### Task 7 收尾（独立评审后，2026-10-26）

- **预设计数追平**：`provider_presets.json` 实际 **33 家**，`使用说明.md`（§1.5/§4.8/§4.2 表格补齐 15 家并加「套餐范围」列）、`spec.md` §6、`API.md`（presets 两处）、`README.md`（两处）、`功能模块拓扑图.md`、`Agent团队设计.md` §12（表格补 15 家 + 历史注记）、`注意事项.md` §13 全部由 18 更新为 33；历史存档（`20261012New/`、`progress.md`、`项目全面分析报告_2026-10-20.md`）未改。
- **`_is_non_chat_id` 过滤盲区修复（TDD）**：原按完整 ID `startswith` 漏过 OpenRouter 厂商段 ID（`vendor/text-embedding-*`）。先写失败测试（`test_is_non_chat_id_matches_vendor_segment` 厂商段嵌入/语音/图像生成应跳过、正常对话 ID 不误伤；`test_sync_skips_vendor_segment_non_chat_models` 同步层 skipped+说明），再最小修复为「完整 ID **或**最后一个 `/` 后末段命中非对话前缀均跳过」，不依赖上游 `output_modalities` 兜底。focused `tests/test_model_sync.py` **76 passed**（原 74 + 新 2）。
- **回归（收尾轮）**：全量后端 `pytest tests -q` **1106 passed**（325.87s，900s 窗口内，日志 `task-7-full-backend.log`）；前端 `npm test` **47 文件 745 passed**（归档 `task-7-full-frontend.log`）；`npm run build` **通过**（13.18s，归档 `task-7-full-build.log`）。
- **数据边界终检**：真实 `backend/data` 与 `task-7-data-hashes-before.json`（115 文件）复核仍 **零 mismatch、零新增**。收尾详情与 RED/GREEN 证据追加在 `task-7-report.md`。

## Task 8：终审集中修复（2026-10-26，final-review-findings 全清单）

- **Important 1 思考探测被同步覆盖**：`model_probe.probe_reasoning` 的 definitive 结论（supported/accepted/unsupported）回写 `supports_thinking`/`thinking_levels` 时，经新增 `_mark_probe_override` 记 `sources=probe` + `manual_fields`（与 `config_store.record_probe_verdict` 同口径）——默认目录/在线同步不再把实测「不支持」翻回目录「支持」；`override_manual=True` 或 reset-overrides 可清除恢复；全部请求失败不写结论也不记覆盖。测试：probe→默认同步保留→reset 恢复、override 清除、全失败不标记共 4 例（`test_model_probe.py`）。
- **Important 2 畸形模型记录裸 500**：`model_sync._require_entries` 与 `_alibaba_entries` 统一校验记录数组元素必须是 JSON 对象（字符串/null/数组/数字 → `SyncRecordError` 整批拒绝，新增 `_require_records` 守卫）；fetch 阶段加 `except Exception` 结构化兜底（只记异常类型，防未来适配器缺陷裸 500）。测试：string/null/list/数字 × 10 解析器参数化、端点结构化失败、适配器未预期缺陷兜底共 6 例（`test_model_sync.py`）。
- **Important 3 override_manual UI 缺失**：`SettingsDrawer.vue` 供应商同步操作区新增「覆盖手动/实测」勾选（`syncOverrideManual` 每供应商独立、默认 false；开启时行内警告 + 同步前 `ElMessageBox.confirm` 明确警告会覆盖手动修改与实测能力，取消不发请求）；`syncModels` 传 `override_manual`；`include_disabled` 不暴露。测试：默认 false、勾选确认流程、取消不发请求、按供应商隔离、模板接线共 5 例（`model-sync.test.js`，54→60 例）。
- **低风险收尾**：`orchestrator._job_scope_refusal` 前缀判断改遍历 `_INTERNAL_SESSION_PREFIXES`（不写死 `[0]`）；`routers/model_probe.reload_presets` 后调 `orchestrator.invalidate()`（session_header/usage_scope 等运行配置立即生效）；`model_catalog.py` remote_status 注释改真实合并行为；`model_capabilities.json` 两条 groq（qwen/qwen3.8-27b、qwen/qwen3.6-27b）删除非 canonical 档位 `default`，目录 note 改「只发目录/管控请求，绝不推理」，新增全目录 canonical 校验测试；`使用说明.md` §4.2「9 家预设」改「每家供应商卡片都有同步按钮 + 目录兜底标注 + override_manual 勾选真实操作说明」；`spec.md`/`API.md`/`算法实现逻辑.md` 同步 as-built。
- **回归（终审轮真实结果）**：全量后端 `pytest tests -q` **1118 passed**（331.30s，900s 窗口内，日志 `final-full-backend.log`）；前端 `npm test` **47 文件 750 passed**（日志 `final-full-frontend.log`）；`npm run build` **通过**（11.69s）——三绿。focused：后端 5 文件 259 passed、前端 3 文件 138 passed（model-sync 60 / model-probe 48 / assistant 30）。
- **数据边界**：真实 `backend/data` 与 `task-7-data-hashes-before.json`（115 文件）复核 **零 mismatch、零新增**（脚本 `final-data-check.py`）。全程零提交、零真实推理、零真实密钥读取。报告：`.superpowers/sdd/doctor-mid-nite-black-bolt-maria-hill/final-fix-report.md`。

## Task 9：中断续聊连续性与角色保持修复（2026-10-27）

- **根因**：原 `_resolve_route` 只按单句路由，“继续/接着说”无关键词且 LLM 未命中时回退默认 `realestate`；同时 `chat_stream` 只在正常完成后落库，客户端停止/断开时已流式产出的正文与思考丢失，下一轮注入历史缺片。
- **修复**：`orchestrator.py` 新增 `_is_continuation` / `_session_current_agent` / `_interrupted_stream_message`；`_resolve_route` 增加 `session_id` 与“续聊”级，优先级为指定 > 高置信关键词 > 续聊 > LLM 仲裁 > 关键词回退 > 默认；内部 `job-*` / `memory-extract` 会话不参与续聊保持。`chat_stream` 累积 delta/thinking，在 `CancelledError` / `GeneratorExit` 时把半轮内容补存为 assistant，正文为空写「（已停止生成）」。
- **测试**：`test_bugfix_agent_core.py` 新增 5 例（续聊保持、显式/关键词覆盖、非续聊仍走 LLM、job 会话不粘、中断半轮落库）；RED 先失败后 GREEN。
- **回归**：全量后端 **1146 passed**（357.54s）；前端 **48 文件 759 passed**；`npm run build` **通过**（10.27s）。
- **文档**：`算法实现逻辑.md` §1、`spec.md` 架构行、`使用说明.md` §4.1 已追平“续聊路由 + 中断内容持久化”；问题报告见 `问题结论-修复方式-对现有代码和使用的影响.md`。
- **数据边界**：本次未启动指向真实 `backend/data` 的服务；全量测试走 conftest 临时 SQLite。旧基线到本次的差异经时间戳核对均早于 Task 9 开始时间，属于用户既有数据变化，非本轮写入。

## 开机自启动配置（2026-10-29，用户每天使用）

- 新增 `开机自启动.vbs`（仓库根）：与 `启动工作台.bat` 同一套逻辑（venv 检查→前端 needs-build 检查→uvicorn 127.0.0.1:8000→端口就绪开浏览器），区别是**无交互、后端窗口最小化**、服务已在运行则只开浏览器。
- 已注册到用户「启动」文件夹：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\个人工作台.lnk`（TargetPath=wscript.exe，指向仓库内 vbs，单一来源）。取消自启删该 lnk 即可。
- 实测（2026-10-29）：vbs 全流程跑通——8000 监听、首页/API 200、`停止服务.bat` 正确定位 PID 并校验 uvicorn 命令行（choice 的 stdin 重定向不工作是 Windows 已知特性，人工双击正常；自动化测试按同逻辑 taskkill 验证停止）；启动+停止全程真实 `backend/data` 117 文件 sha256 零改动。
- 坑位记录：**VBScript 文件必须 CRLF + ANSI（GBK）编码**——Write 工具产出的 UTF-8/LF 文件会被 cscript 报「缺少语句」编译错误；已用 PowerShell 转码修复。测试时勿用 `timeout` 包 cscript（会连带杀死异步启动的 uvicorn 子进程，造成"服务没起来"的误判）。
- `使用说明.md` §1.2 已补「开机自启动」行与说明。
- 同日修复「启动脚本误报后端未能正常启动」：根因是 uvicorn 先跑完 lifespan 才绑定端口，而 bat 只等约 12 秒（10 轮 ping）就判失败——冷启动/高负载时端口绑定晚于探测窗口，服务实际正常启动（实测热启动仅 2.18s）。`启动工作台.bat` 增加「已在运行则只开浏览器、不重复起实例」前置检查、等待放宽到 60 秒（带进度点）、超时文案改为「可能仍在启动」不再断言失败；`开机自启动.vbs` 等待 30→60 秒。验证：真实服务下跑 bat 前后进程一致不重复起实例；隔离 dummy 慢启动 20s 能等到成功、60s 超时文案正确；两文件保持 GBK+CRLF，`使用说明.md` §1.2 同步。
- 坑位补充：编辑/生成 bat 时 MSYS 工具（sed 等）按文本模式读文件会剥掉 CR，LF 的 bat 在 cmd 解析括号块时会错乱（报「xxx 不是内部或外部命令」）——落盘前必须统一回 CRLF；Bash 工具命令行里写 `>nul` 会被改写，重定向内容尽量从文件读取而非内联。
- 同日清理重复自启动（用户选定方案 A）：全量检查启动文件夹/注册表 Run/计划任务后发现工作台被注册**两处**——启动文件夹 `个人工作台.lnk`（wscript→vbs，静默）与计划任务 `\PersonalWorkbench`（Logon 触发 `启动工作台.bat`，交互式），开机会双跑竞态 8000、双开浏览器并留 pause 挂窗。已删除计划任务（`Unregister-ScheduledTask -TaskName PersonalWorkbench`，删除后校验 0 匹配），保留启动文件夹 vbs 为**单一来源**；注册表 Run 无工作台项；`\微信报表监控`、`\TSP-dev-3020` 为不同服务/端口，非冲突。

## 全界面生产级人工模拟测试轮（2026-10-29，报告见 `2026-10-29_0355_全界面生产级人工模拟测试与修复.md`）

- 22 区全过（Playwright 真实浏览器 + 生产构建 + 隔离服务；上传只读自 `2022.5.24起财务工作存档`）。修复 1 项 P2：POS 对账「引擎要求 --project」时前端无入口（后端参数存在但 UI 不传、503 detail 不含原因）——后端把「需指定项目名+从 stderr 解析的可用项目名」并入 error/detail，前端弹窗补填后 `?project=` 重试；前后端各 1 处最小改动 + 前端 4 例/后端 1 例新测试。回归：后端 1147 / 前端 763 / 构建三绿；真实数据与存档零写入。

## 报表看板管线改走计划任务通道（2026-10-29）

- **根因（进程树级写拦截，排查结论）**：本机终端安全软件（文件系统过滤驱动 `QQSysMonX64`（QQ管家实时防护）/ `FileCrypt`（透明加密））按**进程树**拦截写入——凡从 workbench venv python 派生的进程一律禁止写 `D:\AssetLib` 区域。实测矩阵：bash→系统 python 写 OK；venv python→系统 python**子进程**同样 `PermissionError`(errno 13)（与 env/TEMP 无关）——即拦截按祖先进程判定，换解释器无效。症状：报表看板点「运行管线刷新」必失败于 m01 写 `data\baseline_prev.json`（10-26 起复发；10-12/10-13 正常 = 策略后生效），而开机计划任务「微信报表监控」（系统 python、任务计划服务进程树）一直成功；只影响跨项目写的报表看板，写在 `personal-workbench` 内的其它财务模块不受影响。
- **修复**：`backend/app/modules/finance_board/service.py` 的 `run_pipeline` 改为**优先计划任务通道**——`schtasks /run /tn 微信报表监控` 触发，轮询 `微信报表监控/logs/last_run.json`（`started/finished/ok/steps`）判完成（`TASK_CHANNEL_TIMEOUT=600s`，轮询间隔 2s），四步 exit 全 0 才归档快照；已在运行 → `PipelineBusyError`(409)，触发失败/超时/步骤失败 → `PipelineUnavailableError`(503) 并落 failed 快照；**无此计划任务的机器（换机）自动回退**原直接子进程执行（`_run_steps_directly`）。前端 `BoardView.vue` 的 run 调用覆盖全局 axios 10s 超时（管线约 80-100s）为 300s，`tests/finance-board.test.js` 补超时断言。
- **验证（真实路径）**：服务层 `run_pipeline()` 80.1s ok；重启后端后 `POST /api/finance/board/run` 78.1s → `{"status":"ok"…}` 快照归档（stamp 20261029_140713）；focused 后端 `test_module_finance_board.py` 7 passed、前端 `finance-board.test.js` 11 passed、前端全量 48 文件 764 passed + 构建通过；**后端全量 1150 passed（333.60s）**。
- **全量回归抓到的自身缺陷（已修）**：B6 测试（`test_bugfix_debug_finance_misc.py`）monkeypatch 的是 `_run_step`（直接执行接缝），新通道不调它 → 补丁失效后测试触发**真实计划任务**（80s+、写真实数据、整套拖慢 14 倍并超时）。修法：`board_env` fixture 固定 `_scheduled_task_available→False` 走直接分支 + 新增 2 个测试覆盖任务通道成功/失败分支；复跑 22 passed 且确认不触真管线。**教训：新增执行通道时必须同步改测试的 patch 接缝，并防测试误触真实外部任务。**
- **走过的不成功弯路（勿重复）**：① `_run_step` 改系统 python——无效（拦进程树不拦镜像），已回滚；② pipeline 项目 `rpt_common.save_json` 原子写+重试——与本病无关（驱动层拦截重试也过不去），已回滚；③ 用 `*uvicorn app.main:app*` 筛进程停服务时**误杀了自己**（命令行文本含同样字符串），停服务必须按监听 PID 精确 taskkill 或排除自身树。
- **文档**：`使用说明.md` §7.7 补计划任务通道说明与「触发计划任务失败」的恢复办法（`服务-安装.bat`）。
- **另一条待办线索（未闭环）**：本机还有第三方软件每约 30 分钟在 `%TEMP%\ot_sweep_<哈希>\sweep.vbs` 跑 VBScript 并偶发「系统找不到指定的文件」弹窗（Windows VBScript 弃用告警同频出现）；与工作台无关，已挂只读监视器抓进程链，抓到后再定位处理。

## 全量缺陷诊断 + 五个经营/职能模块（2026-10-30）

- **诊断产物 `BUGfix.md`**：8 个只读审计域并行精读 + 关键结论人工复核，覆盖 `backend/app` 161 文件 / `frontend/src` 97 文件 / 129 测试文件 / dev 工具链 / 6 个 bat-vbs。共登记 **215 项**（P0 5 / P1 16 / P2 75 / P3 119），每条带 `文件:行` 证据、影响与最小修复建议；第 16 章澄清「像缺陷但属设计取舍」的 9 项，第 17 章给 6 个修复批次与验收口径。**已知 5 个 P0 全部落在既有测试盲区**（其中 FIL-01 是测试反向锁定错值）。
- **本轮已修（详见 `BUGfix.md` §0.5）**：TAX-01 落库税率改引擎口径（删 5%/10% 自造阶梯 + 接政策年度）、TAX-02 小微判定接 criteria（300 万/300 人/5000 万/限制行业）、TAX-03 第 5 行重复相加、TAX-06 进项转出符号、TAX-15/FE-01 企业档案四字段落库、FIL-01 残保金 <1% 改按 90% 缴纳（同步改 2 条固化错值的测试）、FIL-02 个税累计预扣改累计数相减、BIP-01 凭证键加期间、TL-01/TL-02 分发包剔除 `dev/`+两个 `tests/` 并按 GBK 回退扫描 + 真实名称反查（`dev/pii_terms.py` 从 `dev/pii-terms.local.txt`（gitignore）/映射表/环境变量加载，真实值不入库）。**新增测试 `backend/tests/test_bugfix_p0_20261030.py`（12 例）+ BIP 多月同号回归 1 例。**
- **模块命名空间扩展**：`app/modules/spec.py` 允许 `/api/business/` 前缀（经营/职能模块），`/api/other/` 仍拒绝；`_template` 注释同步。**新增业务模块一律用 `/api/business/<domain>`，不要再塞进 `/api/finance/`。**
- **五个新模块（后端 `app/modules/business_*` + 前端 `src/modules/business-*`，各自 manifest 自动挂载）**：
  | 模块 | 前缀 | 表 | 菜单 | 核心能力 |
  |---|---|---|---|---|
  | `business_leasing` 招商管理 | `/api/business/leasing` | `mod_leasing_unit/lead/followup` | 经营 71 | 铺位台账/空置率、线索漏斗、跟进记录、到期跟进、铺位/线索导出 |
  | `business_operations` 营运管理 | `/api/business/operations` | `mod_ops_contract/bill/work_order` | 经营 72 | 合同台账与到期、按期间生成账单、登记收款（累计改状态/超收拒绝）、收缴率看板、报修投诉工单 |
  | `business_planning` 企划管理 | `/api/business/planning` | `mod_plan_campaign/vendor` | 经营 73 | 活动台账、预算明细（预算/实际/超支）、年度日历、ROI 复盘、供应商台账 |
  | `business_legal` 法务管理 | `/api/business/legal` | `mod_legal_contract_review/case/seal` | 职能 81 | 合同审查（状态/风险/到期）、案件阶段与开庭提醒、用印台账、风险看板 |
  | `business_hr` 行政人事 | `/api/business/hr` | `mod_hr_employee/attendance/payroll` | 职能 82 | 花名册与试用/合同到期、月度考勤、薪资核算（累计预扣个税/社保公积金参数可调）、薪资表导出 |
- **每个模块的工程约定**：金额 `Decimal(ROUND_HALF_UP)` 两位；日期 `YYYY-MM-DD`、期间 `YYYY-MM` 严格校验；状态用 `Literal` + 服务层白名单双保险；删除有级联（合同→账单、线索→跟进、员工→考勤/薪资）；导出 xlsx 对 `= + - @` 前缀加 `'` 防公式注入；每模块 1 个只读 Agent 工具（`leasing_summary`/`ops_summary`/`planning_summary`/`legal_summary`/`hr_summary`），并**预置到对口内置专家**（`leasing`→招商专员、`operations`→运营专员、`planning`→企划策划、`legal`/`hr`→商业地产管家），启动时幂等并入专家白名单；测试全部用临时 SQLite + 合成数据，不写真实 `backend/data`。
- **五模块后端测试 28 例全绿**（leasing 4 / operations 9 / planning 5 / legal 5 / hr 5），覆盖 CRUD 往返、重复 409、状态枚举 422、超收 400、账单重复生成 409、薪资累计预扣跨月、导出 xlsx 魔数；前端新增 `tests/business-modules.test.js`（3 例，路由/菜单/唯一性）。
- **本轮回归**：后端全量 **1281 passed（594.72s）**；收尾改动（AGT-01 一行 + 五模块专家工具预置）另跑聚焦 **94 passed**；前端 **51 文件 820 项**全绿；`npm run build` 通过（9.76s，五个新视图已打进独立 chunk）。后端全量日志 `backend/.pytest_tmp/full-20261030-bugfix.log`。
- **文档**：新增 `新增业务模块需求与设计.md`（岗位痛点→需求→数据模型→API→页面→财务联动→非目标→验收）；`BUGfix.md` 为诊断台账。`使用说明.md` 补「经营/职能菜单」操作入口，`README.md`/`功能模块拓扑图.md` 的模块数需在下一轮文档追平时同步（该轮时 16 个后端模块 + 5 个新业务模块；2026-10-31 已追平为 22 个模块 / 43 张模块表）。

### 第二轮：剩余 P1 + 真实申报数据对拍（2026-10-30 下午）

- **真实数据源**：`D:\2022.5.24起财务工作存档\BaiduSyncdisk\11.税务申报附件`（只读）。仓库只保留数字与口径，不含公司名/税号/人名；对拍脚本不落盘真实文件。
- **对拍结论（全部通过）**：
  - 印花税 2026Q3：6 税目逐项一致，合计 **7,543.49**；顺带修复「买卖合同」不带括号无法命中税目表（`get_rate` 去括号归一）。
  - 增值税 2026-09：销项 224,842.61 / 应纳 197,363.24，本系统差 **0.04 元**（申报表按发票逐张取整 vs 按税目汇总取整，测试按 ±0.10 容差锁定）；附加税 13,815.43 / 5,920.90 / 3,947.26 完全一致。
  - 企业所得税 2025 A106000：2019 年亏损 415.06 万距 2025 已 6 年、申报表未使用；本系统修复后同样标记过期，2025 用 2020 年 101.15 万、2026 用 2021 年 257.95 万，结转明细一致。
  - 残保金 2026 测算表：23 人 / 2,631,736.68 / 0 残疾 / 社平 87,156 → 差额 0.345→0.35、优惠前 40,048.17、30 人以下免征，逐项一致。
- **第二轮修复（详见 `BUGfix.md` §0.6）**：FIL-03 社平 2 倍封顶、TAX-07 亏损 5/10 年期限、TAX-08 小微按弥补后所得、VAT 征收率白名单/月季门槛/留抵/免征政策、四填报分页与期间校验；POS/现金流执行互斥 + 2h 陈旧 running 自动失败；看板改读 `run.lock` 判运行中；技能包原子安装；启动迁移明文 Key；测试请求禁止跟随重定向；`ALLOW_NON_LOOPBACK` 非回环绑定守卫；备份新增整包 ZIP + `restore_backup.py`；日程/任务显式 null → 422；发票冲红金额不可解析不误配 + 批次删除接口；`requirements.txt` 补 PyYAML/python-multipart；发票上传 120s 超时。
- **备份/恢复新口径**：`备份数据.bat` 仍调用 `backend/scripts/backup_db.py`，现在同时产出 `workbench_*.db` 与 `workbench_data_*.zip`（数据库 + uploads/finance/skills/expert-packs/debug + 根 JSON，各留 30 份）。恢复用 `python backend/scripts/restore_backup.py <zip> [--force]`，覆盖前自动留 `.before_restore_*`，并拒绝 zip-slip。
- **新增测试**：`test_tax_real_case_regression.py`（7 例真实数字）、`test_bugfix_p1_20261030.py`（5 例：null 422、冲红、非回环、备份恢复）。**第二轮全量回归**：1290 passed / 5 failed（607.96s）；5 个失败均为本轮改动的测试接缝/文案断言（日程 null 中文文案、图片探测 patch 点），修复后复跑相关套件 87 + 41 全绿；前端 51 文件 820 passed + 构建通过。
- **仍未做**：原报告 P2/P3（前端响应式/键盘可达、CORE-13～17 调度器细节、TAX-10～14 等）按 `BUGfix.md` 第 17 章排期；第二轮列出的 AGT-02/FIL-08/09/FE-03/04/06/10/TL-04/TL-11/12/13 已在第三轮关闭。
- **第三轮（清空剩余清单）**：AGT-02 同会话运行锁（chat 409 / SSE error，删除会话清锁）；FIL-08 个税按 `employee_id` 分组、FIL-09 任职起始月与缺月 warning（减除费用按任职月份数，不再按提交行数）；TL-04 同步脚本 `seal/unseal`；FE-03 七处下载 URL 统一 `api.defaults.baseURL`；FE-04 列映射标签修复；FE-06 四填报删除确认；FE-10 提交锁前移；TL-11 conftest 对 client 用例统一 drop/create（含 7 个自覆写文件）；TL-12 uploads/skills/expert-packs 重定向临时目录；TL-13 `WORKBENCH_ENV_FILE=""` 禁用真实 .env；TL-01 新增 `test_release_scan.py`（剔除 dev/tests、GBK bat 扫描、PII 硬失败）。**测试**：新增 AGT-02 1 例、个税 3 例、打包扫描 3 例；**后端全量 1302 passed（723.07s）**、前端 51 文件 820 passed、构建通过（日志 `backend/.pytest_tmp/full-20261030-final.log`）。


## QwenPaw 15 角色证据评审与后续开发（2026-10-31）

- **输入与本地文件**：根目录 `QwenPaw智能体提示词.json`（15 agents / 62 declared / 58 existing / 4 missing）作为角色基准；JSON 与汇总 md 保留原样并已根锚定加入 `.gitignore`（local-only），防止 PROFILE/MEMORY 词表外信息进提交/分发包。新评审 `20261031_QwenPaw专家团评审_业务需求覆盖度综合判断.md` 与计划 `20261031_QwenPaw后续开发计划.md` 已落盘，历史 `20261030_...` 不改。
- **B0 真实回归**：`business_operations/service.py` 曾在评审后被编辑污染（`text?`、`ops_summary` 截断、`ops_bills_export` 丢失）；已恢复并通过 16 项聚焦。教训继续有效：长文件写后必须 `py_compile`；registry 对导入失败是静默跳过，可用 `test_manifest_loaded` 兜底。
- **本轮闭环**：`utils_money.money` + `finance_iit_common` 统一；招商 `lead.unit_id` + 原子占用/换铺/删除守卫 + 租决五字段/双口径；营运账龄四桶/出账守卫/SLA/设备关联/followup；企划 Decimal ROI/档期/跨月；法务迁移矩阵/逾期/显式 null；人事缺考勤 warning/任职月；`business_facility` 设备+合规；`business_gm` 六域只读聚合与指标字典。
- **模块计数**：当前 **22 个模块、43 张模块表**（宿主 12 张，合计 SQLite 56 含遗留表）。财务菜单 13 + 模块示例；经营/职能菜单 7（GM 70、招商 71、营运 72、企划 73、工程 74、法务 81、人事 82）。
- **验证（最终）**：后端全量 **1427 passed**（738.35s；首轮 3 个失败均为新探针未满足 19 区隔离契约，修复后清零）；前端全量 **57 文件 / 871 passed**；`npm run build` 通过（stamp `b17369e5ff92`）；两个隔离浏览器探针分别为业务闭环 **4/4**、GM **3/3**，`page_errors=0`。真实 `backend/data` 198 文件路径哈希 `d9602a9d...` 在验收窗口内零变化；未提交 git。
- **仍未做（准入制）**：政策税率版本化需逐条官方日期复核；房产税/城镇土地使用税需地方口径+真实脱敏样本；13 周现金流需业务口径。无材料不编税率；Task 8/9 部分独立评审因子代理 429 改为主线程复核，最终 whole-branch review 需重点看 GM/facility。

## 交付止血 + P0 申报单位 + 准入制机制化（2026-10-31 晚）

- **第 0 批（交付止血）**：`git add -A` 一次提交 75 个文件（`bd36e08`，40 改 + 35 新增，含 3 个未跟踪模块与 `utils_money.py`/`finance_iit_common.py`/`business_metrics.py`），避免 `git commit -am` 只提交已跟踪文件导致 5 个 service 的 import 失败、**8/22 模块静默消失**。提交前全量回归：后端 1427 passed、前端 57 文件 871 passed、构建通过。热备份 `backups/workbench_20261031_195627.db`（1252KB）+ `workbench_data_20261031_195627.zip`（6.92MB）。精确 taskkill 监听 PID（命令行同时命中 venv 路径与 `app.main:app`）后用 `开机自启动.vbs` 重启，4 秒就绪；真实库 **56 张表**（新建 `mod_fac_equipment`/`mod_fac_compliance`/`mod_ops_work_order_followup`），facility/operations/hr summary 200、GM 带 `period` 200、招商 10 条路由已挂载。**推送未完成**：`git-credential-manager.exe` 弹窗等待授权，已停止后台推送，待用户授权后重试。
- **第 1 批（P0 + 3 个自伤 500 + 归属校验）**：
  - **P0 企业所得税申报单位契约**：前端表单/`TaxReturnCreate` 入参为**万元**，`mod_tax_return` 落库与 A200000 导出改为**元**（申报表口径）。`calculate_tax` 只在边界换算一次（`taxable_income = 万元 × 10000`），小微判定直接吃万元值；旧实现把万元当元再除 10000，导致 1000 万被判成 0.1 万（25%→5%）且导出少 1 万倍。真实库 `mod_tax_return` 为 0 行，无迁移负担。
  - **A200000 计算收敛**：`_build_a200000_form_data` 原手写 `tax_payable = actual_profit * rate` 而 `actual_profit` 恒为 0（导出行次靠 `to_row_data()` 重算才正确），改为统一 `form_data.calculate()`。
  - **年度接线**：`CalculationRequest` 新增 `year` 兼容字段（前端历史提交名），`tax_year = tax_year or year or 当前年`；`LossCarryForward` 传入申报年度，2019 年亏损在 2026 年正确判过期。缺年度不再静默跳过政策校验。
  - **自伤 500**：`ScheduleUpdate.title` 与 `AgentSpecUpdate.display_name` 增加 strip+非空校验——旧实现先 commit 再被响应 schema 拒绝，该行永久污染、列表持续 500。
  - **模型归属**：`save_provider_model`/`delete_provider_model` 校验 `provider_id` 与模型实际所属一致（跨供应商编辑 400、删除 404），路由 DELETE 不再忽略路径参数。
  - 前端单位标注同步：申报预览/列表/统计由「万元」改为「元」；`使用说明.md` 补「计算页万元、申报记录元」的单位说明。
- **第 5 批（准入制机制化）**：
  - 新增 `finance_tax/config/tax_scope.py`：9 个税种准入登记（5 admitted / 4 blocked），`require_tax_scope` 统一拒绝未准入税种；新增只读路由 `GET /api/finance/tax/scope`、`/scope/{tax_key}`。房产税/土地使用税/土增税/契税登记为 blocked 并写明所需地方口径。
  - 税率版本化（CIT 先行）：`基本税率`/`高新 15%` 补 `effective_date=2008-01-01`、`小微 5%` 补 `2023-01-01`；`calculate_tax_amount` 增加 `_ensure_rate_effective`，所属年度早于登记生效日直接 400（小微仍由 policy_status 校验公告号与到期日，不重复）。
  - GM 指标字典补 `hr.payroll_count`（caveat 指向的字段变为可见），漂移测试要求 `emitted == dictionary` 仍成立。
- **验证**：后端全量 **1443 passed（789.92s）**、前端 **57 文件 871 passed**、`npm run build` 通过（stamp `57d9ca00d38b`）。新增测试 16 例：`test_tax_return_units.py`（5）、`test_bugfix_p1_20261031.py`（5）、`test_tax_scope_and_rate_versioning.py`（6）。
- **第 2 批（数据不变量 + 安全快修，深夜追加）**：
  - **并发收款不丢单**：`pay_bill` 改条件更新（`UPDATE ... WHERE paid_amount=旧值`，rowcount=0 → 回滚 + 中文冲突提示）；线程级并发测试断言「终态实收恒等于成功收款之和」。
  - **删合同守卫**：`contract_delete` 对存在 `paid_amount>0` 账单的合同抛 ValueError（路由 409），账单与实收保留；无已收款账单才级联删。
  - **HR 入职前不出薪**：`payroll_generate` 对「入职月份晚于出薪月份」抛中文 ValueError（409），不再 `max()` 兜底成 1 个月计税；与 `finance_iit` 拒绝口径对齐。
  - **xlsx 公式注入**：新增 `app/utils_xlsx.py::safe_cell`（`= + - @` 前缀加 `'`），finance_iit/invoice/vat 三处导出接入；业务模块 6 份 `_cell` 拷贝的收敛起点。
  - **脱敏过滤器升级**：`git_sanitize_filter.py` 改**字节级双编码替换**（UTF-8 + GB18030）——旧实现只按 UTF-8 匹配，GBK 文件里的中文真实串永远命中不了（`.vbs` 泄漏的根因）；`.gitattributes` 补 `*.example/*.vbs/*.bat/*.html/*.css/*.mjs/*.sh`；`.env.example` 6 行示例路径改占位符；7 个新覆盖文件 renormalize 后暂存 blob 双编码命中 **0**。
- **仍未做**：推送待用户在自己终端授权一次（GCM 弹窗无法由代理点击）；`restore_backup.py` 非原子（恢复演练必须先修）；跨模块勾稽三字段（unit_id/免租期/专项附加扣除）未接线；`.env.example`/vbs 的**历史 blob** 残留需 git filter-repo 单独评估。
- **第 3 批（restore 原子化 + 跨模块勾稽，2026-10-31 深夜二轮）**：
  - **`restore_backup.py` 重写为原子恢复**：先整体解压到同卷临时目录 → `PRAGMA integrity_check` 校验（坏库/加密/截断统一中文拒绝）→ 旧数据**整目录**快照为 `data.before_restore_*` → 一次性换入；任何一步失败目标目录保持原状。新增防恶意包：条目数 ≤50k、解压总量 ≤2GiB、Windows 设备名与结尾点/空格拒绝。roundtrip 测试同步改整目录快照口径；新增 5 例硬化测试（坏库原子性/设备名/zip-slip/双上限/完整快照）。
  - **跨模块勾稽三字段接线（上一轮审计的三大缺口的修复）**：
    - `OpsContract.unit_id`（`mod_ops_contract` 补列）：`_resolve_unit` 校验铺位存在 + **同铺位唯一生效合同**（防重复出租），unit_no 以招商台账回填；显式 `unit_id=null` 解除关联。
    - **出账读租决参数**：`_lease_terms` 经 `合同.unit_id → LeasingLead(stage=signed)` 读免租期/年递增/租金形式——免租月租金 0（物业费照收）、递增率第 13 个月起按年放大（百分数语义 5=5%）、**抽成/保底抽成合同拒绝按固定租金出账**（409）。
    - HR `special_additional`（员工+薪资单补列）：进入累计预扣（当月扣 + 历史月份合计扣），`payroll_to_dict` 回显；与 `finance_iit` 同函数同口径。
    - 前端：OperationsView 合同弹窗「关联铺位」下拉（读招商铺位，招商不可用退化为手填）+ HrView 员工表单「专项附加扣除(元/月)」。
  - **Mimosa 深度扫描（deep，首次完整完成）**：6 findings 全部定性——`package_release.py` 路径/SSRF 3 处为冒烟固定 `127.0.0.1:{free_port}` 误报；`20261012New/scripts/migrate_tax_data.py` SQL f-string 2 处为历史存档（输入是模块常量，按约定不改历史）；`desensitize_finance_data.py` 固定种子 RNG 为保勾稽设计使然（已加注释）。seal `af19d4d6…`，报告在 `~/.mimosa/security-scans/project-3640d8ab…/scan-2026-10-31T14-03-36…/`。
- **验证（第 3 批）**：后端全量 **1462 passed（794.44s）**、前端 **57 文件 871 passed**、构建通过（stamp `7cd63a736357`）。新增测试 13 例：`test_restore_hardening_20261031.py`（5）+ `test_cross_module_reconciliation_20261031.py`（8）。

## 数据收传使用说明 + 导入模板库（2026-11-01）

- **交付**：根目录《数据收传使用说明.md》（总览表/通用限制/数据仓/费用/POS/现金流/发票/BIP/个人记账/五税种字段表/六业务模块字段表/看板/报错速查/模板清单）+ **`数据模板/` 10 份可直接上传的导入模板**（数据仓 csv+xlsx、个人记账 csv、费用银行明细账〔与现金流共用〕、POS 认领记录/辅助明细账/凭证列表〔文件名已含引擎关键词，填完别改名〕、BIP 凭证列表/辅助明细账/辅助余额表**三份独立文件**——BIP 预览为整文件识别单一类型，三合一会识别混乱）。
- **格式依据**：三个并行子代理精读解析代码 + 脱敏数据样本实测表头；关键修正——费用引擎会把「日期→凭证日期」归一（引擎 expense_classification.py:250），模板沿用用友真实表头形态；POS 引擎按文件名关键词找文件，模板名即钥匙；现金流六类文件按关键词自动发现（明细账/当期模板/上月上报版必需，其余可选）。
- **验证**：新增 `backend/tests/test_data_templates.py`（5 passed：数据仓 csv/xlsx 入仓各 2 行、BIP 三类型识别 voucher/aux/balance、个人记账导入 2 行、费用表头定位命中）。模板无 PII，随 git archive 进分发包；分发包剔除 tests 后测试有 skipif 守卫。脱敏数据/ 仍为 gitignore 样本，不入库。
- **本轮故障模式（新变体）**：本会话 Write/Edit **中长内容**高频被注入「占位噪声」（连 Bash python -c 内联脚本也约 50% 中招），短片段+写后立即 `grep 占位噪声` + 定向 Edit 修复可行；长 Write 必须回读校验，模板生成改走「短 python -c 片段逐文件生成+回读」。
- 《使用说明.md》目录补「十三、数据收传」并加 §7 指针。
