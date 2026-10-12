# 逆向分析 · CI/CD（2026-10-12）

> 方法：从仓库事实（无 CI 配置文件、本地 git 20 提交、bat 启动、测试命令）反推当前交付形态，
> 给出与项目约束（Windows/pywin32/本机单人）兼容的 CI/CD 建议。本文件是「分析+建议」，尚未实施。

## 一、逆向结论：当前无 CI/CD

| 事实 | 证据 |
|------|------|
| 无 CI 配置 | 仓库无 `.github/workflows/`、无 `.gitlab-ci.yml`、无 Jenkinsfile |
| 本地为主 | 20 个提交全部本地产生（2026-09-21 → 10-12），未 push |
| 质量靠人工/子代理 | 每个阶段「全量 pytest + vitest + build + 真实冒烟」由开发过程执行，无自动化门禁 |
| 发布靠 bat | `启动工作台.bat` 自建 venv、装依赖、起服务；无打包发布流水线 |

## 二、从项目事实推导的 CI 要素

- **测试命令**（CI 只需三条）：
  - `cd backend && ./venv/Scripts/python.exe -m pytest tests -q`（当前 294 例，约 2.5 分钟）
  - `cd frontend && npx vitest run`（82 例，约 5 秒）+ `npm run build`（约 15 秒）
- **运行器必须是 Windows**：pywin32 是 DPAPI 硬依赖（`ensure_dpapi_available` 缺了拒绝启动），Linux runner 起不了后端
- **Python/Node 版本**：开发态 Python 3.13.15 / Node 24.1.0；requirements-lock.txt 按此冻结
- **无外部服务依赖**：测试全离线（LLM 调用全部 mock），CI 不需要密钥——这是重要优势，应保持

## 三、建议的 CI（GitHub Actions，windows-latest）

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - uses: actions/setup-node@v4
        with: { node-version: "24" }
      - name: 后端测试
        run: |
          cd backend
          python -m venv venv
          venv/Scripts/python.exe -m pip install -r requirements.txt
          venv/Scripts/python.exe -m pytest tests -q
      - name: 前端测试与构建
        run: |
          cd frontend
          npm ci
          npx vitest run
          npm run build
```

注意事项：① DPAPI 在 GitHub Windows runner 可用（当前用户上下文），无需改代码；② 测试并发写临时 SQLite（conftest 按 pid 隔离），CI 并行 job 间无共享；③ 若将来测试需要 LLM，用 mock 密钥 + 录制回放，禁止真实调用。

## 四、建议的 CD（发布）

- **触发**：打 `v*` tag
- **产物**：① `frontend/dist`（已构建）；② 后端 PyInstaller 打包（项目已有 exe 分发先例：FSSC/发票工具）；③ `备份数据.bat` 的数据库快照说明
- **分发形态建议**：zip（backend + frontend/dist + 启动 bat + 使用说明），换机文档见《运行环境要求》
- **不建议**：自动 push 远端（公开仓含本地化代码，push 动作应由用户显式执行——与 V1 数据安全声明一致）

## 五、与数据安全边界的兼容

CI 只跑测试与构建，不接触 `backend/data/`（已 gitignore）；发布产物不含 data/。
`finance_cashflow` 模块本地专用（gitignore），CI 不编译它——前端 glob 收集不到、后端注册器扫不到，属预期。
