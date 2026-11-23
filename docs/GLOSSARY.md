# 术语表（中英 / 别名 / 代码位置）

> 目的：解决“看起来相近、实际不同”的概念。新增模块/字段时，先在这里加一行，再写代码。
> 规则：**代码目录名不轻易改**；显示名/文档名统一；必须改名时保留旧路由/别名一个版本。

## 1. 模块与业务域

| 术语 | 英文/别名 | 代码位置 | 说明 |
|---|---|---|---|
| 财务模块 | finance_* | `backend/app/modules/finance_*` | 19 个生产模块 + `finance_hello` 示例（隐藏） |
| 经营/职能模块 | business_* | `backend/app/modules/business_*` | 9 个：GM/招商/营运/企划/工程/经营管理/法务/人事/DQ |
| 数据池清洗 | finance_vault，旧称“数据仓” | `modules/finance_vault` | DuckDB 底座；画像/清洗/聚合/导出；模板文件名仍叫“数据仓”，不要改 |
| 费用分类 | finance_expense | `modules/finance_expense` | 外部引擎费用分类 |
| 发票处理 | finance_invoice | `modules/finance_invoice` | 批次/明细/冲红配对/项目归类/导出 |
| POS 对账 | finance_pos | `modules/finance_pos` | POS 台账对账 + 刷卡凭证合并 |
| 银行流水 | finance_bankflow | `modules/finance_bankflow` | 流水转 BIP 导入模板；账号只给掩码/令牌 |
| 企业记账（BIP） | finance_ledger_bip | `modules/finance_ledger_bip` | 凭证列表/辅助明细账/辅助余额表三份文件 |
| 个人记账 | finance_ledger_personal | `modules/finance_ledger_personal` | 个人收支/预算/分类/统计 |
| 企业所得税 | finance_tax | `modules/finance_tax` | CIT、政策库、申报共享；不是“所有税种” |
| 增值税 | finance_vat | `modules/finance_vat` | 一般纳税人/小规模；不含附加税（未接入） |
| 个税扣缴 | finance_iit | `modules/finance_iit` | 累计预扣；专项附加标准表 |
| 印花税 | finance_stamp_duty | `modules/finance_stamp_duty` | 按季/按月/按次；季末月校验 |
| 残保金 | finance_dsf | `modules/finance_dsf` | `dsf` = disability security fund；不是“地方税” |
| 报表看板 | finance_board | `modules/finance_board` | 外部四步管线 + 计划任务通道 + 快照/verify |
| 现金流填报 | finance_cashflow | `modules/finance_cashflow` | 13 周现金流（业务口径待定） |
| 银行回单整理 | finance_receipt | `modules/finance_receipt` | 扫描建批/逐笔复核/原单预览/合并 PDF（工作树在途） |
| 租赁核算 | finance_lease | `modules/finance_lease` | 承租人使用权资产/租赁负债初始 + 正常后续计量；简化口径 ROU = 现值；计划口径不对账实付；另有 BIP 科目余额只读对账 |
| 长期待摊 | finance_deferred | `modules/finance_deferred` | 仅租入资产改良支出直线摊销；末期尾差如实披露；超租赁期只 warning |
| 工会经费计提 | finance_union | `modules/finance_union` | 手工工资基数 + 批次费率；本期计提/上期差异/建议入账；不读 HR |
| 经营管理 | business_manage | `modules/business_manage` | 内控/周报/月度；4 张通用表 |
| 营运管理 | business_operations | `modules/business_operations` | 合同/账单/工单 |
| 招商管理 | business_leasing | `modules/business_leasing` | 铺位/线索/跟进/漏斗 |
| 企划管理 | business_planning | `modules/business_planning` | 活动/预算/复盘/供应商 |
| 工程物业 | business_facility | `modules/business_facility` | 设备/合规到期 |
| 法务管理 | business_legal | `modules/business_legal` | 合同审查/案件/用印 |
| 行政人事 | business_hr | `modules/business_hr` | 员工/考勤/薪资 |
| 总经理经营聚合 | business_gm | `modules/business_gm` | 只读聚合；各域经 `service.register_provider()` 显式注册 `DomainProvider`（导入路径 + 汇总口径），新增域只加一条注册，不改 `build_summary` 主流程 |
| 数据质量巡检 | business_dq | `modules/business_dq` | 跨模块只读检查；issue_count=命中行数 |
| 调试中心 | debug_center | `modules/debug_center` + `frontend/src/modules/debug-*` | 文件存储；前端拆 3 个菜单 |

## 1.1 显示名与别名（显示名统一；目录/表/路由一律不改）

> 目的：同一个东西不要有三个名字。**只统一“怎么说”，不动 `modules/` 目录名、表名、路由前缀**；
> 显示名与后端 manifest `title` 必须一字不差（`backend/tests/test_glossary_terms.py` 会校验）。

| 代码名 | 显示名（唯一） | 术语别名（对话/文档可用） | 目录 / 表 / 路由（保持不动） |
|---|---|---|---|
| `finance_vault` | 数据池清洗 | 数据池、数据仓（旧显示名；模板文件名仍叫“数据仓”） | `modules/finance_vault`、`mod_vault_dataset`/`mod_vault_clean_log`、`/api/finance/vault` |
| `finance_dsf` | 残保金填报 | 残保金、残疾人就业保障金、disability security fund | `modules/finance_dsf`、`/api/finance/dsf` |
| `finance_receipt` | 银行回单整理 | 银行回单、回单批次、回单合并 | `modules/finance_receipt`、`mod_receipt_batch`/`mod_receipt_item`、`/api/finance/receipt` |
| `finance_pos` | POS 对账 | POS 台账对账、POS 流水对账 | `modules/finance_pos`、`mod_pos_run`、`/api/finance/pos` |
| `finance_recon` | 商户核销对账 | 商户对账、核销对账、对账工作底稿 | `modules/finance_recon`、`mod_recon_*` 6 张、`/api/finance/recon` |
| `finance_lease` | 租赁核算 | 承租人租赁核算、使用权资产核算、租赁负债核算 | `modules/finance_lease`、`mod_lease_*` 6 张、`/api/finance/lease` |
| `finance_deferred` | 长期待摊 | 租入资产改良、长期待摊摊销、改良支出摊销 | `modules/finance_deferred`、`mod_deferred_*` 3 张、`/api/finance/deferred` |
| `finance_union` | 工会经费计提 | 工会经费、工会费、union fund | `modules/finance_union`、`mod_union_*` 2 张、`/api/finance/union` |
| POS 刷卡凭证（`finance_pos` 子功能） | 刷卡凭证 | POS 凭证、刷卡汇总凭证 | `modules/finance_pos/vouchers_service.py`、`mod_pos_voucher_batch`、`/api/finance/pos/vouchers*` |

**“回单”到底指哪个**（最常混）：

- **银行回单整理**（`finance_receipt`）：输入银行回单 PDF/图片 → 按账期扫描建批 → 逐笔复核分类 → 生成“账户 × 类别”PDF 与清单。产物是**整理结果**，不生成凭证，也不写任何账簿。
- **POS 刷卡凭证**（`finance_pos` 的 `vouchers_service`）：输入 POS 对账单 + 银行回单 PDF → 按 SHOP 匹配合并 → 生成**刷卡凭证**批次（`mod_pos_voucher_batch`）。产物是凭证，不是整理台账。
- 两者互不调用、不共享批次表；改一个不要把另一个的目录/表/路由一起改。

**残保金不是“地方税”**：`dsf` 是残疾人就业保障金，走申报/导出链路但人数与征收口径独立；不要与 `finance_tax` 的准入门槛（5 admitted / 4 blocked）混为一谈。

## 1.2 租赁核算（承租人）关键术语

| 术语 | 英文/别名 | 代码位置 | 说明 |
|---|---|---|---|
| 使用权资产 | ROU asset | `mod_lease_calculation.summary.asset_cost` | 首期**简化口径 = 租赁负债现值**；不含预付租金、初始直接费用、复原成本、租赁激励 |
| 租赁负债 | lease liability | `mod_lease_assessment.liability_balance`、`mod_lease_period.liability_balance` | 付款额现值；逐期滚动 `B_{i+1} = B_i + U_i - amount_{i+1}` |
| 未确认融资费用 | unamortized finance cost | 评估行/期间行同名字段 | **两个不同含义**：评估行 = `amount - PV`（逐期折现差额，源表 S 列）；期间行 = `M2 - 累计已确认利息`（月末余额）。导出文案必须分开 |
| 折现因子 | discount factor, `d_i` | `calculation.py` | `d_1 = 1`（首行不折现），`d_i` 9 位小数递推；`PV = amount / d_i` 2 位 |
| 期间利率 | period rate, `Q_i` | `calculation.py::period_rate` | `ROUND_HALF_UP(年利率 × 该期月数 / 12, 4)`；每期按自有月数，不统一按年 |
| 实际当期利息 | U 列利息 | `mod_lease_assessment.interest_u` | `U_i = ROUND(付款后余额 B_i × Q_i, 2)`；期间表**月利息 = U_i ÷ 该期计租月数**（期内均摊） |
| 计划口径 | plan basis | 本模块全部金额 | 只来自合同 + 付款计划；**不对账实付、不回写凭证**；BIP 账面只经独立**只读**对账读取，不改变计划口径 |
| 过期 / stale | stale calculation | `GET /calculations/{id}.stale` | 合同或付款计划变化后旧测算标记过期；确认与当前导出 409，历史快照需 `allow_stale=true` |
| 检查状态 | `ok` / `failed` / `unknown` | `schemas.py::CHECK_STATUSES` | 缺数据一律 `unknown`，不按 0 通过；`unknown` 阻断确认 |
| 底稿分录 | draft entry / voucher | `entries.py::build_entries` | **逐笔凭证**：`LEASE-{测算id}-INIT`、`-DEP-{YYYY-MM}`、`-INT-{YYYY-MM}`（折旧/利息为 0 不出该凭证）；导出前 `validate_entries` 按凭证号逐张校验借贷相等，不平衡拒绝导出；仅初始确认与月度折旧/利息，不含付款实收/变更/终止/BIP 回写 |
| 汇总级分录检查 | `entry_recognition` / `entry_monthly` | `checks.py` | 校验页的**汇总口径**（首期确认、月度分录合计，容差 1 元）；与逐凭证平衡是两件事，不能互相替代 |
| BIP 对账科目位 | `SUBJECT_KEYS`（五科目位） | `reconciliation_schemas.py` | 使用权资产原值 / 累计折旧 / 租赁付款额（剩余付款）/ 未确认融资费用余额 / 租赁负债净额；中文标签只在该文件定义 |
| BIP 余额方向 | `direction: debit/credit` | `mod_lease_bip_map` | 映射声明该科目位的 BIP 余额方向；借方余额取「期末借−期末贷」，贷方反向 |
| 对账覆盖（coverage） | `selected / stale / covers_period / reason` | `reconciliation.py` | 只取「当前 input_hash + confirmed + 覆盖期间」的最新批次；**任一合同不可用则整批 `unknown`**，不出局部差异 |
| 对账容差 | `TOLERANCE = 0.01` | `reconciliation_schemas.py` | 固定分位，不由界面传入；结论 `failed > unknown > ok` |
| 本行月数（每月=1） | period row months | `exporter.py::_PERIOD_HEADERS` | 导出期间表按月展开、每行代表 1 个月；**不是**源底稿 R 列月期总数（每期月数看评估表「月期数」） |
| 使用权资产净值 | net book value (derived) | `schemas.py::_export_payload`、`exporter.py` | 导出展示值 = 快照现值 − 累计已存折旧（含本月），逐月滚动；不重算折旧规则、不吸收尾差，差异按既有容差 |
| 租赁付款（整期） | `PeriodRow.lease_payment` | `calculation.py::_build_periods`、`MeasurePanel.vue` | 所属付款期的整期金额，期内每月重复展示、**不可逐月求和**；按月金额只有折旧/利息/应付（`payable` 仅付款前一月非 0） |

## 1.3 长期待摊 / 工会经费关键术语

| 术语 | 字段/来源 | 代码位置 | 说明 |
|---|---|---|---|
| 净额 / 税额 | `amount_net` / `tax_amount` | `finance_deferred/calculation.py` | 净额 = ROUND_HALF_UP(含税 ÷ (1+税率), 2)；税额 = 含税 − 净额 |
| 月摊 | `monthly_amount` | 同上 | 净额 ÷ 月数，两位 HALF_UP，每月等额 |
| 末期尾差 | `rounding_difference` | `checks.py`、月度行余额 | 净额 − 月摊 × 月数，**如实留在末期余额**：不吸收、不自动转销、不倒挤；只产出 `warning` |
| 超租赁期 warning | `expiry_after_lease` | `finance_deferred/checks.py` | 摊销到期月月末晚于租赁到期日只 `warning`，**不自动缩短摊销年限** |
| 风险确认 | `risk_acknowledged` / `risk_note` | `DeferredConfirmIn`、`mod_deferred_run` | 存在 `warning` 时确认必须勾选风险并填写非空说明，缺一 409 |
| 手工工资基数 | `current_wage_base` | `mod_union_line` | 人工录入，**不读 HR**；缺失（None）与显式 0 是两件事 |
| 上期差异 | `prior_variance` | `finance_union/calculation.py` | 上期实际 − 上期已计提；负数 = 应冲回 |
| 本期计提 / 本期建议入账 | `current_accrued` / `current_book_amount` | `mod_union_*`、导出 | 计提 = 工资基数 × 批次费率；建议入账 = 本期计提 + 上期差异；缺输入时三者均为 None |
| 批次费率 | `rate` | `mod_union_batch` | **唯一费率来源**（必填无默认，行级无覆盖字段） |

## 2. 执行/批次/任务

| 术语 | 含义 | 代码位置 | 不要混淆 |
|---|---|---|---|
| task | 待办任务 | `tasks` 表、`TasksView` | 不是定时任务，不是执行批次 |
| job | 定时任务 | `scheduled_jobs`、`routers/jobs.py`、`JobsView` | 不是一次执行 |
| run | 一次执行 | `mod_*_run`、`scheduled_job_run_facts` | 不是导入批次 |
| batch | 导入/凭证批次 | `mod_invoice_batch`、`mod_pos_voucher_batch`、`mod_receipt_batch` | 不是一次定时执行 |
| recon batch | 对账批次（工作底稿） | `mod_recon_batch` | 显式对账日/容差/合同范围；状态 draft→checking→ready/failed；不是导入批次 |
| pipeline | 看板四步外部管线 | `finance_board/service.py` | 不是 Agent 任务 |
| session | 聊天会话 | `agent_sessions` | 不是登录会话（本项目无登录） |

## 3. Agent / 知识 / 扩展

| 术语 | 含义 | 代码位置 | 说明 |
|---|---|---|---|
| Agent | 运行实体（模型 + 工具白名单 + 提示词） | `agents/orchestrator.py`、`agent_specs` | UI 常叫“专家” |
| 专家/expert | 预设人格 + 工具白名单 | `agents/team.py`、`seed_experts/`、`expert_packs/` | 与 Agent 运行实例区分 |
| 技能包/skill pack | `SKILL.md` + 资源 | `agents/skill_store.py`、`data/skills` | 不是 QwenPaw 插件 |
| 插件/plugin | QwenPaw 兼容层加载的工具型插件 | `agents/qwenpaw_compat.py` | 实验性；只读模式跳过 |
| 工具/tool | Agent 可调用的函数 | `agents/tools.py::TOOL_SPECS` | 不是 MCP 服务器 |
| MCP | 外部工具服务器（stdio） | `agents/mcp.py`、`mcp_servers` | 工具名 `mcp_<srv>__<tool>` |
| 长期记忆/memory | 跨会话事实/偏好 | `memories`、`agents/memory.py` | 不是笔记，不是文档库；可选 `source_doc_id` 显式引用知识库文档（非法 400、显式 null 清空、删文档置空），召回/注入带 `source_doc_title` |
| 知识库/knowledge | 制度/手册文档库 | `kb_docs`、`kb_chunks`、`knowledge/` | FTS5 + 可选 sqlite-vec + OCR |
| 笔记/notes | 用户随手笔记 | `notes` 表、`NotesView` | BM25 检索 |
| RAG | 检索增强 | `agents/rag.py`、`knowledge/retrieval.py` | 笔记 BM25 + 文档混合检索 |

## 4. 状态与口径

| 术语 | 取值 | 代码位置 | 说明 |
|---|---|---|---|
| 申报状态 | `calculated` / `submitted` / `exported` | 四税种 + CIT models | 已提交/已导出不可静默删改；更正走 PUT |
| 批次状态 | `uploaded` / `running` / `ok` / `failed` | invoice/POS/receipt | 失败要回收；不能卡在 running |
| 对账批次状态 | `draft` / `checking` / `ready` / `failed` | `mod_recon_batch` | ready 为审计留痕不可删（ever_ready）；ready 后改口径参数置 needs_recheck |
| 对账匹配 | `suggested` / `confirmed` / `rejected` | `mod_recon_match` | suggested 机检建议可重跑更新；confirmed/rejected 人工裁定冻结 |
| 对账问题 | `pending` / `confirmed` / `dismissed` / `resolved` | `mod_recon_issue` | + active 标记；重跑不再命中 → resolved；human 来源机检永不触碰 |
| 租赁合同状态 | `draft` / `archived` | `mod_lease_contract`、`schemas.py::CONTRACT_STATUSES` | archived 只读（付款计划、测算、确认全拒）；有测算历史删除 409 |
| 租赁测算状态 | `calculated` / `confirmed` | `mod_lease_calculation` | 仅 `calculated → confirmed`；已确认快照不可改写 |
| 租赁检查状态 | `ok` / `failed` / `unknown` | `schemas.py::CHECK_STATUSES` | 缺数据 `unknown`，不按 0 通过；`failed/unknown` 阻断确认与当前导出 |
| 定时任务运行 | `ok` / `error` / `skipped` / `unknown` | `scheduled_job_run_facts` | `unknown` = 进程中断/超时，不自动重试 |
| 任务优先级 | `low` / `medium` / `high` / `urgent` | `tasks` | — |
| 工作模式 | `readonly` / `standard` / `deep` | `schemas.py`、`orchestrator.py` | 非法值执行侧回退 `readonly` |
| 金额 | 两位小数，`ROUND_HALF_UP` | `utils_money.money` | 禁止 Python `round()` |
| 期间 | `YYYY-MM` | 四税种/报表 | 严格校验；13 月/空值 4xx |
| 税率准入门槛 | 5 admitted / 4 blocked | `finance_tax/config/tax_scope.py` | 房产税/城镇土地使用税/土增税/契税未准入 |

## 5. 常见混淆（一句话区分）

- **数据池 vs 数据仓**：同一个模块的旧/新显示名；代码/表名仍是 `vault`，模板文件名仍是“数据仓”。
- **银行回单 vs POS 凭证**：前者是银行回单整理（`finance_receipt`），后者是 POS 对账单+回单合并生成刷卡凭证（`finance_pos/vouchers_service`）。
- **报表看板 vs 工作台首页**：前者是 `finance_board`（外部管线），后者是 `DashboardView`（任务/日程/统计概览）。
- **企业账 vs 个人账**：`finance_ledger_bip` 是企业 BIP 账；`finance_ledger_personal` 是个人收支账。
- **税种模块 vs finance_tax**：`finance_tax` 是企业所得税/政策/共享；增值税/个税/印花税/残保金是独立模块。
- **Agent vs 专家**：Agent 是运行实例；专家是预设人格+工具白名单。
- **技能 vs 插件 vs 工具 vs MCP**：SKILL.md 技能包 / QwenPaw 插件 / Agent 工具 / 外部 MCP 服务器。
- **job vs run vs batch**：定时任务 / 一次执行 / 导入或凭证批次。
- **商户核销对账 vs POS 对账**：前者把外部商管系统的核销/认领/欠费/账单四源导入成对账工作底稿，机检+人工复核后出报告（`finance_recon`）；后者是银行侧 SHOP 对账单/明细账/认领/凭证四方对拍（`finance_pos`）。互不调用。
- **招商/营运的“租赁” vs 租赁核算**：`business_leasing` / `business_operations` 是**出租侧**（铺位、线索、出租合同、账单收款）；`finance_lease` 是**承租人侧**（使用权资产、租赁负债、折旧与利息）。两者不共享表、不互相调用。
- **机检建议 vs 人工裁定（对账）**：`suggested`/`pending` 可被重跑更新；`confirmed`/`rejected` 与人工说明冻结，重跑不得覆盖（INV-015）。
