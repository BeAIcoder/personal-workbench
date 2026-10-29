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
重复导入 13,001 行凭证全部 skipped 而非报错——「重复即跳过并计数」的幂等语义对用户最友好。
WorkBuddy 的 epoch-marker、QwenPaw 导入的【QwenPaw 整合】标记，本质都是**幂等标记**。后续 WorkBuddy
同步导入沿用同一思路（见 Part D 计划）。
