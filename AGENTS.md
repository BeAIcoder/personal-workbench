# AGENTS.md — personal-workbench 项目记忆

> 本文件是项目级长期记忆：跨会话保持的状态、约定与待办。Hub 级记忆（脱敏钥匙等）见 Agent Hub，不在此重复。

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
- 测试：后端 `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q`；前端 `cd frontend && npm test` + `npm run build`。
- **脱敏数据管线**（外部验证数据准备）：生成 `python dev/desensitize_finance_data.py {bip|pos|cash}`，校验 `python dev/verify_desensitized.py`（需 `VERIFY_ATTACH_DIR` 指向会话附件目录）。产物在 `脱敏数据/`（gitignore）。规则：保勾稽随机数（凭证借=贷、明细账余额链、余额表配平）；泛化命名**避开 BIP 表头关键词**（账簿/科目/日期/凭证号/借方/贷方/期初/期末/本期/摘要），否则污染 `_locate_header_row`。

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
