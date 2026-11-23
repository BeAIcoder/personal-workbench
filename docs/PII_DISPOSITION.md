# PII 处置登记（PII Disposition）

> **as of 2026-11-17 / working tree**（HEAD `b33f912`，领先 `origin/master` 14 提交，未推送）。
> 本文件是**登记与决策**，不是扫描器配置：规则、退出码、`--archive`/`--staged` 口径一律不变，也不新增任何"按规则类型豁免"的代码分支。
> 关联：`docs/INVARIANTS.md`（INV-010 指向的"复测记录"就是本文件）、`docs/playbooks/release-and-pii.md`（怎么跑、怎么修）、`dev/SANITIZE.md`（clean/smudge 机制）、`dev/check_pii.py`（门禁本体）。

## 1. 结论先行

| 问题 | 结论 |
|---|---|
| 有真实 PII 进过 git 仓库吗？ | 没有证据。`--archive HEAD` = **0 命中**，提交 blob 里全是占位符 |
| 那为什么工作区扫描是红的？ | 9 处**全部是本机工作树现象**：6 处历史快照被 smudge 还原成真实路径、1 处已知 smudge、2 处扫描器自测合成样例 |
| 需要改写历史吗？ | **不需要，且禁止**。历史 blob 已脱敏；改写只会破坏证据链和 clean/smudge 往返一致 |
| 需要降低扫描强度吗？ | **不需要，且禁止**。9 处是"已知项"，不是"豁免项"；新增任何一条仍然必须红 |
| 发布/推送的判据是什么？ | `--archive <待推送 tip>` = 0 且 `--staged` = 0；`--tracked` 允许**恰好等于**本文件的 9 条登记（多一条都不行） |

一句话：**门禁的"红"是本地还原的副作用，不是仓库泄露；判据要分层，不要为了让 `--tracked` 变绿去改历史或改扫描器。**

## 2. 门禁语义（本文件不改变任何一条）

| 项 | 内容 |
|---|---|
| 扫描目标 | 指定路径（文本 + xlsx 单元格）／`--tracked` 工作树／`--staged` 暂存区 blob／`--archive <rev>` 提交树 |
| 规则类型 | `win_path`（本机盘符路径）、`company`（公司全称）、`token`（密钥形态）、`private_key`、`wechat_id`、`wechat_dir`、`local_term`（`--terms` 词表命中） |
| 输出 | 只输出**文件、规则名、行号/单元格**，绝不回显命中值 |
| 退出码 | 命中 `1`；扫描失败（词表缺失/坏 xlsx/无法解码）`2`；干净 `0` |
| 词表 | 只经 `--terms` / `PII_TERMS_FILE` / `PII_TERMS` 显式加载；**不读** `dev/sanitize-map.local.json`、不读 `.env` |
| 内置豁免 | 合成/示例/占位路径（如 `AssetLib`、`示例`、`脱敏`、`<…>`、`/tmp`）不判为泄露 |
| 阻断点 | `dev/hooks/pre-push` 按待推送范围**逐提交**跑 `--archive`；`dev/package_release.py` 打包前跑 `--archive HEAD` |

## 3. 当期命中清单（`--tracked`，9 条）

| # | 文件:行 | 规则 | 类别 | 处置 |
|---:|---|---|---|---|
| 1 | `2026-10-29_0144_BUGFIX.md:3` | `win_path` | 历史快照 | 接受（登记） |
| 2 | `20261012New/README.md:36` | `win_path` | 历史快照 | 接受（登记） |
| 3 | `20261012New/scripts/migrate_tax_data.py:6` | `win_path` | 历史快照 | 接受（登记） |
| 4 | `20261012New/scripts/migrate_tax_data.py:20` | `win_path` | 历史快照 | 接受（登记） |
| 5 | `20261012New/财务模块集成方案_2026-10-12.md:6` | `win_path` | 历史快照 | 接受（登记） |
| 6 | `项目全面分析报告_2026-10-20.md:14` | `win_path` | 历史快照 | 接受（登记） |
| 7 | `backend/app/modules/finance_pos/vouchers_service.py:60` | `local_term` | 已知 smudge 还原 | 接受（登记） |
| 8 | `backend/tests/test_pii_guard.py:466` | `local_term` | 扫描器自测合成样例 | 接受（登记） |
| 9 | `backend/tests/test_pii_guard.py:467` | `local_term` | 扫描器自测合成样例 | 接受（登记） |

复现命令与退出码：

```bash
python dev/check_pii.py --tracked --terms dev/pii-terms.local.txt   # 9 命中，exit=1（预期）
python dev/check_pii.py --staged  --terms dev/pii-terms.local.txt   # 0 命中，exit=0
python dev/check_pii.py --archive HEAD --terms dev/pii-terms.local.txt  # 0 命中，exit=0
```

## 4. 判定规则：登记制，不是豁免制

1. **集合相等才算通过**：当前命中集合必须与本文件 §3 **逐条相同**（路径 + 行号 + 规则名）。少一条可以接受（说明清理了），多一条一律判红。
2. **只登记位置，不登记值**：本文件永不写入命中值的真实内容；任何"把真实值抄进文档"的做法都等于再泄露一次。
3. **行号变动 = 内容被改 = 不再是历史快照**：命中行号变了，说明有人动了历史文件（或文件被重排），本条登记自动失效，必须重新判定。
4. **新增即红**：新文件、新行命中，一律按泄露处理（改占位符/改本地配置），不得"顺手加进白名单"。
5. **白名单不落代码**：禁止在 `dev/check_pii.py` 里加 `if path in {...}: continue` 之类的分支；`dev/check_pii.py` 保持无语义变更。
6. **判定用计数**：`--tracked` 的 **stdout 行数应为 9**（每条命中一行；汇总行走 stderr，`2>&1` 合并后才是 10 行）。用 `| wc -l` 做机器可读判据，不要依赖中文汇总行的编码显示。

## 5. 决策矩阵

三档处置的定义：

- **接受（登记白名单）**：不真实、不可修复，或修复代价大于风险；以本文件登记位置，靠"集合相等"约束；
- **归档**：保留内容原样，只在索引里标注"历史快照"，不当作当前事实（见 `docs/README.md` §3）；
- **清理**：真的改了文件内容（改占位符 / 删测试 / 改本地配置）。

| 类别 | 数量 | 是否真实泄露 | 处置 | 依据 | 复发判据 | Owner |
|---|---:|---|---|---|---|---|
| 历史快照里的本机路径 | 6 | 否（blob 已脱敏，工作树是 smudge 还原） | 归档 + 登记 | 历史不改写；`git diff` 为空，证明内容就是占位符的还原态 | 行号变动或出现第 7 处 | 发布 |
| 已知 smudge 还原（`vouchers_service.py:60`） | 1 | 否（提交 blob 是占位符） | 接受 | 工作区必须是可运行的真实值；smudge 是设计行为 | 该行以外的行命中 | POS/发布 |
| 扫描器自测合成样例 | 2 | 否（故意构造的假值） | 接受 | 门禁的自测必须能命中真实形态，否则测不到 | 数量变化 | 测试 |
| 新增的代码/文档/测试命中 | 0 | 是 | **清理** | INV-010：新增面零命中 | 任何新增命中 | 提交人 |
| 暂存区 / 提交树命中 | 0 | 是（会进远端） | **阻断** | pre-push + 打包门禁 | 非 0 | 发布 |

**本次没有任何"清理"项**，这是结论不是遗漏：9 条中没有一条能通过改文件内容消除——改历史文件会破坏证据与 clean/smudge 往返，改测试会削弱门禁自测，改 `vouchers_service.py` 会让默认项目映射失效。

## 6. 逐项取证

### 6.1 历史快照（6 处）：提交 blob 是占位符，工作树是还原态

- 5 个文件 `git status --short -- <files>` 无输出 → 工作树与 blob 经 clean 后**完全一致**，说明真实路径 100% 来自 smudge。
- `git show HEAD:<file>` 显示的是占位符形式（本机目录名 → `AssetLib`）。
- 结论：这 6 处既不进远端、也不进分发包（打包脚本剔除 dated 报告与 `20261012New/`），只在本机工作区可见。

### 6.2 已知 smudge（1 处）：`finance_pos/vouchers_service.py:60`

- `DEFAULT_PROJECT_BY_POS_CODE` 的内置项目映射里，某个项目名在 blob 中是占位符，工作区被 smudge 还原为真实名。
- 该文件在工作树另有**与本条无关的未提交逻辑改动**（`stmt_pdf_by_receipt` → `stmt_pdf_by_key`），属于在途工作，本文件不涉及、不修改。
- 影响面：仅"未配置 `finance_pos_project_map` 时"使用内置默认值；配置存在时以内置配置为准。
- 结论：保持现状。若要彻底消除本机可见性，只能改为"默认空映射 + 强制配置"，那是**行为变更**，需要单独设计（见 §8 A4）。

### 6.3 合成样例（2 处）：`backend/tests/test_pii_guard.py:466-467`

- 该测试断言前端/看板的文件名净化函数会把某个"像真实公司名"的字符串替换为合成单位；这两行的字符串是**故意构造**的假值，正是为了证明净化生效。
- 结论：保留。删掉它等于删掉门禁的自测（违反"不得降断言"）。

## 7. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 告警疲劳 | `--tracked` 长期为红，日常容易忽略真正的新增泄露 | 用 §4 的"stdout=9 行 + 集合相等"判据区分；发布/推送只看 `--archive`/`--staged` |
| 有人为了让 `--tracked` 变绿改写历史 | 历史文件一旦被编辑，`git status` 变脏、证据失效、clean/smudge 往返破坏 | 本文件第 1/5 节明令禁止；历史文件改动需在 PR/提交说明中单独声明理由 |
| 白名单被扩成"规则豁免" | 一旦按规则类型（如 `win_path`）豁免，真实新路径会被一起放过 | 只登记 (path,line,rule) 三元组；`dev/check_pii.py` 零改动 |
| smudge 映射丢失 | `dev/sanitize-map.local.json` 不在库里，丢失后历史占位符无法自动还原 | 随每周备份一起保存（见 Hub 记忆与 `docs/playbooks/release-and-pii.md`） |
| 公开源码分支/未来分发包含历史 md | 目前 `source-only-release` 与发布包都不含历史文档；若策略变化需重跑 | 打包前必跑 `--archive HEAD`；新增含历史文档的发布物前重跑一次 |
| 行号漂移导致登记过期 | 见 §4.3 | 每次 PII 复测后更新本文件 §3 的行号 |

## 8. 需要用户授权的动作（本文件不做，只列选项）

| ID | 动作 | 为什么需要授权 | 建议 |
|---|---|---|---|
| A1 | 远端历史清理 / `filter-repo` 重写 / `force push` | 不可逆、影响所有协作者；且当前 `--archive` 已 0 命中 | **不做**。无证据表明远端有真实 PII |
| A2 | 就地改写 6 个历史快照文件为占位符（消除 `--tracked` 红） | 等于改写历史证据，破坏 clean/smudge 往返一致性 | **不做**。收益仅"变绿"，不降低任何真实风险 |
| A3 | 删除或归档 `20261012New/`、dated 报告 | 属于历史证据，删除不可逆 | 归档、加索引即可（已完成于 `docs/README.md` §3） |
| A4 | 把 POS 内置项目映射改为"默认空 + 强制配置" | 行为变更（未配置时导出会失败/需要先配置） | 单独设计，走跨层变更流程；本文件不建议顺手改 |
| A5 | 轮换 `dev/pii-terms.local.txt` 词表或重建 smudge 映射 | 影响所有历史占位符的还原能力 | 仅在真实泄露时做；当前不需要 |

## 9. 复测脚本（发布前）

```bash
# 1) 三层门禁：tracked（预期 9 命中）/ staged（0）/ archive（0）
python dev/check_pii.py --tracked --terms dev/pii-terms.local.txt
python dev/check_pii.py --staged  --terms dev/pii-terms.local.txt
python dev/check_pii.py --archive HEAD --terms dev/pii-terms.local.txt

# 2) 机器可读计数：stdout 应为 9 行（每条命中一行；汇总行走 stderr）
python dev/check_pii.py --tracked --terms dev/pii-terms.local.txt | wc -l

# 3) 历史快照未被误改：应无输出（只查这 5 个文件，不用目录通配，
#    避免把 20261012New/ 下其他在途改动误判成"历史文件被改"）
git status --short -- 2026-10-29_0144_BUGFIX.md 20261012New/README.md \
  20261012New/scripts/migrate_tax_data.py \
  "20261012New/财务模块集成方案_2026-10-12.md" \
  "项目全面分析报告_2026-10-20.md"
```

预期：步骤 1 的第 1 条 exit=1（已知项）、第 2、3 条 exit=0；步骤 2 输出 `9`；步骤 3 无输出。

## 10. 变更记录

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-11-17 | 建文件：登记 9 处命中、决策矩阵、风险、授权清单 | `2026-11-17_架构与工程效能复盘.md` §11.5、`2026-11-17_1720_1-12修复计划.md` §2-G |
