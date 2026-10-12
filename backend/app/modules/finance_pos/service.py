"""finance_pos 服务：建行 POS 收款对账（L1 任务型薄壳）。

引擎为原项目「收款认领POS台账」的两个脚本：
- generate_pos_ledger.py：SHOP 对账单 → POS 项目台账 xlsx（--root/--output 可重定向）
- analyze_pos_reconciliation.py：四源交叉对账 → 差异分析（--root，产物写进 root）
本模块不重写引擎：上传归集 → 依次子进程执行 → 产物归档 → 下载。
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session

from ...config import get_finance_data_dir, settings
from .models import PosRun

logger = logging.getLogger(__name__)

LEDGER_SCRIPT = "generate_pos_ledger.py"
ANALYZE_SCRIPT = "analyze_pos_reconciliation.py"
STEP_TIMEOUT = 900
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_SUFFIXES = (".xlsx", ".xls", ".zip")


class EngineUnavailableError(RuntimeError):
    """引擎目录缺失或不可用。"""


def engine_dir() -> Path:
    """POS 台账项目目录：配置优先，否则按默认相对位置推导。"""
    if settings.finance_pos_engine_path:
        return Path(settings.finance_pos_engine_path)
    base = Path(__file__).resolve()
    for parent in base.parents:
        if parent.name == "01-财税自动化":
            return parent / "收款认领POS台账"
    raise EngineUnavailableError("无法定位 POS 引擎目录，请配置 finance_pos_engine_path")


def _runs_root() -> Path:
    d = get_finance_data_dir() / "finance_pos" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> list[str]:
    names: list[str] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.startswith("~$"):
            continue
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest.resolve())):
            raise ValueError(f"ZIP 内含非法路径：{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(info))
        names.append(Path(name).name)
    return names


def _save_inputs(run_dir: Path, files: list[tuple[str, bytes]]) -> list[dict]:
    inbox = run_dir / "input"
    inbox.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for filename, content in files:
        safe = Path(filename or "").name
        if safe in ("", ".", ".."):
            raise ValueError("非法的文件名")
        suffix = Path(safe).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"不支持的文件类型：{safe}（仅 {'/'.join(ALLOWED_SUFFIXES)}）")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"文件 {safe} 超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
        if suffix == ".zip":
            with zipfile.ZipFile(BytesIO(content)) as zf:
                for name in _safe_extract(zf, inbox):
                    saved.append({"name": name, "size": 0})
        else:
            dest = inbox / f"{datetime.now():%H%M%S_%f}_{safe}"
            dest.write_bytes(content)
            saved.append({"name": safe, "size": len(content)})
    return saved


def create_run(db: Session, files: list[tuple[str, bytes]]) -> dict:
    """建运行记录并归集输入（fail-fast 探测引擎）。"""
    if not files:
        raise ValueError("请上传输入文件（SHOP 对账单 + 认领记录 + 辅助明细账 + 凭证列表，或一个 ZIP）")
    edir = engine_dir()
    for script in (LEDGER_SCRIPT, ANALYZE_SCRIPT):
        if not (edir / script).is_file():
            raise EngineUnavailableError(f"POS 引擎缺少脚本：{script}（配置 finance_pos_engine_path 可指定）")
    run = PosRun(status="uploaded")
    db.add(run)
    db.flush()
    run_dir = _runs_root() / f"{run.id:06d}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run.run_dir = str(run_dir)
    run.input_files = _save_inputs(run_dir, files)
    db.commit()
    db.refresh(run)
    return _out(run)


def _run_script(script: str, args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        [sys.executable, script, *args], cwd=str(cwd),
        capture_output=True, text=True, timeout=STEP_TIMEOUT,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, script, proc.stdout, proc.stderr)
    return ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-2000:]


def execute_run(db: Session, run_id: int) -> dict:
    """执行：先台账后差异分析；产物 = output/* + input 下新生成的文件。"""
    run = db.get(PosRun, run_id)
    if run is None:
        raise LookupError("运行记录不存在")
    if run.status == "running":
        raise ValueError("该运行正在执行中")
    edir = engine_dir()
    run_dir = Path(run.run_dir)
    input_dir = run_dir / "input"
    out_dir = run_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    uploaded = {f["name"] for f in (run.input_files or [])}
    before = {p.name for p in input_dir.iterdir() if p.is_file()}
    run.status = "running"
    db.commit()

    logs: list[str] = []
    t0 = time.perf_counter()
    try:
        logs.append(f"== {LEDGER_SCRIPT} ==\n" + _run_script(
            LEDGER_SCRIPT,
            ["--root", str(input_dir), "--output", str(out_dir / "POS项目台账.xlsx")],
            edir,
        ))
        logs.append(f"== {ANALYZE_SCRIPT} ==\n" + _run_script(
            ANALYZE_SCRIPT, ["--root", str(input_dir)], edir,
        ))
    except subprocess.TimeoutExpired:
        run.status = "failed"
        run.error = f"引擎执行超时（>{STEP_TIMEOUT}s）"
        run.elapsed_sec = round(time.perf_counter() - t0, 2)
        db.commit()
        raise EngineUnavailableError(run.error) from None
    except subprocess.CalledProcessError as exc:
        run.status = "failed"
        run.error = f"引擎步骤失败：{exc.cmd}"
        run.log_tail = "\n".join(logs) + "\n" + (exc.stderr or "")[-2000:]
        run.elapsed_sec = round(time.perf_counter() - t0, 2)
        db.commit()
        raise EngineUnavailableError(run.error) from None

    run.elapsed_sec = round(time.perf_counter() - t0, 2)
    run.log_tail = "\n".join(logs)[-4000:]

    # 产物归档：output/* 与 input 下新生成的文件（差集）
    art_dir = run_dir / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict] = []
    sources = [p for p in out_dir.iterdir() if p.is_file()]
    sources += [p for p in input_dir.iterdir()
                if p.is_file() and p.name not in before and Path(p.name).name not in uploaded]
    for f in sorted(set(sources)):
        if f.name.startswith("~$"):
            continue
        dest = art_dir / f.name
        dest.write_bytes(f.read_bytes())
        artifacts.append({"name": f.name, "size": dest.stat().st_size, "path": str(dest)})
    run.artifacts = artifacts
    run.status = "ok" if artifacts else "failed"
    if not artifacts:
        run.error = "引擎执行完成但未产生产物"
    db.commit()
    db.refresh(run)
    return _out(run)


def _out(r: PosRun) -> dict:
    return {
        "id": r.id,
        "status": r.status,
        "input_files": r.input_files or [],
        "artifacts": [{"name": a["name"], "size": a["size"]} for a in (r.artifacts or [])],
        "error": r.error,
        "elapsed_sec": r.elapsed_sec,
        "log_tail": r.log_tail[-500:] if r.log_tail else "",
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


def list_runs(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(PosRun).order_by(PosRun.id.desc()).limit(limit).all()
    return [_out(r) for r in rows]


def get_run(db: Session, run_id: int) -> PosRun:
    from fastapi import HTTPException

    run = db.get(PosRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


def artifact_path(run: PosRun, name: str) -> Path:
    safe = Path(name or "").name
    for a in run.artifacts or []:
        if a["name"] == safe:
            p = Path(a["path"])
            if p.is_file():
                return p
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail=f"产物不存在：{name}")
