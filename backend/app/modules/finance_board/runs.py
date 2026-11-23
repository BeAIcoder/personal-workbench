"""看板异步运行注册表（单进程内存实现）。

当前部署是单进程、单用户（uvicorn 一个 worker，无多实例），因此异步运行只需要
进程内 registry + 后台线程，不引入队列、任务表或外部依赖。进程重启后未完成的
记录会丢失（`GET /runs/{id}` 返回 404，前端提示重新运行），这是既定取舍。

与同步入口的关系：
- 真正的管线执行、重试（`_PIPELINE_ATTEMPTS`）、预算常量、失败快照与告警全部
  复用 `service.run_pipeline` 的本体 `_run_pipeline_locked`，本模块不另起一套逻辑；
- `start_run()` 先抢 `service._PIPELINE_LOCK` 再登记记录：抢不到说明已有运行
  （同步 `/run`、上一次异步运行或计划任务通道占用的 run.lock），由路由层映射 409；
  抢到了才返回 202，即「202 一定代表本次会真正执行」；
- 锁所有权随线程交接：本模块持有、在后台线程结束时释放，因此并发 `/run` 与
  `/runs` 共享同一把互斥锁，不会出现同一 stamp 快照目录互相踩踏。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

from . import service

logger = logging.getLogger(__name__)

# 运行状态：running = 已受理未结束；ok = 管线成功并已归档快照；failed = 失败（原因见 error）
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAILED = "failed"

# 完成记录保留策略：最多 _MAX_RECORDS 条、最多 _RECORD_TTL_SECONDS 秒，防止内存无界增长。
# 运行中的记录永不淘汰（同一时刻至多一条：受 _PIPELINE_LOCK 约束）。
_MAX_RECORDS = 20
_RECORD_TTL_SECONDS = 3600

_REGISTRY_LOCK = threading.Lock()
_RUNS: dict[str, dict] = {}


def _external_pipeline_busy() -> bool:
    """管线自己的 run.lock 是否新鲜（计划任务/其他进程在跑）。

    只读文件 mtime，不触发 schtasks；取不到（目录未配置等）按「不忙」处理，
    让运行按既有失败语义（管线不可用）异步上报，而不是在这里吞成 500。
    """
    try:
        return service._pipeline_lock_fresh()
    except Exception:  # noqa: BLE001  探测失败不得影响受理
        logger.debug("run.lock 探测失败，按不忙处理", exc_info=True)
        return False


def _prune(now: float) -> None:
    """清理过期与超量的完成记录（调用方须持有 _REGISTRY_LOCK）。"""
    for run_id, record in list(_RUNS.items()):
        if record["finished"] and now - record["finished_at"] > _RECORD_TTL_SECONDS:
            _RUNS.pop(run_id, None)
    overflow = len(_RUNS) - _MAX_RECORDS
    if overflow <= 0:
        return
    finished = sorted(
        (r for r in _RUNS.values() if r["finished"]),
        key=lambda r: r["finished_at"],
    )
    for record in finished[:overflow]:
        _RUNS.pop(record["run_id"], None)


def _view(record: dict) -> dict:
    """对外视图（契约字段固定）：运行中给实时耗时，其余字段保持 None 直到有值。"""
    elapsed = record["elapsed_sec"]
    if not record["finished"]:
        elapsed = time.monotonic() - record["started_at"]
    return {
        "run_id": record["run_id"],
        "status": record["status"],
        "snapshot_id": record["snapshot_id"],
        "error": record["error"],
        "elapsed_sec": elapsed,
        "log_tail": record["log_tail"],
    }


def _finish(run_id: str, *, status: str, snapshot_id: int | None = None,
            error: str | None = None, elapsed_sec: float | None = None,
            log_tail: str | None = None) -> None:
    with _REGISTRY_LOCK:
        record = _RUNS.get(run_id)
        if record is None:
            return
        record.update(
            status=status,
            snapshot_id=snapshot_id,
            error=error,
            elapsed_sec=elapsed_sec,
            log_tail=log_tail,
            finished=True,
            finished_at=time.monotonic(),
        )


def _execute(run_id: str, lock: threading.Lock) -> None:
    """后台线程本体：跑管线并登记结果；无论成败都释放锁并在释放后写终态。

    先释放锁再写终态：调用方看到终态即可确定锁已空闲（下一个请求不会误得 409）。
    """
    started = time.perf_counter()
    result: dict = {}
    try:
        snap = service._run_pipeline_locked()
        result = {
            "status": STATUS_OK,
            "snapshot_id": snap.get("id"),
            "elapsed_sec": snap.get("elapsed_sec"),
            "log_tail": snap.get("log_tail") or None,
        }
    except service.PipelineBusyError as exc:
        # 管线自带的 run.lock 在受理后被占用（计划任务/其他进程）：如实记失败，不重试
        result = {
            "status": STATUS_FAILED,
            "error": str(exc),
            "elapsed_sec": time.perf_counter() - started,
        }
    except service.PipelineUnavailableError as exc:
        # 与同步 /run 的 503 同源：失败快照与告警已在管线内落盘，这里只带回原因
        result = {
            "status": STATUS_FAILED,
            "error": str(exc),
            "elapsed_sec": time.perf_counter() - started,
            "log_tail": (getattr(exc, "logs", "") or "") or None,
        }
    except Exception as exc:  # noqa: BLE001  后台线程不得静默死掉，失败原因要能查
        logger.exception("看板异步运行失败：run_id=%s", run_id)
        result = {
            "status": STATUS_FAILED,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": time.perf_counter() - started,
        }
    finally:
        lock.release()
        if not result:
            result = {"status": STATUS_FAILED, "error": "运行线程异常终止"}
        _finish(run_id, **result)


def start_run() -> dict:
    """受理一次异步运行：返回 {run_id, status=running}；已有运行在跑抛 PipelineBusyError。"""
    lock = service._PIPELINE_LOCK
    if not lock.acquire(blocking=False):
        raise service.PipelineBusyError("报表监控管线正在运行中，请等待本次运行结束后再试")
    run_id = uuid.uuid4().hex[:12]
    try:
        if _external_pipeline_busy():
            raise service.PipelineBusyError(
                "报表监控管线正在运行中（计划任务通道），请等待本次运行结束后再试"
            )
        now = time.monotonic()
        with _REGISTRY_LOCK:
            _RUNS[run_id] = {
                "run_id": run_id,
                "status": STATUS_RUNNING,
                "snapshot_id": None,
                "error": None,
                "elapsed_sec": None,
                "log_tail": None,
                "started_at": now,
                "finished": False,
                "finished_at": 0.0,
            }
            # 先登记再淘汰：淘汰只看已完成记录，不会误删本次运行中的这条
            _prune(now)
        thread = threading.Thread(
            target=_execute, args=(run_id, lock), name=f"board-run-{run_id}", daemon=True
        )
        thread.start()
    except BaseException:
        # 锁必须归还：否则后续所有运行都会被误判为忙态
        lock.release()
        with _REGISTRY_LOCK:
            _RUNS.pop(run_id, None)
        raise
    return {"run_id": run_id, "status": STATUS_RUNNING}


def get_run(run_id: str) -> dict | None:
    """按 run_id 查状态；未登记/已过期/服务重启后返回 None（路由层 404）。"""
    with _REGISTRY_LOCK:
        record = _RUNS.get(run_id)
        return _view(record) if record is not None else None


def reset_registry() -> None:
    """清空内存 registry（测试与运维用）；不影响正在执行的管线与 _PIPELINE_LOCK。"""
    with _REGISTRY_LOCK:
        _RUNS.clear()
