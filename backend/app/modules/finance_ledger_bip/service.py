"""finance_ledger_bip 模块业务逻辑与 Agent 工具函数。

核心流程：
- preview_import：上传落盘（扩展名白名单 + 10MB 上限 + 随机文件名防路径穿越）→
  自动定位表头行 → 按列签名识别文件类型 → 返回前 50 行预览 + 列映射建议；
- confirm_import：按映射正式入库——凭证级借贷平衡校验（不平衡记 warning 不阻断）；
  凭证按（核算账簿, 期间, 凭证号）唯一，同来源重导幂等跳过，跨来源（凭证列表 /
  辅助明细账）按金额签名合并补写科目与摘要、不重复落金额（失败/歧义保留可重试）；
  余额表按（核算账簿, 期间, 科目）upsert，只更新本次映射字段；全行无效返回中文 400；
  先提交事务、后留档批次与原文件；
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
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, OperationalError
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


def _r2(x: Any) -> float:
    """保留两位小数。"""
    return round(float(x), 2)


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


def _voucher_sources(voucher: BipVoucher) -> set[str]:
    """凭证已合并的来源类型：优先读 sources 列；旧数据按分录 source_type 反推。"""
    raw = (voucher.sources or "").strip()
    if raw:
        return {s.strip() for s in raw.split(",") if s.strip()}
    return {e.source_type for e in voucher.entries if e.source_type}


def _entry_from_row(
    row: pd.Series,
    *,
    summary_c: str | None,
    scode_c: str | None,
    sname_c: str | None,
    subject_c: str | None,
    debit_c: str | None,
    credit_c: str | None,
) -> dict[str, Any]:
    """单行 → 分录字段；金额不可解析/非有限值时抛 ValueError（调用方跳过该行）。"""
    code = _text(row[scode_c]) if scode_c else ""
    name = _text(row[sname_c]) if sname_c else ""
    if not code and subject_c:
        code, combined_name = _split_subject(_text(row[subject_c]))
        name = name or combined_name
    return {
        "summary": _text(row[summary_c]) if summary_c else "",
        "subject_code": code,
        "subject_name": name,
        "debit": _parse_amount(row[debit_c]) if debit_c else 0.0,
        "credit": _parse_amount(row[credit_c]) if credit_c else 0.0,
    }


def _pick_merge_target(
    candidates: list[BipEntry], src: dict[str, Any]
) -> tuple[BipEntry | None, str]:
    """同金额候选里选合并目标：唯一空科目 → 命中；多空科目按摘要消歧；
    仍多个 → ambiguous（不按顺序硬配）。返回 (target, state)，state:
    hit / filled（候选都已有科目）/ ambiguous / none（无同金额候选）。
    """
    if not candidates:
        return None, "none"
    empty = [e for e in candidates if not (e.subject_code or "").strip()]
    if not empty:
        return None, "filled"
    if len(empty) == 1:
        return empty[0], "hit"
    summary = (src.get("summary") or "").strip()
    if summary:
        same = [e for e in empty if (e.summary or "").strip() == summary]
        if len(same) == 1:
            return same[0], "hit"
    return None, "ambiguous"


def _merge_entries(
    db: Session,
    batch: BipBatch,
    voucher: BipVoucher,
    group: dict[str, Any],
    file_type: str,
    warnings: list[str],
) -> tuple[int, int, int, int]:
    """把新来源的分录合并进已有凭证：只补空缺，不新增金额行（台账 L-070）。

    按（借, 贷）金额签名匹配已有分录：同金额且科目为空的行补写科目/摘要；
    同金额多候选时用摘要二次匹配，仍歧义则记 ambiguous 并告警，绝不按顺序硬配；
    科目已存在则保留原值（编码不一致记冲突告警）；金额对不上记未匹配告警。
    无论哪种情况都不追加新金额行，因此同一凭证不会因多来源导入而翻倍。

    返回（补写分录数（按分录去重）, 未匹配分录数, 歧义分录数, 科目冲突数）。
    """
    entries = list(voucher.entries)
    if not entries:
        for i, src in enumerate(group["entries"], 1):
            db.add(BipEntry(voucher_id=voucher.id, batch_id=batch.id, line_no=i,
                            source_type=file_type, **src))
        return len(group["entries"]), 0, 0, 0

    pool: dict[tuple[float, float], list[BipEntry]] = {}
    for e in entries:
        pool.setdefault((_r2(e.debit), _r2(e.credit)), []).append(e)

    changed_ids: set[int] = set()
    unmatched = ambiguous = conflicts = 0
    for src in group["entries"]:
        sig = (_r2(src["debit"]), _r2(src["credit"]))
        candidates = pool.get(sig) or []
        target, state = _pick_merge_target(candidates, src)
        if state == "none":
            unmatched += 1
            continue
        if state == "ambiguous":
            ambiguous += 1
            continue
        if state == "filled":
            # 同金额分录已带科目：编码不同只告警不覆盖，编码相同仅补空名称
            if src["subject_code"] and all(
                (e.subject_code or "").strip() != src["subject_code"] for e in candidates
            ):
                conflicts += 1
            elif src["subject_name"]:
                for e in candidates:
                    if (e.subject_code or "").strip() == src["subject_code"] and not (e.subject_name or "").strip():
                        e.subject_name = src["subject_name"]
                        changed_ids.add(id(e))
                        break
            if len(candidates) == 1 and not (candidates[0].summary or "").strip() and src["summary"]:
                candidates[0].summary = src["summary"]
                changed_ids.add(id(candidates[0]))
            continue
        # state == "hit"：唯一（或摘要消歧后唯一）的空科目候选
        if target is None:
            continue
        candidates.remove(target)
        if src["subject_code"]:
            target.subject_code = src["subject_code"]
            target.subject_name = src["subject_name"]
            changed_ids.add(id(target))
        if not (target.summary or "").strip() and src["summary"]:
            target.summary = src["summary"]
            changed_ids.add(id(target))

    if not voucher.voucher_full_no and group["full_no"]:
        voucher.voucher_full_no = group["full_no"]
    if group["entries"] and not (voucher.summary or "").strip():
        voucher.summary = group["entries"][0]["summary"]
    label = TYPE_LABELS.get(file_type, file_type)
    if unmatched:
        warnings.append(
            f"凭证 {voucher.ledger}/{voucher.period}/{voucher.voucher_no} 有 {unmatched} 条"
            f"{label}分录金额与已入库凭证不匹配，未合并（未新增金额行；"
            "该来源未标记为已合并，可用更正后的文件重试）"
        )
    if ambiguous:
        warnings.append(
            f"凭证 {voucher.ledger}/{voucher.period}/{voucher.voucher_no} 有 {ambiguous} 条"
            f"{label}分录同金额存在多个候选科目且摘要无法区分，未合并"
            f"（ambiguous_entries={ambiguous}；该来源保留可重试）"
        )
    if conflicts:
        warnings.append(
            f"凭证 {voucher.ledger}/{voucher.period}/{voucher.voucher_no} 有 {conflicts} 条"
            f"分录科目与已入库数据不一致，保留原值（未覆盖；该来源保留可重试）"
        )
    return len(changed_ids), unmatched, ambiguous, conflicts


def _import_voucher_like(
    db: Session, batch: BipBatch, df: pd.DataFrame, mapping: dict[str, str], file_type: str
) -> dict[str, Any]:
    """凭证列表 / 辅助明细账入库：按（核算账簿, 期间, 凭证号）聚合分录。

    - 新建凭证：借贷平衡校验（不平衡记 warning 不阻断）；
    - 已存在且来源不同：按金额签名合并补写科目/摘要，不新增重复凭证或金额行；
      同金额多候选先按摘要消歧，仍歧义计入 ambiguous_entries 并告警；
    - 已存在且来源相同：幂等跳过并计数（同文件重导不翻倍）；
    - 合并有未匹配/歧义/科目冲突时不把该来源记为已合并，可用更正文件重试补齐。
    """
    date_c, ledger_c, vno_c = mapping["date"], mapping["ledger"], mapping["voucher_no"]
    summary_c = mapping.get("summary")
    debit_c, credit_c = mapping.get("debit"), mapping.get("credit")
    full_c = mapping.get("voucher_full_no")
    scode_c, sname_c = mapping.get("subject_code"), mapping.get("subject_name")
    subject_c = mapping.get("subject")

    if file_type == TYPE_VOUCHER:
        # 用友凭证列表的续行不重复凭证日期/凭证号（仅首行填写）：按账簿分组前向填充，
        # 否则续行分录会被当作无效行跳过（外部验证第 3 章 B1 缺口修复的一部分）。
        # 核算账簿列本身也可能在续行为空：先对账簿 ffill，再按账簿分组填日期/凭证号。
        df = df.copy()
        df[ledger_c] = df[ledger_c].ffill()
        df[[date_c, vno_c]] = df.groupby(ledger_c, dropna=False)[[date_c, vno_c]].ffill()
        if full_c:
            df[full_c] = df.groupby(ledger_c, dropna=False)[full_c].ffill()

    # 判重/聚合键必须含期间：真实 BIP 导出是多个月份，凭证号在各月重复
    # （如 1–10 月都有「记-1」）。只用（账簿, 凭证号）会把十个月合成一张凭证，
    # 且下月文件重导时被误判为重复而静默丢弃（BUGfix BIP-01）。
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    warnings: list[str] = []
    skipped_rows = invalid_amount_rows = 0
    for idx, row in df.iterrows():
        ledger, vno = _text(row[ledger_c]), _text(row[vno_c])
        dtext = _to_date_text(row[date_c])
        if not ledger or not vno or not dtext:
            skipped_rows += 1
            continue
        try:
            entry = _entry_from_row(
                row, summary_c=summary_c, scode_c=scode_c, sname_c=sname_c,
                subject_c=subject_c, debit_c=debit_c, credit_c=credit_c,
            )
        except ValueError as exc:
            skipped_rows += 1
            invalid_amount_rows += 1
            warnings.append(f"数据第 {idx + 1} 行（凭证 {vno}）{exc}，已跳过该行")
            continue
        key = (ledger, dtext[:7], vno)
        g = groups.get(key)
        if g is None:
            g = {"date": dtext, "full_no": "", "entries": []}
            groups[key] = g
            order.append(key)
        if full_c and not g["full_no"]:
            g["full_no"] = _text(row[full_c])
        g["entries"].append(entry)

    existing: dict[tuple[str, str, str], BipVoucher] = {}
    periods = {key[1] for key in groups}
    if periods:
        for v in db.query(BipVoucher).filter(BipVoucher.period.in_(periods)).all():
            existing[(v.ledger, v.period, v.voucher_no)] = v

    inserted_v = skipped_v = inserted_e = merged_v = merged_e = ambiguous_e = 0
    for key in order:
        g = groups[key]
        voucher = existing.get(key)
        if voucher is not None:
            if file_type in _voucher_sources(voucher):
                skipped_v += 1
                continue
            changed, unmatched, ambiguous, conflicts = _merge_entries(
                db, batch, voucher, g, file_type, warnings)
            merged_v += 1
            merged_e += changed
            ambiguous_e += ambiguous
            if unmatched == 0 and ambiguous == 0 and conflicts == 0:
                # 只有全部候选都被现有数据覆盖（含零改动）才把来源记为已合并；
                # 匹配失败/歧义/冲突保留未标记，允许用更正后的文件重试补齐。
                voucher.sources = ",".join(sorted(_voucher_sources(voucher) | {file_type}))
            continue
        debit_sum = _r2(sum(e["debit"] for e in g["entries"]))
        credit_sum = _r2(sum(e["credit"] for e in g["entries"]))
        if abs(debit_sum - credit_sum) > 0.005:
            warnings.append(
                f"凭证 {key[0]}/{key[1]}/{key[2]} 借贷不平衡："
                f"借方合计 {debit_sum:.2f}，贷方合计 {credit_sum:.2f}"
            )
        voucher = BipVoucher(
            batch_id=batch.id,
            period=key[1],
            voucher_date=g["date"],
            ledger=key[0],
            voucher_no=key[2],
            voucher_full_no=g["full_no"],
            summary=g["entries"][0]["summary"] if g["entries"] else "",
            sources=file_type,
        )
        db.add(voucher)
        db.flush()
        for i, e in enumerate(g["entries"], 1):
            db.add(BipEntry(voucher_id=voucher.id, batch_id=batch.id, line_no=i,
                            source_type=file_type, **e))
        inserted_v += 1
        inserted_e += len(g["entries"])
        existing[key] = voucher

    if len(warnings) > MAX_WARNINGS:
        warnings = warnings[:MAX_WARNINGS] + [f"……其余 {len(warnings) - MAX_WARNINGS} 条告警从略"]
    return {
        "inserted_vouchers": inserted_v,
        "skipped_vouchers": skipped_v,
        "inserted_entries": inserted_e,
        "merged_vouchers": merged_v,
        "merged_entries": merged_e,
        "ambiguous_entries": ambiguous_e,
        "skipped_rows": skipped_rows,
        "invalid_amount_rows": invalid_amount_rows,
        "warnings": warnings,
    }


def _import_balance(
    db: Session, batch: BipBatch, df: pd.DataFrame, mapping: dict[str, str], period: str
) -> dict[str, Any]:
    """科目余额表入库：按（核算账簿, 期间, 科目编码）upsert（台账 L-072）。

    - 同键已存在 → 只更新本次**已映射**的字段（未映射字段保留旧值并告知），
      内容完全相同则计入 unchanged_balances，不谎报 updated；
    - 跨账簿/跨月各自独立（键含账簿与期间）；旧空账簿历史行在可唯一确定时就地升级；
    - 文件内同键重复 → 按最后一行取值并告警；
    - 缺科目编码 / 金额不可解析 / 非有限值的行跳过并计入无效行，不静默归零。
    """
    code_c, name_c = mapping["subject_code"], mapping["subject_name"]
    ledger_c = mapping.get("ledger")
    amount_fields = (
        "opening_debit", "opening_credit", "period_debit", "period_credit",
        "closing_debit", "closing_credit", "opening_balance", "closing_balance",
    )
    mapped_fields = [f for f in amount_fields if mapping.get(f)]
    if ledger_c:
        # BIP 余额表续行的核算账簿单元格常为空：沿用上一行，避免同科目被误判为
        # 「空账簿行」，或被另一账簿的旧行错误升级/覆盖（复核 C-1 残余）。
        df = df.copy()
        df[ledger_c] = df[ledger_c].map(lambda v: _text(v) or None).ffill()

    payloads: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    skipped_rows = invalid_amount_rows = 0
    for idx, row in df.iterrows():
        code = _text(row[code_c])
        ledger = _text(row[ledger_c]) if ledger_c else ""
        if not code:
            skipped_rows += 1
            continue
        try:
            amounts = {field: _parse_amount(row[mapping[field]]) for field in mapped_fields}
        except ValueError as exc:
            skipped_rows += 1
            invalid_amount_rows += 1
            warnings.append(f"数据第 {idx + 1} 行（科目 {code}）{exc}，已跳过该行")
            continue
        key = (ledger, code)
        if key in payloads:
            warnings.append(
                f"账簿 {ledger or '（空）'} / 科目 {code} 在文件内重复出现，按最后一行数据入库"
            )
        payloads[key] = {"subject_name": _text(row[name_c]), **amounts}

    existing: dict[tuple[str, str], BipBalance] = {}
    legacy_by_code: dict[str, BipBalance] = {}
    duplicate_rows = 0
    if payloads:
        codes = {code for _, code in payloads}
        for row_obj in db.query(BipBalance).filter(
            BipBalance.period == period,
            BipBalance.subject_code.in_(codes),
        ).all():
            key = (row_obj.ledger or "", row_obj.subject_code)
            if key in existing:
                # 历史重复行不自动删除（迁移阶段已告警），本次只更正首行
                duplicate_rows += 1
                continue
            existing[key] = row_obj
            if not (row_obj.ledger or "").strip():
                # 旧唯一索引 (period, subject_code) 保证同期间同科目至多一条空账簿行；
                # 若历史异常出现多条，duplicate_rows 已告警，这里只保留首条用于升级。
                legacy_by_code.setdefault(row_obj.subject_code, row_obj)
        if duplicate_rows:
            warnings.append(
                f"期间 {period} 存在 {duplicate_rows} 条历史重复余额行，未自动删除；"
                "本次仅更正首行，请人工核对清理"
            )

    inserted = updated = unchanged = 0
    preserved: set[str] = set()
    for (ledger, code), data in payloads.items():
        row_obj = existing.get((ledger, code))
        adopted = False
        if row_obj is None and ledger:
            legacy = legacy_by_code.pop(code, None)
            if legacy is not None and not (legacy.ledger or "").strip():
                # 迁移前 ledger 为空的历史行：带账簿重导时就地升级，避免同科目两份；
                # pop 掉后，后续另一账簿同科目不会再升级并改标签覆盖（C-1 残余）。
                existing.pop(("", code), None)
                row_obj = legacy
                existing[(ledger, code)] = row_obj
                row_obj.ledger = ledger
                adopted = True
        if row_obj is None:
            db.add(BipBalance(batch_id=batch.id, ledger=ledger, period=period,
                              subject_code=code, **data))
            inserted += 1
            continue

        changed = adopted
        for field, value in data.items():
            if getattr(row_obj, field) != value:
                setattr(row_obj, field, value)
                changed = True
        for field in amount_fields:
            if field not in data and abs(getattr(row_obj, field) or 0.0) > 0.005:
                preserved.add(FIELD_LABELS[field])
        if changed:
            row_obj.batch_id = batch.id  # 记录最近一次更正来源
            updated += 1
        else:
            unchanged += 1

    if preserved:
        warnings.append("本次未映射的字段保留旧值：" + "、".join(sorted(preserved)))
    if len(warnings) > MAX_WARNINGS:
        warnings = warnings[:MAX_WARNINGS] + [f"……其余 {len(warnings) - MAX_WARNINGS} 条告警从略"]
    return {
        "inserted_vouchers": 0,
        "skipped_vouchers": 0,
        "inserted_entries": 0,
        "inserted_balances": inserted,
        "updated_balances": updated,
        "unchanged_balances": unchanged,
        "skipped_rows": skipped_rows,
        "invalid_amount_rows": invalid_amount_rows,
        "warnings": warnings,
    }


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
        "updated_balances": b.updated_balances,
        "unchanged_balances": b.unchanged_balances,
        "merged_vouchers": b.merged_vouchers,
        "merged_entries": b.merged_entries,
        "ambiguous_entries": b.ambiguous_entries,
        "skipped_rows": b.skipped_rows,
        "invalid_amount_rows": b.invalid_amount_rows,
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
        "sources": voucher.sources or "",
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
    """科目发生额汇总：按期间聚合凭证分录（仅含带科目编码的分录）。

    凭证列表可能不提供科目列：此时汇总为空，但必须明确提示「未提供科目编码」
    而不是让用户误以为导入失败或金额为零（台账 L-071）。
    """
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
    entries_without_subject = (
        db.query(func.count(BipEntry.id))
        .join(BipVoucher, BipVoucher.id == BipEntry.voucher_id)
        .filter(BipVoucher.period == period, BipEntry.subject_code == "")
        .scalar()
    ) or 0
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
    note = ""
    if entries_without_subject:
        if items:
            note = f"另有 {int(entries_without_subject)} 条分录未提供科目编码，未计入汇总"
        else:
            note = (
                "该期间的分录未提供科目编码（凭证列表来源通常如此），无法按科目汇总；"
                "请导入同期间辅助明细账，或在凭证列表导入时映射科目列"
            )
    return {
        "period": period,
        "items": items,
        "subject_count": len(items),
        "debit_total": _r2(sum(i["debit_total"] for i in items)),
        "credit_total": _r2(sum(i["credit_total"] for i in items)),
        "entries_without_subject": int(entries_without_subject),
        "note": note,
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
                # 只有余额表、没有凭证分录时也能取到最近期间
                period = (
                    db.query(func.max(BipVoucher.period)).scalar()
                    or db.query(func.max(BipBalance.period)).scalar()
                    or ""
                )
            if not period:
                return {"period": "", "items": [], "subject_count": 0, "debit_total": 0.0,
                        "credit_total": 0.0, "entries_without_subject": 0, "note": ""}
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
