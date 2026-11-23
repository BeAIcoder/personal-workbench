"""finance_ledger_bip 模块门面：入库编排、查询装配与公共符号再导出。

本文件保留「按 BIP 三份文件类型」拆分后的编排与解析入口（机械拆分，行为不变）：
- preview_import / save_preview_file / _read_staged_excel / read_bip_excel：上传落盘
  （扩展名白名单 + 10MB 上限 + 随机文件名防路径穿越）→ 表头定位 → 类型识别 →
  前 50 行预览；行数上限 MAX_IMPORT_ROWS 在此检查（MAX_IMPORT_ROWS 的补丁面是
  本模块，故行数检查留在门面，测试与调用方 monkeypatch service 仍然生效）；
- confirm_import：合并/校验列映射 → 事务写库（凭证类交 bip_vouchers、余额表交
  bip_balance）→ 提交后留档原文件；并发冲突 409、全行无效 400；
- export_vouchers_to_vault：与 finance_vault 数据仓的可选联动，模块缺失或禁用时抛
  VaultUnavailableError（路由层转 503 中文提示），绝不崩溃。

其余业务实现按职责落在子模块，公共符号（常量 / 异常 / 工具 / 查询 / Agent 工具）
在文末 re-export，router、manifest 与测试仍按 `service.<名字>` 访问。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .bip_common import (
    ALLOWED_SUFFIXES,
    BipImportConflictError,
    BipRowLimitExceeded,
    FIELD_LABELS,
    IMPORT_LOCK_TIMEOUT,
    MAX_IMPORT_ROWS,
    MAX_UPLOAD_BYTES,
    MAX_WARNINGS,
    MODULE_NAME,
    PERIOD_RE,
    PREVIEW_ROWS,
    REQUIRED_FIELDS,
    SHEET_LEDGER_COL,
    TMP_RETENTION,
    TOKEN_RE,
    TYPE_AUX,
    TYPE_BALANCE,
    TYPE_LABELS,
    TYPE_UNKNOWN,
    TYPE_VOUCHER,
    VaultUnavailableError,
    _IMPORT_LOCK,
    _cell,
    detect_file_type,
    module_data_dir,
    suggest_mapping,
)
from .models import BipBatch, BipEntry, BipVoucher

logger = logging.getLogger(__name__)

# ---------------- 目录 / 解析与预览 ----------------

def _sub_dir(name: str) -> Path:
    """取模块数据子目录并确保存在（单独建目录，便于测试整体重定向 module_data_dir）。"""
    d = module_data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_bip_excel(path: Path) -> pd.DataFrame:
    """读取 BIP 导出 Excel：自动定位表头行、归一化/去重列名、剔除全空行与空名列。

    多 sheet 文件（用友凭证列表按账簿分 sheet，无核算账簿列）：逐 sheet 定位表头后
    合并，并追加 SHEET_LEDGER_COL 列承载 sheet 名（= 核算账簿），供无账簿列的
    凭证列表入库（外部验证第 3 章 B1 缺口修复）。

    行数防护：解析完成后按共享上限 MAX_IMPORT_ROWS 检查（合并后总行数），
    超限抛 BipRowLimitExceeded（ValueError 子类，预览/确认入口映射 400），
    避免超大表无界进入内存后续的入库流程。
    """
    engine = "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine=engine)
    if not sheets:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    multi = len(sheets) > 1
    for name, raw in sheets.items():
        df = _read_one_sheet(raw)
        if df.empty:
            continue
        if multi:
            df[SHEET_LEDGER_COL] = str(name)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        df = frames[0]
    else:
        df = pd.concat(frames, ignore_index=True, sort=False)
        if SHEET_LEDGER_COL in df.columns:  # 合成账簿列移到末尾，避免 NaN 列插在中间
            cols = [c for c in df.columns if c != SHEET_LEDGER_COL] + [SHEET_LEDGER_COL]
            df = df[cols]
    if MAX_IMPORT_ROWS is None:
        raise BipRowLimitExceeded("导入行数上限服务暂不可用，请稍后重试")
    if len(df) > MAX_IMPORT_ROWS:
        raise BipRowLimitExceeded(
            f"文件数据行数（{len(df)}）超过单次导入上限（{MAX_IMPORT_ROWS} 行），请拆分后导入"
        )
    return df


def _cleanup_tmp() -> None:
    """清理 tmp 下超过保留时长的预览文件（含 .name 边车文件），失败仅记日志。"""
    try:
        cutoff = datetime.now() - TMP_RETENTION
        tmp_dir = _sub_dir("tmp")
        for f in tmp_dir.iterdir():
            try:
                if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
            except OSError:
                continue
    except OSError:
        logger.debug("tmp 清理跳过", exc_info=True)


def save_preview_file(filename: str, content: bytes) -> tuple[str, Path]:
    """上传落盘到模块 tmp：扩展名白名单 + 大小限制 + 随机文件名（杜绝路径穿越）。"""
    safe_name = Path(filename or "").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"仅支持 {' / '.join(ALLOWED_SUFFIXES)} 格式的 Excel 文件")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("文件超过 10MB 上限")
    token = f"{uuid.uuid4().hex}{suffix}"
    path = _sub_dir("tmp") / token
    path.write_bytes(content)
    # 边车文件留存原始文件名，供确认入库时留档命名
    path.with_name(path.name + ".name").write_text(safe_name or token, encoding="utf-8")
    _cleanup_tmp()
    return token, path


def preview_import(filename: str, content: bytes) -> dict:
    """上传预览：识别文件类型 → 列映射建议 → 前 50 行预览。"""
    token, path = save_preview_file(filename, content)
    df = _read_staged_excel(path)
    columns = [str(c) for c in df.columns]
    file_type = detect_file_type(columns)
    mapping = suggest_mapping(file_type, columns) if file_type != TYPE_UNKNOWN else {}
    return {
        "token": token,
        "filename": Path(filename or "").name,
        "file_type": file_type,
        "type_label": TYPE_LABELS[file_type],
        "columns": columns,
        "mapping": mapping,
        "required_fields": REQUIRED_FIELDS.get(file_type, []),
        "row_count": int(len(df)),
        "preview_rows": [
            {col: _cell(v) for col, v in row.items()}
            for row in df.head(PREVIEW_ROWS).to_dict("records")
        ],
    }


# ---------------- 确认入库 ----------------

def _resolve_token(token: str) -> Path:
    """把预览 token 解析回 tmp 文件路径：格式白名单校验，杜绝路径穿越。"""
    if not TOKEN_RE.fullmatch(token or ""):
        raise ValueError("预览标识不合法，请重新上传预览")
    path = _sub_dir("tmp") / token
    if not path.is_file():
        raise LookupError("预览文件不存在或已过期，请重新上传预览")
    return path


def _discard_preview(path: Path) -> None:
    """清理预览半成品：tmp 文件与 .name 边车（解析失败/行数超限时调用，失败仅记日志）。"""
    for p in (path, path.with_name(path.name + ".name")):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            logger.debug("预览半成品清理跳过：%s", p.name, exc_info=True)


def _read_staged_excel(path: Path) -> pd.DataFrame:
    """解析暂存的预览文件（read_bip_excel，含行数上限检查）。

    失败即清理预览半成品（tmp 文件 + .name 边车）：行数超限与解析失败都不在磁盘
    留下被拒上传。行数超限（BipRowLimitExceeded）的固定文案原样透出，路由层映射
    400；其余异常对外只给通用中文提示，完整异常进服务器日志，不回带本机异常
    路径（审计台账 A06 / A09）。
    """
    try:
        return read_bip_excel(path)
    except BipRowLimitExceeded:
        _discard_preview(path)
        raise
    except Exception as exc:
        _discard_preview(path)
        logger.warning("BIP 暂存文件解析失败：%s", exc, exc_info=True)
        raise ValueError("Excel 解析失败，请检查文件格式或稍后重试") from exc


def _merge_mapping(file_type: str, columns: list[str], user_mapping: dict[str, str] | None) -> dict[str, str]:
    """合并映射：自动建议为底，人工映射覆盖（空值表示显式取消该字段映射）；随后校验。"""
    mapping = suggest_mapping(file_type, columns)
    for k, v in (user_mapping or {}).items():
        if k not in FIELD_LABELS:
            continue
        if v:
            mapping[k] = v
        else:
            mapping.pop(k, None)
    bad = sorted({c for c in mapping.values() if c not in columns})
    if bad:
        raise ValueError(f"列映射引用了不存在的列：{'、'.join(bad)}")
    missing = [f for f in REQUIRED_FIELDS.get(file_type, []) if f not in mapping]
    if missing:
        raise ValueError("列映射缺少必需字段：" + "、".join(FIELD_LABELS[f] for f in missing))
    if file_type in (TYPE_VOUCHER, TYPE_AUX) and not (mapping.get("debit") or mapping.get("credit")):
        raise ValueError("借方金额 / 贷方金额列至少需要映射一项")
    return mapping


def _classify_constraint_error(exc: IntegrityError) -> tuple[str, bool]:
    """数据库约束错误分类：返回（中文提示, 是否并发唯一键冲突）。

    唯一键命中按凭证/余额分别提示（路由 409，可重试）；其余约束（NOT NULL /
    CHECK 等）归为数据问题（400），只给中文提示，不回带 SQL 细节。
    """
    text = str(getattr(exc, "orig", exc) or exc).lower()
    is_unique = "unique" in text or "duplicate key" in text or "uq_bip_" in text
    if is_unique and ("mod_bip_voucher" in text or "uq_bip_voucher_scope" in text):
        return ("相同凭证已被另一导入任务写入，请稍后重试（重复数据会自动跳过）", True)
    if is_unique and ("mod_bip_balance" in text or "uq_bip_balance_period_subject" in text
                      or "uq_bip_balance_scope" in text):
        return ("相同账簿/期间/科目的余额行已被另一导入任务写入，请稍后重试", True)
    return ("数据约束冲突（唯一键或必填字段不满足），请检查数据后重试", False)


def confirm_import(
    db: Session,
    *,
    token: str,
    file_type: str | None = None,
    user_mapping: dict[str, str] | None = None,
    period: str | None = None,
) -> dict:
    """确认入库：解析预览文件 → 合并/校验列映射 → 事务写库 → 提交后留档原文件。

    并发/双击：进程内导入锁把写库段串行化，后到者等前者提交后按重复跳过；
    多进程/多 worker 由数据库唯一索引兜底（冲突回滚并返回 409，绝不重复入库）。
    归档顺序：先 commit 再写 inbox，归档失败只丢留档、不留孤儿文件与半批数据。
    """
    path = _resolve_token(token)
    df = _read_staged_excel(path)
    if df.empty:
        raise ValueError("文件内没有可导入的数据（或表头识别失败），请检查文件")
    columns = [str(c) for c in df.columns]

    eff_type = (file_type or "").strip() or detect_file_type(columns)
    if eff_type not in (TYPE_VOUCHER, TYPE_AUX, TYPE_BALANCE):
        raise ValueError("无法识别文件类型，请指定文件类型（voucher/aux/balance）并完善列映射")
    mapping = _merge_mapping(eff_type, columns, user_mapping)
    if eff_type == TYPE_BALANCE:
        if not period:
            raise ValueError("科目余额表导入必须提供期间 period（格式 YYYY-MM）")
        if not PERIOD_RE.fullmatch(period):
            raise ValueError("期间格式应为 YYYY-MM（如 2026-09）")

    name_file = path.with_name(path.name + ".name")
    orig_name = name_file.read_text(encoding="utf-8").strip() if name_file.is_file() else token

    if not _IMPORT_LOCK.acquire(timeout=IMPORT_LOCK_TIMEOUT):
        raise BipImportConflictError("另一个导入任务正在进行，请稍后重试")

    dest: Path | None = None
    batch = BipBatch(
        filename=orig_name,
        file_type=eff_type,
        period=(period or "") if eff_type == TYPE_BALANCE else "",
        total_rows=int(len(df)),
    )
    try:
        try:
            db.add(batch)
            db.flush()
            if eff_type == TYPE_BALANCE:
                stats = _import_balance(db, batch, df, mapping, period or "")
            else:
                stats = _import_voucher_like(db, batch, df, mapping, eff_type)
            batch.inserted_vouchers = stats["inserted_vouchers"]
            batch.skipped_vouchers = stats["skipped_vouchers"]
            batch.inserted_entries = stats["inserted_entries"]
            batch.inserted_balances = stats.get("inserted_balances", 0)
            batch.updated_balances = stats.get("updated_balances", 0)
            batch.unchanged_balances = stats.get("unchanged_balances", 0)
            batch.merged_vouchers = stats.get("merged_vouchers", 0)
            batch.merged_entries = stats.get("merged_entries", 0)
            batch.ambiguous_entries = stats.get("ambiguous_entries", 0)
            batch.skipped_rows = stats["skipped_rows"]
            batch.invalid_amount_rows = stats.get("invalid_amount_rows", 0)
            batch.warnings = json.dumps(stats["warnings"], ensure_ascii=False)

            # 全行无效守卫（台账 L-074）：一条都没解析出来时明确 400，而不是
            # 返回「新增凭证 0 张」让用户误以为成功。重复/未变化的重导不算无效
            # （parsed > 0），仍按幂等返回 200。
            if eff_type == TYPE_BALANCE:
                parsed = batch.inserted_balances + batch.updated_balances + batch.unchanged_balances
            else:
                parsed = (batch.inserted_vouchers + batch.skipped_vouchers
                          + batch.merged_vouchers)
            if parsed == 0 and batch.skipped_rows > 0:
                raise ValueError(
                    f"文件中没有任何可导入的有效数据（无效行 {batch.skipped_rows} 条），"
                    "请检查表头识别与列映射后重试"
                )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            message, conflict = _classify_constraint_error(exc)
            if conflict:
                raise BipImportConflictError(message) from exc
            raise ValueError(message) from exc
        except OperationalError as exc:
            db.rollback()
            detail = str(getattr(exc, "orig", exc) or exc).lower()
            if "locked" in detail or "busy" in detail:
                raise BipImportConflictError(
                    "数据库忙，请稍后重试（本次已回滚，不会产生半批数据）"
                ) from exc
            raise
        except Exception:
            db.rollback()
            raise

        # 数据已提交，再做原文件留档：归档失败不丢数据，也不留下孤儿文件
        try:
            dest = _sub_dir("inbox") / f"{batch.id:06d}_{Path(orig_name).name}"
            dest.write_bytes(path.read_bytes())
            batch.stored_path = str(dest)
            db.commit()
        except Exception:
            logger.warning("批次 %s 原文件留档失败（数据已入库）", batch.id, exc_info=True)
            db.rollback()
            if dest is not None:
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    logger.debug("归档半成品清理跳过：%s", dest.name, exc_info=True)
            dest = None
        else:
            try:
                path.unlink(missing_ok=True)
                name_file.unlink(missing_ok=True)
            except OSError:
                logger.debug("预览文件清理跳过", exc_info=True)
    finally:
        _IMPORT_LOCK.release()

    if eff_type == TYPE_BALANCE:
        message = (
            f"导入完成：新增余额行 {batch.inserted_balances} 条、"
            f"更正 {batch.updated_balances} 条、未变化 {batch.unchanged_balances} 条"
            f"（无效行 {batch.skipped_rows} 条）"
        )
    else:
        message = (
            f"导入完成：新增凭证 {batch.inserted_vouchers} 张、分录 {batch.inserted_entries} 条；"
            f"合并处理凭证 {batch.merged_vouchers} 张（补写分录 {batch.merged_entries} 条）；"
            f"歧义未合并分录 {batch.ambiguous_entries} 条；"
            f"跳过重复凭证 {batch.skipped_vouchers} 张；警告 {len(stats['warnings'])} 条"
        )
    return {
        "batch_id": batch.id,
        "file_type": eff_type,
        "type_label": TYPE_LABELS[eff_type],
        "total_rows": batch.total_rows,
        "inserted_vouchers": batch.inserted_vouchers,
        "skipped_vouchers": batch.skipped_vouchers,
        "inserted_entries": batch.inserted_entries,
        "inserted_balances": batch.inserted_balances,
        "updated_balances": batch.updated_balances,
        "unchanged_balances": batch.unchanged_balances,
        "merged_vouchers": batch.merged_vouchers,
        "merged_entries": batch.merged_entries,
        "ambiguous_entries": batch.ambiguous_entries,
        "skipped_rows": batch.skipped_rows,
        "invalid_amount_rows": batch.invalid_amount_rows,
        "warnings": stats["warnings"],
        "message": message,
    }


# ---------------- 数据仓联动（finance_vault 可选依赖） ----------------

def _load_vault_ingest():
    """加载数据仓模块的入仓函数；模块未安装 / 被禁用 / 导入失败时返回 None。"""
    try:
        from ..registry import get_loaded_modules

        if "finance_vault" not in {m.name for m in get_loaded_modules()}:
            return None
        from app.modules.finance_vault.service import ingest_dataframe
    except Exception:
        logger.warning("数据仓模块 finance_vault 不可用", exc_info=True)
        return None
    return ingest_dataframe


def export_vouchers_to_vault(db: Session, period: str | None = None) -> dict:
    """凭证数据入仓：组装 DataFrame 后调 finance_vault 契约函数 ingest_dataframe。

    指定期间时按 append 追加，否则全量 replace；vault 不可用时抛 VaultUnavailableError。
    """
    if period is not None and not PERIOD_RE.fullmatch(period):
        raise ValueError("期间格式应为 YYYY-MM（如 2026-09）")
    ingest = _load_vault_ingest()
    if ingest is None:
        raise VaultUnavailableError("数据仓模块 finance_vault 不可用（未安装、未启用或加载失败），无法入仓")
    # 导出不走分页接口（其有 200 条上限），直接全量取数
    agg = (
        db.query(
            BipEntry.voucher_id.label("vid"),
            func.count(BipEntry.id).label("entry_count"),
            func.coalesce(func.sum(BipEntry.debit), 0.0).label("debit_total"),
            func.coalesce(func.sum(BipEntry.credit), 0.0).label("credit_total"),
        )
        .group_by(BipEntry.voucher_id)
        .subquery()
    )
    q = db.query(
        BipVoucher,
        func.coalesce(agg.c.entry_count, 0),
        func.coalesce(agg.c.debit_total, 0.0),
        func.coalesce(agg.c.credit_total, 0.0),
    ).outerjoin(agg, agg.c.vid == BipVoucher.id)
    if period:
        q = q.filter(BipVoucher.period == period)
    # 行数护栏前移到取数阶段：超限时最多多取 1 行即拒绝，不先把全量凭证组装进
    # DataFrame 再让数据仓发现超限（个人库也可能达到上限，内存峰值不应先发生）
    if MAX_IMPORT_ROWS is None:
        raise ValueError("导入行数上限模块缺失，拒绝导出")
    rows = q.order_by(BipVoucher.voucher_date, BipVoucher.id).limit(MAX_IMPORT_ROWS + 1).all()
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"凭证数据行数超过单次入仓上限（{MAX_IMPORT_ROWS} 行），请按期间分批导出")
    if not rows:
        return {"ok": True, "table": "bip_voucher", "mode": "append" if period else "replace",
                "rows": 0, "result": {}, "message": "无符合条件的凭证数据，未执行入仓"}
    df = pd.DataFrame([_voucher_to_dict(v, c, d, cr) for v, c, d, cr in rows])
    mode = "append" if period else "replace"
    result = ingest(table="bip_voucher", df=df, mode=mode)
    return {"ok": True, "table": "bip_voucher", "mode": mode, "rows": len(df),
            "result": result if isinstance(result, dict) else {"value": str(result)},
            "message": f"已入仓 {len(df)} 条凭证数据（表 bip_voucher，模式 {mode}）"}


# ---------------- 公共符号再导出（调用方 / manifest / 测试按 service.<名字> 访问） ----------------

from .bip_aux import (  # noqa: E402,F401
    _entry_from_row,
    query_entries,
)
from .bip_balance import (  # noqa: E402,F401
    _BALANCE_MONEY_FIELDS,
    _balance_to_dict,
    _import_balance,
    balance_summary,
    query_balances,
)
from .bip_common import (  # noqa: E402,F401
    COMPACT_DATE_RE,
    _DIRECTION_EXCLUDE,
    _HEADER_EXACT,
    _HEADER_FUZZY,
    _MONEY,
    _locate_header_row,
    _match_col,
    _norm_col,
    _parse_amount,
    _r2,
    _read_one_sheet,
    _split_subject,
    _text,
    _to_date_text,
    _uniquify,
)
from .bip_query import (  # noqa: E402,F401
    _batch_to_dict,
    _voucher_to_dict,
    bip_balance_query,
    bip_voucher_query,
    get_voucher_detail,
    list_batches,
    query_vouchers,
)
from .bip_vouchers import (  # noqa: E402,F401
    _import_voucher_like,
    _merge_entries,
    _pick_merge_target,
    _voucher_sources,
)
