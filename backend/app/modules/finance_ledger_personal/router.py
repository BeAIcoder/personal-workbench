"""finance_ledger_personal 模块接口：/api/finance/ledger/personal（前缀由注册器挂载）。

上传安全约束（与宿主 assistant 上传同口径）：扩展名白名单（仅 .csv）、
5MB 大小限制（经共享 read_upload_limited 分块限额读取）、文件名清洗 +
resolve 校验防路径穿越；原件留档在模块数据目录 inbox/。
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...config import get_finance_data_dir
from ...database import get_db
from . import service
from .schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    BudgetOut,
    BudgetPut,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    ImportResult,
    MonthlyStatsOut,
    TransactionCreate,
    TransactionListOut,
    TransactionOut,
    TransactionUpdate,
    TrendItem,
)

try:
    # 统一上传防护由 core 任务提供（app/upload.py）：分块限额读取替代全量 file.read()；
    # 共享模块未落地期间名称置空，端点明确 503 失败，绝不退回无界读取。
    from ...upload import read_upload_limited
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    read_upload_limited = None

logger = logging.getLogger(__name__)

router = APIRouter(tags=["财务模块·个人记账"])


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """经共享 read_upload_limited 限额读取上传内容（审计 A01）。

    超限/读取失败映射 400；共享读取器缺失时 503 明确失败，不退回全量 await file.read()。
    """
    if read_upload_limited is None:
        logger.error("共享上传模块 app.upload 不可用，拒绝个人记账上传读取")
        raise HTTPException(status_code=503, detail="上传读取服务暂不可用，请稍后重试")
    try:
        return await read_upload_limited(file, max_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("个人记账上传受限读取失败（上限 %s 字节）：%s", max_bytes, exc)
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败或超过 {max_bytes // 1024 // 1024}MB 大小上限",
        ) from exc


def _run(fn, *args, **kwargs):
    """统一异常映射：LookupError → 404，ValueError → 400。"""
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------- 账户 ----------------

@router.get("/accounts", response_model=list[AccountOut], summary="账户列表")
def list_accounts(db: Session = Depends(get_db)):
    return service.list_accounts(db)


@router.post("/accounts", response_model=AccountOut, status_code=201, summary="新建账户")
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    return _run(service.create_account, db, payload)


@router.put("/accounts/{account_id}", response_model=AccountOut, summary="更新账户")
def update_account(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)):
    return _run(service.update_account, db, account_id, payload)


@router.delete("/accounts/{account_id}", summary="删除账户（有交易引用的拒绝）")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    _run(service.delete_account, db, account_id)
    return {"ok": True, "id": account_id}


# ---------------- 分类 ----------------

@router.get("/categories", response_model=list[CategoryOut], summary="分类列表（flat，可按类型过滤）")
def list_categories(kind: str | None = Query(default=None), db: Session = Depends(get_db)):
    return service.list_categories(db, kind)


@router.post("/categories", response_model=CategoryOut, status_code=201, summary="新建分类（最多两级）")
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    return _run(service.create_category, db, payload)


@router.put("/categories/{category_id}", response_model=CategoryOut, summary="更新分类名称 / 排序")
def update_category(category_id: int, payload: CategoryUpdate, db: Session = Depends(get_db)):
    return _run(service.update_category, db, category_id, payload)


@router.delete("/categories/{category_id}", summary="删除分类（有子分类或交易的拒绝）")
def delete_category(category_id: int, db: Session = Depends(get_db)):
    _run(service.delete_category, db, category_id)
    return {"ok": True, "id": category_id}


# ---------------- 交易 ----------------

@router.get("/transactions", response_model=TransactionListOut, summary="交易列表（月份/分类/账户/关键词筛选 + 分页）")
def list_transactions(
    month: str | None = Query(default=None, description="YYYY-MM"),
    category_id: int | None = Query(default=None),
    account_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None, description="备注关键词"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    total, rows = _run(service.list_transactions, db, month, category_id, account_id, keyword, page, page_size)
    accounts, cats = service._category_maps(db)
    return {"total": total, "items": [service.transaction_out(tx, accounts, cats) for tx in rows]}


@router.post("/transactions", response_model=TransactionOut, status_code=201, summary="记一笔")
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    tx = _run(service.create_transaction, db, payload)
    accounts, cats = service._category_maps(db)
    return service.transaction_out(tx, accounts, cats)


@router.put("/transactions/{tx_id}", response_model=TransactionOut, summary="更新交易")
def update_transaction(tx_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)):
    tx = _run(service.update_transaction, db, tx_id, payload)
    accounts, cats = service._category_maps(db)
    return service.transaction_out(tx, accounts, cats)


@router.delete("/transactions/{tx_id}", summary="删除交易")
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    _run(service.delete_transaction, db, tx_id)
    return {"ok": True, "id": tx_id}


# ---------------- 统计 ----------------

@router.get("/stats/monthly", response_model=MonthlyStatsOut, summary="月度汇总（收入/支出/结余 + 分类占比）")
def stats_monthly(month: str = Query(..., description="YYYY-MM"), db: Session = Depends(get_db)):
    return _run(service.monthly_summary, db, month)


@router.get("/stats/trend", response_model=list[TrendItem], summary="最近 N 个月收支趋势")
def stats_trend(months: int = Query(default=6, ge=1, le=36), db: Session = Depends(get_db)):
    return service.trend(db, months)


# ---------------- 月度预算 ----------------

@router.get("/budgets", response_model=BudgetOut, summary="某月预算视图（总预算 + 各一级支出分类）")
def get_budgets(month: str = Query(..., description="YYYY-MM"), db: Session = Depends(get_db)):
    return _run(service.get_budgets, db, month)


@router.put("/budgets", response_model=BudgetOut, summary="整月预算整体保存（幂等，amount<=0 清除该项）")
def put_budgets(payload: BudgetPut, db: Session = Depends(get_db)):
    return _run(service.put_budgets, db, payload)


# ---------------- CSV 导入 / 导出 ----------------

@router.post("/import/csv", response_model=ImportResult, summary="导入 CSV（中文列名，上限 5MB）")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    safe_name = Path(file.filename or "import.csv").name
    if safe_name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="非法的文件名")
    if Path(safe_name).suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="仅支持 .csv 文件")
    content = await _read_upload(file, service.MAX_IMPORT_BYTES)
    inbox = get_finance_data_dir() / "finance_ledger_personal" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    if dest.resolve().parent != inbox.resolve():
        raise HTTPException(status_code=400, detail="非法的文件名")
    dest.write_bytes(content)
    # 解析入库为重活：丢线程池执行，避免阻塞事件循环（审计 A05）
    result = await asyncio.to_thread(_run, service.import_csv, db, content)
    return result


@router.get("/export/csv", summary="导出 CSV（utf-8-sig，中文列名，可带筛选条件）")
def export_csv(
    month: str | None = Query(default=None, description="YYYY-MM"),
    category_id: int | None = Query(default=None),
    account_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    text = _run(service.export_csv, db, month, category_id, account_id, keyword)
    fname = f"ledger_{month or 'all'}.csv"
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
