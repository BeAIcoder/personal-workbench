# 报表看板排障 Playbook

> 适用：`finance_board` 运行 409/503/超时、快照不更新、verify 差异、计划任务通道异常。

## 1. 先看状态，不要先重跑

```bash
# 后端日志（按你的数据根）
tail -100 backend/data/logs/*.log

# 管线锁/最近运行（外部管线目录）
ls -l <pipeline_dir>/logs/run.lock
cat <pipeline_dir>/logs/last_run.json

# 计划任务状态（Windows）
schtasks /query /tn "微信报表监控" /v /fo LIST
```

关键事实：

- `run.lock` 是“正在运行”的唯一在途信号；`last_run.json` 只在跑完后写。
- 计划任务通道：`schtasks /run` 返回 0 不等于脚本成功；启动宽限 60s 内必须看到 `run.lock`。
- 单次尝试预算 660s，最多 2 次，最坏 1,385s；前端 1,500s。
- 失败会落 failed 快照 + 调试中心 P2；连续两条 failed → DQ error。

## 2. 常见分支

| 现象 | 判断 | 处理 |
|---|---|---|
| 409 运行中 | `run.lock` 新鲜 | 等；不要重复点；陈旧锁 3h 才视为过期 |
| 503 快速失败 | schtasks 0 但无 `run.lock` | 看计划任务“上次结果”、脚本路径、Python 环境 |
| 503 超时 | 600s 内无 `last_run.json` | 看四步日志；单步 `STEP_TIMEOUT=600` |
| 快照不更新 | `last_run.json` 旧/失败 | 看 `verify`、产物 mtime、`_save_snapshot` |
| verify 差异 | 内嵌 HTML vs `report_data.json` | 看 `truncated`/真实总数；不要只信 20 条截断 |
| 前端假失败 | 前端超时 < 后端预算 | 看 `BoardView.vue` 1,500s；`check_contracts` |

## 3. 直接通道回退

没有 `微信报表监控` 任务的机器会自动回退直接子进程：

- 四步共用 `DIRECT_ATTEMPT_BUDGET_SECONDS=600`，每步按剩余预算收紧。
- 失败重试 2 次，间隔 5s；重试前等残留锁最多 60s。
- 直接通道失败不会写计划任务 `last_run.json`，看应用日志/快照。

## 4. 安全与边界

- 不要删除 `run.lock` 来“解除 409”；先确认没有真实进程在跑。
- 不要绕过计划任务通道直接写项目根（本机安全软件会拦）。
- 不要把真实产物/客户名贴进 issue；用脱敏样本。
- 重跑前确认 `report_data.json` 与快照时间；避免用旧数据覆盖新快照。

## 5. 验证

```bash
curl -s http://127.0.0.1:8000/api/finance/board/verify
curl -s http://127.0.0.1:8000/api/finance/board/snapshots?limit=5
cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_module_finance_board.py -q
cd .. && python dev/check_contracts.py
```

期望：`verify.fail=0`、最新快照时间晚于 `report_data.json`、无 `map` 敏感键、前端不再 1,500s 假失败。
