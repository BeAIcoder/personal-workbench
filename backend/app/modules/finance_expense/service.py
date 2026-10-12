"""finance_expense 模块业务逻辑：费用分类引擎定位、分类执行、运行记录与 Agent 工具。

引擎复用（L0 直挂）：真实引擎在 01-财税自动化/现金流滚动预测/费用分类汇总系统-v2.0/
（expense_classification.py 的 ExpenseClassifier + rules_loader.py + classification_rules.json）。
规则 JSON 是单一权威源，本模块只读引用，不复制其内容。

故障隔离：引擎目录缺失或导入失败时，模块本身照常加载（manifest/路由/表均不受影响），
依赖引擎的接口返回 503 + 中文提示；运行记录查询等接口不受引擎状态影响。
"""
import importlib
import os
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...config import BASE_DIR, get_finance_data_dir, settings
from ...database import SessionLocal
from .models import ExpenseRow, ExpenseRun

MODULE_NAME = "finance_expense"

# 引擎默认相对位置：01-财税自动化/现金流滚动预测/费用分类汇总系统-v2.0
_ENGINE_RELATIVE = Path("现金流滚动预测") / "费用分类汇总系统-v2.0"
_ENGINE_REQUIRED_FILES = ("expense_classification.py", "rules_loader.py", "classification_rules.json")

# 上传约束：扩展名白名单 + 大小上限（测试可 monkeypatch 调低）
ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class EngineUnavailableError(RuntimeError):
    """费用分类引擎不可用（目录缺失或导入失败），由路由层映射为 503。"""


# 引擎加载缓存：仅缓存成功结果；失败不缓存，下次调用重新探测（目录后补上也无需重启）
_ENGINE: dict[str, Any] = {"classifier_cls": None, "load_rules": None, "app_config": None, "engine_dir": ""}


def _reset_engine_state() -> None:
    """清空引擎缓存（测试隔离与故障恢复用）。"""
    _ENGINE.update(classifier_cls=None, load_rules=None, app_config=None, engine_dir="")


def module_subdir(kind: str) -> Path:
    """模块数据子目录：get_finance_data_dir()/finance_expense/inbox|outbox|tmp。"""
    if kind not in ("inbox", "outbox", "tmp"):
        raise ValueError(f"未知模块子目录类型：{kind}")
    path = get_finance_data_dir() / MODULE_NAME / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_engine_dir(path: Path) -> bool:
    """目录须同时具备引擎入口、规则加载器与规则 JSON 权威源。"""
    return path.is_dir() and all((path / name).is_file() for name in _ENGINE_REQUIRED_FILES)


def find_engine_dir() -> Path | None:
    """定位费用分类引擎目录。

    settings.finance_expense_engine_path 非空时只用配置值（无效即不可用，不回退）；
    否则从 BASE_DIR 向上找「01-财税自动化」目录，推导其下的 现金流滚动预测/费用分类汇总系统-v2.0。
    """
    configured = (settings.finance_expense_engine_path or "").strip()
    if configured:
        candidate = Path(configured)
        return candidate if _is_engine_dir(candidate) else None
    for parent in BASE_DIR.parents:
        if parent.name == "01-财税自动化":
            candidate = parent / _ENGINE_RELATIVE
            return candidate if _is_engine_dir(candidate) else None
    return None


def _ensure_engine() -> dict[str, Any]:
    """懒加载引擎：首次调用时定位目录、插入 sys.path 并 import，成功后缓存。

    引擎 import 的副作用处理：
    - expense_classification 顶层按相对路径创建 expense_classification.log，
      import 期间临时把 CWD 切到模块 tmp 目录，避免污染宿主工作目录；
    - 引擎以顶层 `import config` 读取自带 config.py，import 期间临时遮蔽
      sys.modules 里可能存在的同名外来模块，import 完成后恢复原状（引擎内部
      已绑定自身 config 引用，本模块加载规则时也显式传入该引用）。
    """
    if _ENGINE["classifier_cls"] is not None:
        return _ENGINE

    engine_dir = find_engine_dir()
    if engine_dir is None:
        raise EngineUnavailableError(
            "费用分类引擎不可用：未找到引擎目录（现金流滚动预测/费用分类汇总系统-v2.0），"
            "请检查 FINANCE_EXPENSE_ENGINE_PATH 配置或确认引擎项目在位"
        )

    tmp_dir = module_subdir("tmp")
    engine_key = str(engine_dir)
    prev_cwd = os.getcwd()
    stashed_config = sys.modules.pop("config", None)
    try:
        if engine_key in sys.path:
            sys.path.remove(engine_key)
        sys.path.insert(0, engine_key)
        os.chdir(tmp_dir)
        try:
            ec_mod = importlib.import_module("expense_classification")
            rl_mod = importlib.import_module("rules_loader")
        finally:
            os.chdir(prev_cwd)
    except EngineUnavailableError:
        raise
    except Exception as exc:
        raise EngineUnavailableError(f"费用分类引擎加载失败：{exc}") from exc
    finally:
        # 恢复 sys.modules 中的外来 config（若有）；引擎自带的 config 引用已绑定在模块内
        sys.modules.pop("config", None)
        if stashed_config is not None:
            sys.modules["config"] = stashed_config

    _ENGINE.update(
        classifier_cls=ec_mod.ExpenseClassifier,
        load_rules=rl_mod.load_classification_rules,
        app_config=getattr(ec_mod, "_app_config", None),
        engine_dir=engine_key,
    )
    return _ENGINE


def ensure_engine_ready() -> None:
    """供路由层快速失败的引擎可用性探测（不可用抛 EngineUnavailableError）。"""
    _ensure_engine()


def _load_rules() -> dict:
    """按引擎同口径加载分类规则（JSON 权威源 + 引擎自带 config 覆盖），每次调用重读文件。"""
    engine = _ensure_engine()
    return engine["load_rules"](config_module=engine["app_config"])


def run_to_dict(run: ExpenseRun) -> dict:
    """运行记录转响应 dict。"""
    return {
        "id": run.id,
        "original_filename": run.original_filename,
        "total_rows": run.total_rows,
        "matched_rows": run.matched_rows,
        "status": run.status,
        "duration_sec": run.duration_sec,
        "artifact_path": run.artifact_path,
        "created_at": run.created_at,
    }


def classify_file(db: Session, original_filename: str, stored_path: Path) -> ExpenseRun:
    """执行一轮费用分类：读取银行明细账 → 整合 → 清洗 → 分类 → 汇总 → 导出，结果入库。

    失败时运行记录落库为 failed 并把中文错误抛给路由层（400）。
    引擎不可用抛 EngineUnavailableError（503），此时不写运行记录。
    """
    engine = _ensure_engine()
    run = ExpenseRun(original_filename=original_filename[:300], status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.perf_counter()
    try:
        clf = engine["classifier_cls"]()
        if clf.read_bank_data(str(stored_path)) is None:
            raise ValueError("银行明细账读取失败：请确认上传的是银行存款辅助明细账（表头在第 11 行，含 科目编码/日期/摘要/贷方本币 等列）")
        if clf.integrate_data() is None:
            raise ValueError("数据整合失败")
        if clf.clean_data() is None:
            raise ValueError("数据清洗失败")
        classified = clf.classify_expenses()
        if classified is None:
            raise ValueError("费用分类执行失败")
        if clf.summarize_expenses() is None:
            raise ValueError("费用汇总计算失败")
        out_name = f"run_{run.id}_费用分类结果.xlsx"
        out_path = module_subdir("outbox") / out_name
        if clf.export_results(str(out_path)) is None:
            raise ValueError("结果报表导出失败")

        rows: list[ExpenseRow] = []
        matched = 0
        for row_no, (_, record) in enumerate(classified.iterrows(), start=1):
            category = str(record.get("费用类别") or "未分类")
            hit = category != "未分类"
            matched += int(hit)
            rows.append(
                ExpenseRow(
                    run_id=run.id,
                    row_no=row_no,
                    summary=str(record.get("摘要") or "")[:500],
                    amount=float(record.get("贷方本币") or 0),
                    category=category[:50],
                    hit=hit,
                )
            )
        db.add_all(rows)
        run.status = "success"
        run.total_rows = len(rows)
        run.matched_rows = matched
        run.duration_sec = round(time.perf_counter() - started, 3)
        run.artifact_path = f"{MODULE_NAME}/outbox/{out_name}"
        db.commit()
        db.refresh(run)
        return run
    except Exception:
        run.status = "failed"
        run.duration_sec = round(time.perf_counter() - started, 3)
        db.commit()
        raise


def list_runs(db: Session, page: int, page_size: int) -> dict:
    """分页查询运行记录（按 id 倒序）。"""
    total = db.query(func.count(ExpenseRun.id)).scalar() or 0
    items = (
        db.query(ExpenseRun)
        .order_by(ExpenseRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"total": total, "items": [run_to_dict(r) for r in items]}


def get_run(db: Session, run_id: int) -> ExpenseRun | None:
    """按 id 取运行记录。"""
    return db.get(ExpenseRun, run_id)


def run_detail(db: Session, run: ExpenseRun) -> dict:
    """运行详情：记录本体 + 按命中分类聚合的金额/行数统计 + 全部明细行。"""
    rows = db.query(ExpenseRow).filter(ExpenseRow.run_id == run.id).order_by(ExpenseRow.row_no).all()
    stats_map: dict[str, list] = {}
    for r in rows:
        bucket = stats_map.setdefault(r.category, [0, 0.0])
        bucket[0] += 1
        bucket[1] += r.amount
    stats = [
        {"category": category, "count": v[0], "amount": round(v[1], 2)}
        for category, v in sorted(stats_map.items(), key=lambda kv: kv[1][1], reverse=True)
    ]
    return {
        "run": run_to_dict(run),
        "stats": stats,
        "rows": [
            {
                "id": r.id,
                "row_no": r.row_no,
                "summary": r.summary,
                "amount": r.amount,
                "category": r.category,
                "hit": r.hit,
            }
            for r in rows
        ],
    }


def resolve_artifact(run: ExpenseRun) -> Path | None:
    """解析运行产物路径：必须落在模块 outbox 内（防路径穿越），文件须存在。"""
    rel = (run.artifact_path or "").strip()
    if not rel:
        return None
    base = get_finance_data_dir().resolve()
    candidate = (base / rel).resolve()
    outbox = (base / MODULE_NAME / "outbox").resolve()
    if outbox not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def rules_summary() -> dict:
    """规则摘要：分类条数 / 关键词总数 / 来源路径（引擎不可用时抛 503）。"""
    engine = _ensure_engine()
    rules = _load_rules()
    return {
        "engine_dir": engine["engine_dir"],
        "rules_file": str(Path(engine["engine_dir"]) / "classification_rules.json"),
        "total_categories": len(rules),
        "total_keywords": sum(len(rule.get("keywords", [])) for rule in rules.values()),
        "categories": list(rules.keys()),
    }


def reload_rules() -> dict:
    """重载规则：rules_loader 每次调用都重读 JSON 文件，重新加载并返回最新摘要即可。"""
    return {"ok": True, "summary": rules_summary()}


def expense_runs_summary(limit: int = 5) -> dict:
    """Agent 工具（readonly）：最近 N 次费用分类运行统计。"""
    limit = max(1, min(int(limit), 20))
    with SessionLocal() as db:
        total = db.query(func.count(ExpenseRun.id)).scalar() or 0
        runs = db.query(ExpenseRun).order_by(ExpenseRun.id.desc()).limit(limit).all()
        recent = [
            {
                "id": r.id,
                "original_filename": r.original_filename,
                "total_rows": r.total_rows,
                "matched_rows": r.matched_rows,
                "命中率": f"{(r.matched_rows / r.total_rows * 100):.1f}%" if r.total_rows else "-",
                "status": r.status,
                "duration_sec": r.duration_sec,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            }
            for r in runs
        ]
    return {"ok": True, "total_runs": total, "recent": recent}
