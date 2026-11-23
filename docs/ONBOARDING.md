# 5 天上手路径（强工程师版）

> 你不需要先读完 1.77 MB 文档。按这个顺序，5 天内能独立做一个安全的小改动。
> 先读：`README.md` → `docs/ARCHITECTURE.md` → `docs/GLOSSARY.md` → `docs/INVARIANTS.md`。

## Day 1：跑起来，知道东西在哪

**目标**：启动应用、跑聚焦测试、读懂一个模块的 4 个文件。

```bash
# 1. 后端（Python 3.12+）
cd backend
python -m venv venv
venv/Scripts/python.exe -m pip install -r requirements.txt
venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. 前端（Node >= 24.22.1，另开终端）
cd frontend
npm ci
npm run dev

# 3. 聚焦测试（不要一上来跑全量）
cd backend
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_module_finance_vat.py -q
```

读这 4 个文件：

1. `backend/app/modules/finance_vat/manifest.py` —— 模块怎么声明。
2. `backend/app/modules/finance_vat/router.py` —— 路由怎么校验/映射状态码。
3. `backend/app/modules/finance_vat/service.py` —— 计税/金额/日期。
4. `frontend/src/modules/finance-vat/manifest.js` + `views/` —— 菜单/页面怎么接。

**当天要能回答**：

- 版本号在哪？`backend/app/config.py`。
- 菜单在哪？前端 `src/modules/*/manifest.js`；后端 `manifest.menu` 不生效。
- 金额怎么算？`utils_money.money`，不是 `round()`。
- 数据在哪？`backend/data`（gitignore）；测试用临时 SQLite。

## Day 2：理解边界与契约

**目标**：知道哪些是“设计”，哪些是“坑”。

读：

- `docs/ARCHITECTURE.md` §3–§7。
- `docs/INVARIANTS.md` 全部。
- `backend/app/modules/registry.py`（fail-soft、requires、preset）。
- `backend/app/modules/spec.py`（manifest 字段）。
- `frontend/src/modules/index.js`（菜单分组/去重）。

**当天要能回答**：

- 为什么模块加载失败只跳过？为什么 CI 要 strict？
- 为什么看板走计划任务通道？为什么不能改成纯子进程？
- 为什么 DPAPI 只支持 Windows？为什么不能明文降级？
- 为什么 `manifest.menu/tables` 不能当权威？

## Day 3：做第一个安全改动

**目标**：改一个显示文案/字段说明，跑通“代码 → 测试 → 文档契约”。

建议任务（任选）：

- 给某个模块的 400 文案加“下一步”提示（只改 service + 测试）。
- 给某个列表加一个只读字段（model/schema/router/前端/测试）。
- 修一个 P3 文档/帮助链接（跑 `test_docs_contract.py`）。

必跑：

```bash
cd backend
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_docs_contract.py -q
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_module_<name>.py -q
cd ../frontend && npm test -- --run tests/<related>.test.js
```

**不要**：

- 不要改 `backend/data` 里的真实数据；用临时数据根。
- 不要为了让测试过而 skip/降断言。
- 不要一次改 3 个模块；先做一个垂直切片。

## Day 4：走一条真实业务链

**目标**：理解“上传 → 解析 → 入库 → 导出 → 对账”的一条链。

选一条：

- 发票：`finance_invoice` 上传批次 → 明细 → 冲红配对 → 项目归类 → 导出。
- BIP：凭证列表/辅助明细账/辅助余额表三份文件 → 合并/查询。
- 数据池：上传 → 画像 → 清洗预览 → 应用 → 聚合/导出。
- POS：源目录 → 对账 → 刷卡凭证合并。

读对应的 `service.py` + `router.py` + `tests/test_module_*.py`，跑聚焦测试。重点看：

- 失败时返回 400/409/503 的边界。
- 状态是否会卡住（running/uploaded）。
- 金额是否 `money()`，日期/期间是否严格。
- 导出文件名是否 ASCII + `filename*`。

## Day 5：做一次跨层小改动

**目标**：体验“契约变更四件套”。

选一个字段/超时/菜单/状态，按顺序改：

1. 后端 schema/service（唯一源）。
2. 前端 API/view（消费方）。
3. 测试（契约 + 行为）。
4. 文档（API.md / 使用说明 / 帮助索引，按需）。
5. 跑 `python dev/check_contracts.py` + `python dev/check_architecture.py`。

如果卡住，先看 `docs/playbooks/cross-layer-change.md`。

## 必读 / 选读 / 不要读

**必读（约 2 小时）**

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/GLOSSARY.md`
- `docs/INVARIANTS.md`
- 一个模块的 manifest/router/service + 对应测试

**选读（按任务）**

- `spec.md`：需求与设计规格（长，按章节查）。
- `API.md`：路由索引 + 语义章节（长，按路径查）。
- `使用说明.md`：用户手册（写帮助/改文案时查）。
- `算法实现逻辑.md`：外部引擎/算法细节。
- `注意事项.md`：环境坑位。

**不要一开始读**

- `progress.md`（210KB 过程史）
- `BUGfix.md`（119KB 缺陷台账）
- 根级 dated 报告（历史快照）
- `20261012New/`（早期逆向分析）
- `.superpowers/sdd/`（任务过程，按需查证据）

## 三个最容易踩的坑

1. **把 `manifest.menu` 当菜单源**：改后端不生效，菜单在前端 manifest。
2. **用 Python `round()` 算钱**：项目要求 `money()`（Decimal ROUND_HALF_UP）；财务模块新增 `round()` 会被检查器拦。
3. **测试依赖真实路径/端口/外部任务**：必须 `tmp_path`/随机端口/mock；否则 CI 或换机必红。

## 第一天结束时的自检

- 我能启动前后端，并跑通一个模块的聚焦测试。
- 我知道版本/菜单/金额/期间的唯一源。
- 我知道哪些是“有意设计”，不会去“修”。
- 我知道遇到跨层改动要看哪份 playbook。
