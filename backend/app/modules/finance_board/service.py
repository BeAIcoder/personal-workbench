"""finance_board 数据仓服务：微信报表监控管线运行 + HTML 快照管理。

管线保留在原项目（`01-财税自动化/微信报表监控`，纯标准库脚本 m01→m04，
产物为内嵌 ECharts 的单文件 HTML 看板，脱敏由原项目 masking.py 保证）。
本模块只做三件事：按需触发管线（子进程）、把产物复制为自包含快照留档、
提供快照清单与最新快照访问。换机/移目录后配置 finance_board_pipeline_path。

计划任务通道契约（外部 `run_service.py`）：启动即创建 `logs/run.lock`，正常/异常
退出都在 finally 删除，随后写 `logs/last_run.json`（只在结束时写）。本模块以
run.lock 的出现/消失作为「管线已启动/已退出」的唯一在途信号；自定义的计划任务
动作必须沿用该 run_service（或至少遵守同样的 run.lock 生命周期与状态落盘顺序），
否则只能退回按计划任务模式（schtasks）判断，可能误判。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ...config import get_finance_data_dir, settings
from ...utils import decode_engine_output, engine_subprocess_env, find_sibling_project, resolve_engine_root
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
# 直接回退（无计划任务通道）单次尝试总预算（秒）：4 步共用一个预算，按剩余时间
# 收紧每步 subprocess 超时，避免 4×STEP_TIMEOUT 撑爆前端 1500s 超时（集成审查 I2）
DIRECT_ATTEMPT_BUDGET_SECONDS = 600
# schtasks 查询/触发子进程的超时（秒）；前端 1500s 预算按这两项 + 单次尝试计入
TASK_QUERY_TIMEOUT_SECONDS = 15
TASK_TRIGGER_TIMEOUT_SECONDS = 30
# 失败自动重试（2026-11-02）：总尝试次数与重试间隔（秒）；进程内锁仍在整套重试期间持有
_PIPELINE_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 5
# 计划任务通道轮询参数（秒）：run.lock 是管线「正在运行」的唯一在途信号
# （run_service.py 启动即建、结束/异常退出时在 finally 删除），last_run.json 只在
# 跑完后写；因此用锁的出现/消失判断「已启动/已提前退出」，不能只看状态文件。
TASK_POLL_INTERVAL_SECONDS = 2
# 触发计划任务后必须在该宽限内看到 run.lock，否则判定为未启动（快速失败）
TASK_START_GRACE_SECONDS = 60
# run.lock 消失后等待 last_run.json 落盘的宽限；超过仍无记录 = 脚本崩溃提前退出
TASK_EXIT_GRACE_SECONDS = 10
# 启动宽限后查询计划任务模式的复查间隔：排队/运行不误判失败（独立审查 M7）
TASK_STATE_RECHECK_SECONDS = 30
# 重试前等待上一次失败尝试残留 run.lock 释放的上限（秒）
RETRY_LOCK_WAIT_SECONDS = 60
# 本机计划任务名兜底默认（微信报表监控项目的 服务安装.ps1 注册）；
# 生效值经 _task_name() 取 settings.finance_board_task_name，配置为空才回落到这里
BOARD_TASK_NAME = "微信报表监控"

# 管线互斥锁：stamp 精度到秒且是快照目录名，并发 /run 会共用同一快照目录互相踩踏；
# 非阻塞抢占，抢不到即视为「运行中」由路由层返回 409。
_PIPELINE_LOCK = threading.Lock()
# 直接执行本次尝试的截止时间（线程本地）：_run_steps_directly 设置，_run_step 读取
# 并按剩余预算收紧单步超时；保持 _run_step(script) 的单参数调用/补丁接缝不变。
_DIRECT_DEADLINE = threading.local()


class PipelineUnavailableError(RuntimeError):
    """管线目录缺失或不可用。"""


class PipelineBusyError(RuntimeError):
    """已有管线正在运行中，由路由层映射为 409。"""


class PipelineChannelUnavailableError(PipelineUnavailableError):
    """计划任务通道本身无法启动（非忙态）。仅此错误允许回退直接执行。"""


def _decode_console(raw: bytes | str | None) -> str:
    """解码 schtasks 输出：本机中文 Windows 为 GBK，优先 UTF-8，其次 GBK/本地编码。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def pipeline_dir() -> Path:
    """微信报表监控项目目录：配置优先，否则按AssetLib默认相对位置推导。"""
    configured = (settings.finance_board_pipeline_path or "").strip()
    if configured:
        # 支持目录或单一文件（文件取其父目录）——见 utils.resolve_engine_root；
        # configured 已判非空，resolve_engine_root 此处不会返回 None。
        return resolve_engine_root(configured)
    # 锚点目录名取 settings.finance_asset_anchor（空则兜底 01-财税自动化），见 utils.find_sibling_project
    found = find_sibling_project("微信报表监控", Path(__file__))
    if found is None:
        raise PipelineUnavailableError("无法定位微信报表监控目录，请配置 finance_board_pipeline_path")
    return found


def _task_name() -> str:
    """报表监控计划任务名：配置优先，空值兜底 BOARD_TASK_NAME。"""
    return settings.finance_board_task_name or BOARD_TASK_NAME


def _snapshots_dir() -> Path:
    d = get_finance_data_dir() / "finance_board" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_step(script: Path) -> str:
    """执行单个管线脚本（cwd=管线目录），返回输出尾部；失败抛 CalledProcessError。

    单步超时默认 STEP_TIMEOUT；直接执行设置了本次尝试截止时间时按剩余预算收紧，
    预算耗尽抛 subprocess.TimeoutExpired（由调用方统一落失败快照）。
    """
    timeout = STEP_TIMEOUT
    deadline = getattr(_DIRECT_DEADLINE, "value", None)
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(cmd=script.name, timeout=STEP_TIMEOUT)
        timeout = min(STEP_TIMEOUT, remaining)
    # 统一字节捕获后经 decode_engine_output 解码（UTF-8 优先、GB18030 回退）：
    # 引擎直写 GBK buffer 时 text=True + errors="replace" 会丢中文（M-7 残余）。
    proc = subprocess.run(
        [sys.executable, script.name],
        cwd=str(script.parent),
        capture_output=True,
        timeout=timeout,
        env=engine_subprocess_env(),
    )
    stdout = decode_engine_output(proc.stdout)
    stderr = decode_engine_output(proc.stderr)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, script.name, stdout, stderr)
    return stdout[-2000:]


def _scheduled_task_available() -> bool:
    """本机是否注册了报表监控计划任务（服务安装.ps1 注册）；查询有超时，失败即不可用。"""
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", _task_name()],
            capture_output=True, timeout=TASK_QUERY_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("schtasks 查询计划任务超时或不可执行", exc_info=True)
        return False
    return proc.returncode == 0


def _scheduled_task_state() -> str | None:
    """查询计划任务当前模式：running / queued / ready / disabled / unknown。

    查询失败或输出不可解析返回 None。用于启动宽限后区分「排队中/运行中」与
    「未启动」，避免把 Task Scheduler 排队或刚启动误判为失败（独立审查 M7）。
    兼容中文「模式: 就绪」与英文「Status: Ready」两种 schtasks 输出。
    """
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", _task_name(), "/fo", "LIST", "/v"],
            capture_output=True, timeout=TASK_QUERY_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    text = _decode_console(proc.stdout or b"")
    mode = ""
    for line in text.splitlines():
        m = re.match(r"^\s*(?:模式|Status)\s*[:：]\s*(.+?)\s*$", line, re.IGNORECASE)
        if m:
            mode = m.group(1).strip()
            break
    if not mode:
        return None
    lowered = mode.lower()
    for state, markers in (
        ("running", ("正在运行", "running")),
        ("queued", ("已排队", "queued")),
        ("ready", ("就绪", "ready")),
        ("disabled", ("已禁用", "disabled")),
    ):
        if any(marker in mode or marker in lowered for marker in markers):
            return state
    return "unknown"


def _scheduled_run_state() -> dict:
    """管线服务自记的上次运行状态（logs/last_run.json，run_service.py 写）。"""
    try:
        return json.loads((pipeline_dir() / "logs" / "last_run.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


# 与管线 run_service.py 的 STALE_LOCK_HOURS 保持一致：锁文件新鲜 = 正在运行
PIPELINE_STALE_LOCK_SECONDS = 3 * 3600


def _lock_mtime() -> float | None:
    """管线运行锁 mtime（不存在/不可读返回 None）；锁本身是「在跑」的唯一在途信号。"""
    lock = pipeline_dir() / "logs" / "run.lock"
    try:
        return lock.stat().st_mtime
    except OSError:
        return None


def _pipeline_lock_fresh() -> bool:
    """以管线自己的 logs/run.lock 判「正在运行」（mtime 新鲜）。

    run_service.py 只在结束时写 last_run.json，运行中只有 run.lock 留痕；
    因此不能用 last_run.json 的 finished 字段判运行中（BUGfix BRD-01）。
    """
    mtime = _lock_mtime()
    return mtime is not None and (time.time() - mtime) < PIPELINE_STALE_LOCK_SECONDS


def _wait_pipeline_lock_release(timeout: float) -> bool:
    """等待上一次失败尝试残留的 run.lock 释放；超时返回 False。"""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if not _pipeline_lock_fresh():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(1.0, TASK_POLL_INTERVAL_SECONDS))


def _run_steps_via_scheduled_task() -> str:
    """经计划任务通道跑完整管线并等待结束，返回日志摘要。

    本机终端安全软件（QQ管家/透明加密过滤驱动）按进程树拦截写入：工作台后端
    （venv python）派生的进程禁止写 <项目根目录> 区域，直接跑管线脚本必
    PermissionError。计划任务实例由任务计划服务派生、不在被拦截的进程树内，
    实测可稳定跑通（每日定时任务一直走此通道）。无计划任务的机器走直接执行。
    """
    before = _scheduled_run_state()
    before_started = before.get("started", "")
    # 运行中判定以管线自己的 run.lock 为准：last_run.json 只在结束时写，
    # 用 finished 字段判「正在运行」永远不成立（BUGfix BRD-01）。
    if _pipeline_lock_fresh():
        raise PipelineBusyError("报表监控管线正在运行中（计划任务），请等待结束后再试")
    try:
        proc = subprocess.run(
            ["schtasks", "/run", "/tn", _task_name()],
            capture_output=True, timeout=TASK_TRIGGER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise PipelineChannelUnavailableError("触发计划任务超时（schtasks 无响应）") from None
    except OSError as exc:
        raise PipelineChannelUnavailableError(f"无法执行 schtasks：{exc}") from exc
    msg = _decode_console(proc.stderr or proc.stdout).strip()
    if proc.returncode != 0:
        # 本机中文 Windows 输出为 GBK（如「错误: 计划任务 ... 已在运行中」），
        # 旧实现按 UTF-8 解码后永远匹配不到中文忙态（L-080）。
        lowered = msg.lower()
        busy_markers = ("已经运行", "已在运行", "正在运行", "正在执行", "already running", "is running")
        if any(marker in msg or marker in lowered for marker in busy_markers):
            raise PipelineBusyError("报表监控管线正在运行中（计划任务），请等待结束后再试")
        raise PipelineChannelUnavailableError(f"触发计划任务失败：{msg[:200]}")
    deadline = time.monotonic() + TASK_CHANNEL_TIMEOUT
    triggered_at = time.time()
    next_state_check = time.monotonic() + TASK_START_GRACE_SECONDS
    seen_running = False
    exited_at: float | None = None
    last: dict = {}
    while time.monotonic() < deadline:
        time.sleep(TASK_POLL_INTERVAL_SECONDS)
        last = _scheduled_run_state()
        if last.get("started") and last.get("started") != before_started and last.get("finished"):
            break
        lock_mtime = _lock_mtime()
        if lock_mtime is not None and lock_mtime >= triggered_at - 1:
            seen_running = True
            exited_at = None
            continue
        if seen_running:
            # 锁已出现又消失 = 管线实例结束；正常结束会紧接着写 last_run.json，
            # 超过落盘宽限仍无记录说明脚本崩溃/被强杀，立即失败而不是等满通道超时。
            if exited_at is None:
                exited_at = time.monotonic()
            elif time.monotonic() - exited_at > TASK_EXIT_GRACE_SECONDS:
                raise PipelineUnavailableError(
                    "计划任务已提前结束但未写入运行记录（run.lock 已消失、"
                    "logs/last_run.json 未更新），管线脚本可能崩溃，"
                    "请查看微信报表监控 logs\\service_*.log"
                )
        elif time.monotonic() >= next_state_check:
            # 宽限内既没看到新锁也没看到状态更新：查一次计划任务模式，区分
            # 「排队中（busy，不是失败）」「运行中（run.lock 尚未落盘，继续等）」
            # 与明确未启动（就绪/禁用/未知 → 快速失败）（独立审查 M7）。
            next_state_check = time.monotonic() + TASK_STATE_RECHECK_SECONDS
            if _pipeline_lock_fresh():
                raise PipelineBusyError("报表监控管线正在运行中（计划任务），请等待结束后再试")
            state = _scheduled_task_state()
            if state == "queued":
                raise PipelineBusyError(
                    "报表监控计划任务正在排队等待启动，本次未执行，请稍后刷新或重试"
                )
            if state == "running":
                continue
            mode = f"任务模式：{state}" if state else "任务模式查询不可用"
            raise PipelineUnavailableError(
                f"计划任务已触发，但超过 {TASK_START_GRACE_SECONDS} 秒仍未检测到管线启动"
                f"（run.lock 未出现，{mode}），请查看微信报表监控 logs 目录"
            )
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
    lines = [f"== 计划任务通道（{_task_name()}，started={last.get('started')}）=="]
    for s in last.get("steps", []):
        lines.append(f"{s.get('step')} exit={s.get('exit')} {s.get('seconds')}s")
    return "\n".join(lines)


def _run_steps_directly(pdir: Path, stamp: str, t0: float, save_failed: bool = True) -> str:
    """直接以子进程执行四步管线（无计划任务的机器/换机场景）。

    save_failed=False 时不在本函数落失败快照（由重试循环在最终失败时统一落盘），
    日志挂在异常的 logs 属性上供调用方收集；整次尝试共用
    DIRECT_ATTEMPT_BUDGET_SECONDS 总预算，按剩余时间收紧每步 subprocess 超时。
    """
    logs: list[str] = []
    deadline = time.monotonic() + DIRECT_ATTEMPT_BUDGET_SECONDS
    previous_deadline = getattr(_DIRECT_DEADLINE, "value", None)
    _DIRECT_DEADLINE.value = deadline
    try:
        for step in PIPELINE_STEPS:
            script = pdir / "scripts" / step
            if not script.is_file():
                raise PipelineUnavailableError(f"缺少管线脚本：{script}")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd=step, timeout=DIRECT_ATTEMPT_BUDGET_SECONDS)
            logs.append(f"== {step} ==\n{_run_step(script)}")
    except subprocess.TimeoutExpired as exc:
        tail = "\n".join(logs) + f"\n超时：{exc}"
        if save_failed:
            _try_save_snapshot(stamp, "failed", "", tail, time.perf_counter() - t0)
        err = PipelineUnavailableError(
            f"管线执行超时（本次尝试总预算 {DIRECT_ATTEMPT_BUDGET_SECONDS}s）"
        )
        err.logs = tail
        raise err from exc
    except subprocess.CalledProcessError as exc:
        tail = "\n".join(logs) + f"\n{exc.stderr[-2000:] if exc.stderr else ''}"
        if save_failed:
            _try_save_snapshot(stamp, "failed", "", tail, time.perf_counter() - t0)
        err = PipelineUnavailableError(f"管线步骤失败：{exc.cmd}")
        err.logs = tail
        raise err from exc
    finally:
        _DIRECT_DEADLINE.value = previous_deadline
    return "\n".join(logs)


def _execute_attempt(pdir: Path, stamp: str, t0: float) -> str:
    """执行一次管线（计划任务通道或直接子进程）；失败不落快照，交由重试循环处理。

    仅当「非忙」且计划任务通道本身无法启动（未注册/拒绝启动/schtasks 超时）时
    回退直接执行；任务已在运行（忙）或任务已启动但步骤失败，绝不回退——后者
    回退会重复跑一遍管线并被误报成修复成功（L-080）。
    """
    if _scheduled_task_available():
        try:
            return _run_steps_via_scheduled_task()
        except PipelineBusyError:
            raise
        except PipelineChannelUnavailableError as exc:
            logger.warning("计划任务通道不可用，回退直接执行：%s", exc)
            try:
                return _run_steps_directly(pdir, stamp, t0, save_failed=False)
            except PipelineUnavailableError as direct_exc:
                direct_exc.channel_error = str(exc)
                raise
    return _run_steps_directly(pdir, stamp, t0, save_failed=False)


def _notify_failure(message: str) -> None:
    """最终失败告警：登记到调试中心「问题记录」（同标题未关闭则去重，避免刷屏）。"""
    try:
        from ..debug_center import service as debug_service
        from ..debug_center.schemas import IssueCreate
        opened = [i for i in debug_service.load_issues()
                  if i.get("title") == "报表看板管线运行失败" and i.get("status") not in ("done", "wontfix")]
        if opened:
            return
        debug_service.create_issue(IssueCreate(
            title="报表看板管线运行失败",
            severity="P2",
            status="open",
            source="analysis",
            page="/finance/board",
            description=f"最多尝试 {_PIPELINE_ATTEMPTS} 次（重试 {_PIPELINE_ATTEMPTS - 1} 次）后仍失败：{message[:800]}",
        ))
    except Exception:  # 告警失败不得影响主流程
        logger.warning("看板失败告警登记失败", exc_info=True)


def _try_save_snapshot(stamp: str, status: str, html_path: str, log_tail: str, elapsed: float) -> str:
    """失败快照尽力入库；入库失败只记日志并返回说明，绝不覆盖原始管线错误。"""
    try:
        _save_snapshot(stamp, status, html_path, log_tail, elapsed)
    except Exception as exc:  # noqa: BLE001  失败快照与告警都不得掩盖真实失败
        logger.exception("看板失败快照入库失败：stamp=%s", stamp)
        return f"失败快照未能入库（{type(exc).__name__}）"
    return ""


def _final_failure(stamp: str, t0: float, attempt: int,
                   exc: PipelineUnavailableError, extra: str = "") -> PipelineUnavailableError:
    """构造最终失败（503）：登记失败快照 + 调试中心告警，返回异常供 raise。"""
    detail = getattr(exc, "logs", "") or str(exc)
    msg = f"尝试 {attempt} 次（重试 {attempt - 1} 次）后仍失败：{exc}"
    if extra:
        msg += f"；{extra}"
    note = _try_save_snapshot(stamp, "failed", "", f"{msg}\n{detail}"[:4000], time.perf_counter() - t0)
    if note:
        msg += f"；{note}"
    _notify_failure(str(exc))
    err = PipelineUnavailableError(msg)
    err.logs = detail
    return err


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

    stamp = _unique_stamp()
    t0 = time.perf_counter()
    attempt = 0
    first_error: PipelineUnavailableError | None = None
    busy_waited = False
    while True:
        attempt += 1
        try:
            log_text = _execute_attempt(pdir, stamp, t0)
            break
        except PipelineBusyError as exc:
            if first_error is None:
                # 首轮就忙 = 确有其它的管线在跑，按 409 上报
                raise
            # 重试阶段的忙态来自上一次失败尝试残留的 run.lock：有界等待释放后继续；
            # 等不到就报首轮真实失败（503），绝不能用 409 把真实原因掩盖掉。
            if busy_waited or not _wait_pipeline_lock_release(RETRY_LOCK_WAIT_SECONDS):
                # 本次忙态并未真正执行：按已执行次数（attempt-1）计，避免文案虚增尝试数
                raise _final_failure(
                    stamp, t0, attempt - 1, first_error,
                    extra=f"重试被残留运行锁阻塞（{exc}），本次未执行",
                ) from exc
            busy_waited = True
            attempt -= 1  # 忙态不是一次真实执行，不重复计数
            continue
        except PipelineUnavailableError as exc:
            if first_error is None:
                first_error = exc
            if attempt < _PIPELINE_ATTEMPTS:
                logger.warning("看板管线第 %s/%s 次执行失败，%s 秒后重试：%s",
                               attempt, _PIPELINE_ATTEMPTS, _RETRY_DELAY_SECONDS, exc)
                time.sleep(_RETRY_DELAY_SECONDS)
                continue
            raise _final_failure(stamp, t0, attempt, exc) from exc

    # 产物复制为自包含快照（原文件保持不动）
    src_html = pdir / "output" / DASHBOARD_NAME
    if not src_html.is_file():
        note = _try_save_snapshot(stamp, "failed", "", log_text, time.perf_counter() - t0)
        msg = f"管线完成但未找到看板产物：{src_html}"
        if note:
            msg += f"；{note}"
        raise PipelineUnavailableError(msg)
    dest_dir = _snapshots_dir() / stamp
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_html = dest_dir / DASHBOARD_NAME
    shutil.copy2(src_html, dest_html)

    try:
        snap = _save_snapshot(stamp, "ok", str(dest_html), log_text, time.perf_counter() - t0)
    except Exception as exc:  # noqa: BLE001  HTML 已落盘：给出可恢复状态，不吞成 500
        logger.exception("看板快照入库失败：stamp=%s", stamp)
        raise PipelineUnavailableError(
            f"管线已成功且快照文件已生成（stamp={stamp}，目录 {dest_dir}），"
            f"但快照入库失败：{exc}；产物已保留，请刷新快照列表或稍后重新运行"
        ) from exc
    out = _out(snap)
    out["attempts"] = attempt  # 定时任务/前端可据此展示重试结果（2026-11-02 v5）
    return out


def _unique_stamp() -> str:
    """生成不与既有快照/目录冲突的 stamp（同一秒内连续运行追加 _2/_3 后缀）。"""
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = _snapshots_dir()
    candidate = base
    i = 1
    while True:
        with SessionLocal() as db:
            exists = db.query(BoardSnapshot).filter(BoardSnapshot.stamp == candidate).first() is not None
        if not exists and not (root / candidate).exists():
            return candidate
        i += 1
        candidate = f"{base}_{i}"


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


# ---------------- 产物内容级巡检（2026-11-02 v5） ----------------
# HTML 内嵌数据块由 m04 以 `const R = __DATA__;` 形式写入（report_data.json 去掉 map）
_DATA_MARKER_RE = re.compile(r"^const R = (\{.*\});\s*$", re.M)
# 产物规模下限（正常看板内嵌 echarts，≥1MB；测试可 patch）
_MIN_HTML_BYTES = 100_000
# 巡检结果缓存：key = (快照 id, HTML 大小, 数据文件 mtime)；避免 dq 每次全量重解析
_VERIFY_CACHE: dict[str, dict] = {}
_VERIFY_CACHE_MAX = 8


def _walk_diff(expected, actual, path: str, out: list, limit: int = 20,
               stats: dict | None = None) -> None:
    """递归比对 JSON（数值容差 0.01）；明细最多 limit 条，stats["count"] 累计真实总数。"""
    if stats is None:
        stats = {"count": 0}

    def _push(item: dict) -> None:
        stats["count"] += 1
        if len(out) < limit:
            out.append(item)

    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in expected:
            if k not in actual:
                _push({"path": f"{path}.{k}", "expected": expected[k], "actual": "<缺失>"})
            else:
                _walk_diff(expected[k], actual[k], f"{path}.{k}", out, limit, stats)
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            _push({"path": path, "expected": f"len={len(expected)}", "actual": f"len={len(actual)}"})
        for i, (x, y) in enumerate(zip(expected, actual)):
            _walk_diff(x, y, f"{path}[{i}]", out, limit, stats)
        return
    if isinstance(expected, bool) or isinstance(actual, bool):
        if expected != actual:
            _push({"path": path, "expected": expected, "actual": actual})
        return
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if abs(float(expected) - float(actual)) > 0.01:
            _push({"path": path, "expected": expected, "actual": actual})
        return
    if expected != actual:
        _push({"path": path, "expected": expected, "actual": actual})


def verify_snapshot(snapshot_id: int | None = None) -> dict:
    """看板产物内容级巡检：HTML 内嵌数据 vs 管线 data/report_data.json（只读）。

    校验项：① HTML 文件规模；② 内嵌 `const R` 数据块可解析；③ 顶层键集合一致；
    ④ 递归数值比对（容差 0.01；明细最多 20 处，`mismatch_total` 为真实总数，
    `truncated` 标明是否截断）；⑤ 关键 KPI（出租率/开业率/收缴率/空铺数）齐全。
    结果按（快照 id、HTML 大小、数据文件 mtime）缓存，dq 巡检可复用。
    所有返回（含 available=false 的早退）都固定带 mismatch_total/truncated，
    调用方可以直接下标读取而不必先判分支。
    """
    with SessionLocal() as db:
        snap = (db.get(BoardSnapshot, snapshot_id) if snapshot_id
                else db.query(BoardSnapshot).filter(BoardSnapshot.status == "ok")
                .order_by(BoardSnapshot.id.desc()).first())
        if snap is None:
            return {"available": False, "gap_reasons": ["暂无可巡检的快照"],
                    "checks": [], "mismatches": [], "mismatch_total": 0, "truncated": False}
        snap_view = {"id": snap.id, "stamp": snap.stamp, "status": snap.status}
        html_path = Path(snap.html_path or "")
    if not snap.html_path or not html_path.is_file():
        return {"available": False, "gap_reasons": ["快照 HTML 文件不存在"], "snapshot": snap_view,
                "checks": [], "mismatches": [], "mismatch_total": 0, "truncated": False}
    try:
        data_path = pipeline_dir() / "data" / "report_data.json"
        src_mtime = data_path.stat().st_mtime if data_path.is_file() else 0
        size = html_path.stat().st_size
    except (OSError, PipelineUnavailableError) as exc:
        return {"available": False, "gap_reasons": [f"管线数据不可用：{exc}"], "snapshot": snap_view,
                "checks": [], "mismatches": [], "mismatch_total": 0, "truncated": False}
    cache_key = f"{snap.id}:{size}:{src_mtime}"
    if cache_key in _VERIFY_CACHE:
        return _VERIFY_CACHE[cache_key]
    checks: list[dict] = []
    mismatches: list[dict] = []
    mismatch_total = 0
    truncated = False
    try:
        html = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"available": False, "gap_reasons": [f"读取快照失败：{exc}"], "snapshot": snap_view,
                "checks": [], "mismatches": [], "mismatch_total": 0, "truncated": False}
    checks.append({"name": "文件规模", "ok": size > _MIN_HTML_BYTES, "detail": f"{size / 1e6:.2f} MB"})
    m = _DATA_MARKER_RE.search(html)
    embedded = None
    if m:
        try:
            embedded = json.loads(m.group(1))
            checks.append({"name": "内嵌数据", "ok": True, "detail": f"可解析（顶层键 {len(embedded)} 个）"})
        except json.JSONDecodeError as exc:
            checks.append({"name": "内嵌数据", "ok": False, "detail": f"JSON 解析失败：{exc}"})
    else:
        checks.append({"name": "内嵌数据", "ok": False, "detail": "未找到 const R = {...} 数据块"})
    if embedded is not None:
        # 脱敏映射（码→真名）绝不进 HTML；旧版 m04 产物会带 map，属高风险遗留
        checks.append({"name": "敏感键剔除", "ok": "map" not in embedded,
                       "detail": "未包含 map" if "map" not in embedded else "产物包含脱敏映射 map，请重新运行管线生成新快照"})
        try:
            src = json.loads(data_path.read_text(encoding="utf-8"))
            src.pop("map", None)
        except (OSError, json.JSONDecodeError) as exc:
            src = None
            checks.append({"name": "数据源对照", "ok": False, "detail": f"读取 report_data.json 失败：{exc}"})
        if src is not None:
            # meta 是运行元数据（生成时间/文件计数），不同次运行天然不同 → 不参与比对
            expected_view = {k: v for k, v in src.items() if k != "meta"}
            actual_view = {k: v for k, v in embedded.items() if k not in ("meta", "map")}
            missing = sorted(set(expected_view) - set(actual_view))
            extra = sorted(set(actual_view) - set(expected_view))
            checks.append({"name": "顶层键一致", "ok": not missing and not extra,
                           "detail": f"缺 {missing} 多 {extra}" if (missing or extra) else "一致"})
            diff_stats = {"count": 0}
            _walk_diff(expected_view, actual_view, "", mismatches, limit=20, stats=diff_stats)
            mismatch_total = diff_stats["count"]
            truncated = mismatch_total > len(mismatches)
            detail = (
                f"差异 {mismatch_total} 处" if not truncated
                else f"至少 {len(mismatches)} 处（已截断，总数 {mismatch_total} 处）"
            )
            checks.append({"name": "数值一致性", "ok": mismatch_total == 0, "detail": detail})
        kpi = embedded.get("kpi") or {}
        required = ["出租率", "开业率", "收缴率", "空铺数"]
        missing_kpi = [k for k in required if k not in kpi]
        checks.append({"name": "关键 KPI", "ok": not missing_kpi,
                       "detail": "缺失：" + "、".join(missing_kpi) if missing_kpi else "齐全"})
    fail = sum(1 for c in checks if not c["ok"])
    result = {"available": True, "snapshot": snap_view, "checks": checks, "mismatches": mismatches,
              "mismatch_total": mismatch_total, "truncated": truncated,
              "kpi": (embedded or {}).get("kpi") or {}, "summary": {"ok": len(checks) - fail, "fail": fail}}
    if len(_VERIFY_CACHE) >= _VERIFY_CACHE_MAX:
        _VERIFY_CACHE.clear()
    _VERIFY_CACHE[cache_key] = result
    return result
