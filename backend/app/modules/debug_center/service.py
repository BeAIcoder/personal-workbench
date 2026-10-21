"""debug_center 数据服务：问题记录 / 测试运行归档 / 前端报错采集。

纯 JSON 文件存储（不建表），目录结构：

    <根目录>/issues.json                  # 问题记录数组
    <根目录>/runs/<run_id>/result.json    # 单次测试运行结果（E2E 脚本写入，见 dev/e2e/debug_reporter.py）
    <根目录>/runs/<run_id>/shots/*.png    # 运行截图
    <根目录>/client_errors.jsonl          # 前端报错（每行一条 JSON）

根目录默认 backend/data/debug/，环境变量 DEBUG_DATA_DIR 可覆盖（测试隔离，
参照 config.get_finance_data_dir 的 FINANCE_DATA_DIR 做法；此处运行时读取，
便于测试用 monkeypatch.setenv 按用例切换）。

写入统一走「临时文件 + os.replace」原子写，读取对缺失/损坏文件宽容降级为空，
绝不因数据文件问题让接口 500。dev/e2e/debug_reporter.py 与本文件共用同一格式。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ...config import DATA_DIR

logger = logging.getLogger(__name__)

ISSUES_FILE = "issues.json"
CLIENT_ERRORS_FILE = "client_errors.jsonl"
RUNS_DIR = "runs"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
OPEN_STATUSES = ("open", "in_progress")

# issues.json 读-改-写锁：_atomic_write_json 只防撕裂不防丢更新，
# create/update/delete 的 load→改→save 临界区须串行化（并发写会丢更新，
# Windows 上并发 os.replace 还会撞 PermissionError）。
_ISSUES_LOCK = threading.Lock()

# 种子问题（analysis 来源）：首次使用（issues.json 不存在）时写入，之后不再触碰
SEED_ISSUES: list[dict[str, str]] = [
    {
        "title": "技能包停用开关不影响注入 Agent",
        "severity": "P0",
        "page": "/skill-packs",
        "description": "orchestrator.py:120-134 用 LocalSkillLoader 全目录加载，不看 _store.json 的 disabled 列表（skill_store.py:220-231），停用包照样注入。",
    },
    {
        "title": "聊天模型 chip 显示 env 兜底模型，与下拉不一致",
        "severity": "P0",
        "page": "/assistant",
        "description": "供应商全关时 config_store.py:278-288 回退 .env LLM_MODEL，chip 显示如 Qwen/Qwen3.5-122B-A10B 但下拉（models_flat, config_store.py:459-478）无此项，界面看不出正在用 env 兜底。",
    },
    {
        "title": "专家包导入无确认，覆盖内置专家界面修改",
        "severity": "P0",
        "page": "/expert-packs",
        "description": "import_pack（expert_packs.py:263-317）upsert 用文件覆盖 DB 显示名/提示词/描述，专家团队 tab 的编辑被静默冲掉。",
    },
    {
        "title": "有差异未展示具体差异字段",
        "severity": "P0",
        "page": "/expert-packs",
        "description": "_diff_fields()（expert_packs.py:199-221）已算出差异字段但 list_packs 未返回前端，用户不知差在哪、以谁为准。",
    },
    {
        "title": "从种子重置只覆盖文件不动 DB，且无后续引导",
        "severity": "P1",
        "page": "/expert-packs",
        "description": "reset_from_seed（expert_packs.py:68-73）只复制 seed_experts→data/expert-packs，须再点全部导入才生效，页面无提示；与专家团队 tab 恢复出厂（assistant.py:405-431 用 team.py TEAM 覆盖 DB）数据源不同，三方漂移。",
    },
    {
        "title": "提示文案指引错误：技能目录 vs 技能池目录",
        "severity": "P1",
        "page": "/skill-packs",
        "description": "SkillPackView.vue:14 让用户找「技能目录」，实际设置项叫「技能池目录」（SettingsDrawer.vue:84-100）；同提示内 data/skills 与 backend/data/skills 两种路径写法并存。",
    },
    {
        "title": "供应商关闭但显示已配置/模型已接入，状态矛盾",
        "severity": "P1",
        "page": "/assistant",
        "description": "开关全关仍显示「已配置」徽标与聊天页「模型已接入」，实际在用 env 兜底。",
    },
    {
        "title": "导出为文件覆盖同名文件无确认",
        "severity": "P2",
        "page": "/expert-packs",
        "description": "export_pack（expert_packs.py:347-364）直接覆盖写 <name>.md。",
    },
    {
        "title": "菜单名技能包与页面标题技能包管理不一致",
        "severity": "P2",
        "page": "菜单",
        "description": "AppLayout.vue:53 vs router/index.js:57。",
    },
    {
        "title": "能力探测无批量入口",
        "severity": "P2",
        "page": "/assistant",
        "description": "每模型 4 个探测按钮（SettingsDrawer.vue:534-543），全部探测需逐个确认，新接供应商体验断档。",
    },
    {
        "title": "一级菜单平铺过多，AI 配置分散无分组",
        "severity": "P2",
        "page": "菜单",
        "description": "知识与记忆/MCP工具/技能包/专家团文件/AI助手设置 散落五处，仅财务组有 sub-menu。",
    },
]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def data_dir() -> Path:
    """调试数据根目录：DEBUG_DATA_DIR 环境变量优先，默认 backend/data/debug（不存在则创建）。"""
    override = os.environ.get("DEBUG_DATA_DIR", "").strip()
    path = Path(override) if override else DATA_DIR / "debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write_json(path: Path, data: Any) -> None:
    """原子写 JSON：同目录临时文件 + os.replace，避免半写文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _read_json(path: Path, default: Any) -> Any:
    """宽容读 JSON：文件缺失/损坏时返回 default 并记日志。"""
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        logger.warning("%s 解析失败，按空数据处理", path, exc_info=True)
        return default


# ---------- 问题记录（issues.json） ----------


def _issues_path() -> Path:
    return data_dir() / ISSUES_FILE


def load_issues() -> list[dict]:
    data = _read_json(_issues_path(), [])
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []


def save_issues(issues: list[dict]) -> None:
    _atomic_write_json(_issues_path(), issues)


def ensure_seed_issues() -> int:
    """issues.json 不存在时写入种子问题（幂等：文件已存在则不动）。返回写入条数。"""
    path = _issues_path()
    if path.exists():
        return 0
    stamp = now_str()
    issues = [
        {
            "id": _new_id(),
            "title": s["title"],
            "severity": s["severity"],
            "status": "open",
            "source": "analysis",
            "page": s["page"],
            "description": s["description"],
            "evidence": "",
            "created_at": stamp,
            "updated_at": stamp,
        }
        for s in SEED_ISSUES
    ]
    save_issues(issues)
    logger.info("已写入调试中心种子问题：%d 条", len(issues))
    return len(issues)


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def list_issues(status: str = "", severity: str = "") -> list[dict]:
    issues = load_issues()
    if status:
        issues = [i for i in issues if i.get("status") == status]
    if severity:
        issues = [i for i in issues if i.get("severity") == severity]
    return sorted(issues, key=lambda i: (i.get("severity", "P9"), i.get("created_at", "")))


def create_issue(payload: Any) -> dict:
    """新建问题；payload 为 schemas.IssueCreate。"""
    with _ISSUES_LOCK:
        issues = load_issues()
        existing = {i.get("id") for i in issues}
        issue_id = _new_id()
        while issue_id in existing:
            issue_id = _new_id()
        stamp = now_str()
        issue = {
            "id": issue_id,
            "title": payload.title.strip(),
            "severity": payload.severity,
            "status": payload.status,
            "source": payload.source,
            "page": payload.page.strip(),
            "description": payload.description,
            "evidence": payload.evidence.strip(),
            "created_at": stamp,
            "updated_at": stamp,
        }
        issues.append(issue)
        save_issues(issues)
        return issue


def update_issue(issue_id: str, payload: Any) -> dict:
    """更新问题（未传字段保持不变），自动刷新 updated_at；不存在抛 LookupError。"""
    with _ISSUES_LOCK:
        issues = load_issues()
        for issue in issues:
            if issue.get("id") == issue_id:
                for field, value in payload.model_dump(exclude_none=True).items():
                    issue[field] = value.strip() if isinstance(value, str) and field in ("title", "page", "evidence") else value
                issue["updated_at"] = now_str()
                save_issues(issues)
                return issue
    raise LookupError(f"问题不存在：{issue_id}")


def delete_issue(issue_id: str) -> None:
    """删除问题；不存在抛 LookupError。"""
    with _ISSUES_LOCK:
        issues = load_issues()
        remaining = [i for i in issues if i.get("id") != issue_id]
        if len(remaining) == len(issues):
            raise LookupError(f"问题不存在：{issue_id}")
        save_issues(remaining)


# ---------- 测试运行归档（runs/<run_id>/） ----------


def _runs_dir() -> Path:
    path = data_dir() / RUNS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_run_id(run_id: str) -> str:
    """校验 run_id：只允许字母数字/下划线/连字符，杜绝路径穿越。"""
    raw = run_id or ""
    if not RUN_ID_PATTERN.match(raw):
        raise ValueError(f"非法的 run_id：{raw!r}")
    return raw


def _run_dir(run_id: str) -> Path:
    return _runs_dir() / _check_run_id(run_id)


def _run_summary(run_dir: Path, result: dict) -> dict:
    totals = result.get("totals") if isinstance(result.get("totals"), dict) else {}
    return {
        "id": result.get("id") or run_dir.name,
        "kind": result.get("kind") or "",
        "base": result.get("base") or "",
        "started_at": result.get("started_at") or "",
        "finished_at": result.get("finished_at") or "",
        "totals": {
            "pages": int(totals.get("pages") or 0),
            "bad_pages": int(totals.get("bad_pages") or 0),
            "tests": int(totals.get("tests") or 0),
            "failed_tests": int(totals.get("failed_tests") or 0),
        },
        "shots": len(list((run_dir / "shots").glob("*.png"))) if (run_dir / "shots").is_dir() else 0,
    }


def list_runs() -> list[dict]:
    """运行列表（按目录名倒序，即时间倒序）；跳过无 result.json 的目录。"""
    runs: list[dict] = []
    root = _runs_dir()
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        result = _read_json(child / "result.json", None)
        if not isinstance(result, dict):
            continue
        runs.append(_run_summary(child, result))
    return runs


def get_run(run_id: str) -> dict:
    """运行详情（result.json 全文 + 截图文件名列表）；不存在抛 LookupError。"""
    run_dir = _run_dir(run_id)
    result = _read_json(run_dir / "result.json", None)
    if not isinstance(result, dict):
        raise LookupError(f"测试运行不存在：{run_id}")
    shots_dir = run_dir / "shots"
    result["shots"] = sorted(p.name for p in shots_dir.glob("*.png")) if shots_dir.is_dir() else []
    return result


def shot_path(run_id: str, filename: str) -> Path:
    """解析截图文件路径；路径穿越抛 ValueError，文件缺失抛 LookupError。"""
    shots_dir = (_run_dir(run_id) / "shots").resolve()
    candidate = Path(filename or "")
    if candidate.is_absolute() or ".." in candidate.parts or candidate.name != filename:
        raise ValueError(f"非法的截图文件名：{filename!r}")
    target = (shots_dir / candidate.name).resolve()
    if not target.is_relative_to(shots_dir):
        raise ValueError(f"非法的截图文件名：{filename!r}")
    if not target.is_file():
        raise LookupError(f"截图不存在：{filename}")
    return target


def delete_run(run_id: str) -> None:
    """整目录删除一次运行；不存在抛 LookupError。"""
    run_dir = _run_dir(run_id)
    if not run_dir.is_dir():
        raise LookupError(f"测试运行不存在：{run_id}")
    shutil.rmtree(run_dir)


# ---------- 前端报错（client_errors.jsonl） ----------


def _client_errors_path() -> Path:
    return data_dir() / CLIENT_ERRORS_FILE


def append_client_error(payload: Any) -> dict:
    """追加一条前端报错。宽容解析：任何非法输入都归一成结构化记录，绝不抛异常。"""
    if not isinstance(payload, dict):
        payload = {"message": payload}
    entry = {
        "ts": str(payload.get("ts") or now_str())[:40],
        "page": str(payload.get("page") or "")[:200],
        "message": str(payload.get("message") or "")[:2000],
        "stack": str(payload.get("stack") or "")[:4000],
        "ua": str(payload.get("ua") or "")[:300],
    }
    path = _client_errors_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def list_client_errors(limit: int = 200) -> list[dict]:
    """尾部读取最近 limit 条（按写入顺序返回）。"""
    path = _client_errors_path()
    if not path.is_file():
        return []
    limit = max(1, min(int(limit or 200), 1000))
    entries: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
    except OSError:
        logger.warning("%s 读取失败", path, exc_info=True)
        return []
    return entries[-limit:]


def clear_client_errors() -> None:
    """清空前端报错记录。"""
    path = _client_errors_path()
    if path.exists():
        path.unlink()


def count_client_errors() -> int:
    path = _client_errors_path()
    if not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


# ---------- 汇总 ----------


def summary() -> dict:
    """调试中心总览：未决问题按严重度计数 / 问题总数 / 运行次数 / 最近一次运行 / 前端报错数。"""
    issues = load_issues()
    open_by_severity = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for issue in issues:
        if issue.get("status") in OPEN_STATUSES:
            sev = issue.get("severity") or "P3"
            open_by_severity[sev] = open_by_severity.get(sev, 0) + 1
    runs = list_runs()
    last_run = None
    if runs:
        latest = runs[0]
        last_run = {
            "id": latest["id"],
            "kind": latest["kind"],
            "finished_at": latest["finished_at"],
            "bad_pages": latest["totals"]["bad_pages"],
            "failed_tests": latest["totals"]["failed_tests"],
        }
    return {
        "open_issues_by_severity": open_by_severity,
        "total_issues": len(issues),
        "runs_count": len(runs),
        "last_run": last_run,
        "client_errors_count": count_client_errors(),
    }
