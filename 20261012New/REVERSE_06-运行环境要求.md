# 逆向分析 · 运行环境要求（2026-10-12）

> 从代码硬依赖（pywin32/DPAPI）、启动脚本、引擎推导路径反推出的环境规格。

## 一、硬要求（不满足无法运行）

| 项 | 要求 | 依据 |
|----|------|------|
| 操作系统 | **Windows 10/11 x64** | `ensure_dpapi_available()` 缺 pywin32 拒绝启动（API Key 加密硬依赖，不降级明文） |
| Python | **3.12+**（开发/锁定 3.13.15） | requirements-lock.txt 按 3.13.15 冻结；agentscope 2.0.7 要求 |
| Node | **22+**（开发 24.1.0） | 前端构建（Vite 5） |
| 磁盘 | ≥ 1.5 GB（venv 419MB + node_modules + 构建产物） | du 实测 |
| 端口 | 8000 可占用（开发模式另需 5173） | uvicorn/vite |
| 网络 | 首次 `启动工作台.bat` 需访问 PyPI/npm 源 | 自动装依赖 |

## 二、软要求（影响部分功能）

| 项 | 说明 |
|----|------|
| AssetLib目录结构 | 费用分类/发票/现金流/POS 引擎按**相对路径推导**（`D:\AssetLib\01-财税自动化\...`）；目录被移动需在 `backend/.env` 配置对应 `finance_*_engine_path` |
| 微信文件目录 | 报表看板管线读取原项目数据目录（`微信报表监控/`），无数据时管线产空看板 |
| LLM 端点 | AI 助手/探测/记忆抽取需要：任一 OpenAI 兼容或 Anthropic 端点 + API Key（界面配置，DPAPI 加密存储） |
| Office/WPS | 收入确认附件整理（未模块化，桌面 exe）需要；模块化功能不依赖 |

## 三、明确不需要

- Docker / WSL / 数据库服务（SQLite 单文件）
- 管理员权限（计划任务与 robocopy 除外）
- 外网常连（除 LLM 调用与依赖安装；测试全离线）

## 四、换机/重建清单

1. 拷贝仓库（或 git clone + 按 `20261012New/progress/finance_cashflow_开发记录` 重建本地专用模块）
2. 按本要求装 Python 3.13 + Node 24
3. 双击 `启动工作台.bat`（自建 venv）
4. 若AssetLib路径变化：配置 `.env` 的引擎路径项
5. 恢复 `backend/data/`（备份数据.bat 的产物）——注意 data/ 不入 git
6. 所得税历史数据：`python 20261012New/scripts/migrate_tax_data.py`（幂等）
