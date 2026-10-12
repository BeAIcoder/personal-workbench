# ADR-001：定时调度不引入 APScheduler，cron 自实现 + 模块动作

> 日期：2026-10-12 ｜ 状态：已生效 ｜ 背景：阶段四原计划「APScheduler 替换 20s tick 调度循环」

## 决策

不引入 APScheduler。在现有 asyncio tick 循环上实现：

1. `cron_matches()` 纯函数（五段 cron：`* , - /`，周 0/7=周日，同自然分钟不重复触发）；
2. `JOB_ACTIONS` 模块动作注册表（`register_action`），定时任务按 `action` 字段分流到模块同步 handler（线程池执行），Agent 对话链路不变。

## 理由

- 现有调度循环 134 行、`job_due` 纯函数、201 个测试全绿，是经过验证的稳定子系统；换成 APScheduler（AsyncIOScheduler + 持久化 + 触发器语义）是纯风险，无功能收益
- 真实需求是** cron 表达式**与**非 LLM 的模块动作**（如看板定时刷新），两者用约 90 行增量代码即可满足，且保持单文件、可纯函数测试
- 单用户本机场景不存在多进程/多机调度竞争，APScheduler 的核心卖场（持久化 JobStore、多执行器、missed job 合并）用不上

## 后果

- 接受：调度精度受 tick 间隔（20s）限制；无 missed-job 持久化恢复（重启后到点即跑，语义简单可预期）
- 若将来出现多机部署、任务量大到需要持久化队列，再评估引入 APScheduler 或外部队列，并按 ADR 流程修订本记录
