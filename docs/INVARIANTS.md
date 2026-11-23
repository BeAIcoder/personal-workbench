# 不变量登记（Invariants）

> 台账回答“发生过什么”；本文件回答“什么永远不能发生”。
> 每条必须有：**陈述 + 测试/检查 + owner + 违反后果**。新增 P0/P1 修复时，先在这里加一行，再加测试。

| ID | 不变量 | 执行方式 | Owner | 违反后果 |
|---|---|---|---|---|
| INV-001 | 版本号只有 `backend/app/config.py` 一个源；`package.json`、`package-lock.json` 与文档声明必须一致 | `dev/check_contracts.py` + `test_engineering_contracts.py` | 发布 | 分发包版本错/升级误判 |
| INV-002 | 所有已声明模块必须成功加载；CI strict 模式不允许静默跳过 | `test_architecture_contracts.py::test_all_manifests_load` | 后端 | 菜单/路由/工具静默消失 |
| INV-003 | 金额统一 `Decimal(ROUND_HALF_UP)` 两位；新增金额路径禁止 `round()`（存量基线只减不增） | `dev/check_architecture.py`（存量基线，只拦新增） | 财务 | 金额差 1 分/申报错 |
| INV-004 | 日期 `YYYY-MM-DD`、期间 `YYYY-MM` 严格校验；非法值 4xx，不得回退当前期间 | 四税种/报表测试 + `check_contracts` 命名黑名单 | 税务 | 错期间写入/重复申报 |
| INV-005 | 已提交/已导出申报不可静默删改；删除企业不得级联删已提交申报 | `test_delete_enterprise_with_submitted_return_returns_409_and_keeps_return`、`test_delete_submitted_409_and_artifact_kept` | 税务 | 数据丢失/合规风险 |
| INV-006 | 银行账号在预览/导出边界必须掩码或令牌化；响应不得含完整账号 | `test_bank_preview_masks_account_and_token_is_stable`、`test_bank_masked_account_ambiguity_requires_token` | 财务 | PII 泄露 |
| INV-007 | 非法枚举/模式/编码/Key 不得静默回退到可写/当前/借用；返回 4xx 或 `unknown` | `test_jobs` mode 用例 + 代码审查 | 全栈 | 安全承诺失效/错数据 |
| INV-008 | 测试不得依赖真实盘符、真实目录名、固定端口、真实外部任务；非 client 用例自动建表 | `test_test_isolation_contract.py` + CI 干净 checkout | 测试 | CI 必红/换机失败 |
| INV-009 | 看板前端超时必须覆盖后端最坏预算（当前 1,385s < 1,500s） | `test_board_direct_budget_fits_frontend_timeout_1500s` + `check_contracts` | 看板 | 假失败/重复触发 |
| INV-010 | PII 在新增代码/文档/测试中零命中；历史快照、扫描器合成样例、已知 smudge 走显式白名单 | `dev/check_pii.py --tracked/--staged/--archive` + pre-push；白名单见复测记录 | 发布 | 公开仓库泄露 |
| INV-011 | 模块间不得直接 import 其他模块的 `models/router/service`，除非在 `requires` 声明并经公共函数 | `dev/check_architecture.py`（存量基线） | 后端 | 边界腐化/循环依赖 |
| INV-012 | 单文件软上限 800 行、硬上限 1,200 行；超限只减不增 | `dev/check_architecture.py`（基线 ratchet） | 全栈 | 不可维护 |
| INV-013 | 文档必须分层（用户/开发/历史/生成物）；当前事实标 as-of；历史不改写 | `docs/README.md` + 代码审查 | 文档 | 认知成本/假漂移 |
| INV-014 | 外部引擎/计划任务失败必须有可诊断错误（日志/快照/状态），不得只留超时 | 看板/引擎测试 + 运行手册 | 财务 | 无法排障/重复等待 |
| INV-015 | 商户核销对账机检重跑不得覆盖人工结论：`confirmed`/`dismissed` 问题与 `confirmed`/`rejected` 匹配冻结，human 来源问题机检永不触碰 | `test_finance_recon_engine.py` 重跑保持用例（人工裁定后重跑，状态/说明/分摊不变） | 财务 | 人工结论被静默推翻/审计失效 |
| INV-016 | 租赁核算三组恒等式必须成立：Σ月折旧 = 使用权资产原值、Σ应付 = 租赁付款额、Σ月利息 = 未确认融资费用；合计容差 1 元，不得倒挤末期、不得放宽容差 | `test_finance_lease_calculation.py::test_three_identities_hold_within_tolerance`、`::test_identity_checks_are_reported_as_ok`、`::test_reverse_identity_detects_two_yuan_error` | 财务（租赁） | 底稿勾稽假通过/差额被藏进末期 |
| INV-017 | 评估表 U 列实际利息 = 付款后余额 × 期间利率；期间表月利息必须 = U ÷ 该期计租月数（期内均摊），月折旧 = 原值 ÷ 总月数；不得用 S 列折现差额或全期统一摊 | `test_finance_lease_calculation.py::test_interest_uses_post_payment_balance`、`::test_monthly_interest_is_rounded_period_interest_over_months`、`::test_monthly_depreciation_is_h2_over_total_months` | 财务（租赁） | 利息/负债滚动与折旧错报 |
| INV-018 | 租赁核算缺数据必须 `unknown`，不得按 0 判 `ok`，也不得显示为通过；`failed/unknown` 阻断确认与当前导出（409） | `test_finance_lease_calculation.py::test_contract_range_mismatch_is_unknown`、`test_module_finance_lease.py::test_api_month_mismatch_and_unknown_block_confirm`、`test_finance_lease_io.py::test_export_missing_checks_is_unknown_not_pass` | 财务（租赁） | 缺数据被当成通过/错报 |
| INV-019 | 已确认快照不可改写；输入或规则版本变化后旧结果标过期（`stale`），确认与当前导出 409，历史快照导出须显式 `allow_stale=true` 并标注非当前结果 | `test_module_finance_lease.py::test_confirm_success_and_snapshot_immutable`、`::test_confirm_blocks_stale_failed_unknown_and_rule_change`、`::test_api_error_mapping_stale_and_delete_guard`；`test_finance_lease_io.py::test_export_stale_calculation_is_marked` | 财务（租赁） | 过期结果冒充当前/审计留痕失效 |
| INV-020 | 长期待摊末期尾差必经披露、不得暗吸：净额 − 月摊 × 月数如实留在末期余额（不吸收/不自动转销/不倒挤），超租赁期只 `warning` 且不缩短年限；存在 warning 时确认必须 `risk_acknowledged` + 非空 `risk_note` | `test_finance_deferred_calculation.py::test_monthly_residual_is_disclosed_not_absorbed`、`::test_expiry_after_lease_is_warning_and_years_not_shortened`；`test_module_finance_deferred.py::test_expiry_warning_requires_explicit_risk_note`、`::test_residual_warning_also_requires_ack` | 财务（长摊） | 尾差被隐藏/超期摊销冒充合规/风险无留痕 |
| INV-021 | 租赁 × BIP 对账严格只读且覆盖不完整不得出假差异：映射只写 `mod_lease_bip_map`、余额只经 BIP 公共只读适配、不写 BIP；只采用当前 input_hash + confirmed + 覆盖期间的最新批次；任一合同不可用整批 `unknown` | `test_finance_lease_reconciliation.py::test_put_mappings_replaces_scope_and_only_writes_own_table`、`::test_api_real_engine_check_and_bip_untouched`、`::test_one_unusable_contract_blocks_partial_sum`、`::test_stale_confirmed_calculation_is_excluded` | 财务（租赁） | 写坏 BIP / 局部求和出假差异 / 用过期批次对账 |
| INV-022 | 工会经费计提缺输入必须 `unknown`（不按 0），只有显式 0 才参与计算；费率唯一来自批次；确认后批次只读 | `test_finance_union_calculation.py::test_missing_amount_is_unknown_not_zero`、`::test_explicit_zero_wage_base_is_computable`、`::test_rate_comes_from_batch_only`；`test_module_finance_union.py::test_confirm_then_batch_is_read_only` | 财务（工会） | 缺数据被当 0 计提/费率漂移/确认后被改 |

| INV-023 | 知识检索 RRF 融合**每通道每文档只取最佳 rank**：同一文档多块命中不得重复累加 `1/(60+rank)`；展示证据块按关键词/向量两通道 rank 择优（并列时关键词优先），不得用关键词 rank 更差的块覆盖向量最佳证据 | `test_knowledge_retrieval_ranking.py::test_rrf_does_not_accumulate_multi_chunks_of_same_doc`、`::test_rrf_uses_best_keyword_rank_across_docs`、`::test_display_chunk_prefers_better_vector_rank_over_worse_keyword_chunk` | AI（知识） | 多块长文档堆分压制单块最佳文档 / 展示证据与结论来源不符 |
| INV-024 | 知识库解析正文不得丢失且页码必须准确：PPT 组合形状与表格单元格、跨页章节正文都要进索引正文；切块在跨页处 flush 并保留页码映射；句式编号行（含句号/叹号/问号）按正文处理，不得当标题吞掉 | `test_knowledge_extraction_pages.py::test_pptx_group_shapes_and_table_cells_extracted_with_slide_no`、`::test_cross_page_section_keeps_page_mapping`、`::test_pdf_section_spanning_pages_keeps_both_pages`、`::test_docx_numbered_body_sentence_stays_text`、`::test_chunk_treats_sentence_like_heading_block_as_body` | AI（知识） | 命中块错页 / 正文缺失导致引用不可核对 |
| INV-025 | 知识问答接地：检索结果没有明确支持时，不得先答「可以/能」也不得给无来源的操作步骤，须答「知识库未收录，无法确认」并列出缺项；补充一般常识必须与知识库结论分开标注；口径冲突时并列披露各口径原文与出处、说明差异，不得自行替用户选定口径（含不选「字段更全」的那个）。规则经 `compose_system_prompt` 统一组装到所有专家 | `test_knowledge_grounding_prompt.py::test_capability_questions_need_retrieval_support`、`::test_conflicting_statements_disclosed_side_by_side`、`::test_grounding_rules_apply_to_all_modes`、`::test_compose_entry_matches_built_agent` | AI | AI 凭常识编造制度/操作步骤、擅选口径误导业务判断 |
| INV-026 | 记忆来源引用必须有效且可溯源：`source_doc_id` 非法/不存在返回 400，显式传 `null` 才清空引用（未触碰该字段的更新保持既有引用）；删除知识库文档时引用自动置空（记忆本体保留，不出孤儿 doc id）；列表/召回返回 `source_doc_title`，会话注入展示「（来源：《…》）」；系统不自动从文档生成记忆 | `test_memory_source_recall.py::test_memory_source_doc_invalid_id_rejected`、`::test_doc_delete_clears_memory_reference`、`::test_injection_hits_carry_source_doc_title`、`test_knowledge_grounding_prompt.py::test_memory_hit_with_source_doc_title_shows_source` | AI（记忆） | 记忆指向已删文档 / 无法溯源 / 来源引用静默漂移 |

## 使用规则

1. **新增 P0/P1 修复**：先写不变量陈述，再写一个会失败的测试，再修。
2. **不允许**：为了让测试过而 skip/删断言/降阈值；这会直接违反本表。
3. **基线 ratchet**：存量违反进 `dev/architecture-baseline.json`；清理一项，基线减一项；只减不增。
4. **复审**：每个发布周期检查一次“是否有不变量没有测试/owner”。

## 与台账的关系

- `BUGfix.md` / `2026-11-11_检查结果.md`：症状与修复证据。
- 本文件：跨轮次仍必须成立的事实；同一不变量再次被违反时，不新建编号，直接指向本表 + 失败测试。
