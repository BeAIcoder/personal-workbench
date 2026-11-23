# BUGfix.md — 全模块 / 全功能缺陷诊断台账

> 生成时间：2026-10-30
> 诊断对象：`示例主体13\01-财税自动化\personal-workbench`（git master，工作区含未提交改动）
> 覆盖范围：`backend/app` 161 个 Python 文件（33,455 行）、`frontend/src` 96 个 Vue/JS 文件（13,927 行）、129 个测试文件、`dev/` 工具链、6 个 bat/vbs 脚本、打包发布管线
> 诊断方式：8 个只读审计域并行精读 + 关键结论人工复核（代码原文 + 只读命令 + 聚焦测试）；未启动指向真实 `backend/data` 的服务，未联网，未修改任何业务代码

## 0. 阅读与使用说明

### 0.1 严重度定义

| 级别 | 定义 | 处置时限建议 |
|---|---|---|
| **P0** | 会直接导致**错税、少缴/多缴、敏感数据外泄**，且正常操作路径可达 | 立即修，修完单独回归 |
| **P1** | 功能结果错误、静默丢数据、安全承诺与实现不一致，用户可感知但可能不立即发现 | 本迭代必修 |
| **P2** | 特定边界下的错误/500、可恢复性差、状态残留、明显体验缺陷 | 排期修 |
| **P3** | 代码质量、文档漂移、死代码、可移植性、测试盲区 | 顺手修/登记跟踪 |

### 0.2 状态标记

- `[确认]`：审计员给出代码原文 + 行号，且经复核或可静态确定
- `[待确认]`：可疑但依赖外部事实（政策原文、真实数据形态、第三方库行为），修前需二次确认
- `[设计如此]`：看起来像缺陷，但属于已知设计取舍，单列在第 19 章澄清

### 0.3 缺陷总览

| 域 | P0 | P1 | P2 | P3 | 小计 |
|---|---:|---:|---:|---:|---:|
| 平台核心 + routers | 0 | 0 | 6 | 23 | 29 |
| 智能体 / 助手（agents） | 0 | 1 | 4 | 19 | 24 |
| finance_tax | 2 | 7 | 15 | 9 | 33 |
| finance_ledger_bip | 1 | 1 | 5 | 5 | 12 |
| finance_ledger_personal | 0 | 0 | 6 | 6 | 12 |
| finance_vault | 0 | 0 | 3 | 3 | 6 |
| finance_invoice | 0 | 0 | 3 | 2 | 5 |
| finance_expense | 0 | 0 | 1 | 2 | 3 |
| finance_pos | 0 | 0 | 1 | 3 | 4 |
| finance_board | 0 | 0 | 1 | 4 | 5 |
| finance_cashflow | 0 | 0 | 2 | 3 | 5 |
| finance_stamp_duty / vat / iit / dsf | 1 | 2 | 7 | 7 | 17 |
| finance_hello / _template / debug_center | 0 | 0 | 0 | 5 | 5 |
| 前端（Vue/JS/测试） | 0 | 2 | 8 | 12 | 22 |
| 工具链 / 打包 / 测试体系 | 1 | 3 | 13 | 16 | 33 |
| **合计** | **5** | **16** | **75** | **119** | **215** |

### 0.4 十项必修（按风险排序）

1. **P0-01 分发包带真实客户/员工名称与内部路径**（工具链）— 对外分发即泄密
2. **P0-02 企业所得税保存走自造 5%/10% 税率阶梯**（finance_tax）— 落库税额与页面展示两套口径
3. **P0-03 A200000 小微判定只看人数/资产 → 任意规模按 5% 出表**（finance_tax）
4. **P0-04 残保金 <1% 分档按 10% 征收（法定 90%）**（finance_dsf）— 少征约 9 倍，测试还固化了错值
5. **P0-05 BIP 凭证按（账簿, 凭证号）聚合、期间不参与 → 多月凭证被合并/跨月重导被误判重复**（finance_ledger_bip）
6. **P1-01 个税累计预扣法逐月 `max(0, …)` 截断**（finance_iit）— 低薪月份不能冲减，系统性多扣
7. **P1-02 只读模式仍注入 Bash**（agents）— 安全承诺失效
8. **P1-03 企业档案员工数/资产/限制行业前端可填、后端丢弃**（前后端契约）— 放大 P0-03
9. **P1-04 增值税进项税额转出符号相反**（finance_tax）— 少计 2×转出额
10. **P1-05 备份只含 workbench.db，财务/附件/技能/专家包全丢**（工具链）

### 0.5 本轮修复记录（2026-10-30）

> 诊断完成后按「先修会算错钱/会泄密的」原则修了一批；未修的条目保留在正文，按第 17 章路线图排期。

| 编号 | 状态 | 修复内容 | 验证 |
|---|---|---|---|
| TAX-01 | ✅ 已修 | `calculate_tax` 删除 5%/10% 自造阶梯，改为复用引擎（小微 5%/高新 15%/其余 25%），并接 `tax_year` 政策有效期（过期中文 400） | `test_bugfix_p0_20261030.py` 3 例 |
| TAX-02 | ✅ 已修 | `_is_small_micro` 接 `check_small_micro_enterprise`（300 万所得/300 人/5000 万资产/限制行业），字段缺失返回「无法判定」 | 同文件 1 例 |
| TAX-03 | ✅ 已修 | A200000 第 5 行删除重复相加的 `income_reduction_amount` | 代码复核（该路径暂无路由入口） |
| TAX-06 | ✅ 已修 | 增值税一般纳税人进项转出改 `+`（销项 − 进项 + 转出），docstring 同步 | 同文件 1 例（13/0/10 → 23） |
| TAX-15 / FE-01 | ✅ 已修 | `EnterpriseCreate/Update` 补 `industry_type/employee_count/total_assets/is_restricted_industry`；创建/更新落库；响应字段给默认值防历史 NULL 500 | 同文件 1 例 + PUT 往返 |
| FIL-01 | ✅ 已修 | 残保金 <1% 改「按应缴费额 90% 缴纳」，文案改「减收 10%」；**同步修正 2 条固化错值的测试** | 100 人/1000 万 → 135,000 |
| FIL-02 | ✅ 已修 | 个税累计预扣改「累计数相减」，负累计可冲减；本期为负时置 0 且保留已预扣 | 同文件 1 例（1 月 2 万 + 2 月 3 千 → 390） |
| BIP-01 | ✅ 已修 | 凭证聚合/判重键改 `(账簿, 期间, 凭证号)`，期间写入正确；不平衡提示带期间 | `test_module_finance_ledger_bip.py` 新增多月同号用例 |
| AGT-01 | ✅ 已修 | 只读模式不再注入 Bash（`if cfg.get("enable_bash") and mode != "readonly"`），与「写入类工具已关闭」承诺一致 | `test_bugfix_agent_core.py::test_readonly_mode_does_not_inject_bash`（只读无 Bash / 标准模式有 Bash） |
| TL-01 | ✅ 代码已修 | 打包递归剔除 `dev/`、`backend/tests/`、`frontend/tests/`；新增真实名称反查（`dev/pii_terms.py` 从 `pii-terms.local.txt` / 映射表 / 环境变量加载，**真实值不入库**；本报告已同步脱敏） | 编译通过；词表加载 25 条；**待下一次 `打包发布.bat` 实测**（当前工作区有改动，预检要求 git 干净） |
| TL-02 | ✅ 代码已修 | 扫描器改 UTF-8 → GB18030 → UTF-16 回退，GBK 的 bat/vbs 不再跳过 | 同上，待打包实测 |
| — | ✅ 新增能力 | `spec.py` 允许 `/api/business/` 前缀（经营/职能模块命名空间），`/api/other/` 仍拒绝 | 既有 `test_manifest_validate_rules` 仍通过 |

**回归结果（本轮）**：
- 后端全量 `pytest tests -q`：**1281 passed**（594.72s，日志 `backend/.pytest_tmp/full-20261030-bugfix.log`）。该轮在最后 1 处一行修复（AGT-01）与五模块专家工具预置之前启动，因此收尾改动另跑聚焦 **94 passed**（`test_bugfix_agent_core` + `test_agent_specs` + `test_modules` + 五个业务模块）。
- 前端 `npm test`：**51 文件 820 passed**；`npm run build`：通过（9.76s，五个新视图已生成独立 chunk）。
- 本轮新增测试：P0 回归 12 例、BIP 多月同号 1 例、五模块 28 例、AGT-01 1 例，共 42 例。

### 0.6 第二轮修复（2026-10-30，剩余 P1 + 真实申报数据对拍）

| 编号 | 状态 | 修复内容 | 验证 |
|---|---|---|---|
| FIL-03 | ✅ 已修 | 残保金增加 `local_avg_wage`，按「当地社平×2」封顶；未传社平显式 warning；差额人数改 Decimal 两位（0.345→0.35） | 真实测算表对拍 + 高薪封顶用例 |
| TAX-07 | ✅ 已修 | `LossCarryForward` 增加 `tax_year`/`carryforward_years`；超过 5 年（高新 10 年）的亏损不再弥补，返回 `expired_losses` | 2025/2026 真实亏损序列用例 |
| TAX-08 | ✅ 已修 | 小微判定改用**弥补亏损后**的应纳税所得额 | 利润 400 万−亏损 200 万 → 小微 10 万 |
| TAX-09（部分） | ✅ 已修 | 企业所得税已有年度校验；VAT 1% 与 30/10 万免征均接政策有效期 | 过期年度中文拒绝用例 |
| FIL-04 | ✅ 已修 | 小规模征收率白名单 `{1%,3%,5%}`，负值/任意值拒绝 | 负征收率拒绝用例 |
| FIL-05 | ✅ 已修 | 增加 `filing_period`（月/季）：月 10 万、季 30 万；按季申报校验季末月 | 月/季门槛与季末月用例 |
| FIL-06 | ✅ 已修 | 一般纳税人增加进项转出（+）、期末留抵 `carryforward_vat` 与中文 warning | 留抵用例 + 真实 9 月申报表 |
| FIL-07 | ✅ 已修 | 免征判定同样走 `vat_small_scale_exempt10w` 政策有效期 | 过期年度拒绝 |
| FIL-13 | ✅ 已修 | 印花税/VAT 所属期正则收紧到 01-12 | 422 用例 |
| FIL-14 | ✅ 已修 | 四填报模块分页 `page≥1`、`page_size≤200` | 契约 |
| 印花税 | ✅ 已修 | 税目名支持带/不带括号（「买卖合同」命中「买卖合同（动产买卖）」） | 真实 Q3 申报表 6 税目 |
| POS-01 / CF-01 | ✅ 已修 | 执行加进程内互斥；running 超过 2 小时自动置 failed（可重跑） | 既有运行/失败路径回归 |
| BRD-01 | ✅ 已修 | 以管线 `logs/run.lock` 新鲜度判运行中；`schtasks` 加 30s 超时 | 既有看板测试 |
| AGT-03 | ✅ 已修 | 技能包先装临时目录再原子替换；停用标记失败回滚，绝不丢旧包 | 安装成功路径回归 |
| AGT-04 | ✅ 已修 | 启动时调用 `migrate_seal_keys()`，存量明文 Key 一次性 DPAPI 迁移 | 启动日志 |
| AGT-05 | ✅ 已修 | 测试/多模态探测禁止跟随重定向，3xx 直接失败 | 既有探测测试 |
| CORE-06 | ✅ 已修 | 新增 `ALLOW_NON_LOOPBACK`（默认 0）；按 `scope["server"]` 实际绑定地址拦截非回环 `/api`（健康检查除外），503 中文提示 | 中间件单测（0.0.0.0→503、127.0.0.1→200） |
| TL-03 | ✅ 已修 | 备份新增 `workbench_data_*.zip`（DB 快照 + uploads/finance/skills/expert-packs/debug + 根 JSON）；新增 `restore_backup.py`（防 zip-slip、覆盖前留 `.before_restore_*`） | 备份/恢复往返单测 |
| TL-10 | ✅ 已修 | `requirements.txt` 显式加入 `PyYAML`、`python-multipart` | 依赖清单 |
| FE-02 | ✅ 已修 | 发票批次上传显式 120s 超时，避免 10s 误报后重复建批次 | 前端契约 |
| CORE-03/04/05 | ✅ 已修 | 日程/定时任务显式 null → 422；更新后校验「提示词或模块动作至少一个」 | 两条例外用例 |
| INV-02 | ✅ 已修 | 冲红配对金额不可解析返回 None，不再 0==0 误配 | 配对单测 |
| INV-03 | ✅ 已修 | 失败批次清理 inbox；新增 `DELETE /batches/{id}`（明细+inbox+导出 ZIP） | 路由契约 |
| TAX-17 | ✅ 已修 | 同一企业同年度/季度/类型重复保存 → 409 中文提示（应用层查重；DB 唯一约束需单独迁移，暂未加） | 路由契约 |
| TAX-18 | ✅ 已修 | Q4 预缴所属期由 01-01~12-31 改为 10-01~12-31 | 表单用例 |
| FE-05 | ✅ 已修 | 优惠政策标签改读 `name/policy_basis` 并显示减免额 | 代码复核 |
| FE-07 | ✅ 已修 | POS/现金流执行失败改用 error 样式提示，不再绿色「成功」 | 代码复核 |

**第二轮回归**：真实数据对拍 7 例 + P1 回归 5 例 + 受影响模块 157 例全绿。后端全量 `pytest tests -q`：**1290 passed / 5 failed（607.96s）**，5 个失败全部定位并已修复——2 个是日程 null 文案断言（改成中文「结束时间/开始时间不能为空」后通过）、3 个是模型图片探测测试仍 patch `urllib.request.urlopen` 而生产代码已改走不跟随重定向的 `_probe_urlopen`（把测试接缝改到 `config_store._probe_urlopen` 后通过）。修复后复跑相关套件 **87 + 41 passed**。日志 `backend/.pytest_tmp/full-20261030-p1.log`。

### 0.7 第三轮修复（2026-10-30，清空「仍未做」清单）

| 编号 | 状态 | 修复内容 | 验证 |
|---|---|---|---|
| AGT-02 | ✅ 已修 | `orchestrator.session_run_lock(sid)`：同一会话 chat/chat_stream 串行；非流式被占用 → 409，SSE → 流内 error；配置失效只清未持有锁，会话删除清锁 | 并发锁单测（start-a/busy-b/end-a）+ 清理断言 |
| FIL-08 | ✅ 已修 | 个税行新增 `employee_id`，按工号分组；同名不同工号不再合并累计 | 同名两员工各 450 元（合计 900）用例 |
| FIL-09 | ✅ 已修 | 新增 `employment_start_month`；减除费用按「任职月份数 × 5000」，缺月不警告但不漏扣；重复月份拒绝；前端加「工号/证件号」「任职起始月」列并展示 warnings | 缺 2 月用例（累计 25,000）、重复月拒绝 |
| TL-04 | ✅ 已修 | `sync_zcode_models.py` 写入 `seal(api_key)`、探测前 `unseal`，不再把明文 Key 落库 | 代码复核 + 既有 config_store 密封测试 |
| FE-03 | ✅ 已修 | 7 处硬编码 `/api/...` 下载/iframe 地址统一改 `${api.defaults.baseURL}/...`，`VITE_API_BASE` 生效 | 前端构建 + 契约测试 |
| FE-04 | ✅ 已修 | BIP 列映射去掉 `el-select #prepend`（不存在的插槽），改为左侧字段名 + 右侧下拉 | 代码复核 |
| FE-06 | ✅ 已修 | 印花税/VAT/IIT/残保金删除加 `ElMessageBox.confirm`（显示期间/年度） | 代码复核 |
| FE-10 | ✅ 已修 | 企业表单提交锁移到 `await validate()` 之前；问题记录加 `if (saving) return` | 代码复核 |
| TL-11 | ✅ 已修 | conftest 新增 autouse `_isolate_database`：凡请求 `client`（含依赖它的夹具）即 drop/create；7 个自覆写 client 的文件也隔离 | 受影响 8 文件 129 passed |
| TL-12 | ✅ 已修 | conftest 把 `UPLOAD_DIR`、`skill_store.DATA_DIR`、`expert_packs.DATA_DIR` 重定向到临时目录，未显式 patch 的用例也不写真实 data | 同上 |
| TL-13 | ✅ 已修 | `config.py` 支持 `WORKBENCH_ENV_FILE=""` 禁用 .env；conftest 设置后测试不再读开发者本机 .env | `test_config_paths` 等通过 |
| TL-01 | ✅ 已修（测试侧） | 新增 `test_release_scan.py`：合成 staging 验证剔除 dev/两个 tests/根级 md、GBK bat 参与扫描、命中词表硬失败 | 3 例通过 |

> 至此第二轮列出的「仍未做」已全部关闭。原报告中的 P2/P3（前端响应式/键盘可达、CORE-13～17 调度器细节、TAX-10～14 等）仍按第 17 章路线图排期，不在本轮范围。

**第三轮回归**：后端全量 `pytest tests -q` **1302 passed（723.07s，exit 0）**；前端 `npm test` **51 文件 820 passed**；`npm run build` 通过。日志 `backend/.pytest_tmp/full-20261030-final.log`。

---

## 20. 真实申报数据对拍（2026-10-30）

> 数据来自本机只读归档目录（`<本地归档>/11.税务申报附件`），**只读**；仓库测试只保留数字与口径，不含公司名、税号、人名。

### 20.1 印花税：2026Q3《财产和行为税纳税申报表》

| 税目 | 计税依据 | 申报表税额 | 本系统计算 |
|---|---:|---:|---:|
| 技术合同 | 193,600.00 | 58.08 | 58.08 |
| 买卖合同 | 37,840.30 | 11.35 | 11.35 |
| 承揽合同 | 420,002.60 | 126.00 | 126.00 |
| 建设工程合同 | 360,085.18 | 108.03 | 108.03 |
| 租赁合同 | 7,240,028.10 | 7,240.03 | 7,240.03 |
| **合计** | | **7,543.49** | **7,543.49** |

结论：13 税目税率表、千分比换算、四舍五入与申报表**逐项一致**；同时发现并修复「买卖合同」不带括号时无法命中税目表的问题。

### 20.2 增值税：2026-09《增值税及附加税费申报表（一般纳税人）》

| 项目 | 申报表 | 本系统 |
|---|---:|---:|
| 销项税额 | 224,842.61 | 224,842.57（按税目汇总取整，差 0.04） |
| 进项税额 | 27,479.37 | 27,479.37 |
| 应纳税额 | 197,363.24 | 197,363.20（差 0.04） |
| 城建税（7%） | 13,815.43 | 13,815.43 |
| 教育费附加（3%） | 5,920.90 | 5,920.90 |
| 地方教育附加（2%） | 3,947.26 | 3,947.26 |

结论：公式与附加税率正确；差异来自申报表按发票逐张取整、本系统按税目汇总取整（0.04 元/22 万元），已在测试中按 ±0.10 元容差锁定并注释说明。

### 20.3 企业所得税：2025 年度 A106000 亏损结转

真实数据：2019 年亏损 415.06 万、2020 年 292.45 万、2021 年 284.87 万；2025 年弥补 101.15 万，申报表**未使用 2019 年亏损**（距 2025 已 6 年）；2026Q3 弥补 257.95 万，应使用 2021 年（2020 年已过期）。

本系统修复后：2025 年 `expired_losses={2019:415.06}`、`used_by_year={2020:101.15}`；2026 年 `expired_losses={2020:191.30}`、`used_by_year={2021:257.95}`——与申报表口径一致。修复前会先补 2019 年，结转明细错误（税额可能因亏损充足而不变，但 A106000 会错）。

### 20.4 残保金：2026 年度税局口径测算表

真实数据：23 人、应发工资 2,631,736.68 元、0 名残疾人、云南比例 1.5%、当地社平 87,156 元。
- 年平均工资 114,423.33；社平 2 倍上限 174,312（未触发封顶）
- 差额人数 0.345 → **0.35**（税局表按两位小数）
- 优惠前应纳 0.35 × 114,423.33 = **40,048.17**
- 30 人（含）以下 → 免征，应缴 0，但仍须零申报

本系统输出与之逐项一致；并补上原缺失的「社平 2 倍封顶」参数（高薪企业不再多缴）。

### 20.5 未能对拍/待确认

- 2022 年及以前年度申报表为扫描件（无文本层），未做 OCR；未纳入逐项对拍。
- 个税：存档中无逐人工资明细（仅汇总与截图），个税模块仍以合成用例验证公式；建议后续用工资表逐人复核。
- 业务招待费「无收入场景」、印花税「不足一角免征」仍需主管税务机关口径确认（见 §12.6）。
- 增值税 2026 年 10 月起《增值税法》施行后的征收率/起征点表述，本次未联网核对。

---

## 1. 平台核心层（main / database / config / models / utils / scheduler / routers）

### 1.1 缺陷清单

| 编号 | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| CORE-01 | P2 | `backend/app/scheduler.py:245-279,306-308` | 调度器在事件循环协程里直接做同步 SQLite 读写（`SessionLocal()`/`commit()`） | SQLite 写锁竞争时 `commit` 等满 5s timeout，期间**全部 HTTP 请求停摆**（含静态页）；违背项目既有 A05「慢同步活必须 offload」契约 | 调度循环与 `_record` 的 DB 访问包 `asyncio.to_thread`，或改短事务 |
| CORE-02 | P2 | `backend/app/scheduler.py:293-296`、`database.py:33` | 到期任务无并发上限，`run_job` 从 `db.get` 到 `_record` 全程持有会话；实测连接池 QueuePool(5,10) | ≥15 并发任务或任务+请求即 `pool_timeout` 30s；异常分支再开会话也拿不到连接，任务静默消失且 `last_status` 不更新 | 加并发信号量（建议 3），长任务先落 running 后释放会话 |
| CORE-03 | P2 | `backend/app/routers/schedules.py:71-82`、`schemas.py:92-99` | `ScheduleUpdate` 所有字段可 None，`setattr` 直接写非空列 | `PUT /api/schedules/{id}` 传 `{"title": null}` → IntegrityError 500（tasks/notes 已有防护，schedules 漏） | 复用 tasks 的 `_clean_update` 口径 |
| CORE-04 | P2 | `backend/app/routers/jobs.py:35-57`、`schemas.py:350-361` | `ScheduledJobUpdate` 可显式 null 写非空列 | `{"name": null}` → 500 | 同 CORE-03 |
| CORE-05 | P2 | `backend/app/routers/jobs.py:38-55`、`schemas.py:323-343,363-373` | 更新不做「prompt 与 action 至少一个非空」不变量校验；`ScheduledJobOut` 复用同一 validator | 一次 `{"prompt": ""}` 让该行永久不合规，`GET /api/jobs` 整表 500，且到点用空提示词跑 LLM | Update 侧对合并后结果做跨字段校验；输入/输出模型分离 |
| CORE-06 | P2 | `backend/app/main.py:86-91,347-349`、`routers/mcp.py:103-108,130-137` | 非回环绑定警告只读 `settings.host`，与实际 `--host` 参数脱钩；`/api/mcp/servers` 新增+连接无需凭证，无 Origin 的客户端直接放行 | 绕过 bat 以 `--host 0.0.0.0` 启动时：局域网任意主机可新增/连接 MCP server = 宿主机任意命令执行 | 启动校验真实绑定地址；非回环时拒绝启动或要求显式确认开关 |
| CORE-07 | P3 | `backend/app/main.py:324-330,333` | `_origin_allowed(origin, host_header)` 的 `host_header` 从未使用；`_split_host_header` 无调用方 | 签名误导维护者以为仍在做 Host 互证 | 删除死参数/死函数 |
| CORE-08 | P3 | `backend/app/main.py:211-217,340` | CORS 来源整串精确比较；未拒绝 `*` + `allow_credentials=True` | 配置 `*` 时 Starlette 回显 Origin + credentials，等价放开任意来源写操作，OriginGuard 白名单同时失效 | 启动时用 urlsplit 规范化并拒绝 `*`/尾斜杠/路径 |
| CORE-09 | P3 | `backend/app/main.py:347-349`、`config.py:19-20` | `HOST=0.0.0.0` 时浏览器 Origin 永远不含 `0.0.0.0` | 局域网访问下所有 POST/PUT/DELETE 被 403，且无配置入口 | 文档明确须同时配 CORS_ORIGINS，或对 0.0.0.0 特判 |
| CORE-10 | P3 | `backend/app/main.py:166-176` | `_ensure_sqlite_columns` 全部表/列共用一个 try/事务，且未按方言门控 | 任一 ALTER 失败 → **本轮所有补列一起回滚**，只留一条 warning；非 SQLite 库先撞 `PRAGMA` 语法错再整批放弃 | 逐表逐列 try（对齐 `_ensure_sqlite_indexes`），入口判断 sqlite |
| CORE-11 | P3 | `backend/app/main.py:113-114`、`scheduler.py:293-296` | 停机只 cancel 调度协程，不等待/不取消已派生 `run_job` | 停机时任务仍在写 note/commit，随后 MCP 已被拆；进程退出报 pending task，cron 下次启动补跑（at-least-once） | 记录派生任务集合并 gather 取消 |
| CORE-12 | P3 | `backend/app/main.py:456-457` | 静态托管在 import 时判定一次；`/api` 未匹配路径落到 StaticFiles | 启动后构建 dist 需重启；`GET /api/typo` 返回 `text/plain: Not Found` 而非 JSON 404 | 给 `/api/{path:path}` 加 JSON 404 兜底 |
| CORE-13 | P3 | `backend/app/scheduler.py:198-210`、`schemas.py:367` | `ScheduledJob.last_error` 从不写入 | API 恒返回空串，错误只能从 recent_runs 看 | status=error 时写 last_error，成功清空 |
| CORE-14 | P3 | `backend/app/scheduler.py:249-254` | 模型未配置导致 skipped 时仍推进 `last_run_at` | daily 任务最长要等 24h 才重跑 | skipped 不更新 last_run_at |
| CORE-15 | P3 | `backend/app/scheduler.py:130-151` | cron 的「日」与「周」实际为 AND（标准为 OR），docstring 自相矛盾；`sunday = 7 if weekday == 7 else weekday` 为空操作 | `0 9 1 * 1` 只在「同为周一的 1 号」触发，与用户预期不符 | 改 OR 语义，修正注释，删空操作 |
| CORE-16 | P3 | `backend/app/scheduler.py:154-177` | 非法 cron 静默永不到期，每 tick 回溯 1440 次 | 脏表达式任务永不触发且无报错 | 先用 `validate_cron_expr` 判非法并记录 |
| CORE-17 | P3 | `backend/app/scheduler.py:249` | 手动「立即运行」不更新 daily/cron 的 `last_run_at` | 到达时刻前后手动跑一次，tick 会再自动跑一次（重复 LLM 消耗） | 记录「本窗口已消费」标记 |
| CORE-18 | P3 | `backend/app/routers/knowledge.py:81,91` | 记忆更新/删除用裸 `int` 而非 `DbId`；knowledge 校验失败返回 400，其它 CRUD 用 422 | 超 int64 依赖全局 OverflowError 兜底；状态码口径分裂 | 统一 `DbId` 与 422 |
| CORE-19 | P3 | `backend/app/routers/search.py:77` | `total` 是本次返回条数（每类 limit 5，最多 15），不是命中总数 | 前端用它判断「还有更多」永远 false | 改名 `returned` 或补真实 count |
| CORE-20 | P3 | `backend/app/routers/search.py:18-24` | 用小写串下标切原文 | `İ`/`ẞ` 等大小写长度变化时片段错位（中文不受影响） | 用 `re.search(..., IGNORECASE).span()` 在原串取 |
| CORE-21 | P3 | `backend/app/routers/notes.py:23-30` | `/notes/tags` 全表加载所有笔记 tags JSON 去重 | 笔记量大时线性变慢，无上限 | 限制扫描/缓存/建标签表 |
| CORE-22 | P3 | `backend/app/routers/dashboard.py:27-31` | `total_tasks` 只汇总 todo/in_progress/done | 脏状态时总数偏小、完成率失真，与列表 total 不一致 | `sum(status_counts.values())` |
| CORE-23 | P3 | `backend/app/seed.py:10-44` | `seed_agent_specs` 会复活用户已删除的内置专家（无墓碑），与 docstring 相反 | 删除内置专家后下次启动自动回来 | 加删除墓碑，或按「仅首次灌入」语义 |
| CORE-24 | P3 | `backend/app/init_db.py:13-30` | `--reset` 只 drop/create 12 张核心表，模块表原样保留，也不跑 seed_agent_specs | reset 后财务模块数据仍在，可能跨表悬挂 | 先 import 模块 models/load_modules 再 drop/create |
| CORE-25 | P3 | `backend/app/document_text.py:78-92` | PDF 提取只限页数与字符数，无解压体量护栏（OOXML 有 64MB 兜底，PDF 无） | 10MB PDF 可携带高压缩内容流，单页解压达数百 MB/高 CPU，截断发生在解压之后 | 解析前限制对象数/流长度，或放子进程限内存 |
| CORE-26 | P3 | `backend/app/schemas.py:315,350-361`、`models.py:241-244` | `mode` 无 Literal/长度校验 | 任意字符串落库并透传编排器，行为静默退化 | `Literal["standard","readonly","deep"]` |
| CORE-27 | P3 | `backend/app/models.py:14-16,36` | `Task.created_at` 在 mixin 与类体重复定义 | 两处 default 重复、索引语义藏在一处 | 删 mixin 版本或显式注释 |
| CORE-28 | P3 | `backend/app/models.py:130,185,217,240,255`、`database.py` | 全库零 ForeignKey，`PRAGMA foreign_keys` 未开启 | 删 provider/model/note 后留下悬挂引用（仅 notes 手工清理 last_note_id） | 能加外键的加 `ondelete=SET NULL` 并开 pragma（需迁移） |
| CORE-29 | P3 | `backend/app/utils.py:116-131,91-94` | `repair_upload_mojibake` 启发式可能误改合法混合文件名；`find_sibling_project` 不检查 start 自身是否锚点 | 文件名被改成另一串中文导致引擎匹配失败；start 恰为锚点目录时返回 None | 加可逆校验；先判 `resolved.name == anchor` |

### 1.2 已验证正确的点（抽样）

- SQL 注入面：过滤全部参数绑定；排序走 `SORTS` 白名单；LIKE 全部 `autoescape=True` / `escape_like` / `tag_like_pattern`（在内存库实测 `tags LIKE '%"报销"%'` 正常命中）
- 分页边界：tasks/notes/schedules 均 `page ge=1`、`page_size le=100/200`
- ID 越界：核心 CRUD 走 `DbId`，全局 `OverflowError→422` 且响应不含堆栈
- Origin 校验：`Origin: null` → 403、外站 → 403、无 Origin 放行 CLI、回环任意端口；403 不带 ACAO 有测试锁定
- 上传：`read_upload_limited` 分块限额、超限即停；413 只发一次且丢弃应用后续响应
- OOXML：先校验成员数/声明解压总量再解析；soffice 用 argv 列表（无 shell）+ 60s 超时 + 输出路径 `relative_to` + `mkstemp+replace`
- 迁移顺序 `create_all → 补列 → 补索引` 正确；索引逐条 try 隔离失败
- 后端不渲染用户 HTML：无 `HTMLResponse/TemplateResponse`，`v-html` 在 backend 零命中

### 1.3 未能确认项

- PDF 压缩炸弹实际放大倍数（依赖 pdfplumber 内部实现，未读第三方源码）
- 真实并发下 SQLite 锁竞争频率（未启动服务、未压测）
- 多 worker 部署是否会发生（启动脚本为单进程；`--workers>1` 时调度器会重复执行）
- 用户 `backend/.env` 的 `HOST`/`CORS_ORIGINS` 实际取值（未打开该文件以免读取密钥）

---

## 2. 智能体与助手（backend/app/agents + routers/assistant.py）

### 2.1 P1

**AGT-01 只读模式仍注入 Bash，可任意写盘/执行命令** `[确认]`
- 位置：`backend/app/agents/orchestrator.py:291-297`
- 现状：
  ```python
  if cfg.get("enable_bash"):
      ...
      await toolkit.add_tool(Bash(cwd=cwd))
  ```
  紧邻的插件加载明确写了 `and mode != "readonly"`（`:302`），只有 Bash 没有 mode 判断。
- 影响：用户选「只读咨询」时界面与 `READONLY_SUFFIX`（`:90`）都承诺「写入类工具已对你关闭」，但 Agent 可 `python -c "open(...,'w')"` 改任意数据（含 `backend/data`）；技能池在只读模式照常注入，技能正文里的脚本动作因此可执行。
- 建议：`if cfg.get("enable_bash") and mode != "readonly":`；若只读场景确需跑技能脚本，另设白名单命令或独立 `allow_readonly` 标志。

### 2.2 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| AGT-02 | `orchestrator.py:359-376,926,944` | 同一会话并发对话共享同一 Agent 实例，锁只包住「构建」，`reply_stream` 在锁外 | 双击发送/双标签页/定时任务撞同一 session 时，两协程同时对同一 Agent 调 `reply_stream`，内部 state 交错 | 加按 key 的执行锁覆盖整个 reply 生命周期，或返回「当前会话正在生成」 |
| AGT-03 | `skill_market.py:155-163`、`workbuddy_sync.py:219-227` | 技能包「先 rmtree 再 copytree」 | copytree 中途失败即丢旧包；首次安装失败留下半成品目录且**未写 disabled**，残缺技能对 Agent 生效 | 先拷到临时目录 → 成功后原子替换；失败清理并确保 disabled |
| AGT-04 | `secrets_box.py:78-95` 定义，`main.py:99-103` 未调用 | `migrate_seal_keys` 从未接线，历史明文 API Key 永不迁移 | 早于密封机制写入的 `model_providers.api_key` 永久明文存于 SQLite，与「Key 静态加密」口径不符 | 启动时与 `migrate_env_secrets` 并列调用一次，失败仅告警 |
| AGT-05 | `config_store.py:1216,1258` | 连通性/图片测试用 `urllib.request.urlopen` 默认跟随重定向，会复制 Authorization/x-api-key | base_url 指向的服务器（或明文中间人）301 到任意主机即可收到真实 Key；`model_sync` 已显式 `follow_redirects=False`，此处未对齐 | 改 httpx 且不跟随重定向，3xx 直接判失败 |

### 2.3 P3

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| AGT-06 | `routers/model_probe.py:31-37`、`routers/assistant.py:221-230` | 探测结论落库后不调 `orchestrator.invalidate()` | 已构建模型/会话继续用旧思考参数，探测结果对已有会话不生效直到重启 | 探测成功后 invalidate |
| AGT-07 | `orchestrator.py:60-72,146-155` | 进程内缓存（`_session_models` 等）跨线程无锁，`move_to_end` 存在 KeyError 窗口 | 线程池 `invalidate()` 与事件循环写入竞争，窄窗口下该轮对话 502 | 缓存访问加 `threading.Lock`，或失效标记统一投递到循环线程 |
| AGT-08 | `routers/assistant.py:48`、`orchestrator.py:1011,380-381,1024-1027` | 用户会话 id 允许 `job-` 前缀，而该前缀被当内部会话 | 以 `job-` 开头的用户会话：不出现列表、不注入历史、续聊失效、可能被套餐边界拒绝 | `_check_session_id` 拒绝内部前缀，或改用 `__job__` 命名空间 |
| AGT-09 | `orchestrator.py:853-866` | 非流式 `chat()` 失败时不落库用户消息（流式已先落库） | 模型错误时提问整条丢失，重试无上下文；两接口语义不一致 | 统一 `_begin_turn/_end_turn`，失败补错误消息成对 |
| AGT-10 | `orchestrator.py:992` | 流式成功后最终 `_persist` 在 try 之外 | DB 短暂不可用时前端已收到完整 delta，再收一条「服务不可用」，且该轮未入历史 | 包 try，失败降级 warning 后照常 done |
| AGT-11 | `mcp.py:125-142,349-359,158` | MCP 崩溃/超时后无自动恢复；`_read_line` 每读一行新起线程 | server 崩溃后该组工具静默不可用直至手动重连；通知密集时线程抖动 | 检测 `not is_alive()` 时有界重启+重注册；改常驻读线程+队列 |
| AGT-12 | `model_sync.py:555-560` | 响应体上限在 `resp.content` 完整缓冲之后检查 | 无 Content-Length 的 chunked 巨响应先吃内存再被拒 | 改 `client.stream()` 累计字节，超限 abort |
| AGT-13 | `memory.py:56-65` + `import_markdown:234-253` | 去重每条都全表扫 `Memory.content`；导入逐条 `add_memory` | 200k 字导入（上千条）呈 O(n²)，秒级到分钟级卡顿 | 一次载入候选集内存去重 + 批量 insert |
| AGT-14 | `rag.py:161-170` | 每次查询对全部块重新分词（只缓存 df/chunks，不缓存 tf） | 笔记多时每次 `search_knowledge` 都 O(全部字符)，Agent 每轮可能多次调用 | 建索引时缓存每块 tf |
| AGT-15 | `expert_packs.py:317` | 专家包导入默认 `enabled=True`，与「新技能包默认停用」相反 | WorkBuddy 导入的 SOUL/IDENTITY 草稿未经审阅即进入路由名册 | 新建行 `enabled=False`，由用户显式启用 |
| AGT-16 | `workbuddy_sync.py:24`、`skill_market.py:25` | 源码硬编码本机绝对路径（`C:/Users/<用户名>/...`、`示例主体19/...`） | 违反项目「环境相关默认值走 backend/.env」约定；脱敏分发可能带出用户名/目录结构；换机默认指向不存在目录 | 移入 `config.py` settings，`.env.example` 同步 |
| AGT-17 | `config_store.py:756-758` | 供应商 `protocol`/`base_url` 保存侧不校验（预设有校验） | `protocol="Anthropic"` 落库后走 OpenAI SDK 静默错误；base_url 可存 `http://user:pass@host` 并回显前端 | 复用 `_normalize_preset` 白名单与 `validate_base_url` |
| AGT-18 | `skill_market.py:44`、`team.py:223,434`、`workbuddy_sync.py:179-180` | 死代码：`_safe_dir_join` 无调用、`ROUTE_KEYWORDS` 仅导出、`route_by_keyword` 无调用、`memory_mod` 未使用 | 安全边界函数存在却未生效，易误以为已覆盖 | 删除或在安装路径真正使用 |
| AGT-19 | `skill_market.py:11` vs `:57-58` | 文档称「单文件 4MB 上限」，实际只校验 SKILL.md/SKILL.cn.md，安装是整目录 copytree | 包内其它大文件不受限 | 改文档或安装前遍历统计 |
| AGT-20 | `mcp.py:201-205`、`routers/mcp.py:156` | `test_call` 返回未截断的 `raw` | 异常 server 可让管理页响应膨胀，原始错误细节进前端 | 管理页不返回 raw 或同样限长 |
| AGT-21 | `tools.py:5-6` vs `:105,160-161,223-224` | 文档承诺「出错返回 ok:False」，实际 `int("abc")` 抛 ValueError | 模型传参错误得到通用工具错误，无法结构化自纠 | 参数入口统一 try/except 转中文 `{ok:false,error}` |
| AGT-22 | `config_store.py:769,777-781` | API Key / 管控 AK-SK 静默截断到 300 字符 | 超长令牌保存成功但鉴权 401，用户无从判断 | 超长直接 400 并提示长度 |
| AGT-23 | `skill_market.py:164-165` | `install_from_market` 恒返回 `enabled: False`，与真实状态不符 | 界面显示「已安装且停用」，实际可能仍启用 | 返回 `skill_store` 真实状态 |
| AGT-24 | `team.py:388-392` | 路由打分按「匹配文本」反查关键词下标 | 两个关键词命中同一段文本时指向错误关键词，边界输入路由抖动 | 记录 `(keyword, matched)` 二元组 |

### 2.4 已验证正确的点（抽样）

- 密钥不外泄：`masked()` 短 key 整体掩码、只回显后 4 位 + `api_key_set`；`list_providers` 只给布尔；MCP env 全 `***`
- URL 校验：拒绝非 http(s)、无主机名、内嵌 userinfo、控制字符；错误信息不含 URL 主体
- 火山管控面：host 白名单 + 根路径 + Action 三值白名单 + 强制 Version；`X-Content-Sha256` 按实际 body 计算
- 同步诚实性：200+空列表/结构不可识别一律 `RemoteFetchError`；在线失败 → 目录兜底 + `partial=true`；`truncated` 由警告前缀精确置位
- 探测诚实性：`accepted ≠ supported`；全失败不写结论；429 有界退避；错误进 `error` 字段不逃逸
- 中断持久化：`CancelledError/GeneratorExit` 时把已产出 delta/thinking 补存为 assistant，空正文写「（已停止生成）」
- 只读工具门控：`_readonly_tool_names` 快照迭代；MCP 只认 `annotations.readOnlyHint`，缺省按可写
- 路径穿越：技能包名正则 + `is_relative_to` 二次校验；会话附件必须 `relative_to(UPLOAD_DIR)`；图片 10MB 上限
- DPAPI fail-fast：`seal` 一律加密（含已带前缀的明文），`ensure_dpapi_available` 启动即校验，缺 pywin32 拒绝启动

### 2.5 未能确认项

- AgentScope `Agent.reply_stream` 对同一实例并发调用的实际后果（未读第三方源码、未实测）
- 线程池 `invalidate()` 与循环线程缓存的竞态在实际调度下的命中概率（GIL 下窗口极窄）
- urllib 在各 Python 小版本是否都把 Authorization/x-api-key 带入重定向（按 CPython 实现判断，未抓包）
- 前端是否另有「同会话禁止并发发送」前置限制（仅审后端）

---

## 3. finance_tax（企业所得税 / 增值税 / 附加税 / 递延所得税 / 年度申报表 / 政策库）

> 该模块是从旧项目整体迁移进来，**引擎（`corporate_income_tax_calculator` 等）实现较完整，但路由层另写了一套自造税率与表单口径**，形成「页面展示一套、落库/导出另一套」的结构性问题。以下 P0/P1 均已在代码中定位到具体行。

### 3.1 P0

**TAX-01 申报记录落库用自造税率阶梯：≤300 万一律 5%、≤1000 万 10%** `[确认]`
- 位置：`backend/app/modules/finance_tax/routers/tax_returns.py:92-100`
- 现状：
  ```python
  if taxable_income <= 3000000:      # 300万以下
      tax_rate = 0.05                # 实际税率5%
  elif taxable_income <= 10000000:   # 1000万以下
      tax_rate = 0.10                # 实际税率10%
  else:
      tax_rate = 0.25
  ```
- 影响：`POST /api/finance/tax/tax-returns/` 把税额写进 `mod_tax_return.tax_amount`（前端 `TaxCalculateView.saveReturn` 正常调用）。应纳税所得额 250 万的非小微公司被存成 12.5 万（应 62.5 万），500 万存成 50 万（应 125 万）；且完全无视高新/小微/限制行业。页面「计算结果」走 `CorporateIncomeTaxCalculator`，**展示与落库是两套口径**，用户保存后数据静默变错。
- 建议：删除本地税率表，保存接口复用 `/calculate` 的引擎返回值；税率只保留 `tax_rates.py` 单一来源。修复后补「同一输入：展示税额 == 落库税额」往返测试。

**TAX-02 A200000 小微判定只看人数/资产，不看 300 万所得与限制行业 → 任意规模按 5% 出表** `[确认]`
- 位置：`routers/tax_returns.py:633-642`、`services/tax_form_generator.py:205-212`
- 现状：
  ```python
  if (enterprise.employee_count or 0) > 300: return False
  if (enterprise.total_assets or 0) > 5000: return False
  return True
  ```
  ```python
  if self.enterprise.is_small_micro:
      self.small_micro_reduction = self.actual_profit * 0.20
  ```
- 影响：实测「利润 1000 万 + is_small_micro=True」→ 第 10 行 250 万、第 11 行减免 200 万、第 14 行 **50 万（5%）**。叠加 TAX-09（创建企业时人数/资产被丢弃，库里恒为 0），**所有通过 API 建的企业导出的 A200000 都按 5% 计税**，少缴 20 个百分点。
- 建议：复用 `SMALL_MICRO_CRITERIA.is_qualified`（含 300 万所得与限制行业）；`A200000FormData.calculate()` 内对 `actual_profit > 300 万` 强制不适用 5%。

### 3.2 P1

**TAX-03 A200000 第 5 行「所得减免」被重复相加两次** `[确认]`
- 位置：`services/tax_form_generator.py:186-193`
- 现状：`row5 = income_reduction_amount + agriculture + income_reduction_amount + ...`
- 影响：实测 `income_reduction_amount=100` → `row5=200`，第 8 行实际利润额 900→800，税额少 25%。
- 建议：删除重复项，补 `row5 == 各项之和` 单测。

**TAX-04 A100000 高新企业：15% 税率上再减 10 个点 → 实际按 5% 计征** `[确认]`
- 位置：`services/annual_tax_form_generator.py:848-851,864-870`
- 现状：`tax_rate=0.15`、`high_tech_reduction = taxable_income*(0.25-0.15)`、`actual = tax_amount - deduction - credit`
- 影响：应纳所得 500 万 → 第 31 行 75 万、第 33 行减免 50 万、第 36 行 **25 万（5%）**，正确应 75 万。该文件当前无路由引用，属潜伏 P0。
- 建议：二选一——官方表口径（第 30 行 25%、第 31 行 ×25%、第 33 行减免 ×10%），或保留 15% 税率时减免置 0。

**TAX-05 A105000 视同销售成本被记成「纳税调增」（方向相反）** `[确认]`
- 位置：`annual_tax_form_generator.py:211-212,308-309`
- 现状：`deduction_increase = deemed_sales_cost + max(0, employee_compensation)`
- 影响：收入 100/成本 80 → `total_increase=180`，正确净调整 +20，多计 160 万调增（多缴税）。
- 建议：`deemed_sales_cost` 移入 `deduction_decrease`。

**TAX-06 增值税一般纳税人「进项税额转出」符号相反** `[确认]`
- 位置：`services/value_added_tax_calculator.py:267-268`
- 现状：
  ```python
  # 应纳税额 = 销项税额 - 可抵扣进项税额 - 进项税额转出
  vat_payable = total_output_tax - deductible_input_tax - input_tax_transferred_out
  ```
- 影响：转出是「已抵扣进项要加回」，正确为 `销项 - (进项 - 转出) = 销项 - 进项 + 转出`。销项 13/转出 10 → 现算 3，正确 23，误差 2×转出额。免税项目、集体福利、非正常损失场景全部少缴。
- 建议：改为 `+ input_tax_transferred_out`，补三态用例。

**TAX-07 弥补亏损没有 5 年/10 年期限，任何年份亏损都能抵当年所得** `[确认]`
- 位置：`services/corporate_income_tax_calculator.py:83-92,628-671`
- 现状：`for year in sorted(remaining_losses)` 只排序不判年限；docstring 自己写「最长不得超过 5 年」。
- 影响：传入 `{2000: 100}`，2026 年仍全额弥补；无法识别高新 10 年；填错年份即永久少缴。
- 建议：`LossCarryForward` 增加 `tax_year`/`carryforward_years`，循环内 `if tax_year - year > years: continue`，并列出过期未用亏损。

**TAX-08 小微判定用「弥补亏损前」应纳税所得额 → 符合条件企业被按 25% 计征** `[确认]`
- 位置：`corporate_income_tax_calculator.py:806-814`
- 现状：`taxable_income_wan=taxable_income_before_loss`
- 影响：利润 400 万 + 以前年度亏损 200 万 → 弥补后 200 万（符合小微，应纳 10 万），引擎判「不符合」按 25% 计 50 万，**多缴 40 万**。
- 建议：先算弥补，再以弥补后金额做小微判定（两阶段：上限判定 → 弥补 → 复核）。

**TAX-09 政策有效期只覆盖「小微 5%」一处；UI 从不传 `tax_year`** `[确认]`
- 位置：`routers/tax_returns.py:232,540`；`value_added_tax_calculator.py:303-345`；`additional_tax_calculator.py:308-379`；`config/tax_rules.py:124-149`；`tax_form_generator.py:205-221`
- 现状：`policy_validity.py` 登记 9 条政策，全模块只有 `corporate_income_tax_calculator.py:721-725` 调用一次；前端发送的是 `year`（模型无此字段，被 Pydantic 丢弃），从不发 `tax_year`。
- 影响：2028 年做 2028 年度申报时，增值税仍按 1%、六税两费仍减半、研发仍按 120%、表单仍按 5%，全部无提示；只有直接调 API 并手工传 `tax_year="2028"` 才会得到中文拒绝。
- 建议：各计算函数增加 `tax_year` 并在入口统一 `policy_status(...)`；前端加「所属年度」并传 `tax_year`。

### 3.3 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| TAX-10 | `corporate_income_tax_calculator.py:210-215` | 高新优先于小微，纳税人无法选低税率 | 同时符合时按 15% 而非 5%，多缴 10 个点 | 两者都符合时按较低税负，结果标注「已选择小微」 |
| TAX-11 | `corporate_income_tax_calculator.py:482-491` | 设备一次性扣除按全额购置款调减，未扣当年会计折旧 | 资本化场景重复扣折旧，少缴税 | 入参区分购置额/当年折旧，调减取差额 |
| TAX-12 | `tax_rules.py:266-285`、`corporate_income_tax_calculator.py:299` | 广告费超限结转额恒为 0（函数不返回 `carry_forward_to_next_year`） | 以后年度可扣额与 A105000 第 1401 行恒 0 | 补返回 `max(0, expense - limit)` |
| TAX-13 | `annual_tax_form_generator.py:479-500` | A107012 漏计委托境外/无形资产/销售研发；`allowed_rd_expense` 未用限额 | 实测允许扣除 150 大于可加计合计 111.11，自相矛盾；境外 80 万完全没进 | 用 `actual_other`；境外并入（80% 且不超境内 2/3）；`enterprise_type` 与费率二选一 |
| TAX-14 | `annual_tax_form_generator.py:839-845`、`tax_form_generator.py:202` | 应纳税所得额/实际利润额被 `max(0, …)` 截断，亏损年度不留痕 | 第 29/8 行显示 0，本年亏损不落表、不进结转（与 TAX-07 叠加） | 保留负数原值，只对应纳税额取 0 |
| TAX-15 | `routers/enterprises.py:15-22,73-83` | 创建企业静默丢弃 `employee_count`/`total_assets`/`is_restricted_industry` | 库里恒为 NULL → `_is_small_micro` 恒判小微，放大 TAX-02 | Schema 增补三字段并映射；缺失时返回「无法判定」而非默认小微 |
| TAX-16 | `tax_form_generator.py:459`、`annual_tax_form_generator.py:1325` | Excel 公式注入：企业名/税号/行业以 `=` 开头写入即变公式 | 导出底稿被转发/打开时执行公式（外链、DDE 类风险） | 用户文本统一加 `'` 前缀或 `data_type='s'` |
| TAX-17 | `models.py:129-134`、`routers/tax_returns.py:131-176` | 申报记录无唯一约束、无删除/更正接口；列表无 `order_by` | 同企业同年度可产生任意多条 draft/submitted，无法删除；分页不稳定 | 加唯一约束或提交查重 409；补 DELETE/重算；列表 `order_by(id desc)` |
| TAX-18 | `routers/tax_returns.py:595`、`tax_form_generator.py:56-74` | Q4 的「税款所属期间」被当成全年（01-01~12-31）；年度记录导出预缴表 A200000 | 表头期间与报表种类双错，报税被退回 | Q4 改 10-01~12-31；年度导出 A100000 或明确 400 |
| TAX-19 | `deferred_tax_calculator.py:339-357` | 未来预测为负时确认比例为负 → 负的递延所得税资产 | 负资产增加所得税费用 | `ratio = max(0, min(1, ratio))` |
| TAX-20 | `deferred_tax_calculator.py:339-348,645-659` | 未提供预测时按 100% 确认 DTA；税率变动影响未计入费用合计 | 无盈利证据也全额确认资产；税率变动若 UI 再加一次即重复计量 | 无预测时确认比例 0 或返回「无法验证」；税率影响并入费用并注明勾稽 |
| TAX-21 | `routers/tax_returns.py:143-171,82-90` | 丢弃 `adjustments` 明细；`tax_preferential` 当所得式减免 | 调整明细页恒空；税额式优惠被当所得式 | 落库调整明细或删除字段；优惠改税额扣减 |
| TAX-22 | `tax_rules.py:225-232` | 业务招待费「收入为 0 时仍允许扣 60%」，与 5‰ 上限冲突（且被测试固化） | 无收入企业普遍少计调增 | 增「是否筹办期」入参；否则按 `min(60%, 5‰)` |
| TAX-23 | `additional_tax_calculator.py:65-78,401-403` | 印花税税率表是 2022 年前旧口径（承揽/运输 0.5‰、保留「权利许可证照 5 元/件」），与 `finance_stamp_duty` 双份漂移 | 同一系统两套印花税口径；旧税目已取消 | 删除本表，统一引用 `finance_stamp_duty` |
| TAX-24 | `routers/tax_returns.py:368-383,491` | `/calculate` 读的小微判定字段名与引擎不符（`employee_count_qualified` 等），前端恒显示不满足；`loss_used_by_year` 取的是剩余亏损 | 用户被误导；亏损使用口径错 | 按 `conditions` 结构取 `passed/value/threshold`；用 `used_by_year` |

### 3.4 P3

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| TAX-25 | `services/tax_calculator.py:33-51` | 死代码：另一份 2.5%/10% 过期档位税率表，无引用 | 删除或委托引擎 |
| TAX-26 | `config/tax_rates.py:45,171-218` | `Dict[str, any]` 用了内建 `any`；附加税率三处重复 | 改 `Any`；税率常量收敛 |
| TAX-27 | `routers/tax_returns.py:431,447` | 政策依据文案过期（小微写 2023 年第 6 号、研发写财税〔2018〕99 号） | 统一从 `POLICY_REGISTRY` 取 |
| TAX-28 | `services/tax_policy_service.py:51-65,503-549`；`annual_tax_form_generator.py:771-773` | 政策摘要/税率查询无路由（前端也无请求）；A100000 附表数据在 calculate 中从未读取 | 补路由并在政策页展示；主表从附表汇总或禁止同时传 |
| TAX-29 | `financial_data_importer.py:1220-1221,1247-1248,1300-1319`；`routers/financial_import.py:130-141` | 两处裸 `except: pass`；`"2026年13月"` 可解析出 quarter=5；`/import/{id}` 不校验企业 | 至少 `logger.exception`；clamp 1-4；企业不存在 404 |
| TAX-30 | `routers/tax_returns.py:576-580` | `/calculate` 把内部异常文本回传客户端 | 记录堆栈，对外固定中文文案 |
| TAX-31 | `config/policy_validity.py:55-77` | 只按年份比较；`tax_year="2026abc"` 被截断成合法年度静默通过 | 按 YYYY-MM 比较；非法格式显式报错 |
| TAX-32 | `annual_tax_form_generator.py:801-809` | `has_revenue_data` 只看 7 个字段，漏其他收益/投资收益/营业外收支 | 判定「任一利润表字段非零」 |
| TAX-33 | `tax_form_generator.py:210-212` | A200000 高新分支把第 9 行税率写成 15%，与官方「25% + 第 11 行减免 10%」结构不符 | 第 9 行固定 25%，第 11 行填减免 |

### 3.5 已验证正确的点（抽样）

- 金额舍入：附加税用 `Decimal(str(...)).quantize(ROUND_HALF_UP)`，`2.675→2.68`、`1.005→1.01`
- 六税两费减半：合计先减半再按最大余额法回填分项，保证「分项之和 == 合计」
- 小微判定阈值与边界：300 人/5000 万/300 万均按 `<=`；`300.0` 合格、`300.0001` 不合格
- 弥补亏损顺序：按年度升序、先亏先补、`min(剩余所得, 剩余亏损)`，不产生负所得（仅缺年限）
- 增值税价税分离、13/9/6 映射、小规模 1%/10 万/30 万阈值（`<=`）、超限全额计征
- A200000 主表第 4/8/10/14/16 行公式（除第 5 行重复缺陷）
- 上传与导出安全：扩展名白名单 + 20MB + 分块限额；xlsx 走 defusedxml + 单部件 64MB 上限；下载头剔除 CR/LF
- 删除级联：Enterprise → financial_data/tax_returns、FinancialData → TaxAdjustment 均 `cascade="all, delete-orphan"`

### 3.6 未能确认项

- A100000 高新 5% 是否曾对外承诺（该文件无路由、无测试引用）
- 生产库 `mod_tax_enterprise` 是否存在 `is_restricted_industry` 为 NULL 的旧行（可能导致列表 500）
- 印花税「权利许可证照 5 元/件」是否为历史年度补申报有意保留
- 2026-01-01《增值税法》是否改变小规模征收率/起征点表述（禁止联网，未核对）

---

## 4. finance_ledger_bip（用友 BIP 凭证 / 辅助明细账 / 科目余额表导入）

### 4.1 P0

**BIP-01 凭证聚合与去重键缺「期间」，真实多月导出被合并、跨月重导被误判重复** `[确认]`
- 位置：`backend/app/modules/finance_ledger_bip/service.py:547,552-557,586-593`
- 现状：
  ```python
  existing = {(l, n) for l, n in db.query(BipVoucher.ledger, BipVoucher.voucher_no).all()}
  ...
  key = (ledger, vno)
  ...
  period=g["date"][:7], voucher_date=g["date"],
  ```
- 影响：用项目自带脱敏样本（`脱敏数据/脱敏数据凭证列表_2026-10-22 23_28_25.xlsx`，即 spec 所述原稿 13,001 行）只读复算：
  - 当前分组 **356 组**；正确按 `(ledger, period, voucher_no)` 应为 **2,525 组**
  - 4 个 sheet 各含 2026-01…2026-10 十个月，`记-1` 在 10 个月重复出现却全部并入一张凭证
  - `period`/`voucher_date` 取首行（1 月）→ 2–10 月查询/按期间入仓全为空
  - 借贷平衡校验从「逐月」变成「跨月合计」，各月不平衡可互相抵消而漏报
  - 同账簿下月文件再导入 → 全部计入 `skipped_vouchers`，用户以为「重复已跳过」，实际丢数据
  - 辅助明细账同样：13,468 有效行 → 当前 359 组，含期间应为 2,507 组
- 建议：聚合键改 `(ledger, period, voucher_no)`（优先用「期间」列，缺失时由凭证日期推导），`period` 参与唯一性；补真实形状的多月回归用例，断言凭证数、各期间凭证数与 `voucher_date`。

### 4.2 P1

**BIP-02 真实两级表头的科目余额表：六个金额列全部无法映射，全 0 入库且返回 200** `[确认]`
- 位置：`service.py:399-406,631-642`（根因 `_read_one_sheet` 与 `suggest_mapping`）
- 现状：
  ```python
  put("opening_debit", _match_col(cols, all_of=("期初", "借")))
  put("period_debit", _match_col(cols, all_of=("借",), any_of=("本期", "发生"), exclude=("期初", "期末")))
  ```
- 影响：用真实文件 `脱敏数据26.10科目辅助余额表_2026-10-22 23_39_49.xlsx` 复现——表头定位到第 15 行（正确），清洗后列为 `[科目编码, 科目名称, 核算账簿, 辅助项, 期初余额, 本期发生, nan.1, 本年累计, nan.2, 期末余额, nan.3]`，`suggest_mapping` 只返回 `{subject_code, subject_name}`，六个金额字段全缺；`REQUIRED_FIELDS[balance]` 只要求科目编码/名称，于是 **全部行以六个 0.0 入库并提示「导入完成」**，无任何 warning。真实文件里 `期初余额` 列存的是方向「借/贷」，金额在匿名列中，人工也无法映射出「期初借方/本期贷方」。
- 建议：识别两级表头（主表头 + 子表头）后合并列名；金额列一个都没映射上时直接 400，而不是全 0 成功。

### 4.3 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| BIP-03 | `service.py:547,576-578,953` | 跨来源共用去重键：先导凭证列表、再导辅助明细账会全部跳过 | 凭证列表导入的分录无科目列，科目汇总依赖 `subject_code != ""`；aux 被当重复跳过 → 科目汇总永远为空 | 判重键纳入 `source_type`，或 aux 优先、凭证列表补头信息 |
| BIP-04 | `service.py:291,296-297,384-386,520-522` | 单 sheet 凭证列表（真实格式无核算账簿列）无法导入（只有 `len(sheets)>1` 才合成 sheet 名账簿） | 把真实任一 sheet 单独导出后，`suggest_mapping` 缺 `ledger`，抛「列映射缺少必需字段：核算账簿」，前端也不渲染可选列，用户无法补救 | 单 sheet 也合成 sheet 名账簿；前端提供「以 sheet 名作为核算账簿」 |
| BIP-05 | `service.py:654-733,714-720` | 全部行无效/被跳过时仍返回 200「导入完成」 | 列映射错、全为合计行时 `inserted_vouchers=0` 仍成功 | `inserted==0 and skipped>0` 返回 400 或 `ok=false` |
| BIP-06 | `service.py:614-651`、`models.py:74-89`、`service.py:940-974,1058-1080` | 科目余额表只写不读；同期间重复导入产生重复行 | 全仓库无查询端点/Agent 工具，导入后界面看不到结果；批次显示行数但表内成倍累积 | 实现余额查询/对账并按 period upsert，或明确「仅留档」 |
| BIP-07 | `service.py:1033-1034`、`frontend/.../VoucherListView.vue:151-158` | 「按期间入仓」走 DuckDB `append`，无按期间先删/去重；UI 全量 replace 被禁用 | 连点两次或超时重试即表内重复；无「覆盖」入口 | 按 period 先删后插；前端提供模式选择 |

### 4.4 P3

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| BIP-08 | `service.py:269-272` | `_uniquify` 先把空表头改成 `nan.1/nan.2`，随后 `!= "nan"` 只过滤第一个 | 先按原始表头过滤空列，再去重 |
| BIP-09 | `service.py:162-184,507-525` | 金额解析静默归零、`inf` 可入库；未知映射键/重复列静默 | 不可解析记 warning/拒绝；拒绝非有限值；未知键 400；校验映射唯一 |
| BIP-10 | `service.py:237,195-201` | 表头只扫前 25 行；整数 `yyyymmdd` 日期被当 Excel 序列号拒绝 | 扫描窗口按空行截断；整数日期尝试 `%Y%m%d` |
| BIP-11 | `models.py:44-45`、`service.py:703-712` | 无 DB 唯一约束，并发/双击可重复入库；先归档后 commit 留孤儿文件 | 加 `UniqueConstraint(ledger, period, voucher_no)`；commit 与归档顺序对调或补偿 |
| BIP-12 | `service.py:547` | 每次导入全表加载已有 `(ledger, voucher_no)` 到内存 set | 改按本次涉及键批量查询或临时表 JOIN |

---

## 5. finance_ledger_personal（个人记账）

### 5.1 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| PL-01 | `schemas.py:98-108`、`service.py:337-361` | `TransactionUpdate` 显式 null：`amount: None` 抛 `TypeError`（非 ValidationError → 500）；`occurred_on/kind: null` 通过 schema 后写 NOT NULL 列抛 IntegrityError → 500 | 客户端一次 null 即 500，正确应为 422/400 | 改用 `exclude_none`（或单独处理「清空分类」语义），validator 对 None 直接返回 None |
| PL-02 | `service.py:324,351`、`schemas.py:13-18`、`models.py:51,70` | 金额用 float 做两位舍入 | `round(2.675,2)==2.67`、`round(1.005,2)==1.00`，用户期望四舍五入会少记 1 分；SQLite REAL 存储，SUM 为浮点累加 | 入参 `Decimal` + `ROUND_HALF_UP`，或 API 限制最多两位小数并写明存储限制 |
| PL-03 | `service.py:696-700` | CSV 导入不校验金额上限与非有限值 | `"1e20"` 可入库（超 `Numeric(14,2)` 声明，SQLite 不约束）；`inf` 入库后 `GET /transactions` 在 `allow_nan=False` 下序列化 500；`"nan"` 在 Agent 工具路径未捕获 IntegrityError | 导入与工具统一 `math.isfinite`、`0 < amount <= MAX_AMOUNT` |
| PL-04 | `service.py:573` | CSV 导出静默截断 5000 行（丢弃 total） | 超 5000 条只导前 5000，无提示 | 读 total，超限拒绝并提示分批，或流式导出全部 |
| PL-05 | `service.py:585` | CSV 导出对账户名不做公式注入转义（分类/备注已做） | `=HYPERLINK(...)` 形式的账户名在 Excel 中成为公式 | 账户名也走 `_csv_cell` |
| PL-06 | `service.py:647-738` | CSV 重复导入无幂等/去重（无批次/内容哈希） | 同一 CSV 导两次即两套交易；导出→再导入翻倍 | 加批次/内容哈希与「跳过已导入」，或前端二次确认 |

### 5.2 P3

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| PL-07 | `service.py:625,552-562,565-590` | CSV 往返不一致：分类名含 `>`/`/` 会被拆层级；前导 `'` 导入不剥离；tags 从不导出 | 明确转义协议；限制/转义分隔符；补 tags 列或文档声明 |
| PL-08 | `router.py:198-204`、`service.py:689` | 导入失败仍在 inbox 留档；同秒同名覆盖；逐行 SAVEPOINT | 成功后再移动/失败 unlink；时间戳加微秒；每 500 行一个 savepoint |
| PL-09 | `service.py:615,638`、`models.py:24,38` | 账户/分类名截断 50 字符可造同名重复，无唯一约束 | 截断前查重或拒绝超长；加 `(name,parent_id)` 唯一索引 |
| PL-10 | `service.py:252-262,194-200`、`database.py:34-47` | 删分类不检查预算 → 孤儿预算；SQLite 未开外键；非法 `kind` 静默返回全部 | 删除时清理/拦截预算；启用外键；非法 kind 400 |
| PL-11 | `service.py:689` | 逐行 `begin_nested()` + flush，接近 20 万行时 20 万次往返 | 分块 savepoint |
| PL-12 | `router.py:190`、`service.py:313` | 上传文件名未走共享 `repair_upload_mojibake`，GBK 客户端上传名可能乱码留档 | 统一修复 |

---

## 6. finance_vault（DuckDB 数据仓 + 只读 SQL 查询）

### 6.1 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| VAULT-01 | `service.py:254-279,555-568`、`router.py:92-99` | 建表与元数据登记非原子；DB 文件重置/登记失败后 `preview_table` 抛 `duckdb.Error`（非 ValueError）→ 500 | 物理表存在但不在 `mod_vault_dataset` → 不可访问孤儿；反向则 500（`/query` 同类错误 400） | 登记与建表同一 try；`preview` 捕获 `duckdb.Error` 返回 404/409/503 |
| VAULT-02 | `service.py:293,297,267` | 字段类型完全依赖 pandas 推断 | `"007"`→整数 7、`1000`→`1000.0`，科目编码/银行账号/邮编被静默改值；无 dtype 声明入口 | 提供 `dtype=str` 选项或按列声明类型，至少在结果中提示「类型由 pandas 推断」 |
| VAULT-03 | `service.py:415-416,482-484,496-501` | 只读 SQL 校验器误拒合法 DuckDB 语法 | `AS MATERIALIZED`/`AS NOT MATERIALIZED`/`WITH x(a) AS` 被判未注册；FROM-first 被拒；`LIMIT 1e3` 追加后变双 LIMIT 语法错 | 支持 MATERIALIZED/列清单；放行 FROM；无法识别 LIMIT 时拒绝而非二次追加 |

### 6.2 P3

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| VAULT-04 | `service.py:254-255,278,507-509`、`router.py:76-77` | 异常映射不一致：锁冲突 `ConnectionException` 在 try 外、登记异常裸抛；查询结果 inf/NaN 序列化 500 | `_connect`/登记纳入 try，映射 503/500；`_cell` 对非有限 float 返回 None |
| VAULT-05 | `service.py:42,292-293,324-327` | 多 sheet 默认只入第一个无提示；`.xls` 不支持；数字 sheet 名被当索引；inbox 永久留档 | 默认拒绝多 sheet 或提示选择；支持 .xls；名称优先；加保留策略 |
| VAULT-06 | `service.py:313` | 上传名未走 `repair_upload_mojibake` | 统一修复 |

### 6.3 已验证正确的点（BIP / 个人 / 数据仓）

- BIP 表头定位：带 12 行元数据块的辅助明细账定位到第 12 行、凭证列表第 1 行、余额表第 15 行；「关键词类别数」评分有效
- BIP 多 sheet 合并 + sheet 名作为账簿 + 续行 `ffill`：真实四 sheet 文件正确处理
- BIP 查询：LIKE 通配符转义、分页、凭证详情借贷对称与平衡标记、科目前缀转义
- BIP 上传：扩展名白名单、10MB、uuid token 正则、tmps 路径不可穿越；解析失败/超限删除 tmp 与边车
- 个人：坏行只回滚本行且剔除中途创建的账户分类；错误文案不回带 SQL；月区间跨年正确；分页超大页码返回空
- 数据仓：写操作/DDL/系统命令关键词、多语句、非 SELECT/WITH 首词、文件路径表函数、未注册 FROM/JOIN 全部拒绝；表名白名单 + 引号；DuckDB 短连接 + 写锁 + `interrupt()` 超时 + 结果上限 1001 行

### 6.4 未能确认项

- 单 sheet 凭证列表无法导入是代码路径 + 真实 sheet 复算确认；用户是否真会单独导出取决于操作
- 3 位小数金额是否为合法业务输入（决定 PL-02 实际触发面）
- `backend/data/vault/vault.duckdb` 被删除/重置、多进程部署是否会发生
- BIP 凭证列表与辅助明细账的真实导入顺序（决定 BIP-03 影响面）
- SQL 校验器误拒的 MATERIALIZED CTE / FROM-first 是否会被用户实际写出

---

## 7. finance_invoice（发票批次识别 / 冲红配对 / 项目导出）

| 编号 | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| INV-01 | P2 | `service.py:638-656` | 同一项目跨月时「发票清单.xlsx」只落在首个年月目录，其余月份目录无清单且清单混票 | 10 月票在 `26年10月A项目/`、11 月票在 `26年11月A项目/`，但 `A项目` 清单只出现在 10 月且金额是全年合计，与本目录 PDF 不对应 | 分组键改 `(年月, 项目)`，或每个含票目录写本目录清单，汇总表保留跨月合计 |
| INV-02 | P2 | `service.py:289-297,106-117` | 冲红配对：金额不可解析时一律 0.0，冲红与正票按 `0 == 0` 误配并双向写指针 | 两张「金额读不出」的票被判为配对，`red_matched_count` 与详情页给出错误结论；批次总额静默按 0 计 | `parse_amount` 失败返回 None，配对跳过并计 incomplete 告警 |
| INV-03 | P2 | `router.py:46-131`（无 DELETE）、`service.py:408-419,473-478` | 批次无删除接口；失败路径只改状态不清理 inbox；非 ValueError 异常裸 500 且 outbox 留半成品 | ZIP 解出若干成员后第 N 个非法 → `inbox/batch_<id>/` 永久残留；磁盘与数据库只增不减 | 失败路径 `shutil.rmtree(inbox)`；补 `DELETE /batches/{id}`（明细+inbox+outbox ZIP） |
| INV-04 | P3 | `service.py:370-386,350-359` | ZIP 成员名加固弱于 POS/现金流（未校验 Windows 非法字符/保留名/长度），成员绕过 20MB 单文件限制，无压缩比检查 | `CON.pdf`/`bad:name.pdf`/超长名 `write_bytes` 抛 OSError 拖垮整批；100MB 零字节可压成百 KB | 复用 POS 的 `_safe_member_segment` 与单成员上限 |
| INV-05 | P3 | `service.py:455-459,473-478` | 失败批次已 commit 的 parsed 明细参与跨批次去重（去重查询不看批次状态） | 失败批次的明细把随后重传的同一批票全部标 duplicate，重传无法恢复 | 去重查询排除 `batch.status == failed` |

---

## 8. finance_expense（费用分类引擎）

| 编号 | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| EXP-01 | P2 | `router.py:89-98`、`service.py:373-377`、`models.py:10-23` | 未预期异常（OSError/KeyError/pandas 内部）不清理 inbox、直接冒泡 500；`ExpenseRun` 无 error 字段 | inbox 里 `uuid4().hex.xlsx` 成孤儿；异常文本随 500 丢失，用户看到 Internal Server Error 且无中文原因 | 兜底 `except Exception` 转 `EngineUnavailableError` 并落库（新增 error 列）+ `_discard_stored` |
| EXP-02 | P3 | `service.py:256-276` | 引擎加载期全局 `os.chdir` + 永久 `sys.path.insert(0)` + `sys.modules.pop("config")` | chdir 窗口内其它请求线程相对路径漂移；引擎 `config.py` 会遮蔽后续裸 `import config` | 用 `spec_from_file_location` 隔离加载，或加载后移出 sys.path |
| EXP-03 | P3 | `service.py:359` | 文本型金额被引擎 `to_numeric(errors='coerce')` 归零后直接采信，无告警 | `"1,234.56"`/`"¥1234.56"` 静默变 0，行数与金额统计偏小 | 统计「金额不可解析 N 行」并在运行记录告警 |

---

## 9. finance_pos（POS 台账对账）

| 编号 | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| POS-01 | P2 | `service.py:240-251` | 执行无互斥（检查与置 running 之间 TOCTOU），崩溃/重启后永久卡 running | 并发两次 POST 在同一 `run_dir/output` 跑引擎互相覆盖；进程被杀后该运行永久 400「正在执行中」，无复位入口 | 按 run_id 加进程内锁 + DB 条件更新；启动时把超龄 running 置 failed |
| POS-02 | P3 | `service.py:268-288` | 成功后不复位 `run.error` | 失败后带 `?project=` 重试成功，列表仍显示上次错误 | 成功分支先 `run.error = ""` |
| POS-03 | P3 | `service.py:275-283` | 产物按 basename 拍平归档，同名互相覆盖且 artifacts 重复 | 下载得到最后一份；ZIP 重复 arcname | 按相对路径保留层级或冲突加后缀并去重 |
| POS-04 | P3 | `service.py:243` | `run_dir` 为空时 `Path("") == Path(".")`，产物写进后端 CWD | 污染 `backend/` 工作区且难定位 | run_dir 为空/非绝对路径直接拒绝 |

---

## 10. finance_board（报表看板 / 计划任务通道）

| 编号 | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| BRD-01 | P2 | `service.py:119-131`（配合管线 `微信报表监控/scripts/run_service.py:46-53,97-99`） | 「计划任务正在运行」预检不可达：`last_run.json` 只在结束时写、恒含 `finished`；`run.lock` 跳过时任务侧不写状态 | 并发/重叠触发时模块无法识别「正在运行」，`schtasks /run` 返回成功后轮询条件永不满足 → 空等 `TASK_CHANNEL_TIMEOUT=600s` 抛 503「超时」，而管线其实正常结束；前端 300s 更早超时 | 直接读 `logs/run.lock`（新鲜即运行中），或让任务开始时先写 running 状态；完成信号改为「新 started 或 last_run mtime 变化」 |
| BRD-02 | P3 | `service.py:193,222-231` | 快照 `stamp` 秒级 + UNIQUE，同秒两次运行 → IntegrityError 500（测试用 `sleep(1.05)` 规避） | DB 记录与磁盘内容错位 | stamp 加毫秒/序号，或冲突时 upsert + 重命名目录 |
| BRD-03 | P3 | `service.py:43,195-206,34-36` | 任务通道无回退、`_PIPELINE_LOCK` 仅进程内；后端 600s 与前端 300s 不一致；快照无清理 | 任务被禁用/凭据失效时一律 503 不回退；多 worker 锁失效；用户先看到失败而服务端仍在跑；快照无限累积 | 任务失败按配置回退；换文件锁/DB 租约；统一超时；补保留上限 |
| BRD-04 | P3 | `service.py:56-58,77-100` | 引擎路径配置为纯空白时 `resolve_engine_root(" ")` 返回 None → `None / script` TypeError 500；`schtasks` 调用无 timeout | 误配置得到裸 500 而非中文 503；`schtasks` 卡死会占住管线锁 | 先 `strip()` 再判空；`schtasks` 补超时 |
| BRD-05 | P3 | `service.py:85-86,98` | 子进程输出硬编码 UTF-8 解码，依赖系统区域设置 | 默认 cp936 的中文 Windows 上子进程输出成 `\ufffd`，「已经运行」判定失效（任务侧自己设了 PYTHONUTF8，说明坑已知） | 统一在 `engine_subprocess_env()` 注入 `PYTHONUTF8=1/PYTHONIOENCODING=utf-8`；非 Python 的 `schtasks` 按 locale 解码 |

---

## 11. finance_cashflow（现金流引擎）

| 编号 | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| CF-01 | P2 | `service.py:224-235` | 与 POS 相同的无互斥 + 崩溃后永久 running | 同一 `run_dir/output` 并发写；子进程最长 1800s，卡死窗口更大 | 同 POS-01 |
| CF-02 | P2 | `router.py:39` | 上传无批次总大小/文件数上限，列表推导先把全部文件整读进内存 | 单文件限 50MB 但文件数/合计不限，N×50MB 常驻内存可被单请求打成 OOM（POS 有 100MB 批次上限，此处缺失） | 边读边累计并设总上限，同时限制文件数 |
| CF-03 | P3 | `service.py:263-267` | 产物按 basename 拍平归档 | 同名覆盖、artifacts 重复 | 同 POS-03 |
| CF-04 | P3 | `service.py:268-272` | 成功后不复位 `run.error` | 同 POS-02 | 同 POS-02 |
| CF-05 | P3 | `service.py:231` | `run_dir` 为空时写入 CWD | 同 POS-04 | 同 POS-04 |

### 11.1 已验证正确的点（发票 / 费用 / POS / 看板 / 现金流）

- 无命令注入面：三个引擎调用全部列表 argv + `cwd=`，无 `shell=True`；`project` 作为独立 argv 元素
- ZIP 防护（POS/现金流）：`resolve()` + `is_relative_to` 阻断 zip-slip 与同前缀绕过；扩展名白名单；成员数/总量上限；Windows 非法字符/保留名逐段拒绝；伪造 usize 炸弹拒绝并清理
- 上传限额：invoice/expense/POS 均走 `read_upload_limited` 分块读取，POS 另有 100MB 批次累计；`UploadTooLargeError` 统一 400
- 发票去重与冲红口径：幂等键=号码+校验码、failed 不参与、批内保留首次、冲红一对一且购方一致、双向指针
- 导出/下载路径安全：项目名净化；`export_zip_path` 只取 basename 且父目录须为 outbox；POS/现金流只认 artifacts 登记名
- 费用护栏：行数上限在引擎执行与写库之前；失败清理 inbox 且针对 Windows 句柄占用做 `gc.collect()` 重试；银行表头覆盖用 `finally` 恢复并用锁串行
- 看板：HTML 端点 `resolve()` + `is_relative_to`（403）、CSP `frame-ancestors 'self'` + nosniff；`log_tail` 为 str 字符切片不劈开 UTF-8；`latest` 只取 `status=ok`
- 失败不假成功：POS/现金流「引擎 0 退出但无产物」落 failed 并写原因；归档异常回写 failed 可重跑；建运行失败 rollback + rmtree
- 现金流月份校验双端：Form `ge=1,le=12` + service 再校验，并 fail-fast 检查引擎脚本存在

---

## 12. 四个填报模块（印花税 / 增值税 / 个税 / 残保金）

### 12.1 P0

**FIL-01 残保金 <1% 分档把「按应缴费额 90% 缴纳」实现成「按 10% 缴纳」** `[确认]` `[待税务口径复核]`
- 位置：`backend/app/modules/finance_dsf/service.py:9,67-68`
- 现状：
  ```python
  # 安排比例 <1% → 减收 90%（即按 10% 征收）
  TIER_NINETY = 0.9
  ...
  tier, payable = "减收90%", base_amount * (1 - TIER_NINETY)
  ```
- 影响：财政部 税务总局公告 2023 年第 8 号的口径是「安排比例在 1% 以下的，按规定应缴费额的 **90%** 缴纳」——即**按 90% 缴、减收 10%**。100 人、工资总额 1000 万、0 名残疾人：`base = 150,000`，应为 **135,000 元**，实际输出 **15,000 元**，少征 120,000 元（约 1/9）。底稿与申报表相差 9 倍。
- 测试反向锁定：`backend/tests/test_module_finance_iit_dsf.py:73` 与 `:119` 把 `15000.0` 写成断言 —— 修复时必须同步改测试（这是「测试固化错值」的典型，改代码不改测试会红）。
- 建议：改为 `base_amount * TIER_NINETY`，常量语义统一为「缴纳比例」；文案改「按应缴费额 90% 缴纳（减收 10%）」；同步修正两条断言。

### 12.2 P1

**FIL-02 个税累计预扣法按月把应纳税所得额截断为 0** `[确认]`
- 位置：`backend/app/modules/finance_iit/service.py:87`
- 现状：
  ```python
  cum_taxable += max(0.0, salary - MONTHLY_DEDUCTION - social - special - other)
  ```
- 影响：61 号公告要求「累计收入 − 累计减除费用 − 累计专项扣除 − 累计专项附加扣除 − 累计其他扣除」，是**按累计数相减**，不是逐月取 max(0) 再累加。任一月份收入低于当月扣除额时，该月负数不冲减其他月份，累计应纳税所得额系统性偏高。例：1 月 20,000、2 月 3,000（无其他扣除），法定累计 13,000 → 390 元，代码得 15,000 → 450 元；「余额为负暂不退税」的抵减路径永远不可达。
- 建议：累加原始金额（可为负），对 `cum_taxable * rate - quick` 取 `max(0, ...)`；本期税额保留负值能力或显式置 0 并保持 `cum_withheld` 不变。

**FIL-03 残保金缺「上年在职职工年平均工资 2 倍社平」计征上限** `[确认]`
- 位置：`finance_dsf/service.py:57-59`
- 现状：`avg_salary = total_salary / total_employees`、`base_amount = shortfall * avg_salary`；全模块无「当地社会平均工资」输入项（`grep 社平/2倍` 零命中）。
- 影响：财税〔2015〕72 号规定年均工资超当地社平 2 倍的按 2 倍计征；高薪企业恒按实际年均工资，多缴且底稿无提示。
- 建议：增加可选 `local_avg_wage`，`base_amount` 用 `min(avg_salary, 2*local_avg_wage)`；缺失时在 `warnings` 明示「未校验 2 倍上限」。

### 12.3 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| FIL-04 | `finance_vat/router.py:30`、`service.py:82` | 小规模 `levy_rate` 无白名单/无下限 | `-0.5`、`0.5`、`0.1234` 均通过并落库出负税额；`!=0.01` 时跳过全部政策校验 | 服务层白名单 `{0.01,0.03,0.05}`，schema 同步约束 |
| FIL-05 | `finance_vat/service.py:13,76-77` | 只有季度口径，`period` 与申报周期无关联校验；`MONTHLY_EXEMPT` 定义后未使用 | 文档称「季度 30 万/月 10 万」但月报路径不可达；8 月单月 25 万被当季度 25 万判免征（少缴） | 增加 `filing_period`（month/quarter）或校验月份 ∈ {03,06,09,12}；删除或接通 `MONTHLY_EXEMPT` |
| FIL-06 | `finance_vat/service.py:44,52-53` | 一般纳税人缺留抵结转/进项转出/免税与零税率；`warnings` 恒为空 | `input > output` 时留抵被丢弃不返回不提示；下一期无法接续；测试注释写「底稿提示」但实际无提示 | 输出 `carryforward_vat` 并写入底稿；留抵场景写中文 warning |
| FIL-07 | `finance_vat/service.py:69-77` | 30 万免征未走政策有效期（`vat_small_scale_exempt10w` 定义后无调用） | 减免政策到期后仍给「免征」结论；`levy_rate=0.03` 时完全不查政策 | 免征判定同样 `policy_status(...)`，失效即拒绝 |
| FIL-08 | `finance_iit/service.py:67` | 以姓名为唯一键，同名员工被合并成一个累计序列 | 两名「张伟」各 20,000/月被合并为 40,000/月，直接跳档到 20%，合计多扣税 | 行内增加 `employee_id`（工号/证件号）作分组键；缺省时同名多行告警 |
| FIL-09 | `finance_iit/service.py:71-76,87` | 不校验月份连续，也不接收任职起始月；累计减除费用只按「有行的月份」计 | 只提交 6 月一行时按 1 个月 5,000 扣除（法定按 6 个月），多扣税且状态仍 calculated | 校验连续性或提供 `employment_start_month`；缺月写 warning 并拒绝导出 |
| FIL-10 | `finance_vat/service.py:118`、`finance_iit/service.py:141` | Excel 公式注入：`business_type`/员工姓名以 `=` 开头被写成公式单元格（openpyxl 实测 `data_type='f'`） | 底稿被转发/打开时执行公式 | 用户文本统一加 `'` 前缀或 `data_type='s'` |

### 12.4 P3

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| FIL-11 | `finance_stamp_duty/service.py:72-73,12-26` | 未实现「应纳税额不足一角免征」；税目表缺证券交易（第 14 项）及代扣代缴说明 | `tax<0.1` 归零并标记；税目表加注证券交易不纳入的理由 |
| FIL-12 | 四模块 `service.py` 的 `round(x,2)` | 用 Python `round`（半偶 + 二进制误差）而非财税四舍五入：`round(0.015,2)==0.01`（应 0.02）、`round(2.675,2)==2.67` | 抽 `money()` = `Decimal(str(x)).quantize(0.01, ROUND_HALF_UP)` 四模块共用 |
| FIL-13 | `finance_stamp_duty/router.py:24`、`finance_vat/router.py:24` | `period` 只校验 `^\d{4}-\d{2}$`，月份可为 00/13/99 | 改 `^\d{4}-(0[1-9]\|1[0-2])$`；印花税补 `filing_frequency` |
| FIL-14 | 四模块 `router.py` 列表接口 | `page/page_size` 无上下界（`page=0`、`page_size=1000000` 可放大查询），与 hello 的 `Query(20,ge=1,le=100)` 不一致 | 统一 `Query(1,ge=1)`、`Query(20,ge=1,le=200)` |
| FIL-15 | 四模块 `router.py` 删除/导出 | 删除记录不清理导出 xlsx；导出恒定路径覆盖、无临时文件+替换 | 删除时顺带删产物；导出写临时文件后 `os.replace` |
| FIL-16 | `finance_dsf/service.py:42,79` | 校验用 `disability_fund_exemption`，非免征分支却写 `policy_key: disability_fund_tiered` | 按分支分别校验并写对应 key |
| FIL-17 | `finance_iit/service.py:90`、`finance_dsf/service.py:74` | 税额取整与四舍五入口径不统一 | 同 FIL-12 |

---

## 13. finance_hello / _template / debug_center

| 编号 | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| DBG-01 | P3 | `debug_center/service.py:174-197`、`router.py:25,135` | `ensure_seed_issues` 写种子未持 `_ISSUES_LOCK`；只在 GET 时播种 | 并发 GET/POST 可交错 load→save，后写者整表覆盖；首次调用是 POST 时种子被永久跳过（lifespan 兜底） | 播种纳入锁内，或锁内 exists + 追加式合并 |
| DBG-02 | P3 | `debug_center/service.py:380-401,356-377` | `client_errors` 轮转无锁：追加后读全量→写 tmp→replace，窗口内追加的行被覆盖丢失 | 高频报错页面恰逢轮转时丢日志 | 追加与轮转共用模块级锁，或轮转到时间戳新文件 |
| DBG-03 | P3 | `debug_center/service.py:293-296` | `list_runs` 对畸形 `totals` 做 `int(...)` 未捕获 | `result.json` 中 totals 出现非数字即 `GET /api/debug/runs` 与 `/summary` 500，整个调试看板不可用（违背「数据文件问题绝不 500」承诺） | 抽 `_as_int(v)` 宽容转换 |
| DBG-04 | P3 | `debug_center/service.py:302-313,450-475` | `list_runs`/`summary` 每次全量读取所有 `result.json`（含 pages/tests 明细）只为取 totals | 首屏接口随 run 数×文件大小线性变慢 | 报告器额外写 `summary.json`，列表只读摘要 |
| TPL-01 | P3 | `_template/manifest.py:18`、`finance_hello/service.py:11` | 模板注释称 prefix「必须 /api/finance/ 开头」，与 `spec.py:42-44` 允许 `/api/debug` 不符；示例 `name` 纯空格可落空串（`min_length=1` 已放行） | 新模块照抄会误解；示例表可产生空名条目 | 注释改为「/api/finance/ 或 /api/debug/」；name 去空白后再校验非空 |

### 13.1 已验证正确的点（四填报 + debug_center）

- 印花税 13 个税目与《印花税法》逐项一致（0.05‰/0.3‰/1‰/0.5‰/0.25‰），千分比换算正确；营业账簿 0.25‰ 即法定（已含减半），非二次减半
- 六税两费减半走 `policy_status`，2028 年抛中文错误；`half_saved` 与合计自洽
- 增值税一般纳税人税率白名单 13/9/6（0.17 被拒）；销项逐行取整合计；进项负数拒绝；`payable` 不为负
- 小规模超 30 万是**全额**计税而非仅超出部分；30 万边界 `<=` 与 19 号公告「含本数」一致
- 个税七级预扣率与速算扣除数（0/2520/16920/31920/52920/85920/181920）正确；36000 边界落 3% 档正确；本期税额用未取整累计相减避免逐月漂移；跨年重置
- 残保金 30 人（含）以下免征、`shortfall = max(0, N*ratio - disabled)`、≥1% 档「按 50% 缴纳」正确、`ratio` 限制 `0<r≤0.5`
- 政策登记 fail-closed：未登记 key 返回 `valid=False` + 中文提示；四模块对 `not valid` 一律 `ValueError→400`
- debug_center：issues 读改写持锁、tmp+`os.replace` 原子替换、畸形 JSON 降级、种子幂等；RUN_ID 正则 + 截图 `is_relative_to` 防穿越（`../`、绝对路径、`%2F` 全拒）；client_errors 体积/行数护栏与尾部读取有效
- 四个填报模块导出：文件名由正则化 period + 整型 id 拼成，用户文本不参与路径；下载取 basename；表头单元格含中文前缀不可注入；行数与明细严格一致

### 13.2 未能确认项

- 财政部公告 2023 年第 8 号原文（按政策记忆与代码自身注释判断，修 FIL-01 前建议以税务总局原文二次确认）
- 各地残保金执行差异：1.5% 地方调整、30 人免征是否仅限企业、差额人数是否进位
- 增值税小规模是否应支持按月申报（产品决策）
- 印花税「不足一角免征」在电子税局按分/按角舍入（本报告只指出代码未实现）
- 前端四个视图是否对 period 做 01-12 或季末约束（未逐字段核对）

---

## 14. 前端（Vue 3 + Element Plus + Vite）

> 抽样覆盖 97 个源文件与 50 个测试文件；协议/路径与后端逐条对照过。

### 14.1 P1

**FE-01 企业所得税企业档案核心字段前端可编辑、后端 API 不接收，静默不保存** `[确认]`
- 位置：`frontend/src/modules/finance-tax/views/EnterpriseFormView.vue:50-53,73-90`；`backend/app/modules/finance_tax/routers/enterprises.py:15-30,76-84`
- 现状：前端提交 `industry_type/employee_count/total_assets/is_restricted_industry`，后端 `EnterpriseCreate/Update` 只有 `name/tax_number/legal_person/contact_phone/address/industry/tax_type`，Pydantic 默认 `extra=ignore`，多余字段被丢弃，接口仍返回 200 + 成功 toast。
- 影响：① 行业、从业人数、资产总额、限制行业四个字段新建/编辑都不落库，刷新即回退（列表还在展示，永远为空/否）；② 这三项正是小型微利判定输入，恒为 0 → 小微判定被「默认满足」，直接放大 TAX-02；③ 用户看到「已保存」，属静默数据丢失。
- 建议：后端补字段并统一命名（`industry` vs `industry_type`），加真实 PUT 往返测试锁定。

**FE-02 发票批次上传沿用全局 10s 超时，而后端是同步解析** `[确认]`
- 位置：`frontend/src/modules/finance-invoice/views/BatchListView.vue:93-105`、`frontend/src/api/index.js:18-21`、`backend/.../finance_invoice/router.py:51-87`
- 现状：
  ```js
  await api.post('/finance/invoice/batches', fd, { headers: {...} })  // 无 timeout 覆盖，全局 10000ms
  ```
  后端在请求内 `await asyncio.to_thread(service.create_batch, ...)` 逐张解析后才返回；同仓其它重活接口都放宽（jobs 120s、助手上传 120s、费用 120s、POS 600s）。
- 影响：ZIP/多张 PDF 解析超 10s → 前端报「请求超时，请确认后端服务已启动」，但批次已入库；用户重传 → 重复批次与重复明细。页面提示「上传后自动建批次并后台解析」与实现（同步解析）不符。
- 建议：该调用显式放宽 timeout（120s）并改文案；理想是改成异步任务 + 轮询。

### 14.2 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| FE-03 | `finance-cashflow/.../CashflowRunView.vue:178`、`finance-invoice/.../BatchDetailView.vue:148`、`finance-ledger-personal/.../TransactionListView.vue:130`、`finance-pos/.../PosRunView.vue:170`、`finance-tax/.../FinancialImportView.vue:74`、`TaxReturnListView.vue:106`、`finance-board/.../BoardView.vue:61` | 下载类 URL 硬编码 `/api`，与 `API_BASE`/`api.defaults.baseURL` 混用（另 5 处用 baseURL、2 处用 API_BASE） | `VITE_API_BASE` 配成绝对地址/别的前缀时这 7 处全部 404，且都是下载/看板不可替代功能 | 统一 `fileUrl(path)` helper 基于 `API_BASE`，补单测禁止字面量 `/api/` |
| FE-04 | `finance-ledger-bip/views/ImportWizardView.vue:44-54` | 列映射下拉用了 `el-select` 不存在的 `#prepend` 插槽（属 `el-input`） | 映射区显示一串无标签下拉，用户无法知道哪行是日期/借方/科目编码，而这正是自动识别失败时唯一入口 | 改用 `el-form-item :label` 包裹 |
| FE-05 | `finance-tax/views/TaxCalculateView.vue:93-98` | 优惠政策标签读 `p.title/p.policy_number`，后端返回 `{name, description, policy_basis, amount_wan}` | 命中优惠时渲染若干空 tag，`:key` 全 undefined；用户看不到适用哪条政策 | 改读 `p.name`/`p.policy_basis` 并展示 `amount_wan` |
| FE-06 | `finance-stamp-duty/.../StampDutyView.vue:143-148`、`finance-iit/.../IitView.vue:131-136`、`finance-vat/.../VatView.vue:144-149`、`finance-dsf/.../DsfView.vue:116-121` | 四个填报模块删除无二次确认（其它模块有 `ElMessageBox.confirm`） | 含已生成底稿的申报留痕单击即永久删除 | 统一加确认弹窗（带期间/纳税人信息） |
| FE-07 | `finance-pos/.../PosRunView.vue:140`、`finance-cashflow/.../CashflowRunView.vue:167` | `ElMessage.success(data.status === 'ok' ? ... : '执行失败：...')` | HTTP 200 + `status=failed` 用绿色成功样式提示，用户易漏看错误 | 按 status 分流 success/error |
| FE-08 | `grep "@media" frontend/src` → 0 命中；`AppLayout.vue:3`、`AssistantView.vue:309`、`NotesView.vue:299`、`ScheduleView.vue:359`、`GlobalSearch.vue:9` | 全站零响应式，固定 220/320/380/340px | 1280×720 以下侧栏+固定列+搜索框挤压主内容；助手页左列在窄屏占满 | 至少加 `<1280px` 断点（侧栏收起、左列改抽屉、搜索框 max-width） |
| FE-09 | `ScheduleView.vue:49`、`NotesView.vue:23-28`、`DashboardView.vue:70-75,94-99,119-124`、`SessionList.vue:16-34`、`AgentQuickList.vue:24-47` | 可点击区域是裸 `div`/`el-icon`，无 `tabindex`/`role`/键盘事件/`aria-label` | Tab 无法聚焦，读屏只读到图标无名称 | 列表项 `role=button tabindex=0 @keyup.enter`，图标按钮改 `el-button` 或加 aria-label |
| FE-10 | `EnterpriseFormView.vue:73-76`、`debug-issues/views/IssuesView.vue:50-55` | 提交锁 `submitting=true` 晚于 `await validate()`（项目已把该模式定为 BUG-RACE-001 并在 Tasks/Schedule/Jobs 修复，测试只覆盖这三个文件） | 双击/回车连打在校验窗口内发两次 POST，建重复企业/问题 | 锁前移 + `if (submitting.value) return`，把两文件纳入回归测试 |

### 14.3 P3

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| FE-11 | `SettingsDrawer.vue:943-967` | 「快速预设」不回填 `preset_key`/`models_url`（预设数据本身有） | 一并填充，否则在线同步只能走目录兜底 |
| FE-12 | `SkillPackView.vue:130,226-235` | 防抖定时器无 `onBeforeUnmount` 清理（Notes/Knowledge 都有） | 补清理 |
| FE-13 | `finance-iit/views/IitView.vue:42` | 列宽写死 `style="width: 100"`（缺 `%`，同文件其它列均 `100%`） | 改 `100%` |
| FE-14 | 全站 `onUploadProgress` 0 命中；`AbortController` 仅 ChatPanel | 长请求（费用 120s、POS/现金流 600s、4 个上传）无进度、无取消 | 加进度展示；长任务接口加取消或改任务化轮询 |
| FE-15 | `ChatPanel.vue:585-595,576-578` | 思考深度保存失败不回滚显示值 | 失败回滚到上次成功值 |
| FE-16 | `finance-vault/views/QueryView.vue:69-73` | CSV 导出 `click()` 后立即 `revokeObjectURL`，`a` 未挂 DOM | `setTimeout(revoke, 1000)` 或走下载接口 |
| FE-17 | `AppLayout.vue:103`、`DashboardView.vue:148`、`QuickAddView.vue:110-111` | 「今天/本月」在 setup 固化，`greeting` 虽为 computed 但无响应式依赖 | 用 ref + 定时到整点/零点刷新 |
| FE-18 | `finance-invoice/views/BatchDetailView.vue:51,63,123-132` | 明细行 blur 即 PATCH 双字段，无序号守卫/防抖 | 只提交变更字段，或每行 in-flight 序号 |
| FE-19 | `SettingsDrawer.vue:761-763,1216-1219` | 能力探测无重入锁；实测按钮全局单锁导致点击静默无响应 | 统一忙态守卫 + 「正在测试中」提示 |
| FE-20 | `src/modules/index.js:9-24`；`tests/modules.test.js` | 模块注册无契约校验：name 缺失/重复 path 会静默消失，无测试兜底（当前 17 个 manifest 已人工核对无重复） | 注册时断言并在 dev 期 console.error；补唯一性测试 |
| FE-21 | 6 个 `finance-*.test.js`、`interaction-submit-race.test.js:48-68`、`deep-tasks.test.js:40-45`、`vite.config.js test.environment=node`、`package.json` 未声明 `@vue/compiler-sfc`/`@babel/parser` | 测试质量：6×3 自证式 manifest 测试；42% 文件为源码文本断言；node 环境无法测模板；两个解析器靠传递依赖 | 合并为一份全量契约测试；引入 jsdom + `@vue/test-utils` 覆盖关键页面；显式声明 devDeps |
| FE-22 | `finance-ledger-personal/views/StatsView.vue:84-108` | 自定义 echarts 两处无 resize 监听（`BaseChart.vue` 有） | 补 resize + dispose |

### 14.4 已验证正确的点（抽样）

- 路由：17 个模块 path/name 全唯一；`/:pathMatch(.*)*` 在模块路由之后；代码中 41 个 `$router.push({name})` 全部存在
- `modules/index.js` glob 跳过 `_shared`；懒加载 `component: () => import(...)` 全模块一致
- 列表请求普遍有序号守卫 + catch；`no-unhandled-request.test.js` 用 AST 全量扫描 `src` 并有正反夹具
- SSE：AbortController、`AbortError` 不算失败、失败回滚乐观气泡并恢复草稿与附件、unmount 时 abort
- XSS：唯一 `v-html` 走 `markdown-it({html:false})`，链接强制 `rel="noopener noreferrer"`，异常 fallback `escapeHtml`；看板 iframe `sandbox="allow-scripts"`；日志用 `<pre>` 插值
- `api/index.js` 把 422 的 `detail[]` 拼成「字段：中文消息」，`ECONNABORTED` 单独文案；无 Key/mask 泄漏
- 上传类操作均有忙态锁与 `:loading`（除 FE-10 两处）
- 请求基址语义正确：`/health` 对应 `/api/health`；`/providers/presets` 对应别名；尾斜杠与后端 `@router.post("/")` 一致
- 日期/时区：`fmtDT/fmtDate` 用 dayjs 本地时区（`_shared/dates.js` 明确注释禁用 `toISOString`），日历 `value-format` 全程本地时间

### 14.5 未能确认项

- 未运行 `npm test`/`npm run build`（本轮只读约束），「745/759 passed」与当前代码是否仍全绿未验证
- 发票批次解析常态是否 >10s（取决于文件量与引擎），结论基于「同步解析 + 10s 全局超时」的代码事实
- `#prepend` 依据本机 element-plus 2.14.5 的 `select2.mjs`（仅 `$slots.prefix`），未在浏览器截图确认
- 小微判定是否应强制手工填从业人数/资产总额（产品口径）
- `DsfView.vue` 的 `c.payable.toLocaleString()` 在 `payable` 为 null 且非 exempt 时是否崩（未追到构造点）

---

## 15. 工具链 / 打包发布 / 测试体系

### 15.1 P0

**TL-01 分发包内含真实客户名称、员工姓名与内部业务存档路径** `[确认]`
- 位置：`dev/verify_desensitized.py:24`、`dev/desensitize_finance_data.py:37,45,47,54`、`dev/desensitize_board_data.py:70`、`backend/tests/test_module_finance_pos.py:369`、`frontend/tests/finance-pos.test.js:150`
- 现状：`SENSITIVE` 列表硬编码了 12 个真实客户/项目/员工名称（**本报告不重复列出**，以免二次扩散；清单在 `dev/` 源码中，需按下方建议改为本地词表）；`POS_SRC` 指向真实业务存档路径。
- 复核证据：对 `backups/releases/个人工作台-v1.16.1-20261030.zip` 只读解包——共 562 条目，`dev/desensitize_finance_data.py` 与 `dev/verify_desensitized.py` 在包内，均命中真实客户名/项目名/员工姓名（此处不再展开）。
- 影响：`prune_internal()` 只删根级 `.md` 与 `20261012New/`（`dev/package_release.py:202-207`），`dev/` 与两个 `tests/` 全量入包；`scan_secrets()` 只反查 13 条映射表真实值，这些名称不在映射表内 → 三重扫描全部放行。**对外的「脱敏分发包」实际带出客户与员工隐私**。
- 建议：① 增加 PII 硬编码/正则黑名单（直接复用 `verify_desensitized.SENSITIVE` 词表）；② 打包剔除 `dev/`、`backend/tests/`、`frontend/tests/`（或至少剔除含真实样本的文件）；③ 上述 5 处改读环境变量/映射表，不落字面值；④ **已生成的 20261030 ZIP 应视为已泄露，修复后重新打包并销毁旧包**。

### 15.2 P1

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| TL-02 | `dev/package_release.py:253-258`、`.gitattributes:2-10`、`开机自启动.vbs:14-15` | 扫描器严格 UTF-8 解码，失败即当二进制跳过；`.gitattributes` 不覆盖 `.bat/.vbs` | 实测 ZIP 内 `开机自启动.vbs` 为 GBK，含 `示例主体13\...` 本机路径却完全不参与扫描；6 个中文 bat/vbs 是扫描盲区 | 非 UTF-8 改按字节/GBK/UTF-16 逐编码扫描；`.gitattributes` 加 `*.bat/*.vbs`；bat/vbs 中盘符路径列为结构红线 |
| TL-03 | `backend/scripts/backup_db.py:16-18,45-46`、`使用说明.md:754` | 备份只含 `workbench.db`；`finance` 6.6MB、`uploads` 261KB、`expert-packs` 244KB、`skills` 572KB、`workbuddy_import.json`、`sanitize-map.local.json` 均不在备份内 | 磁盘故障或「只带 DB」迁移后，导入批次、聊天附件、自建专家包、已安装技能全部丢失且页面引用断链 | 备份打包 `data/`（DB 用 backup API + 四个目录 + 映射表）；补 restore 脚本与冒烟；或文档明确「仅数据库」 |
| TL-04 | `backend/scripts/sync_zcode_models.py:40-41,142` | 明文写入 API Key（未 `seal`），探测时把原值直接透传 | 覆盖 UI 已加密值；DB 内变明文；若原本是 `dpapi:` 密文则把密文当 Bearer 发送 | 改 `seal()/unseal()`，或删除脚本改调 `config_store` 公开写入函数 |

### 15.3 P2

| 编号 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| TL-05 | `停止服务.bat:9,22,36` | `findstr /i "uvicorn python"` 是 OR 匹配（空格分隔），循环只保留最后一个 PID；端口匹配无词边界 | 任何 Python 进程监听 8000（Jupyter/http.server/别的 uvicorn）都会被 `taskkill /F`；`:80001` 也会命中 | 匹配完整命令（`-m uvicorn app.main:app` + 工作目录）并校验可执行路径；端口加词边界 |
| TL-06 | `启动工作台.bat:103-110`、`开机自启动.vbs:31-34` | 只做 `netstat` 命中即认为「服务已在运行」 | 端口被别的程序占用时给出误导性成功提示，用户看到别人的页面 | 复用停止脚本的 PID/命令行校验，不一致则提示端口冲突 |
| TL-07 | `dev/package_release.py:172-173` | venv 检查无视 `--skip-smoke`，提示与实际行为矛盾 | 按提示加 `--skip-smoke` 仍失败，无 venv 机器无法只出包 | `preflight(skip_smoke=...)` |
| TL-08 | `dev/package_release.py:387-395` | 冒烟用仓库 venv 且继承父进程环境（仅加两个编码变量） | 从未用包内 requirements 装依赖，缺依赖也能过；开发机 export 的 `DATABASE_URL/FINANCE_DATA_DIR/...` 会让冒烟写真实目录 | scrubbing env（只留 PATH/TEMP）；临时 venv 装包内 requirements 或校验 import 全集 |
| TL-09 | `dev/package_release.py:282-289,97-99` | 密钥正则 ±80 字符窗口内出现 test/fake/example 即降级为 warning | 凡在 `test_*.py`/注释「测试」附近的真实 `sk-...` 都不会中止打包；而 tests 正入包 | 合成标记只允许同行紧邻赋值；tests 目录要求零命中；硬失败优先 |
| TL-10 | `backend/requirements.txt:1-23`、`requirements-lock.txt:2` | 缺两个直接依赖（`PyYAML`、`python-multipart`），仅靠 agentscope 传递依赖被动装上；lock 文件无任何脚本使用 | 上游依赖树一变，`import yaml` 启动即崩；收件人装的是浮动区间，与开发机 lock 不一致 | 两者写入 requirements；安装入口改 lock 或 `-c requirements-lock.txt` |
| TL-11 | `test_memory.py:21`、`test_rag.py:22`、`test_mcp.py:87`、`test_skill_store.py:34`、`test_skill_market.py:54`、`test_module_finance_pos.py:64`、`test_module_finance_cashflow.py:52` | 7 个文件覆写 `client` 夹具但不重置数据库 | 跨文件残留行带进后续用例，换执行顺序/单文件重跑可能失败 | `drop_all/create_all` 提到 conftest autouse |
| TL-12 | `conftest.py:48-53`、`test_swarm3_data_isolation.py:8-14` | 默认隔离只覆盖 finance/debug；`uploads`/`skills`/`expert-packs` 仍指向真实目录，守护测试只查 finance | 新写用例忘记 patch 就写真实 `backend/data`，守护检测不到（已确认 12 个文件靠显式 patch 兜底） | conftest 默认改道三个目录，守护扩展到四个 |
| TL-13 | `conftest.py:54-56`、`test_config_paths.py:48-55` | 测试仍加载真实 `backend/.env`，却断言硬编码默认值 | 开发者按 `.env.example` 配了 `SOFFICE_PATH`/`QWENPAW_SKILL_POOL` 后本套件必红；费用测试会按真实配置找到兄弟引擎并执行外部代码 | conftest 用临时 .env 或 `Settings(_env_file=None)` |
| TL-14 | `main.py:117-176`、`backend/scripts/backup_db.py`、`dev/package_release.py` | 三条关键路径零测试：启动期轻量迁移、备份、打包扫描 | 最危险的启动路径（对真实库 ALTER）失败只留 warning；备份保留策略与扫描规则无回归 | 迁移加「旧库缺列→补列→幂等」；备份加 WAL/并发/保留；扫描抽纯函数加单测 |
| TL-15 | `frontend/scripts/write-build-stamp.js:19-20` | `needs-build` 内容哈希只含 `src` 与 5 个根文件，不含 `scripts/`、`.env*` | 改构建脚本或设 `VITE_API_BASE` 不触发重建，继续用旧产物 | 把 `scripts/` 与存在的 `.env*` 纳入哈希 |
| TL-16 | `开机自启动.vbs:42-48` | 不检查 Node 是否存在、不检查构建退出码 | 分发包「只需 Python」的收件人开机会弹两个失败控制台；开发机构建失败静默用陈旧 dist | 与 bat 对齐：先判 dist/`where node`，失败 MsgBox 明确提示 |
| TL-17 | `frontend/vite.config.js:16-18`；实测 dist 最大 1.3MB/1012KB/768KB | `chunkSizeWarningLimit: 1500` 掩盖大包，无 `manualChunks` | echarts/ag-grid 打进业务 chunk，首屏解析成本高，体积回退不告警 | 恢复默认阈值 + vendor 拆分 + 体积预算 |

### 15.4 P3

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| TL-18 | 5 个 bat 共 20+ 处 `pause`/`choice` | 无人值守调用必挂住（Windows `choice` 不读重定向 stdin） | 统一 `if "%CI%"=="1" exit /b` 或 `--yes` 旁路 |
| TL-19 | `启动工作台.bat:3`、`停止服务.bat:3`、`开发模式.bat:3`、`开机自启动.vbs:22`、`vite.config.js:11` | 端口 8000 在 5 处硬编码，改 `.env` 的 PORT 不会带动 | 由单一来源派生并加启动自检 |
| TL-20 | `开发模式.bat:16-19` | 不检查端口占用、固定等 4 秒；Vite 未 `--strictPort` | 先做监听检查；`--strictPort`；等 5173 就绪再开浏览器 |
| TL-21 | `备份数据.bat:16-20` | 失败分支只 `pause`，无 `exit /b 1` | 失败传播退出码 |
| TL-22 | `dev/package_release.py:424-434,431-432,360-369` | 冒烟失败不清理（rmtree 在 try/finally 外）；`if returncode not in (0,None): pass` 死代码；`free_port` TOCTOU | 清理移入 finally；删死代码；bind 失败重试 |
| TL-23 | `dev/package_release.py:202-207` | 只剔除根级 md，`dev/e2e/*.md` 内部报告随包 | 递归白/黑名单 |
| TL-24 | `dev/install_filter.bat:6-10` | `git read-tree`/`checkout-index -f -a` 覆盖全部工作区，未提交修改无提示丢失；`required false` 允许静默明文提交 | 先 `git status --porcelain` 非空即中止；`required` 改 true 或加提交钩子旁路检查 |
| TL-25 | `dev/git_sanitize_filter.py:20-25,36-39` | 映射表出现 `"real": ""` 时 `str.replace("", x)` 会在每个字符间插入占位符 | 加载时拒绝空/长度<2 的 real |
| TL-26 | `debug_center/service.py:126` vs `backend/.env.example` | `DEBUG_DATA_DIR` 代码支持但未列入「完整清单」 | 补入并标注「仅测试/诊断用」 |
| TL-27 | `.github/workflows/ci.yml:10-27` | 无 pip/npm 缓存；Node `"24"` 浮动（低于安全下限 24.22.1 时无感）；不用 lock | `cache: pip/npm`；`node-version-file: .nvmrc`；后端用 lock 或 `-c` |
| TL-28 | `vite.config.js:19-21`、`package.json:29-33` | 前端无 DOM 测试（`environment: node`，无 jsdom/@vue/test-utils），21/50 文件为源码文本断言 | 对 3-4 个高频视图引入 jsdom 挂载冒烟 |
| TL-29 | `frontend/tests/no-unhandled-request.test.js:28-30` | `@vue/compiler-sfc`、`@babel/parser` 靠传递依赖，未在 package.json 声明 | 加入 devDependencies |
| TL-30 | `test_dashboard_search.py:43`、`test_bugfix_sched_misc.py:120`、`test_bugfix_debug_finance_misc.py:298`、`test_swarm_bip_tax.py:156,181` 等 | 时间/睡眠依赖，无 pytest-timeout | 注入 now/today；装 pytest-timeout 设 120s |
| TL-31 | `backend/requirements.txt:7` | `pytest` 打进最终用户运行依赖 | 拆 `requirements-dev.txt` |
| TL-32 | `backend/scripts/sync_zcode_models.py:27` | `--config` 无值时 `sys.argv.index(...) + 1` 抛 IndexError | 用 argparse |
| TL-33 | `开机自启动.vbs:45,47,52` | 依赖中文 ANSI 代码页；构建时 `window style 1` 弹可见控制台 | 构建调用改隐藏并重定向日志；README 标注 GBK 依赖 |

### 15.5 已验证正确的点（抽样）

- 6 个 bat/vbs 全部 CRLF、GBK 可完整解码，`chcp 936` 与文件编码一致；`启动工作台.bat` 等待循环无延迟展开陷阱
- `backup_db.py` 用 `sqlite3.Connection.backup`（在线一致快照）、源库 `mode=ro`；实测库 `journal_mode=delete`，不存在漏 `-wal`；最新备份 `integrity_check=ok`
- 打包用 `git archive HEAD`（非 clone/bundle），`extractall(filter="data")`；实测 ZIP 内 `node_modules` 0、`.env` 0、`backend/data` 仅 3 个 `.gitkeep`、`AGENTS.md` 已剔除、`dist/.build-stamp.json` 存在
- CI 在测试后确实执行 `npm run build`，无 `continue-on-error`；`test_stop_script.py` 有真实 findstr 回归
- 前端生产 API 走相对 `/api`；`prebuild` 清 dist、`postbuild` 写内容哈希印章，配套 9 个 build-stale 用例
- conftest 在 `import app` 前写好 4 个隔离环境变量并清空 4 个 `LLM_*`，tempdir 钉到经写探针验证的临时目录；每个 `client` 用例 drop/create
- 外部网络请求全部 mock（`httpx.MockTransport`、`urlopen` 桩）；`test_mcp.py` 用临时本地假 server；看板 fixture 固定 `_scheduled_task_available→False`，不触真实计划任务
- 80 个后端测试文件与 16 个模块基本一一对应，`_template` 由 `test_modules.py` 覆盖，无整模块空白

### 15.6 未能确认项

- CI 是否真实长期为绿（仓库内无运行记录/徽章）
- 映射表 13 条真实值是否穷尽本机敏感串（只能确认「已知 5 个文件含真实名称/路径却不被拦」）
- `tar.extractall` 的 `TypeError` 回退分支（Python <3.12）未在本机验证
- 按约束未执行 bat/vbs，端口占用/重复启动/开机自启的实机行为仅由静态阅读与既有产物推断

---

## 16. 澄清：以下「像缺陷」但属设计取舍，不计入统计

| 现象 | 结论 | 说明 |
|---|---|---|
| 发票跨批次重复识别 | **设计如此** | 幂等键=发票号码+校验码，跨批次命中即标 duplicate，目的是防重复报销；单批内保留首次是正确口径 |
| BIP 重复导入跳过 | **设计如此** | 幂等导入本身正确；真正的缺陷是判重键缺期间（BIP-01）与跨来源共用键（BIP-03） |
| 单机无鉴权、默认只绑回环 | **设计如此** | 产品定位本机单人；非回环绑定的风险与「警告与绑定脱钩」已单列 CORE-06 |
| `_template` 目录不加载 | **设计如此** | 注册器按 `_` 前缀跳过；模板注释与实际校验不符才是 TPL-01 |
| 数据仓只读 SQL 强制追加 `LIMIT` | **设计如此** | 防全表拉取；校验器误拒合法语法才是 VAULT-03 |
| 费用/发票/POS/现金流/看板引擎位于兄弟项目 | **范围外** | 本仓库只做上传、argv 调用、产物归档的薄壳；引擎内部公式/阈值/Decimal 使用不在本次审计范围，建议对五个引擎另做一轮专项审计 |
| 测试使用合成 Key、脱敏数据 | **正确实践** | 与「真实 Key 不入库/不进日志/不进证据」的约定一致 |
| `job-*` 前缀是内部会话 | **设计如此** | 定时任务/记忆抽取专用；用户会话可占用同前缀才是缺陷（AGT-08） |
| 现金流子进程 1800s 超时 | **设计如此** | 引擎处理大文件需要长窗口；缺少运行互斥才是缺陷（CF-01） |
| 申报记录删除不删导出文件 | **缺陷** | 已列 FIL-15，不在此澄清 |

---

## 17. 修复路线图（建议按批次推进，每批独立回归）

### 批次 0：数据外泄与备份（当天）

| 项 | 动作 | 验收 |
|---|---|---|
| TL-01 | 打包剔除 `dev/`、`backend/tests/`、`frontend/tests/`；扫描器加 PII 词表；5 处真实字面值改配置/环境变量 | 重新打包后解包全量扫描：PII 词表 0 命中；旧 ZIP 销毁 |
| TL-02 | 扫描器支持 GBK/UTF-16/字节级；`.gitattributes` 覆盖 bat/vbs；bat/vbs 盘符路径列红线 | 合成 GBK 文件含真实值 → 打包中止 |
| TL-03 | 备份打包 `data/` 全目录（DB + 4 个文件目录 + 映射表），补 restore | 空库恢复后页面引用不 404；备份清单含 4 个目录 |
| TL-04 | `sync_zcode_models.py` 改 `seal/unseal` | 脚本写入后 DB 值以 `dpapi:` 开头；探测用明文 |

### 批次 1：计税正确性（需税务口径复核后改）

| 项 | 动作 | 验收 |
|---|---|---|
| FIL-01 | 残保金改 `base * 0.9`，文案改「按 90% 缴纳」，**同步改 2 条测试断言** | 100 人/1000 万/0 残疾 → 135,000；2028 年仍拒绝 |
| TAX-01 | 删除 `calculate_tax` 本地阶梯，保存复用引擎结果 | 同一输入：`/calculate` 税额 == 落库税额 |
| TAX-02 | 小微判定接 `SMALL_MICRO_CRITERIA`，加 300 万/限制行业 | 利润 1000 万 → 250 万（25%），非 50 万 |
| TAX-06 | 进项转出改为加项 | 销项 13/转出 10 → 23 |
| TAX-03/04/05 | 修 A200000 第 5 行重复、A100000 高新叠加、A105000 成本方向 | 三个最小用例锁定 |
| TAX-08 | 小微判定改用弥补后所得 | 利润 400 万 − 亏损 200 万 → 按小微 10 万 |
| FIL-02 | 个税累计不再逐月截断 | 1 月 2 万 + 2 月 3 千 → 390 元 |
| FIL-03 | 残保金加 2 倍社平上限 | 高薪场景按 2 倍计征并 warning |
| FE-01 | 后端补 4 字段并统一命名 | 新建/编辑 → 刷新后值保留；PUT 往返测试 |

### 批次 2：数据完整性

- **BIP-01**：判重键加 period（含真实多月样本回归）；**BIP-02**：两级表头映射 + 金额列全缺时 400
- **TAX-09**：所有计税入口接 `policy_status(tax_year)`；前端加「所属年度」
- **PL-01/02/03**：更新显式 null → 422；金额 Decimal；CSV/工具校验有限值与上限
- **TAX-17**：申报记录唯一约束/查重 409 + 删除/更正接口
- **CORE-10**：迁移逐列 try；**CORE-23/24**：seed 删除语义与 reset 覆盖模块表

### 批次 3：安全与可恢复性

- **AGT-01**：只读模式禁 Bash；**AGT-03**：技能包原子替换；**AGT-04**：Key 迁移接线；**AGT-05**：禁止跟随重定向
- **CORE-06**：真实绑定地址校验，非回环拒绝或显式确认
- **POS-01 / CF-01**：运行互斥 + 启动回收 stale running
- **BRD-01**：以 `run.lock` 判运行中，修 600s 空等
- **TL-05/06**：停止/启动按完整命令行 + 可执行路径校验，端口词边界

### 批次 4：契约与体验

- FE-02（上传超时）、FE-03（fileUrl 统一）、FE-04（映射标签）、FE-05（优惠标签）、FE-06（删除确认）、FE-07（失败样式）、FE-10（提交锁）
- CORE-03/04/05（显式 null、jobs 不变量）、INV-02（冲红金额 None）、INV-03（批次删除与清理）
- FIL-04/05/06/07（VAT 征收率白名单、申报周期、留抵、免征政策）、FIL-08/09（个税工号与月份连续）

### 批次 5：质量与测试

- TL-10/11/12/13/14（依赖声明、测试隔离、真实 .env、关键路径测试）
- FE-21（前端测试引入 DOM 挂载）、TL-28/29（jsdom 与测试依赖）
- TL-15/16/17（构建哈希、vbs 构建检查、chunk 拆分）
- 其余 P3 按模块顺手清理

### 建议的回归口径（每批必跑）

```bash
cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q
cd frontend && npm test && npm run build
```

新增修复必须带「先红后绿」测试；涉及计税的修复必须补一条「页面展示 == 落库 == 导出底稿」三方一致的断言。

---

## 18. 审计覆盖、方法与局限

### 18.1 已精读范围

| 域 | 文件 |
|---|---|
| 平台核心 | `main.py`、`database.py`、`config.py`、`models.py`、`schemas.py`、`utils.py`、`upload.py`、`document_text.py`、`import_limits.py`、`seed.py`、`init_db.py`、`scheduler.py`、`routers/*`（除 assistant） |
| 智能体 | `agents/*` 全部 19 个模块 + `routers/assistant.py` |
| 财务模块 | `modules/finance_tax/**`、`finance_ledger_bip`、`finance_ledger_personal`、`finance_vault`、`finance_invoice`、`finance_expense`、`finance_pos`、`finance_board`、`finance_cashflow`、`finance_stamp_duty`、`finance_vat`、`finance_iit`、`finance_dsf`、`finance_hello`、`debug_center`、`_template`、`registry.py`、`spec.py` |
| 前端 | `src/**` 97 个文件 + `tests/**` 50 个文件（抽读全部 manifest、api、router、布局、助手组件、17 个模块视图与关键测试） |
| 工具链 | `dev/**`、`backend/scripts/**`、5 个 bat、1 个 vbs、`.github/workflows/ci.yml`、`vite.config.js`、`package.json`、`requirements*.txt`、`tests/conftest.py` |

### 18.2 审计中实际执行的聚焦测试（只读、隔离环境）

| 命令 | 结果 |
|---|---|
| `pytest tests/test_module_finance_tax.py -q` | 7 passed（未覆盖 TAX-01/02 等，缺陷在盲区） |
| `pytest tests/test_module_finance_ledger_bip.py tests/test_module_finance_vault.py -q` | 36 passed（未覆盖 BIP-01/02） |
| `pytest tests/test_module_finance_board.py tests/test_module_finance_pos.py -q` | 27 passed（未触发真实计划任务） |
| `pytest tests/test_module_finance_stamp_duty.py tests/test_module_finance_vat.py tests/test_module_finance_iit_dsf.py -q` | 30 passed（FIL-01 的错误值被断言固化） |

> 说明：聚焦测试全绿并不代表无缺陷——本轮 5 个 P0 全部落在现有测试的盲区，其中 FIL-01 是测试反向锁定错值，TAX-01/FE-01 是「前端/路由层另写一套、引擎测试覆盖不到」。

### 18.3 局限

1. **未做全量回归**：本次以静态审计为主，未运行 `pytest tests`/`npm test`/`npm run build`；「当前代码是否仍全绿」以各域聚焦测试与代码事实为准，修复批次开始时须先跑一次基线。
2. **真实数据未读取**：除脱敏样本的只读复算（BIP）与发布 ZIP 的只读解包（PII）外，未打开真实 `backend/data`，因此「生产库是否存在 NULL/脏行」类结论一律标为未能确认。
3. **外部引擎未审**：费用/发票/POS/现金流/看板五个引擎在兄弟项目，本报告只审本仓库的调用与归档层。
4. **政策原文未联网核对**：FIL-01、TAX-22、TAX-23 等涉及政策口径的结论标注 `[待税务口径复核]`，修复前应以税务总局原文/主管税务机关口径二次确认。
5. **第三方库行为**：pdfplumber 内部流限制、AgentScope 并发语义、urllib 重定向头复制等按公开行为判断，未做抓包/压测复现。

---

## 19. 附：缺陷编号索引（便于跟踪）

- 平台核心：CORE-01 … CORE-29
- 智能体：AGT-01 … AGT-24
- 企业所得税/增值税/附加税/递延/年表：TAX-01 … TAX-33
- BIP：BIP-01 … BIP-12
- 个人记账：PL-01 … PL-12
- 数据仓：VAULT-01 … VAULT-06
- 发票：INV-01 … INV-05
- 费用：EXP-01 … EXP-03
- POS：POS-01 … POS-04
- 看板：BRD-01 … BRD-05
- 现金流：CF-01 … CF-05
- 四填报：FIL-01 … FIL-17
- 调试中心/模板：DBG-01 … DBG-04、TPL-01
- 前端：FE-01 … FE-22
- 工具链：TL-01 … TL-33

---

## 21. 2026-11-11 全量审计台账（增量）

> 本章为**增量台账**，由 Task 0 治理蜂群建立。它不回溯修改 §0.3 的 2026-10-30 统计（215 项）与 §17 的路线图，也不改写任何历史 dated 报告；历史结论按快照语义保留，状态变化只在本章与 `2026-11-11_检查结果.md` 登记。
>
> **本章没有运行测试、没有修复代码。** 所有“已确认”仅表示代码原文/行号成立或已由只读命令复核；需真实运行、真实数据、官方口径或真人参与的项目，均标为“待复现”“外部阻断”或“需决策”。

### 21.1 编号与统计

- 本轮编号：`R11-001` … `R11-112`（Round 2026-11-11）。
- 不复用既有编号空间：本章的 CORE/AGT/TAX/FIL/BIP/INV/POS/CF/BRD/FE/TL 与 `2026-11-03_检查结果.md` 的 `L-001…L-177` 均只作“来源编号”引用。
- 逐项明细（含文件行号、owner、关闭证据）在 `2026-11-11_检查结果.md` §7–§11；本章只做索引与差异说明。

| 严重度 | 条数 | 已确认 | 待复现 | 外部阻断 | 需决策 |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0 | 3 | 3 | 0 | 0 | 0 |
| P1 | 11 | 10 | 1 | 0 | 0 |
| P2 | 59 | 53 | 1 | 4 | 1 |
| P3 | 39 | 37 | 2 | 0 | 0 |
| **合计** | **112** | **103** | **4** | **4** | **1** |

### 21.2 领域分布

| 领域 | 条数（P0/P1/P2/P3） | 主要 Owner |
| --- | --- | --- |
| 敏感信息与脱敏门禁 | 5（3/1/0/1） | T1 |
| 测试、CI、版本与发布 | 11（0/1/8/2） | T7 |
| AI、知识库、MCP、定时任务 | 12（0/0/4/8） | T2 |
| 税务、财报与申报 | 11（0/3/5/1） | T3 |
| POS、银行、BIP、发票、数据池 | 14（0/2/9/2） | T4（删除/改名端点与 T6 协同） |
| 经营、调度、看板、文件边界 | 14（0/0/13/1） | T5 |
| 前端交互、导航、竞态 | 10（0/2/6/2） | T6 |
| 开发文档与 API 契约 | 18（0/0/8/9） | T8 |
| 用户手册与页内帮助 | 12（0/1/4/5） | T9 |
| 外部阻断/决策 | 5（0/0/2/0，另计 3 项跨领域） | 用户 + T10 |

> 分布表按“主要归属”计数，与 §21.1 的严重度计数是同一批 112 行的不同切面，故合计不等于 112 以外的任何数字。

### 21.3 与既有编号的冲突与更正

| 既有编号 | 原状态 | 更正 |
| --- | --- | --- |
| L-151（P1） | “已修待验（首跑外阻）”/“CI 从未执行” | **过期**。CI 已运行 12 次，最近 `0d0a87c` 为 failure（后端步骤约 42 分钟退出码 1，前端/构建 skipped）。拆为 R11-005（红灯根因，待复现）、R11-052（看板测试依赖真实目录名，CI 必红）、R11-058（缺 JUnit/artifact/超时）。 |
| L-156（P2） | “仅历史提交含真实路径，当前 HEAD 已占位” | **过期**。当前 `origin/master` 树仍含个人存档盘绝对路径与 3 个真实项目/客户名，并随 3 个文件进入远端；升级为 P0 的 R11-003。 |
| L-028（P3） | “已知限制，自动重连未做” | 提级 P2 的 R11-018，纳入 T2 实施。 |
| CORE-26（P3） | “未修” | 提级 P2 的 R11-016：`mode` 非法值 fail-open 到可写模式。 |
| TAX-29（P3） | “未修” | 提级 P2 的 R11-021：期间静默回退与 13 月 `quarter=5`。 |
| AGENTS.md 的 HEAD/clean/push/CI 描述 | “`3cacb0b`、clean、未 push、CI 从未首跑” | 与事实不符，登记 R11-065，由 T8 统一改写。 |

### 21.4 关闭规则（与 `2026-11-11_检查结果.md` §12 一致）

1. 每项关闭必须附：命令、退出码、通过数、日志路径；仅“代码已改”不算关闭。
2. 待复现项必须先取证再定性：成立则修，不成立则移入 `2026-11-11_检查结果.md` §11“误报与已澄清”并写明证据。
3. 外部阻断项只能降级为“文档说明/已知限制”，不得声称解决。
4. P0 在远端清理与推送复核完成前保持未关闭，即使工作区已无字面值。
5. 不得为通过测试而 skip、删除断言或降低阈值。

### 21.5 已知的“不要重复审计”结论

`2026-11-11_检查结果.md` §11 已登记 19 条经复核不成立或属设计取舍的项（备份/恢复加固、上传统一限流、下载防注入、招商 CAS、密钥静态加密、模型同步不假成功、只读工具门控、会话摘要不阻塞、前端注册去重与分包约束、请求守卫、文档路径与计数等）。后续任务若未发现代码变化，不应重复开单；若代码变化导致前提失效，需在该节更新理由。

---

## 22. 2026-11-23 知识检索 / 解析 / 接地 / 记忆来源台账（增量）

> 本章为**增量台账**（Round 2026-11-23，编号 `R23-001`…），格式沿用 §21：只登记本轮发现并已在工作区修复的缺陷，不改写 §0.3/§17 与任何历史章；关闭规则沿用 §21.4。本条目只描述现象/根因/修复/测试，不变量登记在 `docs/INVARIANTS.md`（INV-023~INV-026）。
>
> 说明：知识库问答的「无依据不得肯定/不得给无来源步骤/口径冲突不擅选」与长期记忆「来源引用」属于**能力/契约缺口**（原先未实现），也按缺陷登记格式收口，便于追踪与回归。

### 22.1 逐项明细

**R23-001（P2，知识检索）RRF 多块堆分**
- **现象**：同一制度文档多个块命中关键词时，每块都累加 `1/(60+rank)`，长文档（块多）在混合排序中压过只有单一最佳块命中的更相关文档。
- **根因**：`knowledge/retrieval.py` 融合按 hit 逐条累加，未做「每通道每文档取最佳 rank」。
- **修复**：新增 `_channel_best()`——单通道内每文档只保留首个（最佳）rank 进入 RRF 累加。
- **测试**：`test_knowledge_retrieval_ranking.py::test_rrf_does_not_accumulate_multi_chunks_of_same_doc`、`::test_rrf_uses_best_keyword_rank_across_docs`。

**R23-002（P2，知识检索）展示证据块固定关键词优先**
- **现象**：展示给用户的命中块无条件取关键词命中块；向量 rank 1 的最佳证据会被关键词 rank 更差的块覆盖，引用与答案依据不符。
- **根因**：展示块选择只有 `keyword_rank` 一个维度，没有比较两通道 rank。
- **修复**：`_pick_display()` 在关键词/向量各自最佳候选中按通道 rank 择优，并列时关键词优先。
- **测试**：`test_knowledge_retrieval_ranking.py::test_display_chunk_prefers_better_vector_rank_over_worse_keyword_chunk`、`::test_display_chunk_keeps_keyword_when_keyword_rank_not_worse`、`::test_display_chunk_keeps_keyword_chunk_when_doc_vector_only_worse`。

**R23-003（P2，解析）PPT 组合形状与表格丢失**
- **现象**：PPTX 里组合（group）形状内的文本与表格单元格没有进入索引正文，检索命不中。
- **根因**：`extract.py::_extract_pptx` 只遍历顶层 shape，不递归展开组合形状。
- **修复**：新增 `_walk_pptx_shapes()` 递归展开 group shapes（排除标题占位符），组合形状与表格单元格文本随幻灯片页码入库。
- **测试**：`test_knowledge_extraction_pages.py::test_pptx_group_shapes_and_table_cells_extracted_with_slide_no`。

**R23-004（P2，解析）章节跨页正文截断、页码错**
- **现象**：同一章节正文跨页时，正文被截到单页，页码映射与原文不符，引用页码不可核对。
- **根因**：`chunk.py::build_chunks` 不在页边界 flush，跨页正文被并入单页块。
- **修复**：正文遇到页码变化先 flush 再续接（跨页章节保留两页各自映射），页码取本组实际出现的页码。
- **测试**：`test_knowledge_extraction_pages.py::test_cross_page_section_keeps_page_mapping`、`::test_pdf_section_spanning_pages_keeps_both_pages`。

**R23-005（P3，解析）句式编号正文被当标题吞**
- **现象**：「1. xxx 这句是正文要求。」等含句号的编号行被标题判定吞掉，正文缺句。
- **根因**：标题形态正则（三级编号）不排除句子特征。
- **修复**：`looks_like_sentence()`（含句号/叹号/问号）命中时不按标题处理；切块遇此类 heading 块按正文续接。
- **测试**：`test_knowledge_extraction_pages.py::test_docx_numbered_body_sentence_stays_text`、`::test_chunk_treats_sentence_like_heading_block_as_body`。

**R23-006（P1，AI 接地）无依据也肯定 / 给无来源步骤 / 冲突擅选口径**
- **现象**：问「能不能/怎么操作」类问题时，检索无命中仍直接答「可以」并编造操作步骤；补充常识时与制度结论混在一起；不同文档口径不一致时自行替用户选定（偏向字段更全的那个）。
- **根因**：系统提示词没有知识问答接地边界，各专家拼装路径不一致（数据库自定义专家可能缺规则）。
- **修复**：`orchestrator_prompt.py` 新增 `KNOWLEDGE_GROUNDING_RULES`（8/9/10 三条：无明确支持不肯定/不给无来源步骤并说明「知识库未收录」；常识标注「知识库之外的通用参考」且不得推翻结论；冲突并列披露原文与出处、不擅选），`compose_system_prompt()` 成为系统提示词唯一组装入口（orchestrator `_build_agent` 经此拼装）。
- **测试**：`test_knowledge_grounding_prompt.py` 12 例（含 `test_grounding_rules_apply_to_all_modes`、`test_compose_entry_matches_built_agent`、冲突并列与常识分离用例）。

**R23-007（P1，长期记忆）召回大小写敏感 / 泛词噪声 / 来源引用缺失**
- **现象**：英文记忆大小写不命中；查询含「的/关于」等泛词时召回结果被噪声支配；记忆条目无法关联知识库文档，来源不可溯源。
- **根因**：召回打分按原文匹配、未做 token 噪声阈值；`memories` 表没有来源引用列。
- **修复**：token 与记忆内容统一 `casefold`；泛词（单字符/通用词）按有限噪声阈值过滤，短查询单 token 不被静默破坏；`memories` 新增可选轻量列 `source_doc_id`（index），create/update 接受该字段（非法/不存在 400、显式 `null` 清空、未触碰保持原引用），列表/召回返回 `source_doc_title`（一次批量查询防 N+1），文档删除把引用置空（记忆本体保留），会话注入在命中行尾展示「（来源：《…》）」，前端可选来源文档。
- **测试**：`test_memory_source_recall.py` 8 例（casefold/噪声/单 token、CRUD 输出、非法 id 400、其他字段更新保引用、删文档置空、注入来源标题）+ `test_knowledge_grounding_prompt.py::test_memory_hit_with_source_doc_title_shows_source` 等 4 例。
