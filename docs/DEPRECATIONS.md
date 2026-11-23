# 兼容层退役清单（Deprecations）

> **as of 2026-11-17 / working tree**（HEAD `b33f912`；`docs/` 整体尚未首次提交，本文件随该目录一起入库）。
> 目的：把"为什么还在兼容""什么时候能删""删了怎么验证、怎么回滚"写死，避免两种失败——**不敢删**（兼容层越积越多）和**误删**（旧库/旧前端直接坏）。
> 铁律（复盘 §6.2）：**先弃用，再删除**；删除必须带替代路径、迁移说明、回滚手段，并补一个"新契约"测试。
> 关联：`docs/PII_DISPOSITION.md`（PII 类"接受"项的登记）、`docs/INVARIANTS.md`（不变量）、`docs/README.md`（文档分层）、`docs/GLOSSARY.md`（命名别名）。

## 0. 口径与版本规则

- **一个发布周期 = 一个 minor**（v1.19.0 → v1.20.0 → v1.21.0）。本清单登记于 **v1.19.0**。
- **最早删除版本**默认 = **v1.21.0**（登记后跨过一个完整 minor 发布）；涉及密钥/历史的项可再推迟。
- 状态只有三种：**已登记**（还在，可观察）→ **删除就绪**（前置条件全满足，等版本窗口）→ **已删除**（代码已移除，本条保留为历史记录）。
- "已删除"的行**不删除**，改为在 §6 变更记录里留痕：下一个人需要知道它存在过。

## 1. 速查表

| ID | 兼容层 | 位置（符号） | 替代路径 | 状态 | 最早删除 |
|---|---|---|---|---|---|
| DEP-001 | LLM 供应商 Key / 旧 `agent_settings.api_key` 明文迁移 | `agents/secrets_box.py::migrate_seal_keys`、`::purge_legacy_agent_settings_keys` | 新写入强制 DPAPI 密封；读取端 `unseal()` 兼容明文 | 已登记 | v1.21.0 |
| DEP-002 | MCP env 明文迁移 | `agents/mcp.py::migrate_env_secrets` | `seal_env_values()` 写、`unseal_env_values()` 读 | 已登记 | v1.21.0 |
| DEP-003 | 历史脏定时任务归一化 | `scheduler.py::normalize_legacy_jobs` | 服务层白名单（`mode`/`schedule_type`）+ `next_due_at()` 容错 | 已登记 | v1.21.0 |
| DEP-004 | 列表裸数组兼容 | `frontend/src/modules/_shared/pagedList.js::pageState`（`legacyArray` 分支） | 后端统一 `{items,total,page,page_size}` | 已登记 | v1.21.0 |
| DEP-005 | 银行流水导出旧掩码入参 | `modules/finance_bankflow/router.py::_resolve_request_account`（掩码分支）+ `/template` 的 `account` 表单字段 | `/preview` 签发 `account_token`，导出只认令牌 | 已登记 | v1.21.0 |
| DEP-006 | 依赖双源（`requirements.txt` + `requirements-lock.txt`） | `backend/requirements*.txt` + `tests/test_release_package.py` 的 3 个 lock 用例 | 单一 `requirements.txt`（范围约束 + 干净 venv 冒烟） | 已登记 | v1.21.0 |
| DEP-007 | manifest 已删字段兼容（`menu`/`tables`） | `modules/spec.py::REMOVED_FIELDS` + `ModuleManifest.__init__(**removed)` | 菜单源=前端 `manifest.js`；表以 `__tablename__` 为准 | 已登记 | v1.21.0 |

## 2. 删除流程（每一项都必须走完这 5 步）

1. **消费者归零**：给出 `grep`/测试证据，证明没有调用方（含前端、脚本、测试、文档）。
2. **补新契约测试**：先写"新路径必须成立"的测试（例如"掩码入参返回 400"），否则下次会以"兼容旧数据"为由加回来。
3. **删代码 + 更新本文件**：状态改 `已删除`，写明提交与日期；不要删掉本行。
4. **聚焦验证**：跑该项 §3 的"验证"命令 + 干净库启动一次（`scripts/migrate.py` 幂等）。
5. **用户可见性**：若影响操作（手册/接口字段），同步 `使用说明.md` / `API.md`；跨层变更走 `docs/playbooks/cross-layer-change.md`。

## 3. 逐项登记

### DEP-001 明文 LLM Key / 旧 `agent_settings.api_key` 迁移

| 项 | 内容 |
|---|---|
| 为什么存在 | v1.19 之前 `model_providers.api_key` 可能以明文落库；更早版本还把 Key 存在 `agent_settings.api_key`。前者需一次性密封，后者是**已废弃列**，只需清空。两者都只在"老库首次升级"时有意义。 |
| 现状位置 | 函数在 `backend/app/agents/secrets_box.py`；接线在 `backend/app/bootstrap/legacy.py::run_all`，只由 `backend/scripts/migrate.py secrets` 显式触发。启动路径**不再**执行写操作，只调 `report_pending_secrets()` 做只读计数并告警。 |
| 替代路径 | 新写入一律 `seal()`（DPAPI，无明文降级）；读取端 `unseal()` 对历史明文原样返回。所以**不迁移也不丢功能**，只是安全卫生问题。 |
| 删除前置 | ① `scripts/migrate.py status` 在本机库返回全 0；② 一个完整 minor 发布（v1.20.x）内无"老库升级失败/明文残留"报告；③ 手册已有"升级后跑一次 `migrate.py secrets`"的步骤。 |
| 验证 | `cd backend && pytest tests/test_ai_security_20261111.py -k legacy_agent_settings -q`；`venv/Scripts/python.exe scripts/migrate.py status`；`git grep -n "purge_legacy_agent_settings_keys"` |
| 回滚 | `git revert` 删除提交（函数与脚本在同一提交内，回滚即恢复）；最坏情况下从 `git log -S` 取回实现。数据面无回滚需求（迁移是单向密封，密封值可正常 `unseal`）。 |
| 风险 | 中。旧备份恢复后库里又出现明文，而启动只告警不自动迁移 → 需要用户手动跑脚本。删掉迁移后连"能跑"的路径也没了，**告警文案必须保留并指明脚本**。 |
| 备注 | `agent_settings.api_key` 列的物理删除属另一件事（走 `_ensure_sqlite_columns` 补丁体系，不在本清单）。 |

### DEP-002 MCP env 明文迁移

| 项 | 内容 |
|---|---|
| 为什么存在 | 早期 MCP 服务器 `env` 直接存明文；现在写入走 `seal_env_values()`，历史行需要一次密封。 |
| 现状位置 | `backend/app/agents/mcp.py::migrate_env_secrets`；接线同 DEP-001（`bootstrap/legacy.py` + `scripts/migrate.py secrets`）。 |
| 替代路径 | 写：`_sync_server_row`/保存路径统一 `seal_env_values`；读：`_row_to_server` 用 `unseal_env_values`（`dpapi:` 前缀走 DPAPI，否则按明文兼容返回）。 |
| 删除前置 | 同 DEP-001（`migrate.py status` 的 `mcp_env` 归零 + 一个发布周期）。 |
| 验证 | `pytest tests/test_swarm3_backend_hardening.py -k "mcp_env or migrate_env_secrets" -q` |
| 回滚 | `git revert`。密封是单向的，不需要反向迁移。 |
| 风险 | 中。与 DEP-001 同口径：旧备份恢复后需手动迁移，否则明文继续留在库中（但功能正常）。 |

### DEP-003 历史脏定时任务归一化

| 项 | 内容 |
|---|---|
| 为什么存在 | v1.4 之前的定时任务行可能缺 `interval_minutes`、`daily_at`/`cron_expr` 非法、`schedule_type` 未知。`normalize_legacy_jobs` 会把危险行**禁用**并归一化，避免调度器在非法值上抛错。 |
| 现状位置 | `backend/app/scheduler.py::normalize_legacy_jobs`；启动期由 `backend/app/bootstrap/seeds.py::run_startup_seeds → normalize_jobs_and_experts` 调用（每次启动幂等，二次执行返回 `[]`）。 |
| 替代路径 | 新写入已受校验：`schedule_type` 白名单、`interval_minutes` 范围、`cron_expr` 语法、`mode` 白名单（非法值执行侧一律回退 `readonly`）。`next_due_at()` 已覆盖 interval/daily/cron 三种调度。 |
| 删除前置 | ① 至少一次启动后 `normalize_legacy_jobs()` 返回 `[]`（说明本机库已干净）；② 把"`next_due_at()` 遇到非法行不抛异常"固化成测试（新契约）；③ 一个发布周期。 |
| 验证 | `pytest tests/test_task1_scheduler_lifecycle.py -k legacy -q` |
| 回滚 | `git revert`；若已删而需要临时救急，可在停服状态下手工把非法任务 `enabled=0`。 |
| 风险 | 低。极旧库若残留非法行，删除后可能表现为"任务不触发/启动报错"。缓解=先补 `next_due_at` 容错测试，再删归一化。 |

### DEP-004 列表裸数组兼容（`pagedList`）

| 项 | 内容 |
|---|---|
| 为什么存在 | 早期列表接口直接返回数组；现在统一返回 `{items,total,page,page_size}`。`pageState()` 的 `legacyArray` 分支把裸数组当"单页列表"，避免静默显示空表。 |
| 现状位置 | `frontend/src/modules/_shared/pagedList.js`（`pageState` 内 `const legacyArray = Array.isArray(data)` 与三元分支）。消费方：`business-hr`、`business-legal`、`finance-invoice` 三个视图。 |
| 替代路径 | 后端分页契约 `{items,total,page,page_size}`（HR/法务/发票批次接口均已返回）；前端 `toPageParams` 统一带 `page/page_size`。 |
| 删除前置 | ① 三个视图对应接口的契约测试断言响应是对象而非数组；② 前端 `api` 层无任何"裸数组兜底"；③ 一个发布周期（前后端同包发布，风险窗口极小）。 |
| 验证 | `cd frontend && npx vitest run tests/paged-lists.test.js`；删除前把用例「旧后端裸数组响应兼容为单页列表（不静默显示空）」改写为「裸数组按契约错误处理（不静默显示空表）」。 |
| 回滚 | `git revert`（纯前端，无数据面影响）。 |
| 风险 | 低。若用户浏览器跑旧缓存前端 + 新后端，会看到空表；缓解=错误提示走拦截器统一文案。 |

### DEP-005 银行流水导出旧掩码入参

| 项 | 内容 |
|---|---|
| 为什么存在 | 旧前端把 `/preview` 返回的掩码账号（含 `*`）回传给 `/template` 导出。为不打断已安装的旧前端，服务端保留了"掩码唯一匹配"分支，同时**已拒绝完整账号**。 |
| 现状位置 | `backend/app/modules/finance_bankflow/router.py`：`_resolve_request_account()` 的 `legacy` 掩码分支；`/template` 的 `account: str = Form(default="")` 与"`account_token` 与 `account` 二选一"校验。 |
| 替代路径 | `/preview` 为每个账号签发 `account_token`，导出只传 `account_token`；前端 `BankflowView.vue` 已只用 `selectedToken`。不变量见 `docs/INVARIANTS.md` INV-006。 |
| 删除前置 | ① 现有兼容测试 `test_bank_template_export_by_masked_account_for_ui_compat` **改写为断言 400**（新契约）；② 一个发布周期；③ `使用说明.md`/`API.md` 不再提 `account` 入参。 |
| 验证 | `cd backend && pytest tests/test_finance_correctness_20261111.py -k bank -q`、`pytest tests/test_module_finance_bankflow.py -q`；`cd frontend && npx vitest run tests/bankflow-view.test.js` |
| 回滚 | `git revert`；掩码分支是纯函数内分支，回滚无数据面影响。 |
| 风险 | 低-中。旧缓存前端会拿到 400；现有文案已引导"请使用预览返回的账号令牌"，删除时保留该文案。 |

### DEP-006 依赖双源（`requirements.txt` + `requirements-lock.txt`）

| 项 | 内容 |
|---|---|
| 为什么存在 | 锁文件是"Windows + Py3.13 精确复现"的产物，用来固定 OCR/向量/xdist 的传递依赖。 |
| 现状位置 | `backend/requirements.txt`（范围约束）、`backend/requirements-lock.txt`（`==` 全量）、`backend/tests/test_release_package.py` 的 3 个 lock 契约用例（必须精确锁定、必须覆盖 requirements 全部条目、必须含 xdist/OCR/向量）。 |
| 替代路径 | **事实**：CI（`.github/workflows/ci.yml`）、打包冒烟（`dev/package_release.py`）与运行时说明都只消费 `requirements.txt`；lock 只被上述 3 个测试引用。因此二选一时应优先"**删除 lock，只留 `requirements.txt`**"，把测试改成断言 `requirements.txt` 覆盖核心依赖（`REQUIRED_REQUIREMENTS` 已有此检查）。若坚持保留 lock，则必须继续用全新临时 venv 重生成（命令在 lock 文件头）。 |
| 删除前置 | ① 选定单一源并在本文件登记；② 删除/改写 `test_release_package.py` 的 3 个 lock 用例（不得 skip）；③ `docs/README.md` §4 的 lock 行改成最终结论（现为"双源去留见 DEP-006"）；④ 干净 venv `pip install -r requirements.txt` + 抽样跑测试通过。 |
| 验证 | 全新 venv：`pip install -r backend/requirements.txt` → `cd backend && venv/Scripts/python.exe -m pytest tests -q -n 4 --timeout=60`（抽样或分片）；`pytest tests/test_release_package.py -q` |
| 回滚 | 从 `git log` 取回 lock；或按文件头命令用全新 venv 重新生成（几分钟）。 |
| 风险 | 中。无锁 = 上游破坏性升级风险（`requirements.txt` 已用 `>=x,<y` 上下界缓解）；反之**保留 lock 也有风险**——它是 Windows/Py3.13 专用，随包分发会让收件人在其他环境误用（缺 OCR/向量/xdist 的老版本锁曾导致 `ModuleNotFoundError`）。 |
| 备注 | `requirements-lock.txt` 目前仍在 `git archive` 内（打包只剔除 `dev/`、tests、`20261012New/`、dated 报告），因此该风险是"真实可达"的，不只是文档问题。 |

### DEP-007 manifest 已删字段兼容（`menu` / `tables`）

| 项 | 内容 |
|---|---|
| 为什么存在 | 2026-11-17 删除了 `ModuleManifest.menu` / `.tables` 两个字段（无任何消费者：菜单唯一源是前端 `manifest.js`；建表靠 `__tablename__`）。为不打断**在途未提交模块**，构造时传这两个字段不报错、值被忽略、记入 `removed_fields`。 |
| 现状位置 | `backend/app/modules/spec.py`：`REMOVED_FIELDS = frozenset({"menu","tables"})` 与 `__init__(**removed)` 的兼容分支。 |
| 替代路径 | 菜单：`frontend/src/modules/*/manifest.js`；表清单：`models.py` 的 `__tablename__`（`spec.md`/`AGENTS.md` 的计数只是描述，不是声明）。 |
| 删除前置 | ① `grep -rn "menu=\|tables=" backend/app/modules/*/manifest.py` **无输出**（当前仅未提交的 `finance_receipt` 两行）；② 该在途模块合并；③ 一个发布周期。 |
| 验证 | `grep -rn "menu=\|tables=" backend/app/modules/*/manifest.py`；`pytest tests/test_architecture_contracts.py::test_all_manifests_load -q` |
| 回滚 | `git revert`（纯校验分支，无数据面影响）。 |
| 风险 | 低。忽略不生效，不会写错数据；唯一风险是"以为改了菜单/表却无效"，这也是要删它的理由。 |

## 4. 明确不删（防止误删）

| 对象 | 为什么不删 |
|---|---|
| `_template/` 模块脚手架 | 新增模块的样板；不计入运行库（目录以 `_` 开头，注册器不加载） |
| `finance_hello` | 模块机制的端到端示例；可直达 `/#/finance/hello` 用于排障 |
| 历史审计报告、`progress.md`、`BUGfix.md`、`20261012New/` | 证据链；只归档加索引（`docs/README.md` §3），不改写 |
| DPAPI「无明文降级」 | 安全承诺（N-1）；Linux 启动即拒是**预期设计**，不是缺陷 |
| 看板 `schtasks` 计划任务通道 | 安全软件按进程树拦截 venv python 写项目根，直连子进程只是回退路径 |
| `column_patches` 轻量迁移 | 单用户/单进程下的迁移方案，切 PostgreSQL + Alembic 之前不动 |
| `dev/sanitize-map.local.json` / `dev/pii-terms.local.txt` | 反向钥匙与本地词表，不入库、随备份保存 |
| `finance_receipt`（未提交在途） | 未提交功能，等模块作者合并；本清单不碰 |

## 5. 命名别名：不删，但要统一说法

| 旧说法 | 现说法 | 处理 |
|---|---|---|
| "数据仓" | 数据池清洗（`finance_vault`） | 只改文档/注释；**模板文件名不改**（改名会打断已有上传习惯与引擎识别） |
| "回单" vs "POS 凭证" | 回单整理（`finance_receipt`）/ POS 刷卡凭证（`finance_pos` vouchers） | 术语表登记，目录/表/路由不改 |
| `finance_dsf` | 残保金（disabled security fund） | 术语表登记，见 `docs/GLOSSARY.md` |

详见 `docs/GLOSSARY.md`。

## 6. 变更记录

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-11-17 | 建文件：登记 DEP-001…007，含替代/验证/回滚/最早删除版本 | `2026-11-17_架构与工程效能复盘.md` §3.2/§6、`2026-11-17_1720_1-12修复计划.md` §2-G；`backend/app/modules/spec.py` 已前向引用本文件 |
