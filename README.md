# Personal Workbench 个人工作台

个人工作台：任务、日程、笔记、全局搜索与数据仪表盘，外加可配置的 AI 助手 Agent 团队。
**本机运行、数据不出本机** —— 所有数据保存在本地 SQLite 单文件中。

## ✨ 功能特性

- **工作台概览**：统计卡片、任务状态饼图、近 7 天完成趋势
- **任务管理**：增删改查、状态/优先级/分类、关键词搜索、组合筛选、逾期提醒
- **日程管理**：月历视图、按天查看、全天日程、地点/颜色/备注
- **笔记管理**：多标签、置顶、标题/内容关键词搜索
- **全局搜索**：`Ctrl+K` 快捷键，一次检索任务/日程/笔记
- **AI 助手（Agent 团队）**：基于 AgentScope 的 7 位领域专家协同，支持智能路由、工具调用审计、自然语言操作工作台
- **QwenPaw Agent 导入**：支持从本机 QwenPaw 一键导入 Agent 团队（角色定义合并/新建，自动脱敏）

## 📸 界面截图

> 截图待补充，存放于 `docs/screenshots/` 目录：
>
> - `docs/screenshots/dashboard.png` — 工作台概览
> - `docs/screenshots/tasks.png` — 任务管理
> - `docs/screenshots/assistant.png` — AI 助手

## 🚀 快速开始

### 一键启动（推荐，Windows）

双击 **`启动工作台.bat`**，脚本会自动创建虚拟环境、安装依赖并启动服务，浏览器打开 <http://127.0.0.1:8000>。关闭服务双击 `停止服务.bat`。

### 手动方式

```bash
# 后端（Python 3.12+）
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000

# 前端（Node.js 18+，另开终端构建静态文件，由后端托管）
cd frontend
npm install
npm run build
```

## 🛠 开发模式

双击 **`开发模式.bat`**，同时启动：

- 后端：<http://127.0.0.1:8000>（uvicorn `--reload` 热重载）
- 前端：<http://127.0.0.1:5173>（Vite 开发服务器，热更新）

```bash
# 运行测试
cd backend && pytest
cd frontend && npm run test
```

## 🤖 AI 助手配置（可选）

主功能（任务/日程/笔记/搜索/仪表盘）开箱即用，无需任何密钥。AI 助手统一走 OpenAI 兼容协议，复制 `backend/.env.example` 为 `backend/.env`，填齐三项即自动激活（也可以在启动后的界面里配置供应商和模型）：

```bash
LLM_BASE_URL=        # 如 https://api.siliconflow.cn/v1
LLM_API_KEY=         # 你的 API Key
LLM_MODEL=           # 如 deepseek-ai/DeepSeek-V3.2
```

可选渠道：ModelScope 推理、硅基流动、DeepSeek、火山方舟、DashScope 兼容模式、本地 Ollama（任选其一）。未配置时助手显示"模型未配置"，其余功能不受影响。

## 💾 数据与备份

- 数据库为单个 SQLite 文件，位于 `backend/data/workbench.db`（已在 .gitignore 中排除，不会提交）
- 备份：双击 **`备份数据.bat`**，按时间戳复制一份到 `backups/`
- 迁移到新电脑：拷贝整个文件夹（可不含 `backend/venv`、`frontend/node_modules`、`frontend/dist`），把 `workbench.db` 一起带走即可

## 🧱 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.12+ / FastAPI / SQLAlchemy 2.x / Pydantic v2 / SQLite |
| 前端 | Vue 3 / Vite 5 / Element Plus / ECharts / axios / dayjs |
| AI | AgentScope 2.0（ReAct 多 Agent 编排、工具调用、结构化输出） |

## 📁 项目结构

```
personal-workbench/
├── backend/            # FastAPI 后端
│   ├── app/            # 应用代码（routers / agents / models）
│   ├── data/           # SQLite 数据库与上传附件（gitignore）
│   ├── tests/          # pytest 测试
│   └── .env.example    # 环境变量样例
├── frontend/           # Vue 3 前端
│   ├── src/            # 源代码
│   └── tests/          # vitest 测试
├── docs/screenshots/   # 界面截图（占位）
├── backups/            # 数据库备份（gitignore）
├── 启动工作台.bat      # 一键启动
├── 开发模式.bat        # 开发模式（热重载）
├── 备份数据.bat        # 备份 SQLite 数据库
└── 停止服务.bat        # 停止服务
```

## 🗺 Roadmap

- [ ] 长期记忆 / RAG 知识库
- [ ] MCP 工具接入
- [ ] 定时 Agent（计划任务自动执行）
- [ ] 聊天窗口传图与多模态理解
- [ ] 会话归档与导出

## 🔒 安全说明

- 服务默认仅监听 `127.0.0.1` 且无鉴权，请勿改成 `0.0.0.0` 暴露到局域网
- API Key 保存在本机（`.env` 或 SQLite），不会外传；界面中仅显示"已配置"状态
- 「允许 Bash」和「插件兼容层」是默认关闭的实验性能力：开启后 Agent 可执行本机命令/加载本地插件代码，请知悉风险后再启用

## 📄 License

[MIT](LICENSE) © 2026 personal-workbench contributors
