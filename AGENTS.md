# AGENTS.md — personal-workbench 项目记忆

> 本文件是项目级长期记忆：跨会话保持的状态、约定与待办。Hub 级记忆（脱敏钥匙等）见 Agent Hub，不在此重复。

## 项目

Personal Workbench 个人财税自动化工作台（FastAPI + SQLAlchemy + SQLite / Vue 3 + Vite + Element Plus）。开发进度与验证史见 `progress.md`（本地文件，gitignore 管理，不入库）。

## 外部验证计划执行状态（2026-10-22）

依据 `20261022-外部验证计划与Checklist.md`：

- **第 0 章 通用前置 G1–G9**：✅ 完成。代码冻结基线 `6f64572`；`backend/data` 备份在 `backups/verify-20261022-pre/`（115 文件 + sha256，恢复演练通过）；验证期间真实数据零写入（每章结束字节级复核）。
- **第 1 章 真实付费 LLM**：✅ 基本完成（硅基 SiliconFlow，Key 经界面录入）。L1–L7、L10、L12–L14 通过；**L8/L9 部分完成**（DeepSeek context 完整 126032；GLM/Qwen 被硅基 TPM 429 截断；output 探测三模型未建立——DeepSeek 生成 1024 tokens >30s）。记录的问题（错误中文化、30s 超时、400 守卫、停止按钮、429 重试）已全部修复并线上复测。
- **第 2 章 真实 MCP**：✅ 完成（M1–M9 全过）。真实 server 用本机 kimi-datasource 插件（`node plugins/managed/kimi-datasource/bin/kimi-datasource.mjs`）；只读判定以 annotations.readOnlyHint 为唯一依据。
- **第 3 章 用友 BIP、第 4 章 外部财务引擎/管线**：⏸ **待办**——需要用户提供真实样本/业务数据副本后才能继续（见清单第 3/4 章样本要求）。第 5 章微信报表监控、第 6 章真人可用性随后。
- 证据归档：`backend/.pytest_tmp/verify-20261022-{general,llm,mcp}/`（result.json + report.md + 日志）。

## 验证环境约定（重要）

- 隔离服务模板见清单 0.1 节；**路径必须用 `D:/…` Windows 风格**——Git Bash 的 `$PWD`（`/d/…`）会被 SQLAlchemy 在 Windows 上误解析成 `D:\d\…`（2026-10-22 踩过，已清理）。
- 真实 Key 只经界面/环境变量输入，禁止写入文档、日志、提交、截图；证据目录内只放合成值与脱敏记录。
- 请求预算：首轮单主题 ≤50 次真实供应商请求（含 SDK 重试），达到即停。

## 工程约定

- git 提交经 clean/smudge 脱敏过滤器（本机路径/公司名/昵称 → 占位符）；反向恢复钥匙 `dev/sanitize-map.local.json` 不入库，丢失无法自动还原（机制见 `dev/SANITIZE.md`）。
- 提交信息用中文 conventional commits（`fix(assistant): …` / `docs: …` / `test: …`）。
- 测试：后端 `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q`；前端 `cd frontend && npm test` + `npm run build`。
