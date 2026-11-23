# 跨层契约变更 Playbook

> 适用：改 API 字段/状态枚举/超时预算/菜单/版本号/金额精度，或前后端/文档/测试需要同步。
> 相关 Skill：`.agents/skills/pw-cross-layer-change/SKILL.md`。

## 1. 先找单一源

| 事实 | 单一源 | 消费方 |
|---|---|---|
| 版本 | `backend/app/config.py` | `package.json`、`package-lock.json`、README/使用说明 |
| 菜单 | `frontend/src/modules/*/manifest.js` | AppLayout、帮助索引、使用说明 |
| 路由前缀 | `backend/app/modules/*/manifest.py` | 前端 API、API.md |
| 超时 | 后端常量（看板/任务） | 前端 `timeout:` |
| 状态 | `schemas.py` / 服务层白名单 | 前端标签/按钮、测试 |
| 金额 | `utils_money.money` | 所有财务 service/导出 |
| 表 | `models.py` + `manifest.tables` | 备份/文档/测试 |

## 2. 改动清单

1. **后端**：schema → service → router；状态转移/唯一约束/409。
2. **前端**：API 调用 → view → 标签/按钮/空态。
3. **测试**：契约测试 + 行为测试（不是只断言源码字符串）。
4. **文档**：`API.md`（索引重生成）、`使用说明.md`（八段式）、`docs/GLOSSARY.md`（新术语）。
5. **迁移**：旧值兼容期、默认值、回滚说明；一次性迁移放 `scripts/`，不要塞进 lifespan。

## 3. 命令

```bash
python dev/check_contracts.py
python dev/check_architecture.py

cd backend
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_engineering_contracts.py tests/test_docs_contract.py -q
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_module_<name>.py -q

cd ../frontend
npm test -- --run tests/<related>.test.js
```

## 4. 本仓库踩过的坑

- **R11-035**：前端 300s < 服务端 1,205s → 假失败。规则：前端 ≥ 后端最坏预算；看板 1,500s，任务 1,900s。
- **R11-009**：模板引用 `cleanReportDialog`，script 只声明 `cleanReport` → 弹窗永不打开。规则：交互必须有行为测试；静态断言只作补充。
- **R11-053**：版本在 config/package/lock/文档四处 → 分发包版本错。规则：版本单一源 + 契约检查。
- **R11-046/047**：菜单 `meta`/最长前缀 → 子路由不高亮。规则：菜单只改前端 manifest，补直接访问/子路由测试。
- **R11-007**：UI 宣称落库但后端只解析。规则：响应里显式 `persisted`；UI 文案跟随后端事实。

## 5. 完成标准

- 契约检查/架构检查/文档契约/聚焦测试全绿。
- 变更说明包含：旧值→新值、兼容期、回滚方式、影响的菜单/API/文档。
- 没有新增静默回退、`round()`、真实路径依赖。
