# AGENTS.md — personal-workbench 项目记忆

> 本文件是项目级长期记忆：跨会话保持的状态、约定与待办。Hub 级记忆（脱敏钥匙等）见 Agent Hub，不在此重复。

## 项目

Personal Workbench 个人财税自动化工作台（FastAPI + SQLAlchemy + SQLite / Vue 3 + Vite + Element Plus）。开发进度与验证史见 `progress.md`（本地文件，gitignore 管理，不入库）。

## 外部验证计划执行状态（2026-10-23）

依据 `20261022-外部验证计划与Checklist.md`：

- **第 0 章 通用前置 G1–G9**：✅ 完成。代码冻结基线 `6f64572`；`backend/data` 备份在 `backups/verify-20261022-pre/`（115 文件 + sha256，恢复演练通过）；验证期间真实数据零写入（每章结束字节级复核）。
- **第 1 章 真实付费 LLM**：✅ 基本完成（硅基 SiliconFlow，Key 经界面录入）。L1–L7、L10、L12–L14 通过；**L8/L9 部分完成**（DeepSeek context 完整 126032；GLM/Qwen 被硅基 TPM 429 截断；output 探测三模型未建立——DeepSeek 生成 1024 tokens >30s）。记录的问题（错误中文化、30s 超时、400 守卫、停止按钮、429 重试）已全部修复并线上复测。
- **第 2 章 真实 MCP**：✅ 完成（M1–M9 全过）。真实 server 用本机 kimi-datasource 插件（`node plugins/managed/kimi-datasource/bin/kimi-datasource.mjs`）；只读判定以 annotations.readOnlyHint 为唯一依据。
- **第 3 章 用友 BIP**：✅ 11/11 完成（凭证缺口 2026-10-23 修复：检测签名「分录摘要/制单人→voucher、对方科目→aux」+ 多 sheet 以 sheet 名为账簿 + 续行 ffill；原稿 13001 行全导入）。脱敏数据在 `脱敏数据/`（生成器 `dev/desensitize_finance_data.py`，校验器 `dev/verify_desensitized.py`）。
- **第 4 章 外部财务引擎/管线**：🔜 费用分类主链已过（669 行/657 命中，与引擎独立运行对拍一致）；发票 ZIP 批次冒烟通过；POS/现金流/报表看板待逐管线对拍。引擎路径支持**目录或单一文件**（`utils.resolve_engine_root`，文件取父目录）。
- **第 4 章 外部财务引擎/管线**：🔜 数据就绪（POS 8 日包 + 现金流 5 文件已脱敏）；费用分类/发票真实样本待用户提供；引擎本机已定位 5 个。第 5 章微信报表监控、第 6 章真人可用性随后。
- 证据归档：`backend/.pytest_tmp/verify-20261022-{general,llm,mcp,bip}/`（result.json + report.md + 日志）。

## 验证环境约定（重要）

- 隔离服务模板见清单 0.1 节；**路径必须用 `D:/…` Windows 风格**——Git Bash 的 `$PWD`（`/d/…`）会被 SQLAlchemy 在 Windows 上误解析成 `D:\d\…`（2026-10-22 踩过，已清理）。
- 真实 Key 只经界面/环境变量输入，禁止写入文档、日志、提交、截图；证据目录内只放合成值与脱敏记录。
- 请求预算：首轮单主题 ≤50 次真实供应商请求（含 SDK 重试），达到即停。

## 工程约定

- git 提交经 clean/smudge 脱敏过滤器（本机路径/公司名/昵称 → 占位符）；反向恢复钥匙 `dev/sanitize-map.local.json` 不入库，丢失无法自动还原（机制见 `dev/SANITIZE.md`）。
- 提交信息用中文 conventional commits（`fix(assistant): …` / `docs: …` / `test: …`）。
- 测试：后端 `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q`；前端 `cd frontend && npm test` + `npm run build`。
- **脱敏数据管线**（外部验证数据准备）：生成 `python dev/desensitize_finance_data.py {bip|pos|cash}`，校验 `python dev/verify_desensitized.py`（需 `VERIFY_ATTACH_DIR` 指向会话附件目录）。产物在 `脱敏数据/`（gitignore）。规则：保勾稽随机数（凭证借=贷、明细账余额链、余额表配平）；泛化命名**避开 BIP 表头关键词**（账簿/科目/日期/凭证号/借方/贷方/期初/期末/本期/摘要），否则污染 `_locate_header_row`。
