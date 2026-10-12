# 逆向分析 · 当前运行环境概况（2026-10-12 实测快照）

> 全部数据来自本机实测（版本命令 / du / pytest 计数 / sqlite 计数 / git log）。

## 一、软硬件版本

| 项 | 实测值 |
|----|--------|
| OS | Windows 11 x64 |
| Python | 3.13.15（venv：backend/venv，419 MB，117 包） |
| Node | v24.1.0 / npm 11.3.0 |
| 关键依赖 | fastapi 0.141.1 / sqlalchemy 2.x / agentscope 2.0.7.post1 / duckdb 1.5.6 / pandas 2.3.3 / ag-grid-vue3 36.2.0 |
| Git | 20 提交（2026-09-21 → 10-12），工作区干净，**未 push** |

## 二、代码与测试规模

| 项 | 数量 |
|----|------|
| 后端 app/ | 126 个 py / 24,670 行 |
| 前端 src/ | 62 个 vue+js / 9,364 行 |
| 测试文件 | 后端 27 个 / 前端 15 个 |
| 测试用例 | pytest **294** / vitest **82**（全绿，2026-10-12 全量实测） |
| 构建 | npm run build 通过（主 chunk gzip 412KB） |

## 三、运行数据（backend/data/workbench.db）

- 744 KB / **35 张表**；内容：任务 12、笔记 7、专家 13、供应商模型 15、技能包 2、专家包文件 8、记忆 0、MCP 服务器 0、定时任务 0、BIP 凭证 0、个人记账流水 0（财务模块数据随用随增）
- 数据目录：`data/finance/`（vault.duckdb + 各模块 runs/snapshots）、`data/skills/`（2 包）、`data/expert-packs/`（8 文件）

## 四、服务与端口

- 生产形态：uvicorn :8000 单端口托管前端 dist（冒烟常用 889x 临时端口，已清理）
- 计划任务：VaultWeeklyBackup（周日 09:00，10-11 起恢复正常，rc=0）
- AssetLib周备份：robocopy /MIR → C:\LocalBackup（勿放非镜像文件）

## 五、配置状态

- LLM：界面配置（DPAPI 加密）；供应商预设 18 家（provider_presets.json）；模型探测字段已就绪（未对在线模型大规模探测——按需触发）
- 引擎路径：全部按默认相对推导可用（本机AssetLib布局未变）
- 版本：v1.14.0（config.py + package.json 一致）

## 六、风险快照

- 未 push：公开仓无远端同步，本机是唯一副本——建议按 REVERSE_01 的 CD 章节建立外部备份节奏
- finance_cashflow 模块仅本地存在（gitignore），换机需按开发记录重建
- 单日 6 个版本的密集变更：回滚策略 = git 提交级（每阶段可独立 revert）
