"""finance_ledger_bip 共享常量、异常与解析工具（由 service.py 机械拆分）。

三份 BIP 文件（凭证列表 / 辅助明细账 / 科目余额表）共用的常量、异常类型、
单元格归一化（列名 / 文本 / 金额 / 日期）工具，以及表头行定位、文件类型识别与
列映射建议。公共符号统一由 service.py re-export，调用方与 manifest 无需改动。
"""
from __future__ import annotations

import math
import re
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pandas as pd

from ...config import get_finance_data_dir

try:
    # 单次导入行数上限由 core 任务提供（app/import_limits.py）；缺失时失败关闭，
    # 绝不放行无上限导入（与 assistant.py 聊天 Excel 转换同口径）。
    # 常量在此定义、由 service 门面导入：行数检查留在 service，补丁面不变。
    from ...import_limits import MAX_IMPORT_ROWS
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    MAX_IMPORT_ROWS = None

# ---------------- 常量 ----------------

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
    "subject": "科目（编码+名称）",
    "opening_debit": "期初借方",
    "opening_credit": "期初贷方",
    "period_debit": "本期借方",
    "period_credit": "本期贷方",
    "closing_debit": "期末借方",
    "closing_credit": "期末贷方",
    "opening_balance": "期初余额",
    "closing_balance": "期末余额",
}

# 各类型确认入库前必须完成映射的字段
REQUIRED_FIELDS = {
    TYPE_VOUCHER: ["date", "ledger", "voucher_no"],
    TYPE_AUX: ["subject_code", "subject_name", "date", "ledger", "voucher_no"],
    TYPE_BALANCE: ["subject_code", "subject_name"],
}

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}\.(xlsx|xls)$")
COMPACT_DATE_RE = re.compile(r"^\d{8}$")

# 同一进程内的导入串行锁：双击/并发确认时后者等前者提交完再判重，避免重复入库。
# 数据库唯一索引是最终防线（多进程/外部写入仍会被约束拦住）。
_IMPORT_LOCK = threading.Lock()
IMPORT_LOCK_TIMEOUT = 60  # 秒；超时说明另一个导入长时间未结束，明确 409 而不是无限等待

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


class BipImportConflictError(ValueError):
    """并发导入冲突：另一个导入正在进行，或唯一索引命中同凭证写入（路由层映射 409）。"""


def module_data_dir() -> Path:
    """模块数据目录：<finance_data_dir>/finance_ledger_bip（inbox/outbox/tmp 三级，懒创建）。"""
    base = get_finance_data_dir() / MODULE_NAME
    for sub in ("inbox", "outbox", "tmp"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def _norm_col(c: Any) -> str:
    """列名归一化：全角空格转半角、去首尾空白；空单元格/NaN 表头归一为空串。"""
    if c is None:
        return ""
    try:
        if pd.isna(c):
            return ""
    except (TypeError, ValueError):
        pass
    return str(c).replace("　", " ").strip()


def _uniquify(cols: list[str]) -> list[str]:
    """列名去重（归一化后撞名时追加 .1 / .2 后缀，与 pandas 惯例一致）。

    空列名不参与计数也不加后缀：否则两个空表头会被命名成 "nan" 与 "nan.1"，
    在映射界面多出无法使用的空列（台账 L-077）。
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in cols:
        if not c:
            out.append("")
            continue
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


_MONEY = Decimal("0.01")


def _r2(x: Any) -> float:
    """保留两位小数（Decimal ROUND_HALF_UP，避免 round() 的银行家舍入与二进制误差）。"""
    number = Decimal(str(x))
    if not number.is_finite():
        raise ValueError("金额不是有限数值")
    return float(number.quantize(_MONEY, rounding=ROUND_HALF_UP))


def _parse_amount(v: Any) -> float:
    """金额解析：空白单元格按 0；不可解析或非有限值抛 ValueError（调用方跳过该行）。

    不静默归零（台账 L-075）：字符串 "1,2x.3"、"inf"/"Infinity"、float("inf")
    一律拒绝；"nan"/"none" 等伪数字字符串同样拒绝（Excel 空单元格是 float NaN，
    不是字符串）。支持千分位逗号、全角逗号、括号负数与正负号前缀。
    """
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        f = float(v)
        if math.isnan(f):
            return 0.0  # 空单元格（pandas 读 Excel 的空值形态）
        if not math.isfinite(f):
            raise ValueError("金额不是有限数值")
        return _r2(f)
    try:
        if pd.isna(v):
            return 0.0  # pd.NA / NaT 等缺失值
    except (TypeError, ValueError):
        pass
    raw = str(v).strip()
    # 全角括号/正负号/长破折号/斜杠先归一，避免真实导出的占位符被当成负数
    s = (raw.replace("（", "(").replace("）", ")")
         .replace("—", "-").replace("–", "-").replace("−", "-")
         .replace("－", "-").replace("＋", "+").replace("／", "/")
         .replace(",", "").replace("，", "").strip())
    if not s:
        return 0.0
    # "-" / "--" / "—" / "／" 等纯占位符 = 无金额，明确按 0（不能进入符号解析）
    if set(s) <= {"-", "/"}:
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
        if not s or set(s) <= {"-", "/"}:
            return 0.0
    if s.startswith(("-", "+", "/")):
        sign = s[0]
        if sign == "/":
            raise ValueError(f"金额 {raw[:20]!r} 无法解析")
        neg = neg or sign == "-"
        s = s[1:].strip()
    # 只允许一个前导符号：--5 / +-5 / -+5 一律拒绝，绝不把 --5 翻成正 5
    if not s or s[0] in "-+/":
        raise ValueError(f"金额 {raw[:20]!r} 无法解析")
    try:
        val = float(s)
    except ValueError as exc:
        raise ValueError(f"金额 {raw[:20]!r} 无法解析") from exc
    if not math.isfinite(val):
        raise ValueError(f"金额 {raw[:20]!r} 不是有限数值")
    return _r2(-val if neg else val)


def _split_subject(text: str) -> tuple[str, str]:
    """拆分「1001 库存现金」形式的合并科目列：首个空白前为编码，其余为名称。"""
    s = (text or "").strip()
    if not s:
        return "", ""
    parts = s.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _to_date_text(v: Any) -> str:
    """日期归一化为 YYYY-MM-DD；支持 Timestamp / 字符串 / 整数 YYYYMMDD / Excel 序列号。

    整数优先按 %Y%m%d 解析（台账 L-076：20260901 这类整列整数日期以前被当成
    超出 Excel 序列号范围而整列跳过），再退回 1..100000 的 Excel 序列号；
    识别不出返回 ""。
    """
    if v is None:
        return ""
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        f = float(v)
        if not math.isfinite(f):
            return ""
        i = int(f)
        if f.is_integer() and 10000101 <= i <= 99991231:
            try:
                return datetime.strptime(str(i), "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                return ""
        if 1 <= f <= 100000:
            try:
                return (pd.Timestamp("1899-12-30") + pd.to_timedelta(f, unit="D")).strftime("%Y-%m-%d")
            except (OverflowError, ValueError):
                return ""
        return ""
    s = str(v).strip()
    if not s:
        return ""
    if COMPACT_DATE_RE.fullmatch(s):
        try:
            return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
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
    """单 sheet：定位表头行 → 归一化列名 → 剔空行空列 → 返回数据框。

    空表头先归一为空串再去重命名（_uniquify 不给空名加 .1 后缀），最后统一剔除
    空名 / Unnamed 列——映射界面不再出现 "nan.1" 之类的空列（台账 L-077）。
    """
    if raw.empty:
        return pd.DataFrame()
    hdr = _locate_header_row(raw)
    df = raw.iloc[hdr + 1:].copy()
    df.columns = _uniquify([_norm_col(c) for c in raw.iloc[hdr].tolist()])
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    keep = [c for c in df.columns if c and c.lower() != "nan" and not c.lower().startswith("unnamed")]
    return df[keep].reset_index(drop=True)


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
        # 科目列：凭证列表的真实导出也带科目（模板为「科目编码/科目名称」两列，
        # 部分账簿导出是单列「1001 库存现金」）——映射后仅导凭证列表也能按科目汇总
        # （台账 L-071）。对方科目不属于本行科目，必须排除。
        put("subject_code", _match_col(
            cols, exact=("科目编码", "科目代码"), all_of=("科目",),
            any_of=("编码", "代码"), exclude=("对方",)))
        put("subject_name", _match_col(
            cols, exact=("科目名称",), all_of=("科目", "名称"), exclude=("对方",)))
        if "subject_code" not in m:
            put("subject", _match_col(
                cols, exact=("科目",), all_of=("科目",),
                exclude=("对方", "编码", "代码", "名称")))
    if file_type == TYPE_BALANCE:
        put("subject_code", _match_col(cols, exact=("科目编码", "科目代码"), all_of=("科目",), any_of=("编码", "代码")))
        put("subject_name", _match_col(cols, exact=("科目名称",), all_of=("科目", "名称")))
        # 核算账簿：同一期间同科目在不同账簿可并存（唯一键含账簿）
        put("ledger", _match_col(cols, exact=("核算账簿", "账簿"), all_of=("账簿",)))
        if "ledger" not in m and SHEET_LEDGER_COL in cols:
            # 多 sheet 余额表按账簿分 sheet、无账簿列：账簿取 sheet 名（合成列）
            m["ledger"] = SHEET_LEDGER_COL
        # 方向无关的「期初余额 / 期末余额」（模板形状）；借/贷明细列优先于它
        put("opening_balance", _match_col(
            cols, exact=("期初余额",), all_of=("期初",), exclude=("借", "贷", "方向")))
        put("closing_balance", _match_col(
            cols, exact=("期末余额",), all_of=("期末",), exclude=("借", "贷", "方向")))
    if file_type == TYPE_BALANCE:
        # 宽松匹配 期初/本期/期末 × 借/贷 常见变体（如 期初借方余额 / 本期借方发生额）
        put("opening_debit", _match_col(cols, all_of=("期初", "借")))
        put("opening_credit", _match_col(cols, all_of=("期初", "贷")))
        put("period_debit", _match_col(cols, all_of=("借",), any_of=("本期", "发生"), exclude=("期初", "期末")))
        put("period_credit", _match_col(cols, all_of=("贷",), any_of=("本期", "发生"), exclude=("期初", "期末")))
        put("closing_debit", _match_col(cols, all_of=("期末", "借")))
        put("closing_credit", _match_col(cols, all_of=("期末", "贷")))
    return m
