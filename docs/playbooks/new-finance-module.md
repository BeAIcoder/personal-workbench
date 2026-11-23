# 新增财务/业务模块 Playbook

> 适用：新建 `finance_*` / `business_*` 模块，或给现有模块加表/路由/工具/菜单。
> 相关 Skill：`.agents/skills/pw-new-finance-module/SKILL.md`。

## 1. 复制脚手架

```bash
cp -r backend/app/modules/_template backend/app/modules/finance_xxx
cp -r frontend/src/modules/finance-hello frontend/src/modules/finance-xxx
```

不要从零建；`_template` 已包含 manifest/models/router/service/schemas 的最小结构。

## 2. 后端

1. `manifest.py`
   - `name` = 目录名；`prefix` = `/api/finance/xxx` 或 `/api/business/xxx`。
   - `tables` = `mod_xxx_*`；`agent_tools` 的 `readonly`/`params`/`required` 一致。
   - `preset_experts` 只放对口专家；不要把无关工具塞进 `finance_specialist`。
   - `requires` 只放真实依赖；缺失会被跳过，CI strict 会拦。
2. `models.py`
   - 表名 `mod_<domain>_<entity>`；金额用 `Numeric`/`Float`？按现有模块风格（float + money()）。
   - 有 `status` 就写合法转移/失败回收；同期间/同业务键加唯一约束/部分索引。
3. `schemas.py`
   - 期间 `YYYY-MM`、日期 `YYYY-MM-DD`、枚举 `Literal`、金额 `ge=0`。
   - 不接受“空值回退当前期间”；非法值 4xx。
4. `service.py`
   - 业务规则、金额 `money()`、日期严格；`IntegrityError` 映射 409。
   - 外部引擎：用 `utils.resolve_engine_root` / `engine_subprocess_env`；不要硬编码路径。
5. `router.py`
   - 只做参数/状态码/调用 service；中文错误；已提交/已导出记录 409。
6. 测试
   - `tests/test_module_<name>.py`：CRUD/状态/金额/期间/权限边界。
   - 非 client 用例配 `_ensure_tables`（参考 `test_test_isolation_contract.py` 白名单）。

## 3. 前端

1. `src/modules/<kebab>/manifest.js`：路由 + 菜单；`menu.subgroup` 按现有分组。
2. `views/`：列表/表单/确认/导出；请求 try/catch；下载 `utils/download.js::saveBlob`。
3. 菜单必须在 `使用说明.md` 有八段式章节；帮助索引/页内帮助按需。
4. 行为测试：至少覆盖“打开弹窗/提交成功/409/空态”；不要只做源码断言。

## 4. 文档与发布

- `API.md` 索引重生成：`cd backend && PYTHONPATH=. venv/Scripts/python.exe ../dev/gen_api_index.py`。
- `docs/GLOSSARY.md` 加一行术语。
- `spec.md` / `功能模块拓扑图.md` / `README.md` 按需更新，标 `as of`。
- 发布包结构测试要包含新模块文件（若随包）。

## 5. 验证

```bash
cd backend
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_module_<name>.py -q
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_docs_contract.py tests/test_architecture_contracts.py -q

cd ../frontend
npm test -- --run tests/<module>.test.js
npm run build

cd ..
python dev/check_contracts.py
python dev/check_architecture.py
python dev/check_pii.py --tracked --terms dev/pii-terms.local.txt
```

## 6. 常见失败

- 模块没出现在菜单：检查前端 manifest 的 `menu`/`routes`/`path`，不是后端 manifest。
- 模块表没建：`models.py` 未在 manifest 导入，或 `__tablename__` 不是 `mod_`。
- 工具没进专家：`preset_experts` 名字不对/专家未 seed；看 `apply_pending_presets` 日志。
- CI strict 失败：manifest 校验/requires/重名；不要靠运行时 fail-soft 掩盖。
