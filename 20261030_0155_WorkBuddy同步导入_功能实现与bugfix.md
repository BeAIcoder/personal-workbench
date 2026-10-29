# 2026-10-30 01:55 · WorkBuddy 同步导入 · 功能实现与 bugfix 记录

> Part D：新增腾讯 WorkBuddy 数据同步导入（技能包/记忆/身份文件 → 平台），界面 + CLI 双形态，
> 全程可自定义设置（数据目录/导入内容勾选/幂等 upsert）。

## 一、功能实现

1. **service** `backend/app/agents/workbuddy_sync.py`：
   - 扫描（只读）：`~/.workbuddy`（默认，可用界面设置/env `WORKBUDDY_HOME` 覆盖）；
     技能包 buddy-skill-store 深度≤4 rglob SKILL.md → frontmatter 解析（复用 skill_store）；
     MEMORY.md 按 `## ` 分节（**agent-hub 托管块自动剔除**）；SOUL/IDENTITY/USER/BOOTSTRAP.md 清点。
   - 导入（幂等 upsert）：技能包整目录复制进平台技能池（**新包默认停用**，不静默改变 Agent 注入；
     已存在更新 SKILL.md 与参考文件、启停状态保留）；记忆分节 → `add_memory(source='import',
     tags=['workbuddy', 节标题])`（记忆层近重复自动跳过）；身份文件 → 专家团文件草稿
     `workbuddy-*.md`（frontmatter 归一化，正文截 60000）。
   - 设置/上次结果持久化 `backend/data/workbuddy_import.json`（不进 git）。
2. **端点** `/api/assistant/workbuddy/{scan,import,settings}`（scan 只读；import 支持 dry_run）。
3. **CLI** `backend/scripts/import_workbuddy.py`：--dry-run 预览 / --from 指定目录 / --skills-only，
   复用同一 service（与 import_qwenpaw_agents.py 同形态）。
4. **前端**：技能包管理页新增「从 WorkBuddy 导入」按钮 + 弹窗（目录输入、三类内容勾选、
   扫描预览、导入结果摘要），设置读写 /settings。

## 二、真实数据验证（本机 WorkBuddy）

- **只读扫描**：技能包 11（NDA 审查/合同审查/智能检索等法律技能）、记忆 6 节、身份文件 4。
- **真实导入**（用户要求「同步到平台中使用」）：技能包新增 11（默认停用）+ 记忆新增 6
  （source=import）+ 身份草稿 4，全部成功零失败。
- **幂等复跑**：技能 11 全部 updated、记忆 6 全部近重复跳过、身份 4 updated——不叠加。
- **UI 走查**：隔离服务（8399）技能包页弹窗「扫描预览」真实显示 11/6/4；构建通过。

## 三、bugfix 记录（开发过程自纠）

1. `routers/workbuddy.py` 相对导入层级错误（...agents → ..agents）两处——修复。
2. `_import_identity` 先写文件后判存在导致恒记 updated——改为先判后写（added/updated 语义正确）。
3. `_import_memories` 缺 `memory_mod` 局部导入（导入记忆 500）——修复；配套测试暴露
   测试直连 SessionLocal 时缺表（无 client 夹具不触发 create_all）——测试补 autouse 建表夹具。
4. 弹窗模板 el-form 未闭合 + 残留重复闭合标签——修复，构建通过。

## 四、边界与说明

- 源目录只读；真实导入只写平台自身数据目录（技能池/专家包目录/memories 表），未触碰真实库其它数据。
- 导入的技能包默认停用：用户在技能包管理页逐个启用后才注入 Agent（避免静默改变对话行为）。
- 真实 Key 不涉及（WorkBuddy 数据目录内为用户本地文件，未回显任何凭据）。
