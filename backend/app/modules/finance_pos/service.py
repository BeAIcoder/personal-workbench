"""finance_pos 服务：建行 POS 收款对账（L1 任务型薄壳）。

引擎为原项目「收款认领POS台账」的两个脚本：
- generate_pos_ledger.py：SHOP 对账单 → POS 项目台账 xlsx（--root/--output 可重定向）
- analyze_pos_reconciliation.py：四源交叉对账 → 差异分析（--root，产物写进 root）
本模块不重写引擎：上传归集 → 依次子进程执行 → 产物归档 → 下载。
"""
from __future__ import annotations

import logging
import shutil
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
MAX_BATCH_BYTES = 100 * 1024 * 1024  # 单次上传批次（多文件合计）上限
MAX_UNZIP_BYTES = 200 * 1024 * 1024  # ZIP 解压后总量上限（防解压炸弹）
ALLOWED_SUFFIXES = (".xlsx", ".xls", ".zip")
ALLOWED_MEMBER_SUFFIXES = (".xlsx", ".xls")  # ZIP 成员仅限业务数据格式（拒 .py/plugin.json 等）
MAX_ZIP_MEMBERS = 500                       # ZIP 成员（文件）数量上限
MAX_SAFE_STEM = 100                         # 落盘文件名词干上限，留足时间戳前缀与 Windows 255 限制余量
_WIN_ILLEGAL = set('<>:"/\\|?*')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
}


class EngineUnavailableError(RuntimeError):
    """引擎目录缺失或不可用。"""


def _safe_name(filename: str) -> str:
    """把用户上传的文件名转成 Windows/Linux 都能落盘的安全单段名（保留扩展名）。

    仅取末段（防路径穿越），替换 Windows 非法字符与控制字符，去掉首尾点/空格，
    规避设备保留名，并对超长词干截断。返回的是展示名，也是磁盘名的主体。
    """
    raw = (filename or "").replace("\\", "/").split("/")[-1]
    raw = "".join("_" if ch in _WIN_ILLEGAL or ord(ch) < 32 else ch for ch in raw)
    raw = raw.strip(" .")
    if raw in ("", ".", ".."):
        raise ValueError("非法的文件名")
    suffix = Path(raw).suffix
    stem = raw[: -len(suffix)] if suffix else raw
    stem = stem.strip(" .")
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    if len(stem) > MAX_SAFE_STEM:
        stem = stem[:MAX_SAFE_STEM].rstrip(" .")
    if not stem:
        raise ValueError("非法的文件名")
    return f"{stem}{suffix}"


def _safe_member_segment(part: str) -> str:
    """校验 ZIP 成员的单段名：非法 Windows 文件名（含 <>:"/\\|?*、控制字符、
    设备保留名、纯点/首尾点空格）直接拒绝——ZIP 内容不可信，不做静默替换。"""
    if not part or part.strip(" .") == "":
        raise ValueError("非法文件名")
    if any(ch in _WIN_ILLEGAL or ord(ch) < 32 for ch in part):
        raise ValueError("非法文件名")
    if Path(part).stem.upper() in _RESERVED_NAMES:
        raise ValueError("非法文件名")
    return part


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


def _unique_path(target: Path) -> Path:
    """ZIP 内多个条目清洗后可能重名，追加 _1/_2 避免互相覆盖。"""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        candidate = target.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"ZIP 内同名文件过多：{target.name}")


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> list[str]:
    """解压 ZIP 到 dest：限定成员扩展名（业务数据格式）/数量/累计解压后总量，
    is_relative_to 防 zip-slip（拒绝越界条目），成员名逐段做 Windows 合法性校验。"""
    names: list[str] = []
    root = dest.resolve()
    total_unc = 0
    members = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.startswith("~$"):
            continue
        members += 1
        if members > MAX_ZIP_MEMBERS:
            raise ValueError(f"ZIP 内成员过多（超过 {MAX_ZIP_MEMBERS} 个）")
        target = (dest / name).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"ZIP 内含非法路径：{name}")
        total_unc += info.file_size
        if total_unc > MAX_UNZIP_BYTES:
            raise ValueError(f"ZIP 解压后总大小超过 {MAX_UNZIP_BYTES // 1024 // 1024}MB 上限")
        parts: list[str] = []
        for part in name.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError(f"ZIP 内含非法路径：{name}")
            try:
                parts.append(_safe_member_segment(part))
            except ValueError as exc:
                raise ValueError(f"ZIP 内含非法文件名：{name}") from exc
        if not parts:
            continue
        if Path(parts[-1]).suffix.lower() not in ALLOWED_MEMBER_SUFFIXES:
            raise ValueError(
                f"ZIP 内含不支持的文件类型：{name}（仅 {'/'.join(ALLOWED_MEMBER_SUFFIXES)}）"
            )
        target = dest.joinpath(*parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target = _unique_path(target)
            target.write_bytes(zf.read(info))
        except OSError as exc:
            raise ValueError(f"ZIP 条目无法落盘：{name}（{exc}）") from exc
        names.append(target.name)
    return names


def _save_inputs(run_dir: Path, files: list[tuple[str, bytes]]) -> list[dict]:
    """上传文件落入 run_dir/input：ZIP 解包、其余原样；返回清单 [{name, size}]。"""
    inbox = run_dir / "input"
    inbox.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for filename, content in files:
        safe = _safe_name(filename)
        suffix = Path(safe).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"不支持的文件类型：{safe}（仅 {'/'.join(ALLOWED_SUFFIXES)}）")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"文件 {safe} 超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(BytesIO(content)) as zf:
                    for name in _safe_extract(zf, inbox):
                        saved.append({"name": name, "size": 0})
            except zipfile.BadZipFile as exc:
                raise ValueError(f"ZIP 文件损坏或格式不正确：{safe}") from exc
            except OSError as exc:
                raise ValueError(f"ZIP 文件 {safe} 落盘失败：{exc}") from exc
        else:
            dest = inbox / f"{datetime.now():%H%M%S_%f}_{safe}"
            try:
                dest.write_bytes(content)
            except OSError as exc:
                raise ValueError(f"文件 {safe} 落盘失败：{exc}") from exc
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
    try:
        run.input_files = _save_inputs(run_dir, files)
    except Exception:
        # 校验/落盘失败：回滚未提交的运行记录并清掉半成品目录，避免 400 后留垃圾
        db.rollback()
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
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
    except EngineUnavailableError:
        raise
    except Exception as exc:
        # 兜底：归档等任何异常都回写 failed，避免运行记录永久卡在 running 无法重试
        run.status = "failed"
        run.error = f"执行异常：{type(exc).__name__}: {exc}"[:500]
        run.elapsed_sec = round(time.perf_counter() - t0, 2)
        db.commit()
        logger.exception("POS 运行 %s 执行异常", run.id)
        raise EngineUnavailableError(run.error) from exc


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
