# Personal Workbench

个人工作台：任务管理、日程管理、笔记管理、AI Agent 团队协同。

## 功能

- **工作台概览**：统计卡片、任务状态饼图、近 7 天完成趋势
- **任务管理**：增删改查、状态/优先级/分类、关键词搜索、组合筛选
- **日程管理**：月历视图、按天查看、全天日程、地点/颜色/备注
- **笔记管理**：多标签、置顶、标题/内容关键词搜索
- **全局搜索**：Ctrl+K 快捷键，一次检索任务/日程/笔记
- **AI 助手**：基于 AgentScope 的多 Agent 协同（7 位领域专家）

## 技术栈

- **后端**：Python 3.12+ / FastAPI / SQLAlchemy 2.x / Pydantic v2 / SQLite
- **前端**：Vue 3 / Vite 5 / Element Plus / ECharts / axios / dayjs
- **AI**：AgentScope 2.0（ReAct 多 Agent 编排、工具调用、结构化输出）

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/BeAIcoder/personal-workbench.git
cd personal-workbench

# 一键启动（自动创建虚拟环境、安装依赖、启动服务）
启动工作台.bat
```

打开浏览器访问 `http://127.0.0.1:8000`

## 项目结构

```
personal-workbench/
├── backend/            # FastAPI 后端
│   ├── app/           # 应用代码
│   ├── data/          # SQLite 数据库（gitignore）
│   ├── tests/         # pytest 测试（75 例）
│   └── requirements.txt
├── frontend/           # Vue3 前端
│   ├── src/           # 源代码
│   ├── tests/         # vitest 测试（6 例）
│   └── package.json
├── 启动工作台.bat      # 一键启动
├── 开发模式.bat        # 开发模式（热重载）
└── 停止服务.bat        # 停止服务
```

## AI 助手配置（可选）

主功能（任务/日程/笔记/搜索/看板）开箱即用，无需任何密钥。AI 助手统一走 OpenAI 兼容协议，
复制 `backend/.env.example` 为 `backend/.env`，填齐以下三项即自动激活（也可以在启动后的界面里配置供应商和模型）：

```bash
LLM_BASE_URL=        # 如 https://api.siliconflow.cn/v1
LLM_API_KEY=         # 你的 API Key
LLM_MODEL=           # 如 deepseek-ai/DeepSeek-V3.2
```

可选渠道：ModelScope 推理、硅基流动、DeepSeek、火山方舟、DashScope 兼容模式、本地 Ollama（任选其一）。
未配置时助手显示"模型未配置"，其余功能不受影响。

## 数据与备份

- 数据库为单个 SQLite 文件，位于 `backend/data/workbench.db`（已配置 .gitignore，不会提交）
- 备份：双击 `备份数据.bat`，复制一份到 `backups\`
- 迁移到新电脑：拷贝整个文件夹（可不含 `backend\venv`、`frontend\node_modules`、`frontend\dist`），
  把 `workbench.db` 一起带走即可

## 安全说明

- 服务默认仅监听 `127.0.0.1` 且无鉴权，请勿改成 `0.0.0.0` 暴露到局域网
- API Key 保存在本机（`.env` 或 SQLite），不会外传；界面中仅显示"已配置"状态
- 「允许 Bash」和「插件兼容层」是默认关闭的实验性能力：开启后 Agent 可执行本机命令/加载本地插件代码，请知悉风险后再启用

## 开发

```bash
# 后端开发（热重载）
cd backend
python -m uvicorn app.main:app --reload --port 8000

# 前端开发（热更新）
cd frontend
npm run dev

# 运行测试
cd backend && pytest
cd frontend && npm run test
```

## License

MIT
