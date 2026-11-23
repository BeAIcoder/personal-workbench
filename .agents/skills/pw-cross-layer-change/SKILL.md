---
name: pw-cross-layer-change
description: Personal Workbench 跨层契约变更检查清单。当要改 API 字段/状态枚举/超时预算/菜单/版本号，或前后端/文档/测试需要同步时使用。
---

# 跨层契约变更（Personal Workbench）

完整步骤见 `docs/playbooks/cross-layer-change.md`。本 Skill 只给最短检查清单。

## 先判断“谁是单一源”

- 版本：`backend/app/config.py`
- 菜单：`frontend/src/modules/*/manifest.js`
- 超时：后端常量（看板 `finance_board/service.py`；任务 `scheduler.py`）
- 状态枚举：`schemas.py` / 服务层白名单
- 金额：`backend/app/utils_money.py::money`
- 表/路由：`modules/*/manifest.py` + `router.py`

## 改动顺序

1. 后端 schema/service（唯一源）。
2. 前端 API/view（消费方）。
3. 契约测试（`backend/tests/test_engineering_contracts.py` + 相关行为测试）。
4. 文档（`API.md` / `使用说明.md` / `docs/GLOSSARY.md`，按需）。
5. 跑：
   ```bash
   python dev/check_contracts.py
   python dev/check_architecture.py
   cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_engineering_contracts.py tests/test_docs_contract.py -q
   ```

## 必须检查的 6 个坑

- 前端超时是否 ≥ 后端最坏预算（看板 1,385s < 1,500s；任务 1,800s < 1,900s）。
- 菜单只改前端 manifest；后端 `manifest.menu` 不生效。
- 新增字段是否进了 schema/正则/白名单；非法值必须 4xx，不得回退。
- 金额是否 `money()`；不要新增 `round()`。
- 状态是否有合法转移/失败回收；`IntegrityError` 是否映射 409。
- `/api` 路径是否真实存在；新增/删除路由后重跑 `dev/gen_api_index.py`。

## 完成标准

- `check_contracts`、`check_architecture`、文档契约、聚焦测试全绿。
- 变更说明里写清“旧值→新值、兼容期、回滚方式”。
- 不为了过测试而 skip/删断言/降阈值。
