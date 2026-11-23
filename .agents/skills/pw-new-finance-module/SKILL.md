---
name: pw-new-finance-module
description: Personal Workbench 新增财务/业务模块脚手架。当要新建 finance_* 或 business_* 模块、加表/路由/Agent 工具/菜单/测试/文档时使用。
---

# 新增模块（Personal Workbench）

完整步骤见 `docs/playbooks/new-finance-module.md`。先复制 `backend/app/modules/_template/`，不要从零建。

## 后端最小集

- `manifest.py`：`name` 与目录同名；`prefix` 以 `/api/finance/` 或 `/api/business/` 开头；`tables` 用 `mod_` 前缀；`agent_tools` 的 `readonly`/`params`/`required` 一致。
- `models.py`：导入即注册 `Base.metadata`；表名 `mod_<domain>_<entity>`；有 `status` 就写转移表/失败回收。
- `schemas.py`：Pydantic 输入；期间/枚举/金额用 `Field(pattern/ge/le)`。
- `service.py`：业务规则；金额 `money()`；日期/期间严格；`IntegrityError` 映射 409。
- `router.py`：只做参数/状态码/调用 service；中文错误。
- `__init__.py`：空即可。

## 前端最小集

- `src/modules/<kebab>/manifest.js`：路由 + 菜单（菜单唯一源）。
- `views/`：列表/表单/确认/导出；请求全部 try/catch；下载走 `utils/download.js::saveBlob`。
- 新增菜单必须在 `使用说明.md` 有八段式章节；帮助索引/页内帮助按需。

## 测试与验证

```bash
cd backend
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_module_<name>.py -q
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_docs_contract.py -q
cd ../frontend && npm test -- --run tests/<module>.test.js
python dev/check_contracts.py && python dev/check_architecture.py
```

## 不要做

- 不要复制四税种 router 的 150 行同构代码；先看 `finance_filing_common.py`。
- 不要在 `manifest.menu` 里写菜单指望生效。
- 不要在模块里直接 import 另一个模块的 `models/router/service`；用 `requires` + 公共函数，或加到 `dev/architecture-baseline.json` 并说明理由。
- 不要把真实数据/真实路径写进测试或种子。
