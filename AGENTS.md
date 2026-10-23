# AGENTS.md — personal-workbench 项目记忆

> 本文件是项目级长期记忆：跨会话保持的状态、约定与待办。Hub 级记忆（脱敏钥匙等）见 Agent Hub，不在此重复。

## 项目

Personal Workbench 个人财税自动化工作台（FastAPI + SQLAlchemy + SQLite / Vue 3 + Vite + Element Plus）。开发进度与验证史见 `progress.md`（本地文件，gitignore 管理，不入库）。

## 外部验证计划执行状态（2026-10-23）

依据 `20261022-外部验证计划与Checklist.md`：

- **第 0 章 通用前置 G1–G9**：✅ 完成。代码冻结基线 `6f64572`；`backend/data` 备份在 `backups/verify-20261022-pre/`（115 文件 + sha256，恢复演练通过）；验证期间真实数据零写入（每章结束字节级复核）。
- **第 1 章 真实付费 LLM**：✅ 完成（硅基 SiliconFlow，Key 经界面录入）。L1–L7、L10、L12–L14 通过；**L8/L9 完全体已补上**——硅基 TPM 不限流窗口未等到，改用 Step5 preview 的 KimiCode 参数适配推进（context 984,407 完整建立、output 1024、reasoning 四级 supported；429 自动退避重试 30s×2 上线）。GLM/Qwen 被硅基 TPM 429 截断的记录在案（外部约束）。记录的问题（错误中文化、30s 超时、400 守卫、停止按钮、429 重试）已全部修复并线上复测。
- **第 2 章 真实 MCP**：✅ 完成（M1–M9 全过）。真实 server 用本机 kimi-datasource 插件（`node plugins/managed/kimi-datasource/bin/kimi-datasource.mjs`）；只读判定以 annotations.readOnlyHint 为唯一依据。
- **第 3 章 用友 BIP**：✅ 11/11 完成（凭证缺口 2026-10-23 修复：检测签名「分录摘要/制单人→voucher、对方科目→aux」+ 多 sheet 以 sheet 名为账簿 + 续行 ffill；原稿 13001 行全导入）。脱敏数据在 `脱敏数据/`（生成器 `dev/desensitize_finance_data.py`，校验器 `dev/verify_desensitized.py`）。
- **第 4 章 外部财务引擎/管线**：✅ 五管线全部完成——费用分类（对拍一致）、发票（ZIP+冲红配对）、POS（四源对拍一致）、现金流、报表看板（快照+CSP+nosniff）；E4 超限 400 全过；**E9 ±20% 同数据复跑判定通过**（expense +4~12%、POS +18.8%、现金流 +0.9%、看板 +8.5%，均在噪声带内）。引擎路径支持**目录或单一文件**（`utils.resolve_engine_root`）。修复：TEMP 环境变量、上传文件名 mojibake、POS analyze --pos/--project（`utils.engine_subprocess_env` / `repair_upload_mojibake`）。
- **第 5 章 报表看板 W1–W9**：✅ 全过（多次 run 200 / iframe 真实渲染 27,531.6 万元 / log_tail 有界 500 字符 / failed 快照可见 / 目录改名 503 中文 / 并行双 run A200-B409 / 路径安全单测覆盖 / CSP+nosniff / 脱敏源 14 文件 sha256 零改动）。证据：`backend/.pytest_tmp/verify-20261022-exp/board-w1-w9-e9rerun.json`。
- **第 6 章 真人可用性 U1–U10**：🔶 三档证据完成——① API 级代行（U1–U10 流程可达性）；② **browser-use 轮**（Kimi Code Desktop 内置浏览器真实点击/文件对话框/确认框：U2 新建、U3 月历+误操作 422、U4 笔记+搜索、U5 设置抽屉、U6 真实上传分类 98.2%、U7 取消零请求/确认 204）；③ U5/U6/U9/U10 引用第 1/4 章实证。browser-use 轮发现并修复 P3（缺 Key 时「当前生效模型」文案误导，前端 652/652）。U10 ≥80% 无协助阈值**仍待真人复测**（browser-use 不能替代真人迷路/主观评分）。记录表：`backend/.pytest_tmp/verify-20261022-exp/usability-task-script.md`。
- 证据归档：`backend/.pytest_tmp/verify-20261022-{general,llm,mcp,bip,exp}/`（result.json + report.md + 日志）。

## 架构与算法文档（2026-10-23 新建）

- `算法实现逻辑.md`：四级路由 / MCP readOnlyHint 门控 / BIP 导入四机制 / 脱敏保勾稽随机 / 探测二分+429 重试 / 引擎路径解析 / TEMP 与乱码修复。
- `功能模块拓扑图.md`：总体拓扑 / 模块注册机制 / 11 模块清单 / 财务五管线数据流 / AI 栈 / 数据边界 / 前端拓扑。
- `spec.md` 第 12/13/15 节已追平 as-built；`API.md` 接口清单不变更时以代码为准。

## 验证环境约定（重要）

- 隔离服务模板见清单 0.1 节；**路径必须用 `D:/…` Windows 风格**——Git Bash 的 `$PWD`（`/d/…`）会被 SQLAlchemy 在 Windows 上误解析成 `D:\d\…`（2026-10-22 踩过，已清理）。
- 真实 Key 只经界面/环境变量输入，禁止写入文档、日志、提交、截图；证据目录内只放合成值与脱敏记录。
- 请求预算：首轮单主题 ≤50 次真实供应商请求（含 SDK 重试），达到即停。
- **Node 运行时 ≥ v24.22.1**（2026-10-21 官方安全更新：CVE-2026-111497 Brotli 解码器越界写 RCE 等共 10 项，22.x/24.x/26.x 低于修复版均受影响）。本机两处 Node 安装（DevEnv 运行时目录与 Program Files）都须 ≥ 该版本；`frontend/.nvmrc` 与 `frontend/package.json` 的 `engines` 为权威约束。PHP 本项目零使用（2026-10-23 排查确认：无 .php 文件、未安装、无容器），无需维护。

## 工程约定

- git 提交经 clean/smudge 脱敏过滤器（本机路径/公司名/昵称 → 占位符）；反向恢复钥匙 `dev/sanitize-map.local.json` 不入库，丢失无法自动还原（机制见 `dev/SANITIZE.md`）。
- 提交信息用中文 conventional commits（`fix(assistant): …` / `docs: …` / `test: …`）。
- 测试：后端 `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q`；前端 `cd frontend && npm test` + `npm run build`。
- **脱敏数据管线**（外部验证数据准备）：生成 `python dev/desensitize_finance_data.py {bip|pos|cash}`，校验 `python dev/verify_desensitized.py`（需 `VERIFY_ATTACH_DIR` 指向会话附件目录）。产物在 `脱敏数据/`（gitignore）。规则：保勾稽随机数（凭证借=贷、明细账余额链、余额表配平）；泛化命名**避开 BIP 表头关键词**（账簿/科目/日期/凭证号/借方/贷方/期初/期末/本期/摘要），否则污染 `_locate_header_row`。
