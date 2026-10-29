"""finance_board 数据仓服务：微信报表监控管线运行 + HTML 快照管理。

管线保留在原项目（`01-财税自动化/微信报表监控`，纯标准库脚本 m01→m04，
产物为内嵌 ECharts 的单文件 HTML 看板，脱敏由原项目 masking.py 保证）。
本模块只做三件事：按需触发管线（子进程）、把产物复制为自包含快照留档、
提供快照清单与最新快照访问。换机/移目录后配置 finance_board_pipeline_path。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ...config import get_finance_data_dir, settings
from ...utils import engine_subprocess_env, resolve_engine_root
from ...database import SessionLocal
from .models import BoardSnapshot

logger = logging.getLogger(__name__)

# 管线脚本（按序执行，全部来自原项目 scripts/）
PIPELINE_STEPS = ["m01_scan.py", "m02_extract.py", "m03_aggregate.py", "m04_html.py"]
# 原项目产出的看板文件名（m04 写死）
DASHBOARD_NAME = "经营财务报表监控看板.html"
# 单步超时（秒）
STEP_TIMEOUT = 600
# 计划任务通道整条管线的等待上限（秒）：m02 脱敏抽取约 80-90s，给足余量
TASK_CHANNEL_TIMEOUT = 600
# 本机计划任务名（微信报表监控项目的 服务安装.ps1 注册）
BOARD_TASK_NAME = "微信报表监控"

# 管线互斥锁：stamp 精度到秒且是快照目录名，并发 /run 会共用同一快照目录互相踩踏；
# 非阻塞抢占，抢不到即视为「运行中」由路由层返回 409。
_PIPELINE_LOCK = threading.Lock()


class PipelineUnavailableError(RuntimeError):
    """管线目录缺失或不可用。"""


class PipelineBusyError(RuntimeError):
    """已有管线正在运行中，由路由层映射为 409。"""


def pipeline_dir() -> Path:
    """微信报表监控项目目录：配置优先，否则按AssetLib默认相对位置推导。"""
    if settings.finance_board_pipeline_path:
        # 支持目录或单一文件（文件取其父目录）——见 utils.resolve_engine_root
        return resolve_engine_root(settings.finance_board_pipeline_path)
    # backend/app/modules/finance_board/service.py → AssetLib/01-财税自动化/微信报表监控
    base = Path(__file__).resolve()
    for parent in base.parents:
        if parent.name == "01-财税自动化":
            return parent / "微信报表监控"
    raise PipelineUnavailableError("无法定位微信报表监控目录，请配置 finance_board_pipeline_path")


def _snapshots_dir() -> Path:
    d = get_finance_data_dir() / "finance_board" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_step(script: Path) -> str:
    """执行单个管线脚本（cwd=管线目录），返回输出尾部；失败抛 CalledProcessError。"""
    proc = subprocess.run(
        [sys.executable, script.name],
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        timeout=STEP_TIMEOUT,
        encoding="utf-8",
        errors="replace",
        env=engine_subprocess_env(),
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, script.name, proc.stdout, proc.stderr)
    return (proc.stdout or "")[-2000:]


def _scheduled_task_available() -> bool:
    """本机是否注册了报表监控计划任务（服务安装.ps1 注册）。"""
    proc = subprocess.run(
        ["schtasks", "/query", "/tn", BOARD_TASK_NAME],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode == 0


def _scheduled_run_state() -> dict:
    """管线服务自记的上次运行状态（logs/last_run.json，run_service.py 写）。"""
    try:
        return json.loads((pipeline_dir() / "logs" / "last_run.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_steps_via_scheduled_task() -> str:
    """经计划任务通道跑完整管线并等待结束，返回日志摘要。

    本机终端安全软件（QQ管家/透明加密过滤驱动）按进程树拦截写入：工作台后端
    （venv python）派生的进程禁止写 D:\\AssetLib 区域，直接跑管线脚本必
    PermissionError。计划任务实例由任务计划服务派生、不在被拦截的进程树内，
    实测可稳定跑通（每日定时任务一直走此通道）。无计划任务的机器走直接执行。
    """
    before = _scheduled_run_state()
    before_started = before.get("started", "")
    if before_started and not before.get("finished"):
        raise PipelineBusyError("报表监控管线正在运行中（计划任务），请等待结束后再试")
    proc = subprocess.run(
        ["schtasks", "/run", "/tn", BOARD_TASK_NAME],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    msg = (proc.stderr or proc.stdout or "").strip()
    if proc.returncode != 0:
        if "已经运行" in msg or "already running" in msg.lower():
            raise PipelineBusyError("报表监控管线正在运行中（计划任务），请等待结束后再试")
        raise PipelineUnavailableError(f"触发计划任务失败：{msg[:200]}")
    deadline = time.monotonic() + TASK_CHANNEL_TIMEOUT
    last: dict = {}
    while time.monotonic() < deadline:
        time.sleep(2)
        last = _scheduled_run_state()
        if last.get("started") and last.get("started") != before_started and last.get("finished"):
            break
    else:
        raise PipelineUnavailableError(
            f"计划任务运行超时（>{TASK_CHANNEL_TIMEOUT}s），请查看微信报表监控 logs 目录"
        )
    if not last.get("ok"):
        failed = next((s for s in last.get("steps", []) if s.get("exit")), None)
        name = failed.get("step") if failed else "未知步骤"
        raise PipelineUnavailableError(
            f"管线步骤失败：{name}（计划任务通道，详见微信报表监控 logs\\service_*.log）"
        )
    lines = [f"== 计划任务通道（{BOARD_TASK_NAME}，started={last.get('started')}）=="]
    for s in last.get("steps", []):
        lines.append(f"{s.get('step')} exit={s.get('exit')} {s.get('seconds')}s")
    return "\n".join(lines)


def _run_steps_directly(pdir: Path, stamp: str, t0: float) -> str:
    """直接以子进程执行四步管线（无计划任务的机器/换机场景）；失败落失败快照并抛异常。"""
    logs: list[str] = []
    try:
        for step in PIPELINE_STEPS:
            script = pdir / "scripts" / step
            if not script.is_file():
                raise PipelineUnavailableError(f"缺少管线脚本：{script}")
            logs.append(f"== {step} ==\n{_run_step(script)}")
    except subprocess.TimeoutExpired as exc:
        _save_snapshot(stamp, "failed", "", "\n".join(logs) + f"\n超时：{exc}", time.perf_counter() - t0)
        raise PipelineUnavailableError(f"管线执行超时（>{STEP_TIMEOUT}s）") from exc
    except subprocess.CalledProcessError as exc:
        tail = "\n".join(logs) + f"\n{exc.stderr[-2000:] if exc.stderr else ''}"
        _save_snapshot(stamp, "failed", "", tail, time.perf_counter() - t0)
        raise PipelineUnavailableError(f"管线步骤失败：{exc.cmd}") from exc
    return "\n".join(logs)


def run_pipeline() -> dict:
    """触发一次完整管线并归档快照。返回快照信息；管线不可用/失败抛异常。

    运行中重入抛 PipelineBusyError（409）：并发运行会共用同一 stamp 快照目录。
    """
    if not _PIPELINE_LOCK.acquire(blocking=False):
        raise PipelineBusyError("报表监控管线正在运行中，请等待本次运行结束后再试")
    try:
        return _run_pipeline_locked()
    finally:
        _PIPELINE_LOCK.release()


def _run_pipeline_locked() -> dict:
    """run_pipeline 的本体（调用方须已持有 _PIPELINE_LOCK）。"""
    pdir = pipeline_dir()
    if not (pdir / "scripts").is_dir():
        raise PipelineUnavailableError(f"管线目录无效：{pdir}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    t0 = time.perf_counter()
    if _scheduled_task_available():
        # 本机（有终端安全软件进程树拦截）：走计划任务通道
        try:
            log_text = _run_steps_via_scheduled_task()
        except PipelineBusyError:
            raise
        except PipelineUnavailableError as exc:
            _save_snapshot(stamp, "failed", "", str(exc), time.perf_counter() - t0)
            raise
    else:
        # 无计划任务的机器（换机场景）：保持原有的直接子进程执行
        log_text = _run_steps_directly(pdir, stamp, t0)

    # 产物复制为自包含快照（原文件保持不动）
    src_html = pdir / "output" / DASHBOARD_NAME
    if not src_html.is_file():
        _save_snapshot(stamp, "failed", "", log_text, time.perf_counter() - t0)
        raise PipelineUnavailableError(f"管线完成但未找到看板产物：{src_html}")
    dest_dir = _snapshots_dir() / stamp
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_html = dest_dir / DASHBOARD_NAME
    shutil.copy2(src_html, dest_html)

    snap = _save_snapshot(stamp, "ok", str(dest_html), log_text, time.perf_counter() - t0)
    return _out(snap)


def _save_snapshot(stamp: str, status: str, html_path: str, log_tail: str, elapsed: float) -> BoardSnapshot:
    with SessionLocal() as db:
        snap = BoardSnapshot(
            stamp=stamp, status=status, html_path=html_path,
            log_tail=log_tail[-4000:], elapsed_sec=round(elapsed, 2),
        )
        db.add(snap)
        db.commit()
        db.refresh(snap)
        return snap


def _out(s: BoardSnapshot) -> dict:
    return {
        "id": s.id,
        "stamp": s.stamp,
        "status": s.status,
        "html_path": s.html_path,
        "elapsed_sec": s.elapsed_sec,
        # 入库时已截到 4000 字符，读取再取尾部 500：日志有界暴露，不整文件无界返回
        "log_tail": s.log_tail[-500:] if s.log_tail else "",
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
    }


def list_snapshots(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(BoardSnapshot).order_by(BoardSnapshot.id.desc()).limit(limit).all()
    return [_out(r) for r in rows]


def get_snapshot(db: Session, snapshot_id: int) -> BoardSnapshot:
    from fastapi import HTTPException

    snap = db.get(BoardSnapshot, snapshot_id)
    if snap is None or snap.status != "ok" or not snap.html_path:
        raise HTTPException(status_code=404, detail="快照不存在或未成功")
    return snap


def latest_snapshot(db: Session) -> BoardSnapshot | None:
    return (
        db.query(BoardSnapshot)
        .filter(BoardSnapshot.status == "ok")
        .order_by(BoardSnapshot.id.desc())
        .first()
    )
