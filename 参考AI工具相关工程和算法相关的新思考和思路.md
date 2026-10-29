# 参考 AI 工具相关工程和算法相关的新思考和思路

> 记录从本机在用的 AI 工具（qwenpaw / ZCode / kimicode / WorkBuddy）身上观察到的工程与算法思路，
> 以及它们对本工作台（personal-workbench）的借鉴。随各批次持续追加。

## 2026-10-30 · Part A 实测轮观察

### 1. 「证据优先」与三类工具的共同点
- qwenpaw/zcode/workbuddy 都把「记忆/证据」落成**本地文件**（MEMORY.md、audit-log jsonl、
  verify 目录 result.json），而不是只存在会话里。本工作台的 verify-*/ 证据目录 + progress.md 章节化
  与它们同构——证明个人级工具也能承载工程级可复跑验证。
- 本轮验证延续 checklist 口径（G1-G9），发现「验证脚本自身也需要边界校验」：Mimosa 扫描器把测试脚本
  的动态 URL 判为 SSRF——给 `api()` 加了协议/主机/解析 IP/端口四重校验后通过。**观察**：安全扫描器
  对测试脚本与生产代码一视同仁，这倒逼测试代码也保持生产级卫生，是好事。

### 2. 跨批次重复识别是「数据指纹」类能力
发票模块对重复 PDF 的识别（批次 2/3 全部 duplicate）不需要额外算法——哈希去重即可。借鉴 WorkBuddy
的 artifact-index（artifact 哈希索引）思路：数据仓入仓时也可做**文件级哈希台账**（同一文件重复入仓
提示，可选 replace/upsert），这是低成本高价值的下一步。

### 3. BIP 幂等导入的启示
重复导入 13,001 行凭证全部 skipped 而非主动报错——「重复即跳过并计数」的幂等语义对用户最友好。
WorkBuddy 的 epoch-marker、QwenPaw 导入的【QwenPaw 整合】标记，本质都是**幂等标记**。WorkBuddy 同步
导入已按此实现（新增/更新/跳过计数，复跑不叠加）。

## 2026-10-30 · Part D/E：外部工具记忆体系对照与吸收

### 四家工具记忆处置对照

| 工具 | 记忆载体 | 结构 | 本工作台吸收点 |
|---|---|---|---|
| qwenpaw | Hub MEMORY.md（单文件权威源，read-on-edit） | 自由分节 | 单一权威源 + 显式分节导出格式 |
| ZCode | 记忆目录（一文件一事实 + MEMORY.md 索引） | user/feedback/project/reference 四类 + frontmatter | **四类型分类**（Part E 采纳）+ 类型检索加权 |
| kimicode | 项目级 AGENTS.md（会话自动加载，Electron 缓存不承载记忆） | 约定式章节 | 项目上下文入口约定（本工作台 AGENTS.md 已实践） |
| WorkBuddy | agent-hub 托管块 + MEMORY.md 混合 | 托管块标记对 | **托管块边界**：同步导入时自动剔除 hub 块（Part D 已实现） |

### 观察与设计决策
1. **幂等 upsert 是外部导入的公共语义**：qwenpaw【QwenPaw 整合】标记、WorkBuddy 导入的
   added/updated/skipped 计数——重复同步不产生重复数据，用户可随时重跑。
2. **导入默认降权**：WorkBuddy 技能包导入后默认停用（不静默改变 Agent 行为），与 zcode
   「记忆写入需显式确认」的保守取向一致；记忆导入靠近重复去重兜底。
3. **类型化记忆 + 注入预算**：借鉴 zcode 四类与各家「记忆不是越多越好」的共识——
   user/feedback 类 +0.5 权重、memory_inject_max 预算（0=不注入），让注入侧可控。
4. **托管块边界**：WorkBuddy 的 agent-hub 标记对提示了「机器托管区段应显式标记」——
   导入解析器按标记剔除，避免把工具内部状态当作用户记忆导入。
