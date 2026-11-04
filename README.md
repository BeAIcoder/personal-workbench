# Personal Workbench 个人工作台

个人工作台：任务、日程、笔记、全局搜索与数据仪表盘，外加可配置的 AI 助手 Agent 团队、财税模块与经营/职能台账。
**数据只存在本机** —— 任务/日程/笔记/财务等全部数据保存在本地 SQLite/DuckDB 与本地文件；AI 助手需配置模型密钥，对话内容会发送到所选的模型厂商（不配置则助手不可用，其余功能完全离线可用）。

## ✨ 功能特性

- **工作台概览**：统计卡片、任务状态饼图、近 7 天完成趋势、经营提醒
- **任务 / 日程 / 笔记 / 全局搜索**：增删改查、筛选排序、月历、标签置顶、`Ctrl+K`
- **AI 助手（Agent 团队）**：AgentScope 多专家、智能路由、续聊保持、工具调用审计、自然语言操作工作台；内置 8 位专家，可按需从 QwenPaw 导入
- **模型管理**：33 家厂商预设、在线模型同步（只读目录）、模型能力三态探测、按模型生成思考档位、OpenCode Go 会话头
- **知识与记忆**：笔记 RAG 检索（BM25）+ 长期记忆四类型（user/feedback/project/reference）+ 统计 + Markdown 导入导出
- **MCP / 技能包 / 专家团文件**：stdio 服务器（`readOnlyHint` 唯一只读依据）、SKILL.md 技能包与技能市场、专家 `.md` 导入导出
- **定时任务**：间隔/每天/Cron；到点执行 Agent 或模块动作（如看板刷新），失败重试与问题记录告警
- **财务模块（14）**：数据仓、费用分类、发票处理、POS 对账、银行流水转 BIP 模板、个人记账、企业记账（BIP）、企业所得税、印花税、增值税、个税、残保金、报表看板、现金流填报
- **经营/职能模块（8）**：总经理经营聚合、招商、营运、企划、工程物业、法务、行政人事、数据质量巡检
- **调试中心**：问题记录、E2E 测试运行归档、前端报错自动采集

## 📖 文档

| 文档 | 说明 |
| --- | --- |
| [`使用说明.md`](使用说明.md) | **唯一权威用户手册（v1.16.1）**：从零上手、完整菜单地图、每项功能的用途/入口/前置/步骤/产物/限制/常见错误/下一步、八条端到端练习、备份恢复/换机/安全/FAQ |
| [`数据收传使用说明.md`](数据收传使用说明.md) | 导入/导出字段、枚举、模板、限额、导入顺序、幂等与报错速查 |
| [`API.md`](API.md) | HTTP 接口语义 + 全量路由索引（启动后也可访问 <http://127.0.0.1:8000/docs>） |
| [`算法实现逻辑.md`](算法实现逻辑.md) | 路由、MCP 门控、BIP 导入、银行流水、看板校验、脱敏、探测等算法 |
| [`功能模块拓扑图.md`](功能模块拓扑图.md) | 模块拓扑与数据流 |
| [`spec.md`](spec.md) | 需求与设计规格（as-built） |
| [`注意事项.md`](注意事项.md) | 避坑指南（环境、安全、备份、AI、财务） |
| [`数据模板/README.md`](数据模板/README.md) | 10 份导入模板速查 |
| `Agent团队设计.md` | AI 助手架构与专家团队设计细节 |
| `progress.md` / `BUGfix.md` / `新增业务模块需求与设计.md` | 开发仓参考，不随分发包 |

## 🚀 快速开始

### 一键启动（推荐，Windows）

双击 **`启动工作台.bat`**：自动创建虚拟环境、安装依赖、按需构建前端并启动服务，浏览器打开 <http://127.0.0.1:8000>。关闭服务双击 `停止服务.bat`。

> **分发包（ZIP）已附带预构建的前端产物**：解压后即使没有安装 Node.js 也能直接启动；只有改前端源码、需要重新构建时才要求 Node.js 24.22.1+。

### 手动方式

```bash
# 后端（Python 3.12+）
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000

# 前端（Node.js 24.22.1+，另开终端构建静态文件，由后端托管）
cd frontend
npm install
npm run build
```

详细步骤、首次初始化与常见错误见 [`使用说明.md`](使用说明.md) §1。

## 🛠 开发模式

双击 **`开发模式.bat`**，同时启动：

- 后端：<http://127.0.0.1:8000>（uvicorn `--reload` 热重载）
- 前端：<http://127.0.0.1:5173>（Vite 开发服务器，热更新）

```bash
# 运行测试
cd backend && venv\Scripts\python -m pytest -q -n 12
cd frontend && npm run test

# 文档契约（菜单/路由/版本/安全事实/占位噪声）
cd backend && venv\Scripts\python -m pytest tests\test_docs_contract.py -q
```

项目接入 GitHub Actions CI（`.github/workflows/ci.yml`）：push 或 PR 自动跑后端 pytest、前端 vitest 与生产构建。CI 的首次真实运行状态以 GitHub Actions 页面为准，不在文档里预承诺成功。

测试与 `dev/e2e` 探针均跑在隔离环境：pytest 使用独立临时 SQLite、临时财务目录、临时调试目录，并默认不读本机 `backend/.env`；探针另起独立端口与独立数据根，均不指向 `backend/data`。

## 🤖 AI 助手配置（可选）

主功能开箱即用，无需任何密钥。界面配置优先：AI 助手 → ⚙ 设置 → 供应商与模型 → 新增供应商 → 填 Key → 添加模型 → 点模型名激活。

也可在 `backend/.env` 填三项作为兜底（界面未配置时生效）：

```bash
LLM_BASE_URL=        # 如 https://api.siliconflow.cn/v1
LLM_API_KEY=         # 你的 API Key
LLM_MODEL=           # 如 deepseek-ai/DeepSeek-V3.2
```

未配置时助手显示「模型未配置」，其余功能不受影响。完整配置、能力探测、专家与降级表见 [`使用说明.md`](使用说明.md) §3。

## 💾 数据与备份

- 数据库为 `backend/data/workbench.db`（WAL 模式），财务数据在 `backend/data/finance/`，均已 `.gitignore`。
- 备份：双击 **`备份数据.bat`**，同时生成 `workbench_*.db` 与 `workbench_data_*.zip`（数据库 + uploads/finance/skills/expert-packs/debug + 根 JSON；本机配置标 `SENSITIVE`），各保留最近 30 份，建议每周一次。
- **迁移/恢复必须用 `workbench_data_*.zip`**：只带 `.db` 会丢附件、技能包、专家包与财务产物。恢复命令 `python backend/scripts/restore_backup.py <zip> --force`，覆盖前自动留 `<target>.before_restore_*` 整目录快照与 `backups/restore-local/local_*` 配置快照。
- **真实数据保护**：不要在服务运行时直接剪切/覆盖数据库；`backend/data/` 与 `backend/.env` 切勿外发。

## 🧱 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.12+ / FastAPI / SQLAlchemy 2.x / Pydantic v2 / SQLite（WAL）/ DuckDB |
| 前端 | Vue 3 / Vite / Element Plus（按需引入）/ ECharts / axios / dayjs |
| AI | AgentScope 2.0（ReAct 多 Agent 编排、工具调用、结构化输出） |

## 📁 项目结构

```
personal-workbench/
├── backend/            # FastAPI 后端（app/、tests/、data/、.env.example）
├── frontend/           # Vue 3 前端（src/、tests/、dist/）
├── dev/                # 开发工具（脱敏、发布、API 索引生成、E2E）
├── 数据模板/           # 10 份导入模板
├── backups/            # 备份产物（gitignore）
├── 启动工作台.bat      # 一键启动
├── 开机自启动.vbs      # 开机自动启动（可选）
├── 开发模式.bat        # 开发模式（热重载）
├── 备份数据.bat        # 备份（.db + 整包 zip）
└── 停止服务.bat        # 停止服务
```

## 🗺 Roadmap

- [x] 长期记忆 / RAG / MCP / 定时 Agent / 模块注册机制
- [x] 财务模块群（费用/发票/数据仓/个人记账/BIP/所得税/看板/现金流/POS/四税种）
- [x] 经营与职能模块群（招商/营运/企划/法务/人事/工程/GM）
- [x] 数据质量巡检（七域×五维 + 跨表勾稽 + 看板产物/双源对账）
- [x] 银行流水 → BIP 导入模板（建行/中行，保留官方格式）
- [x] 技术栈演进第一批：SQLite WAL、前端 vendor 拆包、CI 缓存、trace 治理
- [x] 演进第二批：pytest-xdist、路由轻量化与缓存、长会话滚动摘要
- [x] 演进第三批：Element Plus 按需引入
- [ ] 政策税率版本化、房产税/城镇土地使用税、13 周滚动现金流：需官方政策原文、地方口径/真实样本与业务定义后实施
- [ ] 长文件拆分、SettingsDrawer/ChatPanel 拆分：噪声高风险，单独轮次执行

## 🔒 安全说明

- **本机无鉴权**：服务默认只监听 `127.0.0.1`，没有任何登录鉴权——能访问 8000 端口的本机程序都能读写全部数据。
- **非回环默认拒绝**：用非回环地址启动且未开 `ALLOW_NON_LOOPBACK=1` 时，除 `/api/health` 外所有 `/api` 返回 **503**；确需局域网访问要同时配置 `CORS_ORIGINS` 与防火墙，并自行加 VPN/反向代理鉴权。
- **Origin 校验**：`/api` 写方法与 discover 类 GET 校验来源，非法 Origin 返回 **403**；普通读取由 CORS 兜底。
- **密钥只存本机**：模型 API Key 与 MCP `env` 经 DPAPI 加密（绑定本机+用户），界面只回显脱敏形式；换机需重新录入。
- **上传/行数**：聊天附件 10MB；财务模块 10/20/50MB 按入口；导入行数上限 200,000 行；全局请求体 128MB。
- **Agent 边界**：不能物理删除；可新建/完成任务、日程、笔记与查询；**财税专员可新增一笔个人记账**（`pl_add_transaction`）；Bash/插件默认关闭。
- **外发内容**：对话正文、附件提取文本、注入记忆、工具结果会发送给你配置的模型供应商；选本地 Ollama/LM Studio 可不出域。

## 📄 License

[MIT](LICENSE) © 2026 personal-workbench contributors
