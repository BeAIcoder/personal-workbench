# -*- coding: utf-8 -*-
"""E2E 结果归档：把一次测试运行写入调试中心（backend/data/debug/）。

与 backend/app/modules/debug_center/service.py 共用同一文件格式（UTF-8、原子写）：

    <debug 目录>/runs/<run_id>/result.json   # {id, kind, base, started_at, finished_at, totals, pages, tests, extra}
    <debug 目录>/runs/<run_id>/shots/*.png   # 截图（从 shots_dir 复制）
    <debug 目录>/issues.json                 # 问题记录（自动建/更新）

问题记录自动维护规则（source="e2e"，severity=P1）：
- 有错误的页面 → title="E2E 页面报错：<name>"，page=路由；
- 失败用例 → title="E2E 用例失败：<name>"，page=""；
- 同 title+page 且 status 为 open/in_progress 的已存在：只刷新 updated_at 与 evidence=run_id；
- 否则新建；全绿时不写 issues.json（不自动关闭任何 open 问题）。

本文件不依赖后端包，E2E 脚本直接以文件方式落库（e2e 后端进程与真实后端共用
backend/data/debug/，读取侧由 /api/debug/* 提供）。
"""
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 调试中心数据根目录（与后端 service.data_dir() 同规则：DEBUG_DATA_DIR 优先）
DEFAULT_DEBUG_DIR = ROOT / "backend" / "data" / "debug"
ISSUES_FILE = "issues.json"

E2E_SEVERITY = "P1"
OPEN_STATUSES = ("open", "in_progress")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _debug_dir() -> Path:
    override = os.environ.get("DEBUG_DATA_DIR", "").strip()
    return Path(override) if override else DEFAULT_DEBUG_DIR


def _atomic_write_json(path: Path, data) -> None:
    """原子写 JSON：同目录临时文件 + os.replace（与后端 service 同一约定）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _load_issues(debug_dir: Path) -> list:
    path = debug_dir / ISSUES_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []


def _page_has_errors(page: dict) -> bool:
    return bool(page.get("console_errors") or page.get("page_errors") or page.get("failed_reqs"))


def _normalize_test(test) -> dict:
    """用例归一为 {id, name, passed, detail}；兼容 (id, name, passed, detail) 四元组。"""
    if isinstance(test, dict):
        return {
            "id": str(test.get("id") or ""),
            "name": str(test.get("name") or ""),
            "passed": bool(test.get("passed")),
            "detail": str(test.get("detail") or ""),
        }
    tid, name, passed, detail = test
    return {"id": str(tid), "name": str(name), "passed": bool(passed), "detail": str(detail or "")}


def _upsert_issue(issues: list, *, title: str, page: str, description: str, run_id: str, stamp: str) -> bool:
    """同 title+page 且 open/in_progress 已存在：只刷新 updated_at/evidence；否则新建。

    返回 True 表示新建了一条。
    """
    for issue in issues:
        if (issue.get("title") == title and issue.get("page") == page
                and issue.get("status") in OPEN_STATUSES):
            issue["updated_at"] = stamp
            issue["evidence"] = run_id
            return False
    issues.append({
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "severity": E2E_SEVERITY,
        "status": "open",
        "source": "e2e",
        "page": page,
        "description": description,
        "evidence": run_id,
        "created_at": stamp,
        "updated_at": stamp,
    })
    return True


def save_run(kind: str, *, base: str, started_at: str, finished_at: str,
             pages=None, tests=None, shots_dir=None, extra=None) -> str:
    """归档一次测试运行到调试中心，返回 run_id。

    pages: [{route, name, status, console_errors, page_errors, failed_reqs, ...}]
    tests: [{id, name, passed, detail}] 或 (id, name, passed, detail) 四元组
    shots_dir: 截图目录（其下 png 复制进 runs/<run_id>/shots/）
    extra: 附加信息（原样写入 result.json）
    """
    pages = [dict(p) for p in (pages or [])]
    tests = [_normalize_test(t) for t in (tests or [])]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    debug_dir = _debug_dir()
    run_id = f"{stamp}_{kind}"
    n = 1
    while (debug_dir / "runs" / run_id).exists():
        n += 1
        run_id = f"{stamp}_{kind}_{n}"
    run_dir = debug_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 截图复制进 run 目录
    if shots_dir:
        src = Path(shots_dir)
        if src.is_dir():
            dst = run_dir / "shots"
            dst.mkdir(parents=True, exist_ok=True)
            for png in sorted(src.glob("*.png")):
                shutil.copy2(png, dst / png.name)

    bad_pages = sum(1 for p in pages if _page_has_errors(p))
    failed_tests = sum(1 for t in tests if not t["passed"])
    result = {
        "id": run_id,
        "kind": kind,
        "base": base,
        "started_at": started_at,
        "finished_at": finished_at,
        "totals": {
            "pages": len(pages),
            "bad_pages": bad_pages,
            "tests": len(tests),
            "failed_tests": failed_tests,
        },
        "pages": pages,
        "tests": tests,
        "extra": extra or {},
    }
    _atomic_write_json(run_dir / "result.json", result)

    # 有错误才触碰 issues.json（全绿时保持既有 open 问题不动，不做自动关闭）
    if bad_pages or failed_tests:
        stamp_now = _now_str()
        issues = _load_issues(debug_dir)
        for p in pages:
            if not _page_has_errors(p):
                continue
            errs = list(p.get("console_errors") or []) + list(p.get("page_errors") or []) + list(p.get("failed_reqs") or [])
            detail = "\n".join(str(e)[:200] for e in errs[:5])
            _upsert_issue(
                issues,
                title=f"E2E 页面报错：{p.get('name') or p.get('route') or '?'}",
                page=str(p.get("route") or ""),
                description=f"kind={kind}，HTTP {p.get('status', '?')}。{detail}",
                run_id=run_id,
                stamp=stamp_now,
            )
        for t in tests:
            if t["passed"]:
                continue
            _upsert_issue(
                issues,
                title=f"E2E 用例失败：{t['name']}",
                page="",
                description=f"kind={kind}，用例 {t['id']}。{t['detail'][:300]}",
                run_id=run_id,
                stamp=stamp_now,
            )
        _atomic_write_json(debug_dir / ISSUES_FILE, issues)
    return run_id
