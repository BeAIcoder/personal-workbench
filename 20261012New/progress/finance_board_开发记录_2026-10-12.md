# finance_board 报表看板 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_board/` + `frontend/src/modules/finance-board/`
> 级别：L2 产物聚合（管线保留在原项目）

## 设计

- 原项目 `01-财税自动化/微信报表监控/` 的四步管线（m01 扫描 → m02 抽取 → m03 聚合 → m04 HTML）**原位保留**，模块只以子进程触发（cwd=管线目录，`sys.executable` 跑脚本，单步超时 600s）
- 产物（内嵌 ECharts 的单文件 HTML 看板，脱敏由原项目 masking.py 保证）复制为自包含快照：`data/finance/finance_board/snapshots/<时间戳>/`，与 SQLite 登记表 `mod_board_snapshot` 对应
- 接口：`GET /snapshots`、`POST /run`、`GET /latest`、`GET /snapshots/{id}/html`（FileResponse + CSP frame-ancestors 'self'，前端 iframe 直接嵌）
- 降级：管线目录缺失/步骤失败/超时 → 503 中文提示；失败也登记快照（status=failed）留痕
- 新增配置 `finance_board_pipeline_path`（留空按默认相对位置推导到 `01-财税自动化/微信报表监控`）

## 前端

看板页：运行按钮 + 快照下拉切换 + iframe（1200px 高）+ 快照记录表。首次刷新约 2 分钟（真实管线耗时）。

## 测试与验收

- pytest 5 例：模块注册、管线目录推导、503 降级（monkeypatch 无效目录）、快照列表/latest/HTML 端点全链、空库 404
- 真实服务冒烟：`POST /run` 实测 132.9s 成功，看板 HTML（1.09MB）经接口 200 服务；该真实快照保留 1 份在数据目录（不入 git）
- 测试不触发真实管线（依赖本机微信文件目录且耗时），快照数据全部合成

## 已知限制

- 管线运行会重写原项目 `output/` 下的看板 HTML（与原工具 bat 行为一致，幂等可重复）
- 暂为手动触发；定时刷新待阶段四 APScheduler 升级后接入
