"""finance_ledger_bip 模块业务逻辑与 Agent 工具函数。

核心流程：
- preview_import：上传落盘（扩展名白名单 + 10MB 上限 + 随机文件名防路径穿越）→
  自动定位表头行 → 按列签名识别文件类型 → 返回前 50 行预览 + 列映射建议；
- confirm_import：按映射正式入库——凭证级借贷平衡校验（不平衡记 warning 不阻断）、
  重复凭证（核算账簿+凭证号）跳过并计数、批次留档（tmp → inbox）；
- 查询：凭证组合筛选分页 / 凭证分录明细 / 分录按科目前缀筛选 / 科目发生额汇总；
- export_vouchers_to_vault：与 finance_vault 数据仓的可选联动，模块缺失或禁用时抛
  VaultUnavailableError（路由层转 503 中文提示），绝不崩溃。

行数防护：Excel 解析后按共享上限 MAX_IMPORT_ROWS（默认 200,000 行）做可检测溢出检查，
超限在预览/确认入口即抛 BipRowLimitExceeded（ValueError 子类，路由层映射 400），
不写库、也不在 tmp 留下被拒上传（无半成品）；其余解析异常对外只给通用中文提示，
完整异常进服务器日志，不回带本机异常路径（审计台账 A09）。
"""
from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...config import get_finance_data_dir
from ...database import SessionLocal
from ...utils import escape_like
from .models import BipBalance, BipBatch, BipEntry, BipVoucher

try:
    # 单次导入行数上限由 core 任务提供（app/import_limits.py）；缺失时失败关闭，
    # 绝不放行无上限导入（与 assistant.py 聊天 Excel 转换同口径）。
    from ...import_limits import MAX_IMPORT_ROWS
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    MAX_IMPORT_ROWS = None

logger = logging.getLogger(__name__)

MODULE_NAME = "finance_ledger_bip"
ALLOWED_SUFFIXES = (".xlsx", ".xls")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 与宿主附件上传口径一致
PREVIEW_ROWS = 50
MAX_WARNINGS = 50  # 批次 warnings 最多留存条数，超出汇总为一条
TMP_RETENTION = timedelta(hours=24)  # tmp 预览文件保留时长

TYPE_VOUCHER = "voucher"   # 凭证列表
TYPE_AUX = "aux"           # 辅助明细账
TYPE_BALANCE = "balance"   # 科目余额表
TYPE_UNKNOWN = "unknown"

TYPE_LABELS = {
    TYPE_VOUCHER: "凭证列表",
    TYPE_AUX: "辅助明细账",
    TYPE_BALANCE: "科目余额表",
    TYPE_UNKNOWN: "未识别",
}

# 规范字段中文名（用于报错提示与前端映射表）
FIELD_LABELS = {
    "date": "日期",
    "ledger": "核算账簿",
    "voucher_no": "凭证号",
    "voucher_full_no": "会计凭证号",
    "summary": "摘要",
    "debit": "借方金额",
    "credit": "贷方金额",
    "subject_code": "科目编码",
    "subject_name": "科目名称",
    "opening_debit": "期初借方",
    "opening_credit": "期初贷方",
    "period_debit": "本期借方",
    "period_credit": "本期贷方",
    "closing_debit": "期末借方",
    "closing_credit": "期末贷方",
}

# 各类型确认入库前必须完成映射的字段
REQUIRED_FIELDS = {
    TYPE_VOUCHER: ["date", "ledger", "voucher_no"],
    TYPE_AUX: ["subject_code", "subject_name", "date", "ledger", "voucher_no"],
    TYPE_BALANCE: ["subject_code", "subject_name"],
}

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}\.(xlsx|xls)$")

# 表头行自动定位的关键词（兼容 BIP 导出顶部带标题/说明行）
_HEADER_EXACT = {
    "日期", "凭证日期", "制单日期", "核算账簿", "账簿", "凭证号", "会计凭证号", "凭证编号",
    "摘要", "科目编码", "科目代码", "科目名称", "借方金额", "贷方金额", "借方本币", "贷方本币",
}
_HEADER_FUZZY = ("凭证号", "科目", "摘要", "账簿", "借方", "贷方", "期初", "期末", "本期", "日期")


class VaultUnavailableError(RuntimeError):
    """数据仓模块 finance_vault 不可用（未安装 / 未启用 / 导入失败）。"""


class BipRowLimitExceeded(ValueError):
    """导入数据行数超过共享上限 MAX_IMPORT_ROWS：解析阶段即中止，无半成品。"""


# ---------------- 基础工具 ----------------

def module_data_dir() -> Path:
    """模块数据目录：<finance_data_dir>/finance_ledger_bip（inbox/outbox/tmp 三级，懒创建）。"""
    base = get_finance_data_dir() / MODULE_NAME
    for sub in ("inbox", "outbox", "tmp"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def _sub_dir(name: str) -> Path:
    """取模块数据子目录并确保存在（单独建目录，便于测试整体重定向 module_data_dir）。"""
    d = module_data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm_col(c: Any) -> str:
    """列名归一化：全角空格转半角、去首尾空白。"""
    return str(c).replace("　", " ").strip()


def _uniquify(cols: list[str]) -> list[str]:
    """列名去重（归一化后撞名时追加 .1 / .2 后缀，与 pandas 惯例一致）。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in cols:
        n = seen.get(c, 0)
        seen[c] = n + 1
        out.append(c if n == 0 else f"{c}.{n}")
    return out


def _text(v: Any) -> str:
    """单元格转字符串：空值归 ""；整数值浮点（如凭证号 32.0）去小数位。"""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _r2(x: Any) -> float:
    """保留两位小数。"""
    return round(float(x), 2)


def _to_float(v: Any) -> float:
    """金额解析：支持千分位逗号、全角逗号、括号负数；空值/无法识别归 0。"""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return 0.0
        return _r2(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s or s.lower() in ("nan", "none"):
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    if s.startswith("-"):
        neg, s = True, s[1:].strip()
    try:
        val = float(s)
    except ValueError:
        return 0.0
    return _r2(-val if neg else val)


def _to_date_text(v: Any) -> str:
    """日期归一化为 YYYY-MM-DD；支持 Timestamp / 字符串 / Excel 序列号，识别不出返回 ""。"""
    if v is None:
        return ""
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        f = float(v)
        if math.isnan(f) or not 1 <= f <= 100000:
            return ""
        try:
            return (pd.Timestamp("1899-12-30") + pd.to_timedelta(f, unit="D")).strftime("%Y-%m-%d")
        except (OverflowError, ValueError):
            return ""
    s = str(v).strip()
    if not s:
        return ""
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return ""
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _cell(v: Any) -> Any:
    """预览单元格 JSON 化：空值归 ""，日期取 ISO 前 10 位，其余原样。"""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    return v


# ---------------- 表头定位与文件类型识别 ----------------

def _locate_header_row(raw: pd.DataFrame) -> int:
    """在前 25 行里定位表头行：命中关键词类别最多且 >=2 类的行。

    评分用「不同关键词类别数」而非总命中数：
    - 用友元数据块可达 16 行，其中「对方科目/显示对方科目」一行即 2 个「科目」命中；
    - 余额表是两级表头：子表头行（借方本币×4/贷方本币×4）总命中更高，但只涉及
      借/贷 2 类；主表头行（科目/账簿/期初/本期/期末）覆盖 5 类，才是真表头。
    """
    best, best_score = 0, (0, 0)
    for i in range(min(25, len(raw))):
        cats: set[str] = set()
        hits = 0
        for v in raw.iloc[i].tolist():
            s = _norm_col(v)
            if not s or s.lower() == "nan":
                continue
            if s in _HEADER_EXACT:
                cats.add(s)
                hits += 1
            else:
                for h in _HEADER_FUZZY:
                    if h in s:
                        cats.add(h)
                        hits += 1
                        break
        if len(cats) >= 2 and (len(cats), hits) > best_score:
            best, best_score = i, (len(cats), hits)
    return best


# 多 sheet 上传（用友凭证列表按账簿分 sheet）时，以 sheet 名合成的账簿列名；
# 用英文名避免命中表头关键词（_locate_header_row 评分），也不与真实列名冲突。
SHEET_LEDGER_COL = "__sheet_ledger__"


def _read_one_sheet(raw: pd.DataFrame) -> pd.DataFrame:
    """单 sheet：定位表头行 → 归一化列名 → 剔空行空列 → 返回数据框。"""
    if raw.empty:
        return pd.DataFrame()
    hdr = _locate_header_row(raw)
    df = raw.iloc[hdr + 1:].copy()
    df.columns = _uniquify([_norm_col(c) for c in raw.iloc[hdr].tolist()])
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    keep = [c for c in df.columns if c and c.lower() != "nan" and not c.lower().startswith("unnamed")]
    return df[keep].reset_index(drop=True)


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


def detect_file_type(columns: list[str]) -> str:
    """按列签名识别文件类型（列签名为 BIP 实测契约）：
    - aux：有科目列 + 日期 + 凭证号；判别特征为「对方科目」列（辅助明细账独有）；
    - voucher：日期 + 凭证号，且带「分录摘要/凭证摘要」或制单/审批/记账人列
      （凭证列表独有——用友凭证列表导出按账簿分 sheet，没有核算账簿列，
      旧契约要求账簿列导致真实导出落入 aux，见外部验证第 3 章 B1）；
    - balance：有科目列但无日期/凭证号，且出现期初/本期/期末；
    - 其余 → unknown（进入人工列映射）。
    """
    cols = list(columns)
    has_subject = any("科目" in c for c in cols)
    has_date = any("日期" in c for c in cols)
    has_vno = "凭证号" in cols
    has_ledger = any("账簿" in c for c in cols)
    has_period_words = any(("期初" in c or "期末" in c or "本期" in c) for c in cols)
    has_counter = any("对方科目" in c for c in cols)  # 辅助明细账独有
    has_entry_summary = any(c in ("分录摘要", "凭证摘要") for c in cols)  # 凭证列表独有
    has_maker = any(c in ("制单人", "审批人", "记账人") for c in cols)  # 凭证列表独有
    if has_date and has_vno:
        if has_entry_summary or has_maker:
            return TYPE_VOUCHER  # 分录摘要/制单人是凭证列表独有列（最可靠判别）
        if has_counter:
            return TYPE_AUX  # 对方科目是辅助明细账独有列
        if has_subject:
            return TYPE_AUX  # 保守回退：旧契约形状（科目+日期+凭证号）
        if has_ledger:
            return TYPE_VOUCHER  # 旧契约凭证列表形状（账簿列+无科目列）
    if has_subject and not has_date and not has_vno and has_period_words:
        return TYPE_BALANCE
    return TYPE_UNKNOWN


def _match_col(
    columns: list[str],
    *,
    exact: tuple[str, ...] = (),
    all_of: tuple[str, ...] = (),
    any_of: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> str | None:
    """在列清单中找第一个满足条件的列名：exact 全等优先，其次关键词组合包含匹配。"""
    for c in columns:
        if c in exact:
            return c
    for c in columns:
        if all(k in c for k in all_of) and (not any_of or any(k in c for k in any_of)):
            if not any(k in c for k in exclude):
                return c
    return None


# 借贷方向列不是金额列，匹配金额列时排除
_DIRECTION_EXCLUDE = ("方向",)


def suggest_mapping(file_type: str, columns: list[str]) -> dict[str, str]:
    """按文件类型给出列映射建议 {规范字段: 实际列名}；识别不了的字段留待人工映射。"""
    cols = list(columns)
    m: dict[str, str] = {}

    def put(field: str, col: str | None) -> None:
        if col:
            m[field] = col

    if file_type in (TYPE_VOUCHER, TYPE_AUX):
        put("date", _match_col(cols, exact=("日期", "凭证日期", "制单日期"), all_of=("日期",)))
        put("ledger", _match_col(cols, exact=("核算账簿", "账簿"), all_of=("账簿",)))
        if file_type == TYPE_VOUCHER and "ledger" not in m and SHEET_LEDGER_COL in cols:
            # 用友凭证列表按账簿分 sheet、无核算账簿列：账簿取 sheet 名（合成列）
            m["ledger"] = SHEET_LEDGER_COL
        put("voucher_no", _match_col(cols, exact=("凭证号",), all_of=("凭证号",), exclude=("会计",)))
        put("voucher_full_no", _match_col(cols, exact=("会计凭证号", "凭证编号"), all_of=("会计", "凭证")))
        put("summary", _match_col(cols, exact=("摘要",), all_of=("摘要",)))
        put("debit", _match_col(
            cols, exact=("借方金额", "借方本币", "本币借方", "借方"),
            all_of=("借",), exclude=_DIRECTION_EXCLUDE + ("贷",)))
        put("credit", _match_col(
            cols, exact=("贷方金额", "贷方本币", "本币贷方", "贷方"),
            all_of=("贷",), exclude=_DIRECTION_EXCLUDE + ("借",)))
    if file_type in (TYPE_AUX, TYPE_BALANCE):
        put("subject_code", _match_col(cols, exact=("科目编码", "科目代码"), all_of=("科目",), any_of=("编码", "代码")))
        put("subject_name", _match_col(cols, exact=("科目名称",), all_of=("科目", "名称")))
    if file_type == TYPE_BALANCE:
        # 宽松匹配 期初/本期/期末 × 借/贷 常见变体（如 期初借方余额 / 本期借方发生额）
        put("opening_debit", _match_col(cols, all_of=("期初", "借")))
        put("opening_credit", _match_col(cols, all_of=("期初", "贷")))
        put("period_debit", _match_col(cols, all_of=("借",), any_of=("本期", "发生"), exclude=("期初", "期末")))
        put("period_credit", _match_col(cols, all_of=("贷",), any_of=("本期", "发生"), exclude=("期初", "期末")))
        put("closing_debit", _match_col(cols, all_of=("期末", "借")))
        put("closing_credit", _match_col(cols, all_of=("期末", "贷")))
    return m


# ---------------- 上传与预览 ----------------

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


def _import_voucher_like(
    db: Session, batch: BipBatch, df: pd.DataFrame, mapping: dict[str, str], file_type: str
) -> dict[str, Any]:
    """凭证列表 / 辅助明细账入库：按（核算账簿, 凭证号）聚合分录，
    凭证级借贷平衡校验（不平衡记 warning 不阻断），重复凭证跳过并计数。"""
    date_c, ledger_c, vno_c = mapping["date"], mapping["ledger"], mapping["voucher_no"]
    summary_c = mapping.get("summary")
    debit_c, credit_c = mapping.get("debit"), mapping.get("credit")
    full_c = mapping.get("voucher_full_no")
    scode_c, sname_c = mapping.get("subject_code"), mapping.get("subject_name")

    if file_type == TYPE_VOUCHER:
        # 用友凭证列表的续行不重复凭证日期/凭证号（仅首行填写）：按账簿分组前向填充，
        # 否则续行分录会被当作无效行跳过（外部验证第 3 章 B1 缺口修复的一部分）
        df = df.copy()
        df[[date_c, vno_c]] = df.groupby(ledger_c, dropna=False)[[date_c, vno_c]].ffill()
        if full_c:
            df[full_c] = df.groupby(ledger_c, dropna=False)[full_c].ffill()

    existing = {(l, n) for l, n in db.query(BipVoucher.ledger, BipVoucher.voucher_no).all()}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    skipped_rows = 0
    for _, row in df.iterrows():
        ledger, vno = _text(row[ledger_c]), _text(row[vno_c])
        dtext = _to_date_text(row[date_c])
        if not ledger or not vno or not dtext:
            skipped_rows += 1
            continue
        key = (ledger, vno)
        g = groups.get(key)
        if g is None:
            g = {"date": dtext, "full_no": "", "entries": []}
            groups[key] = g
            order.append(key)
        if full_c and not g["full_no"]:
            g["full_no"] = _text(row[full_c])
        g["entries"].append({
            "summary": _text(row[summary_c]) if summary_c else "",
            "subject_code": _text(row[scode_c]) if scode_c else "",
            "subject_name": _text(row[sname_c]) if sname_c else "",
            "debit": _to_float(row[debit_c]) if debit_c else 0.0,
            "credit": _to_float(row[credit_c]) if credit_c else 0.0,
        })

    warnings: list[str] = []
    inserted_v = skipped_v = inserted_e = 0
    for key in order:
        if key in existing:
            skipped_v += 1
            continue
        g = groups[key]
        debit_sum = _r2(sum(e["debit"] for e in g["entries"]))
        credit_sum = _r2(sum(e["credit"] for e in g["entries"]))
        if abs(debit_sum - credit_sum) > 0.005:
            warnings.append(
                f"凭证 {key[0]}/{key[1]} 借贷不平衡：借方合计 {debit_sum:.2f}，贷方合计 {credit_sum:.2f}"
            )
        voucher = BipVoucher(
            batch_id=batch.id,
            period=g["date"][:7],
            voucher_date=g["date"],
            ledger=key[0],
            voucher_no=key[1],
            voucher_full_no=g["full_no"],
            summary=g["entries"][0]["summary"] if g["entries"] else "",
        )
        db.add(voucher)
        db.flush()
        for i, e in enumerate(g["entries"], 1):
            db.add(BipEntry(voucher_id=voucher.id, batch_id=batch.id, line_no=i, source_type=file_type, **e))
        inserted_v += 1
        inserted_e += len(g["entries"])
        existing.add(key)

    if len(warnings) > MAX_WARNINGS:
        warnings = warnings[:MAX_WARNINGS] + [f"……其余 {len(warnings) - MAX_WARNINGS} 条不平衡告警从略"]
    return {
        "inserted_vouchers": inserted_v,
        "skipped_vouchers": skipped_v,
        "inserted_entries": inserted_e,
        "skipped_rows": skipped_rows,
        "warnings": warnings,
    }


def _import_balance(
    db: Session, batch: BipBatch, df: pd.DataFrame, mapping: dict[str, str], period: str
) -> dict[str, Any]:
    """科目余额表入库：缺科目编码的行计为无效行跳过；未映射的金额列按 0 处理。"""
    code_c, name_c = mapping["subject_code"], mapping["subject_name"]

    def amount(row: pd.Series, field: str) -> float:
        col = mapping.get(field)
        return _to_float(row[col]) if col else 0.0

    rows: list[BipBalance] = []
    skipped_rows = 0
    for _, row in df.iterrows():
        code = _text(row[code_c])
        if not code:
            skipped_rows += 1
            continue
        rows.append(BipBalance(
            batch_id=batch.id,
            period=period,
            subject_code=code,
            subject_name=_text(row[name_c]),
            opening_debit=amount(row, "opening_debit"),
            opening_credit=amount(row, "opening_credit"),
            period_debit=amount(row, "period_debit"),
            period_credit=amount(row, "period_credit"),
            closing_debit=amount(row, "closing_debit"),
            closing_credit=amount(row, "closing_credit"),
        ))
    db.add_all(rows)
    return {
        "inserted_vouchers": 0,
        "skipped_vouchers": 0,
        "inserted_entries": 0,
        "inserted_balances": len(rows),
        "skipped_rows": skipped_rows,
        "warnings": [],
    }


def confirm_import(
    db: Session,
    *,
    token: str,
    file_type: str | None = None,
    user_mapping: dict[str, str] | None = None,
    period: str | None = None,
) -> dict:
    """确认入库：解析预览文件 → 合并/校验列映射 → 写库并留档批次与原文件。"""
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

    batch = BipBatch(
        filename=orig_name,
        file_type=eff_type,
        period=(period or "") if eff_type == TYPE_BALANCE else "",
        total_rows=int(len(df)),
    )
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
    batch.skipped_rows = stats["skipped_rows"]
    batch.warnings = json.dumps(stats["warnings"], ensure_ascii=False)

    # 原文件留档：tmp → inbox/<批次号>_<原文件名>（失败不阻断入库）
    try:
        dest = _sub_dir("inbox") / f"{batch.id:06d}_{Path(orig_name).name}"
        dest.write_bytes(path.read_bytes())
        batch.stored_path = str(dest)
        path.unlink()
        name_file.unlink(missing_ok=True)
    except OSError:
        logger.warning("批次 %s 原文件留档失败", batch.id, exc_info=True)

    db.commit()

    if eff_type == TYPE_BALANCE:
        message = f"导入完成：余额行 {batch.inserted_balances} 条（无效行 {batch.skipped_rows} 条）"
    else:
        message = (
            f"导入完成：新增凭证 {batch.inserted_vouchers} 张、分录 {batch.inserted_entries} 条；"
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
        "skipped_rows": batch.skipped_rows,
        "warnings": stats["warnings"],
        "message": message,
    }


# ---------------- 批次与查询 ----------------

def _batch_to_dict(b: BipBatch) -> dict:
    """批次 ORM → 字典（warnings 由 JSON 文本还原为数组）。"""
    try:
        warnings = json.loads(b.warnings or "[]")
    except (TypeError, ValueError):
        warnings = []
    return {
        "id": b.id,
        "filename": b.filename,
        "file_type": b.file_type,
        "type_label": TYPE_LABELS.get(b.file_type, b.file_type),
        "period": b.period,
        "total_rows": b.total_rows,
        "inserted_vouchers": b.inserted_vouchers,
        "skipped_vouchers": b.skipped_vouchers,
        "inserted_entries": b.inserted_entries,
        "inserted_balances": b.inserted_balances,
        "skipped_rows": b.skipped_rows,
        "warnings": warnings,
        "stored_path": b.stored_path,
        "created_at": b.created_at,
    }


def list_batches(db: Session, page: int = 1, page_size: int = 20) -> dict:
    """导入批次列表（按 id 倒序分页）。"""
    page, page_size = max(1, page), max(1, min(page_size, 200))
    q = db.query(BipBatch)
    total = q.count()
    rows = q.order_by(BipBatch.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_batch_to_dict(b) for b in rows]}


def _voucher_to_dict(v: BipVoucher, entry_count: int, debit_total: float, credit_total: float) -> dict:
    """凭证列表行组装（含分录统计与平衡标记）。"""
    debit_total, credit_total = _r2(debit_total), _r2(credit_total)
    return {
        "id": v.id,
        "period": v.period,
        "voucher_date": v.voucher_date,
        "ledger": v.ledger,
        "voucher_no": v.voucher_no,
        "voucher_full_no": v.voucher_full_no,
        "summary": v.summary,
        "entry_count": int(entry_count),
        "debit_total": debit_total,
        "credit_total": credit_total,
        "balanced": abs(debit_total - credit_total) <= 0.005,
    }


def query_vouchers(
    db: Session,
    period: str | None = None,
    ledger: str | None = None,
    voucher_no: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """凭证组合筛选（期间精确 / 账簿模糊 / 凭证号模糊 / 摘要关键词含分录摘要）+ 分页。"""
    page, page_size = max(1, page), max(1, min(page_size, 200))
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
    if ledger:
        q = q.filter(BipVoucher.ledger.contains(ledger, autoescape=True))
    if voucher_no:
        q = q.filter(BipVoucher.voucher_no.contains(voucher_no, autoescape=True))
    if keyword:
        q = q.filter(or_(
            BipVoucher.summary.contains(keyword, autoescape=True),
            BipVoucher.id.in_(
                db.query(BipEntry.voucher_id).filter(BipEntry.summary.contains(keyword, autoescape=True))
            ),
        ))
    total = q.count()
    rows = (
        q.order_by(BipVoucher.voucher_date.desc(), BipVoucher.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_voucher_to_dict(v, c, d, cr) for v, c, d, cr in rows],
    }


def get_voucher_detail(db: Session, voucher_id: int) -> dict:
    """凭证详情：凭证头 + 全部分录（借贷两栏对称）+ 合计与平衡标记。"""
    voucher = db.get(BipVoucher, voucher_id)
    if voucher is None:
        raise LookupError(f"凭证 {voucher_id} 不存在")
    entries = (
        db.query(BipEntry)
        .filter(BipEntry.voucher_id == voucher_id)
        .order_by(BipEntry.line_no, BipEntry.id)
        .all()
    )
    debit_total = _r2(sum(e.debit for e in entries))
    credit_total = _r2(sum(e.credit for e in entries))
    return {
        "id": voucher.id,
        "batch_id": voucher.batch_id,
        "period": voucher.period,
        "voucher_date": voucher.voucher_date,
        "ledger": voucher.ledger,
        "voucher_no": voucher.voucher_no,
        "voucher_full_no": voucher.voucher_full_no,
        "summary": voucher.summary,
        "debit_total": debit_total,
        "credit_total": credit_total,
        "balanced": abs(debit_total - credit_total) <= 0.005,
        "entries": [
            {
                "id": e.id,
                "line_no": e.line_no,
                "summary": e.summary,
                "subject_code": e.subject_code,
                "subject_name": e.subject_name,
                "debit": _r2(e.debit),
                "credit": _r2(e.credit),
                "source_type": e.source_type,
            }
            for e in entries
        ],
    }


def query_entries(
    db: Session,
    subject_prefix: str | None = None,
    period: str | None = None,
    ledger: str | None = None,
    voucher_no: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """分录查询：按科目编码前缀 / 期间 / 账簿 / 凭证号 / 摘要关键词组合筛选 + 分页。"""
    page, page_size = max(1, page), max(1, min(page_size, 200))
    q = db.query(BipEntry, BipVoucher).join(BipVoucher, BipVoucher.id == BipEntry.voucher_id)
    if subject_prefix:
        # 通配符/反斜杠按字面匹配：escape_like 转义 % _ \ 后配 escape="\\"（审计台账 A08）
        q = q.filter(BipEntry.subject_code.like(escape_like(subject_prefix) + "%", escape="\\"))
    if period:
        q = q.filter(BipVoucher.period == period)
    if ledger:
        q = q.filter(BipVoucher.ledger.contains(ledger, autoescape=True))
    if voucher_no:
        q = q.filter(BipVoucher.voucher_no.contains(voucher_no, autoescape=True))
    if keyword:
        q = q.filter(BipEntry.summary.contains(keyword, autoescape=True))
    total = q.count()
    rows = (
        q.order_by(BipVoucher.voucher_date.desc(), BipVoucher.id.desc(), BipEntry.line_no)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": e.id,
                "voucher_id": v.id,
                "period": v.period,
                "voucher_date": v.voucher_date,
                "ledger": v.ledger,
                "voucher_no": v.voucher_no,
                "line_no": e.line_no,
                "summary": e.summary,
                "subject_code": e.subject_code,
                "subject_name": e.subject_name,
                "debit": _r2(e.debit),
                "credit": _r2(e.credit),
            }
            for e, v in rows
        ],
    }


def balance_summary(db: Session, period: str) -> dict:
    """科目发生额汇总：按期间聚合凭证分录（仅含带科目编码的分录，即辅助明细账来源）。"""
    if not PERIOD_RE.fullmatch(period or ""):
        raise ValueError("期间格式应为 YYYY-MM（如 2026-09）")
    rows = (
        db.query(
            BipEntry.subject_code,
            BipEntry.subject_name,
            func.count(BipEntry.id),
            func.coalesce(func.sum(BipEntry.debit), 0.0),
            func.coalesce(func.sum(BipEntry.credit), 0.0),
        )
        .join(BipVoucher, BipVoucher.id == BipEntry.voucher_id)
        .filter(BipVoucher.period == period, BipEntry.subject_code != "")
        .group_by(BipEntry.subject_code, BipEntry.subject_name)
        .order_by(BipEntry.subject_code)
        .all()
    )
    items = [
        {
            "subject_code": code,
            "subject_name": name,
            "entry_count": int(cnt),
            "debit_total": _r2(d),
            "credit_total": _r2(c),
        }
        for code, name, cnt, d, c in rows
    ]
    return {
        "period": period,
        "items": items,
        "subject_count": len(items),
        "debit_total": _r2(sum(i["debit_total"] for i in items)),
        "credit_total": _r2(sum(i["credit_total"] for i in items)),
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


# ---------------- Agent 工具（readonly） ----------------

def bip_voucher_query(
    period: str = "", ledger: str = "", voucher_no: str = "", keyword: str = "", limit: int = 10
) -> dict:
    """Agent 工具（readonly）：组合筛选查询 BIP 凭证列表。"""
    with SessionLocal() as db:
        return query_vouchers(
            db,
            period=period or None,
            ledger=ledger or None,
            voucher_no=voucher_no or None,
            keyword=keyword or None,
            page=1,
            page_size=max(1, min(int(limit or 10), 50)),
        )


def bip_balance_query(period: str = "", subject_prefix: str = "", limit: int = 50) -> dict:
    """Agent 工具（readonly）：按期间汇总科目借贷发生额；期间缺省时取数据中最近期间。"""
    try:
        with SessionLocal() as db:
            if not period:
                period = db.query(func.max(BipVoucher.period)).scalar() or ""
            if not period:
                return {"period": "", "items": [], "subject_count": 0, "debit_total": 0.0, "credit_total": 0.0}
            result = balance_summary(db, period)
    except ValueError as exc:
        return {"error": str(exc), "items": []}
    if subject_prefix:
        items = [i for i in result["items"] if i["subject_code"].startswith(subject_prefix)]
        result = {
            **result,
            "items": items,
            "subject_count": len(items),
            "debit_total": _r2(sum(i["debit_total"] for i in items)),
            "credit_total": _r2(sum(i["credit_total"] for i in items)),
        }
    limit = max(1, min(int(limit or 50), 200))
    result["items"] = result["items"][:limit]
    return result
