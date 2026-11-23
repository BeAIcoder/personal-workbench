# 文档地图（四层）

> 问题不是“文档不够”，而是“历史、当前、生成物混在一起”。本文件只做分层与索引，不移动历史文件，避免链接和 PII 风险。
> 规则：**当前事实**必须标 `as of <commit> / working tree`；**历史快照**保留原结论，不回溯改写；**生成物**不手改。

## 1. 用户文档（随包/面向使用者）

| 文档 | 用途 | 维护方式 |
|---|---|---|
| `README.md` | 项目介绍、快速开始、技术栈、安全边界 | 手写；版本/模块数变更时同步 |
| `使用说明.md` | 唯一权威用户手册（菜单地图/步骤/限制/FAQ） | 手写；新增菜单必须加八段式章节 |
| `数据收传使用说明.md` | 数据从哪来、模板、上传、对账 | 手写；模板变更时同步 |
| `数据模板/README.md` | 模板清单与字段 | 手写；模板文件不改名 |
| `功能模块拓扑图.md` | 模块/数据流图 | 手写；模块增减时同步 |
| `算法实现逻辑.md` | 外部引擎/算法说明 | 手写；引擎行为变更时同步 |
| `注意事项.md` | 环境坑位/操作注意 | 手写 |
| `Agent团队设计.md` | Agent/专家/工具设计 | 手写 |
| `API.md` | 路由索引 + 语义章节 | 索引由 `dev/gen_api_index.py` 生成；语义手写 |

## 2. 开发者文档（不随包）

| 文档 | 用途 |
|---|---|
| `docs/ARCHITECTURE.md` | 一页纸架构、边界、运行时约束 |
| `docs/GLOSSARY.md` | 术语/别名/代码位置 |
| `docs/ONBOARDING.md` | 5 天上手路径 |
| `docs/INVARIANTS.md` | 不变量登记（陈述/测试/owner） |
| `docs/PII_DISPOSITION.md` | PII 处置登记：当前命中清单、登记制白名单、决策矩阵、需授权动作 |
| `docs/DEPRECATIONS.md` | 兼容层退役清单：替代/验证/回滚/最早删除版本，含"明确不删"清单 |
| `docs/adr/` | ADR 索引 + ADR-006（知识库向量）；原 ADR-001…005 在 `20261012New/adr/` |
| `docs/playbooks/` | 跨层变更、新财务模块、事故 RCA、看板排障、发布/PII |
| `.agents/skills/` | 项目级 Skill：`pw-cross-layer-change`、`pw-new-finance-module`、`pw-incident-rca` |
| `spec.md` | 需求与设计规格（长；按章节查） |
| `2026-11-17_架构与工程效能复盘.md` | 本次复盘：历史/效能/根因/路线图 |

## 3. 历史归档（保留证据，不当作当前事实）

| 文档/目录 | 说明 |
|---|---|
| `progress.md` | 逐轮开发史（210KB）；当前事实看 AGENTS/README |
| `BUGfix.md` | 2026-10-30 缺陷台账（215 项，历史快照） |
| `2026-11-03_检查结果.md` | L-001…L-175 审计台账（历史快照） |
| `2026-11-11_检查结果.md` | R11-001…R11-112 审计台账（历史快照） |
| `2026-11-12_检查结果.md` | 整改终验（历史快照） |
| 其他 `2026-*.md` / `202610*.md` | 各轮报告/评审/计划 |
| `20261012New/` | 早期逆向分析 + ADR-001…005 |
| `.superpowers/sdd/` | SDD 任务报告/评审 diff（本机，gitignore） |

**归档规则**：

- 不移动、不改写历史文件；用本索引标注“历史快照”。
- 历史结论过期时，在当前文档写“已过期，见 X”，不要改原文。
- 历史文件里的真实路径/客户名**已脱敏**（入库 blob 是占位符）；新增历史文件必须过 `check_pii`。
- 本机工作区里历史文件会因 smudge 过滤器还原出真实路径，`check_pii --tracked` 因此为红——这是**已知项**，处理方式与授权边界见 `docs/PII_DISPOSITION.md`；不要为了让扫描变绿去改写历史文件。

## 4. 生成物（不要手改）

| 生成物 | 生成方式 |
|---|---|
| `API.md` 索引表 | `cd backend && PYTHONPATH=. venv/Scripts/python.exe ../dev/gen_api_index.py` |
| `frontend/dist/.build-stamp.json` | `npm run build` |
| `backend/requirements-lock.txt` | 生成方式见文件头；**双源去留见 `docs/DEPRECATIONS.md` DEP-006**，不要手改单条 |
| `docs/screenshots/` | 截图；旧 v1.1 图已归档到 `docs/archive/screenshots-v1.1/` |

## 5. 当前事实的单一源（速查）

| 事实 | 单一源 |
|---|---|
| 版本号 | `backend/app/config.py` |
| 默认配置 | `backend/app/config.py` / `backend/.env.example` |
| 菜单 | `frontend/src/modules/*/manifest.js` |
| 路由前缀 | 后端 `modules/*/manifest.py` |
| 状态枚举 | 各模块 `schemas.py` / `models.py` / 服务层白名单 |
| 金额精度 | `backend/app/utils_money.py` |
| 超时预算 | 后端 `finance_board/service.py`、`config.py`；前端消费 |
| 表清单 | `__tablename__`（以实际 ORM 为准；模块 manifest **不再**声明 `tables`，见 `docs/DEPRECATIONS.md` DEP-007） |
| 不变量 | `docs/INVARIANTS.md` |
| PII 已接受命中（白名单） | `docs/PII_DISPOSITION.md` §3（只登记位置，不登记值） |
| 兼容层去留与删除版本 | `docs/DEPRECATIONS.md` |

## 6. 新文档准入

新增文档前先问：

1. 它是**当前事实**、**历史快照**还是**生成物**？
2. 当前事实能否由代码/测试/生成器产生？能就不手写。
3. 是否已有同类文档？优先补章节，不新建平行文档。
4. 是否含真实路径/客户名/Key？先过 `check_pii`。
5. 是否需要在 `README.md`/本索引加一行？必须加，否则下一个人找不到。
