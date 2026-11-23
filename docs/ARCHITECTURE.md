# 架构速览（开发者版）

> 目标：让一个没看过本项目的人在 15 分钟内知道“东西在哪、边界在哪、哪些不能动”。
> 更完整的历史与取舍见 `2026-11-17_架构与工程效能复盘.md`；术语见 `GLOSSARY.md`。

## 1. 一句话架构

**浏览器（Vue 3 + Element Plus）→ FastAPI 单体（宿主 routers + 模块 router/service）→ SQLite（宿主表 + `mod_*` 模块表）/ DuckDB（数据池）/ 文件目录（上传、技能、专家包、调试归档）→ 外部 Excel/PDF 引擎与 Windows 计划任务。**

这不是微服务，也不是纯插件系统，而是**模块化单体**：模块有独立目录、manifest、表、路由和 Agent 工具，但共享进程、数据库连接、配置和前端构建。模块注册器解决“接入一致性”，不解决“独立部署/故障隔离”。

## 2. 目录地图

```
backend/
  app/
    main.py                  # FastAPI 入口 + lifespan 编排（中间件/路由/异常收口）
    bootstrap/               # 启动步骤：schema（补列/补索引）、seeds（幂等种子）、legacy（一次性秘密迁移）
    config.py                # 唯一版本源 + 所有默认值
    database.py              # engine/SessionLocal/Base
    models.py, schemas.py    # 宿主表与宿主 schema
    routers/                 # 宿主域：assistant/jobs/knowledge/skills/experts/mcp...
    agents/                  # Agent 栈：orchestrator/team/tools/config_store/model_*/llm/mcp/rag/memory
    modules/
      registry.py            # 扫描 modules/*/manifest.py、校验、挂路由、注册工具
      spec.py                # ModuleManifest 契约
      finance_*/             # 财务模块（19 生产 + finance_hello 示例）
      business_*/            # 经营/职能模块（9）
      debug_center/          # 调试中心（文件存储）
      _template/             # 新模块脚手架（不加载）
    knowledge/               # 文档解析/切块/FTS5/sqlite-vec/OCR
    finance_*_common.py      # 税种申报/个税等跨模块共享逻辑
    utils*.py                # 金额、xlsx、上传、日期等
  tests/                     # pytest（150 文件，约 48k 行）
  scripts/                   # 备份/恢复/初始化/migrate.py（一次性迁移）等运维脚本
frontend/
  src/
    modules/*/manifest.js    # 前端模块清单：路由 + 菜单（菜单的唯一源）
    modules/*/views/         # 模块页面
    components/assistant/    # 聊天/设置/专家团
    api/index.js             # axios 实例 + 宿主 API
    utils/                   # 下载、分页、任务展示、帮助索引
dev/                         # 开发/脱敏/发布/契约检查脚本（不随包）
docs/                        # 开发者文档（不随包）
```

## 3. 请求怎么走

### 3.1 普通 CRUD

```
Vue view → src/api/index.js (axios, baseURL=/api)
  → FastAPI router (宿主或 modules/<name>/router.py)
  → service (事务/校验/金额)
  → SQLAlchemy model (宿主表或 mod_*)
  → JSON
```

约定：

- 路由只做参数校验、状态码映射、调用 service；业务规则在 service。
- 金额用 `app/utils_money.py::money`（Decimal ROUND_HALF_UP），不要用 `round()`。
- 日期 `YYYY-MM-DD`，期间 `YYYY-MM`，严格校验；不要静默回退当前期间。
- 状态用 `Literal` + 服务层白名单；有 `status` 的实体要有合法转移表与失败回收。
- 删除按业务级联；已提交/已导出记录不可静默删改（409 + 中文说明）。

### 3.2 外部引擎

```
service → utils.resolve_engine_root / engine_subprocess_env
  → 子进程（费用/发票/POS/现金流/看板脚本）
  → 产物目录（xlsx/pdf/html/json）
  → 解析/入库/返回摘要
```

约定：

- 外部引擎是独立脚本，不要为了“统一”重写；用适配器隔离路径、编码、超时。
- 子进程环境要显式 UTF-8/GB18030 回退；Windows 中文输出不能假设 UTF-8。
- 运行产物不要写进 `backend/data` 之外的仓库目录；发布/测试用临时数据根。

### 3.3 看板（特殊）

- `finance_board` 优先走计划任务通道（`schtasks /run`），因为本机安全软件会按进程树拦截 venv python 写项目根。
- 以 `logs/run.lock` 判断在途，轮询 `logs/last_run.json`；四步 exit 全 0 才归档快照。
- 单次尝试预算 660s，最多 2 次，最坏 1,385s；前端 1,500s。
- 没有该任务的机器自动回退直接子进程。
- 这是**有意设计**，不要改成“纯子进程”或“纯 HTTP 同步”。

## 4. 模块机制

后端 `manifest.py` 至少包含：

```python
manifest = ModuleManifest(
    name="finance_xxx",          # 目录名
    prefix="/api/finance/xxx",   # 必须以 /api/finance/ 或 /api/business/ 开头
    agent_tools=[...],           # Agent 工具 spec
    requires=[],                 # 依赖模块；缺失则跳过
    preset_experts=["finance_specialist"],  # 工具预置目标专家
    column_patches={},           # 轻量列迁移
    enabled=True,
)
```

注意：

- 菜单唯一源是前端 `src/modules/*/manifest.js`；后端 `ModuleManifest` **不再声明 `menu`**（2026-11-17 删除，后端那份从未有消费者，改它不会生效）。过渡期仍兼容在 manifest 里写 `menu`/`tables`：字段被忽略，注册器加载时记一条 WARNING。
- 表名以模块 `models.py` 的 `__tablename__` 为准（须 `mod_` 前缀），`ModuleManifest` **不再声明 `tables`**；建表靠 `models.py` 导入 + `Base.metadata.create_all`，表名契约由 `backend/tests/test_tool_schema_contract.py` 锁定。
- `title` / `version` 只用于加载日志与排障，不参与任何业务判断。
- `requires` 缺失时模块会被静默跳过；CI 用 strict 模式检查（见 `dev/check_contracts.py`）。
- `agent_tools` 的参数是 `(JSON 类型, 中文说明)` **二元组**，必填参数只写进 `required` 列表，且 `required ⊆ params`；唯一事实源与校验逻辑在 `backend/app/tool_schema.py`（契约测试 `backend/tests/test_tool_schema_contract.py`）。非二元组定义不会被静默容忍：manifest 校验失败即跳过模块，`build_tools` 只跳过该工具并记 ERROR。
- `preset_experts` 会把工具名幂等并入专家白名单；不要在这里塞与模块无关的工具。

## 5. 数据与边界

| 存储 | 用途 | 位置 |
|---|---|---|
| SQLite | 宿主表 + `mod_*` | `WORKBENCH_DATA_DIR/workbench.db`（默认 `backend/data`） |
| DuckDB | 数据池清洗 | `FINANCE_DATA_DIR/vault/vault.duckdb` |
| 文件 | 上传、技能、专家包、调试归档、财务产物 | `DATA_DIR/uploads`、`DATA_DIR/skills`、`DATA_DIR/expert-packs`、`DEBUG_DATA_DIR`、`FINANCE_DATA_DIR` |
| 外部引擎 | 费用/发票/POS/现金流/看板 | 由 `FINANCE_*` 配置指向；不要硬编码本机路径 |

四域环境变量：`WORKBENCH_DATA_DIR`、`DATABASE_URL`、`FINANCE_DATA_DIR`、`DEBUG_DATA_DIR`。测试/探针必须同时设置并在启动前重置自有数据根。

## 6. 运行时硬约束

- **仅 Windows 完整可用**：DPAPI（API Key 加密）是硬依赖；Linux 启动即拒。
- **只监听回环**：`127.0.0.1`；没有登录/鉴权；不要把 `HOST` 改成 `0.0.0.0`。
- **进程树写拦截**：本机安全软件可能禁止 venv python 派生进程写项目根；跨目录写优先走计划任务通道。
- **SQLite WAL**：`journal_mode=WAL`、`busy_timeout=5000`、`temp_store=2`；备份走 backup API，ZIP 跳过 `-wal/-shm`。
- **Node ≥ 24.22.1**：`frontend/.nvmrc` 与 `package.json engines` 是权威。
- **路径风格**：环境变量/命令行用 `D:/…`；Git Bash 的 `/d/…` 会被 SQLAlchemy 误解析。

## 7. 有意设计（不要当缺陷修）

- 模块加载失败只跳过，不拖垮宿主；CI 用 strict 模式兜底。
- SQLite `column_patches` 轻量迁移，不引入 Alembic（单用户/单进程）。
- `main.lifespan` 只做编排：建表/补列/补索引在 `app/bootstrap/schema.py`，幂等启动种子在 `app/bootstrap/seeds.py`；可降级项只告警，模块工具预置（必需项）fail-fast。
- 一次性迁移（Provider 明文 Key、MCP env、旧 `agent_settings.api_key`）只在 `python backend/scripts/migrate.py secrets` 执行，启动路径只做只读统计与告警——启动不再改写密钥列。
- 看板走计划任务通道；无任务时回退直接子进程。
- DPAPI 无明文降级；Linux 不支持。
- 单定时器 + `TICK_SECONDS=300` 兜底，不引入 APScheduler。
- 外部引擎子进程复用现有脚本，不重写。
- 前端 vendor-core 必须把 vue + Element Plus 放同一 chunk；拆开会在运行时炸。

## 8. 修改前先看

- 新模块：`docs/playbooks/new-finance-module.md` / `.agents/skills/pw-new-finance-module`。
- 跨层字段/状态/超时/菜单：`docs/playbooks/cross-layer-change.md` / `.agents/skills/pw-cross-layer-change`。
- 修 bug：`docs/playbooks/incident-rca.md` / `.agents/skills/pw-incident-rca`。
- 发布/PII：`docs/playbooks/release-and-pii.md`。
- 看板排障：`docs/playbooks/board-pipeline-debug.md`。
- 不变量：`docs/INVARIANTS.md`。
