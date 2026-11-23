# -*- coding: utf-8 -*-
"""四类源文件的 xlsx 解析（writeoff 核销 / claim 认领 / arrear 欠费）。

解析按表头别名文本锚点定位，不依赖文件名、合同号前缀或固定行号：
- 合并单元格显式展开到每个格子（参照原 recon_check.load_matrix 的可靠做法）；
- 核销表「应收/核销」表头重复：带限定词（应收(账单)/应收(费用期)）按限定词
  归位，裸重复表头按所在列组语义区分（第 1 组=账单级、第 2 组=费用期级）；
- 金额严格 Decimal（ROUND_HALF_UP 两位），合法数字文本可显式解析，非法
  文本/公式未缓存必须报文件、工作表、行与单元格，不归零、不经 float；
- 无关合同全部导入（是否纳入汇总由批次 scope 决定），不按合同号前缀过滤；
- 空/合计/重复表头行跳过；缺必需表头/多个同构工作表/仅表头无数据行明确失败。

共享的值解析工具（parse_amount_text / parse_date_text / parse_ts_text）在本文件
定义，parsers_pdf.py 单向复用（解析器之间无环）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

from .parser_aliases import (
    AR_GROUP,
    BARE_AR_TEXTS,
    BARE_CL_TEXTS,
    CL_GROUP,
    GROUP_LABELS,
    SPECS_BY_KIND,
    Spec,
)
from .schemas import NormalizedRecord, ParseSourceError

# 进入 xlsx 解析的 kind（bill 走 parsers_pdf）
XLSX_KINDS: tuple[str, ...] = ("writeoff", "claim", "arrear")

_CENT = Decimal("0.01")
_AMOUNT_STRIP = str.maketrans("", "", "¥￥ ,，")
_MAX_AMOUNT = Decimal("10") ** 16  # Numeric(18,2) 容量护栏

_TOTAL_ROW_PREFIXES = ("合计", "小计", "总计", "汇总")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")
_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日",
)
_RANGE_SEPS = ("～", "~", "至", "—", "–")
# 费用期「无期间」占位符字符集（诊断 2026-11-19 确认：真实模板以「-」标注无费用期行）
_PERIOD_PLACEHOLDER_CHARS = set(" 　-—–－﹣~～")

# kind 行级必填金额角色（值缺失报单元格；其余金额列缺值 = None，不归零）
_ROW_REQUIRED_AMOUNTS: dict[str, tuple[tuple[str, str], ...]] = {
    "writeoff": (),  # 四个事实金额允许部分为空，但至少一项非空（行级校验）
    "claim": (("amount_recv", "amount_1"), ("amount_claim", "amount_2")),
    "arrear": (("amount_total", "amount_1"),),
}


# ---------- 单元格定位与报错 ----------


@dataclass(frozen=True)
class _CellCtx:
    """报错定位：文件 + 工作表 + Excel 行 + 列名 + 单元格坐标。"""

    file: str
    sheet: str
    row: int
    label: str
    col_letter: str

    def __str__(self) -> str:
        return (f"{self.file} 工作表「{self.sheet}」第{self.row}行"
                f"「{self.label}」（单元格 {self.col_letter}{self.row}）")


def _norm_header(value) -> str:
    """表头文本归一：去空白、全角括号/冒号转半角（别名匹配用）。"""
    text = "" if value is None else str(value)
    text = text.replace("（", "(").replace("）", ")").replace("：", ":")
    return re.sub(r"\s+", "", text.strip())


def _cell_text(value, ctx: _CellCtx) -> str | None:
    """文本单元格：str/int/float 转 str，空串→None；其余类型（日期/布尔等）报错。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ParseSourceError(f"{ctx}：文本列出现布尔值，无法作为文本读取")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    raise ParseSourceError(f"{ctx}：文本列出现「{type(value).__name__}」类型，无法作为文本读取")


def parse_amount_text(text: str, ctx: _CellCtx) -> Decimal | None:
    """严格金额文本：剥千分位/货币符号后 Decimal 解析；空串→None；禁止 float。"""
    cleaned = text.strip().translate(_AMOUNT_STRIP)
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ParseSourceError(
            f"{ctx}：非法金额文本「{text[:40]}」（应为数字，如 1234.56）"
        ) from exc
    if not value.is_finite():
        raise ParseSourceError(f"{ctx}：非法金额文本「{text[:40]}」（非有限数值）")
    if abs(value) >= _MAX_AMOUNT:
        raise ParseSourceError(f"{ctx}：金额「{text[:40]}」超出允许范围（上限 10^16）")
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _try_parse_date(text: str) -> date | None:
    """无报错版日期解析：合法返回 date，非法/空返回 None（费用期切分探测用）。"""
    raw = text.strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_date_text(text: str, ctx: _CellCtx) -> date:
    """严格日期文本：YYYY-MM-DD / YYYY/M/D / YYYY.M.D / 年月日。"""
    parsed = _try_parse_date(text)
    if parsed is not None:
        return parsed
    raise ParseSourceError(f"{ctx}：非法日期文本「{text[:40]}」（应为 YYYY-MM-DD）")


def parse_ts_text(text: str, ctx: _CellCtx) -> str | None:
    """时间戳文本 → 归一为「YYYY-MM-DD HH:MM:SS」（仅作文本细节，非排序键）。"""
    raw = text.strip()
    if not raw:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    raise ParseSourceError(f"{ctx}：非法时间文本「{text[:40]}」（应为 YYYY-MM-DD HH:MM[:SS]）")


def _probe_formula(ctx: _CellCtx, formulas: "_FormulaLookup", sheet: str) -> None:
    """值视角为 None 时检查公式视角：未缓存计算结果的公式必须显式失败。"""
    if formulas.has_formula(sheet, ctx.row, ctx.col_letter):
        raise ParseSourceError(
            f"{ctx}：单元格为公式但未缓存计算结果（Excel 未保存值），"
            "请在 Excel 中打开并另存为数值后再上传"
        )


def _parse_amount_cell(value, ctx: _CellCtx, formulas: "_FormulaLookup", sheet: str) -> Decimal | None:
    if value is None:
        _probe_formula(ctx, formulas, sheet)
        return None
    if isinstance(value, bool):
        raise ParseSourceError(f"{ctx}：金额列出现布尔值，无法作为金额读取")
    if isinstance(value, (int, float)):
        # openpyxl 数值单元格原生 int/float → Decimal(str) 直转，不走 float 运算
        return parse_amount_text(str(value), ctx)
    if isinstance(value, str):
        return parse_amount_text(value, ctx)
    raise ParseSourceError(f"{ctx}：金额列出现「{type(value).__name__}」类型，无法作为金额读取")


def _parse_date_cell(value, ctx: _CellCtx, formulas: "_FormulaLookup", sheet: str) -> date | None:
    if value is None:
        _probe_formula(ctx, formulas, sheet)
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return parse_date_text(value, ctx)
    raise ParseSourceError(f"{ctx}：日期列出现「{type(value).__name__}」类型，无法读取日期")


def _parse_ts_cell(value, ctx: _CellCtx, formulas: "_FormulaLookup", sheet: str) -> str | None:
    if value is None:
        _probe_formula(ctx, formulas, sheet)
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return parse_ts_text(value, ctx)
    raise ParseSourceError(f"{ctx}：时间列出现「{type(value).__name__}」类型，无法读取时间")


class _FormulaLookup:
    """懒加载公式视角矩阵：只有遇到 None 值单元格时才打开第二份工作簿。"""

    def __init__(self, path: Path):
        self._path = path
        self._grids: dict[str, list[list]] = {}

    def has_formula(self, sheet: str, row: int, col_letter: str) -> bool:
        grid = self._grids.get(sheet)
        if grid is None:
            wb = openpyxl.load_workbook(self._path, data_only=False, read_only=True)
            try:
                ws = wb[sheet]
                grid = [
                    [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
                    for r in range(1, ws.max_row + 1)
                ]
            finally:
                wb.close()
            self._grids[sheet] = grid
        col = column_index_from_string(col_letter)
        if row - 1 >= len(grid) or col - 1 >= len(grid[row - 1]):
            return False
        cell = grid[row - 1][col - 1]
        return isinstance(cell, str) and cell.startswith("=")


# ---------- 工作表读取与表头定位 ----------


def _sheet_grid(ws) -> list[list]:
    """整表矩阵（含合并单元格展开，参照 recon_check.load_matrix）。"""
    matrix = [
        [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        for r in range(1, ws.max_row + 1)
    ]
    for rng in ws.merged_cells.ranges:
        top_left = ws.cell(rng.min_row, rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                matrix[r - 1][c - 1] = top_left
    return matrix


def _match_simple_specs(
    header_norms: list[str], specs: tuple[Spec, ...]
) -> tuple[dict[str, int], list[Spec]]:
    """简单角色：按别名声明顺序精确匹配（不同别名多列取最高优先级，精确
    同名重复仍拒绝）。返回 (role→列 1-based, 未命中必需规格)。"""
    resolved: dict[str, int] = {}
    missing: list[Spec] = []
    for spec in specs:
        col: int | None = None
        for alias in spec.aliases:
            hits = [idx + 1 for idx, norm in enumerate(header_norms) if norm == alias]
            if len(hits) > 1:
                raise ParseSourceError(
                    f"表头「{alias}」出现 {len(hits)} 列（第 {hits[0]}、{hits[-1]} 列），结构歧义无法解析"
                )
            if hits:
                col = hits[0]
                break
        if col is None:
            if spec.required:
                missing.append(spec)
            continue
        resolved[spec.role] = col
    return resolved, missing


def _match_amount_groups(header_norms: list[str]) -> dict[str, int]:
    """核销「应收/核销」双组列归位：qualified 优先，裸重复表头按列组
    （第 1 组=账单级）；裸文本集合内任一命中都计入，同组合计 >2 组拒绝。"""
    resolved: dict[str, int] = {}
    for bare_texts, qualified, doc_role, fee_role in (
        (BARE_AR_TEXTS, AR_GROUP, "doc_ar", "fee_ar"),
        (BARE_CL_TEXTS, CL_GROUP, "doc_cleared", "fee_cleared"),
    ):
        qualified_hits: dict[str, int] = {}
        bare_hits: list[int] = []
        for idx, norm in enumerate(header_norms):
            col = idx + 1
            if norm in qualified[doc_role] and doc_role not in qualified_hits:
                qualified_hits[doc_role] = col
            elif norm in qualified[fee_role] and fee_role not in qualified_hits:
                qualified_hits[fee_role] = col
            elif norm in bare_texts:
                bare_hits.append(col)
        resolved.update(qualified_hits)
        needed = [role for role in (doc_role, fee_role) if role not in resolved]
        bare_label = "/".join(bare_texts)
        if needed:
            if len(bare_hits) < len(needed):
                raise ParseSourceError(
                    f"「{bare_label}」表头仅出现 {len(bare_hits)} 列，无法按列组区分"
                    f"账单级/费用期级（可用「{bare_texts[0]}(账单)」「{bare_texts[0]}(费用期)」"
                    "限定词区分两组列）"
                )
            for role, col in zip(needed, bare_hits[: len(needed)]):
                resolved[role] = col
        if len(bare_hits) > len(needed):
            raise ParseSourceError(
                f"「{bare_label}」表头出现 {len(bare_hits)} 列（最多支持账单级+费用期级两组），"
                "结构歧义无法解析"
            )
    if len(set(resolved.values())) != len(resolved):
        raise ParseSourceError("「应收/核销」列组归位出现重复列，表头结构歧义无法解析")
    return resolved


# 表头定位只扫描每个工作表的前 _HEADER_SCAN_ROWS 行（表头通常在文件头部；
# 超过上限未命中 → 该表视为无匹配表头，最终以「未找到表头」显式报错，
# 不依赖固定行号也不做全表扫描的性能赌博）
_HEADER_SCAN_ROWS = 50


def _locate_sheet(wb_values, kind: str) -> tuple[str, list[list], int, dict[str, int]]:
    """在全部工作表中定位首个能识别必需表头的数据表；多于一个同构表 → 拒绝。

    返回 (sheet 名, 网格, 表头行号 1-based, role→列号)。
    """
    specs = SPECS_BY_KIND[kind]
    candidates: list[tuple[str, int, dict[str, int]]] = []
    group_errors: list[ParseSourceError] = []
    grids: dict[str, list[list]] = {}
    for ws in wb_values.worksheets:
        grid = _sheet_grid(ws)
        grids[ws.title] = grid
        if not grid:
            continue
        for row_idx in range(1, min(len(grid), _HEADER_SCAN_ROWS) + 1):
            header_norms = [_norm_header(v) for v in grid[row_idx - 1]]
            resolved, missing = _match_simple_specs(header_norms, specs)
            if missing:
                continue
            if kind == "writeoff":
                try:
                    resolved.update(_match_amount_groups(header_norms))
                except ParseSourceError as exc:
                    # 必需列齐全但「应收/核销」列组不齐：该行就是核销表头，
                    # 记下最具体的列组报错；若整卷都找不到合格表头则优先呈现
                    group_errors.append(exc)
                    continue
            candidates.append((ws.title, row_idx, resolved))
            break
    if not candidates:
        if group_errors:
            raise group_errors[0]
        names = "、".join(f"「{name}」" for name in grids) or "（空工作簿）"
        first_grid = next(iter(grids.values()), [])
        _, missing = _match_simple_specs([_norm_header(v) for v in (first_grid[0] if first_grid else [])], specs)
        detail = f"，最接近的缺失表头：{'、'.join(spec.label for spec in missing)}" if missing else ""
        raise ParseSourceError(
            f"未在文件的工作表中找到可识别的「{kind}」表头（已扫描：{names}{detail}）"
        )
    if len(candidates) > 1:
        names = "、".join(f"「{name}」第{row}行" for name, row, _ in candidates)
        raise ParseSourceError(
            f"文件含 {len(candidates)} 个同结构工作表（{names}）：请每个工作表单独上传，避免静默丢数据"
        )
    sheet, header_row, roles = candidates[0]
    return sheet, grids[sheet], header_row, roles


# ---------- 行分类与行构建 ----------


def _is_blank_row(row: list) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row)


def _is_total_row(row: list) -> bool:
    for value in row:
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        return str(value).strip().startswith(_TOTAL_ROW_PREFIXES)
    return False


def _is_repeated_header_row(row: list, header_row: list) -> bool:
    """整行非空单元格全部与本列表头相同 → 表头重复行，跳过。"""
    saw_value = False
    for cell, head in zip(row, header_row):
        if cell is None or (isinstance(cell, str) and not cell.strip()):
            if head is not None and str(head).strip():
                return False  # 表头在该列有值而本行为空 → 不是重复表头
            continue
        if _norm_header(cell) != _norm_header(head):
            return False
        saw_value = True
    return saw_value


def _iter_data_rows(grid: list[list], header_row: int):
    for row_idx in range(header_row + 1, len(grid) + 1):
        row = grid[row_idx - 1]
        if _is_blank_row(row) or _is_total_row(row) or _is_repeated_header_row(row, grid[header_row - 1]):
            continue
        yield row_idx, row


def _ctx_for(file: str, sheet: str, row: int, col: int, label: str) -> _CellCtx:
    return _CellCtx(file=file, sheet=sheet, row=row, label=label, col_letter=get_column_letter(col))


def _text_at(row: list, col: int, file: str, sheet: str, row_idx: int, label: str) -> str | None:
    return _cell_text(row[col - 1], _ctx_for(file, sheet, row_idx, col, label))


def _require_text(row: list, col: int, file: str, sheet: str, row_idx: int, label: str) -> str:
    value = _text_at(row, col, file, sheet, row_idx, label)
    if not value:
        raise ParseSourceError(f"{_ctx_for(file, sheet, row_idx, col, label)}：缺少必填「{label}」")
    return value


def _spec_of(specs: tuple[Spec, ...], role: str) -> Spec:
    return next(s for s in specs if s.role == role)


def _build_details(kind: str, file: str, sheet: str, row_idx: int, payload: dict) -> dict:
    from pydantic import ValidationError

    from .schemas import DETAIL_MODELS

    try:
        return DETAIL_MODELS[kind](**payload).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ParseSourceError(
            f"{file} 工作表「{sheet}」第{row_idx}行：详情字段非法"
            f"（{first.get('loc', ())}：{first.get('msg', '校验失败')}）"
        ) from exc


def _parse_period_parts(raw: str, ctx: _CellCtx) -> tuple[str | None, str | None]:
    """费用期文本拆 (起, 止)：ISO 内部连字符不算分隔；纯占位符 → (None, None)；
    显式分隔符全程最多一次，仅连字符区间取唯一可解析切分点，多段/单值拒绝。"""
    # 纯连字符/波浪占位（如「-」）：语义为「无费用期」，与空白同口径，不报错不归零
    if raw and all(ch in _PERIOD_PLACEHOLDER_CHARS for ch in raw):
        return None, None
    # ① 显式分隔符
    sep_hits = [sep for sep in _RANGE_SEPS if sep in raw]
    if sep_hits:
        if sum(raw.count(sep) for sep in _RANGE_SEPS) > 1:
            raise ParseSourceError(f"{ctx}：费用期「{raw[:40]}」含多段分隔（~ / ～ / 至 / —），仅支持一段起止")
        left, right = raw.split(sep_hits[0], 1)
        if not left.strip() or not right.strip():
            raise ParseSourceError(f"{ctx}：费用期「{raw[:40]}」缺少起或止，无法解析为一段起止")
        return left, right
    # ② 仅连字符：按切分点探测
    candidates = []
    for match in re.finditer("-", raw):
        left, right = raw[: match.start()], raw[match.start() + 1:]
        if _try_parse_date(left) is not None and _try_parse_date(right) is not None:
            candidates.append((left, right))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ParseSourceError(
            f"{ctx}：费用期「{raw[:40]}」存在多个可解析的连字符切分点，结构歧义无法解析"
        )
    raise ParseSourceError(
        f"{ctx}：费用期「{raw[:40]}」无法解析为一段起止（多段分隔或单值均不支持；应形如 2025-09-01~2025-09-30）"
    )


def _parse_fee_period(row, roles, specs, formulas, file, sheet, row_idx):
    col = roles["fee_period"]
    ctx = _ctx_for(file, sheet, row_idx, col, _spec_of(specs, "fee_period").label)
    value = row[col - 1]
    if value is None:
        _probe_formula(ctx, formulas, sheet)
        return None, None
    if not isinstance(value, str) or not value.strip():
        raise ParseSourceError(f"{ctx}：费用期应为文本（如 2026.10.01-2026.10.31）")
    left, right = _parse_period_parts(value.strip(), ctx)
    if left is None and right is None:
        return None, None
    return parse_date_text(left, ctx), parse_date_text(right, ctx)


def _build_writeoff_record(row, row_idx, roles, specs, formulas, file, sheet) -> NormalizedRecord:
    contract = _require_text(row, roles["contract"], file, sheet, row_idx, "合同号")
    doc_no = _text_at(row, roles["doc_no"], file, sheet, row_idx, "单据号") or ""
    fee_type = _text_at(row, roles["fee_type"], file, sheet, row_idx, "费用类型") or ""
    period_start, period_end = _parse_fee_period(row, roles, specs, formulas, file, sheet, row_idx)

    def amount(role: str) -> Decimal | None:
        col = roles[role]
        label = _spec_of(specs, role).label if role in {s.role for s in specs} else GROUP_LABELS[role]
        ctx = _ctx_for(file, sheet, row_idx, col, label)
        return _parse_amount_cell(row[col - 1], ctx, formulas, sheet)

    amount_1 = amount("fee_ar")
    amount_2 = amount("fee_cleared")
    amount_3 = amount("left_fee")
    amount_4 = amount("prepaid")
    if all(v is None for v in (amount_1, amount_2, amount_3, amount_4)):
        raise ParseSourceError(
            f"{file} 工作表「{sheet}」第{row_idx}行：未包含任何金额"
            "（费用期应收/核销/核销后欠费/预收款金额），无法形成核销事实"
        )
    doc_amount_1 = _parse_amount_cell(
        row[roles["doc_ar"] - 1],
        _ctx_for(file, sheet, row_idx, roles["doc_ar"], GROUP_LABELS["doc_ar"]),
        formulas,
        sheet,
    )
    doc_amount_2 = _parse_amount_cell(
        row[roles["doc_cleared"] - 1],
        _ctx_for(file, sheet, row_idx, roles["doc_cleared"], GROUP_LABELS["doc_cleared"]),
        formulas,
        sheet,
    )

    def text_of(role: str) -> str | None:
        col = roles[role]
        return _text_at(row, col, file, sheet, row_idx, _spec_of(specs, role).label)

    writeoff_date = None
    if "writeoff_date" in roles:
        writeoff_date = _parse_date_cell(
            row[roles["writeoff_date"] - 1],
            _ctx_for(file, sheet, row_idx, roles["writeoff_date"], "核销日期"),
            formulas,
            sheet,
        )
    cancel_date = None
    if "cancel_date" in roles:
        cancel_date = _parse_date_cell(
            row[roles["cancel_date"] - 1],
            _ctx_for(file, sheet, row_idx, roles["cancel_date"], "取消日期"),
            formulas,
            sheet,
        )
    details = _build_details(
        "writeoff",
        file,
        sheet,
        row_idx,
        {
            "seq_no": text_of("seq_no") if "seq_no" in roles else None,
            "region": text_of("region") if "region" in roles else None,
            "project": text_of("project") if "project" in roles else None,
            "contract_type": text_of("contract_type") if "contract_type" in roles else None,
            "bill_month": text_of("bill_month") if "bill_month" in roles else None,
            "brand": text_of("brand") if "brand" in roles else None,
            "unit_no": text_of("unit_no") if "unit_no" in roles else None,
            "writeoff_type": text_of("writeoff_type") if "writeoff_type" in roles else None,
            "writeoff_date": writeoff_date,
            "cancel_date": cancel_date,
            "tax_rate": text_of("tax_rate") if "tax_rate" in roles else None,
            "prepaid_type": text_of("prepaid_type") if "prepaid_type" in roles else None,
        },
    )
    return NormalizedRecord(
        kind="writeoff",
        row_no=row_idx,
        contract_no=contract,
        doc_no=doc_no,
        fee_type=fee_type,
        fee_period_start=period_start,
        fee_period_end=period_end,
        occur_date=writeoff_date,
        amount_1=amount_1,
        amount_2=amount_2,
        amount_3=amount_3,
        amount_4=amount_4,
        doc_amount_1=doc_amount_1,
        doc_amount_2=doc_amount_2,
        details=details,
    )


def _build_claim_record(row, row_idx, roles, specs, formulas, file, sheet) -> NormalizedRecord:
    doc_no = _require_text(row, roles["doc_no"], file, sheet, row_idx, "父单据")

    def amount(role: str) -> Decimal | None:
        col = roles[role]
        ctx = _ctx_for(file, sheet, row_idx, col, _spec_of(specs, role).label)
        return _parse_amount_cell(row[col - 1], ctx, formulas, sheet)

    amount_recv = amount("amount_recv")
    amount_claim = amount("amount_claim")
    amount_fee = amount("amount_fee")
    for role, field_name in _ROW_REQUIRED_AMOUNTS["claim"]:
        if {"amount_1": amount_recv, "amount_2": amount_claim, "amount_3": amount_fee}[field_name] is None:
            col = roles[role]
            raise ParseSourceError(
                f"{_ctx_for(file, sheet, row_idx, col, _spec_of(specs, role).label)}"
                f"：缺少必填「{_spec_of(specs, role).label}」"
            )

    txn_time = None
    if "txn_time" in roles:
        col = roles["txn_time"]
        txn_time = _parse_ts_cell(
            row[col - 1],
            _ctx_for(file, sheet, row_idx, col, "交易时间"),
            formulas,
            sheet,
        )
    occur_date = date.fromisoformat(txn_time[:10]) if txn_time else None

    def text_of(role: str) -> str | None:
        col = roles[role]
        return _text_at(row, col, file, sheet, row_idx, _spec_of(specs, role).label)

    def tail_of(role: str) -> str | None:
        value = text_of(role)
        return value[-4:] if value else None  # 只留 4 位尾号，完整账号不入库

    details = _build_details(
        "claim",
        file,
        sheet,
        row_idx,
        {
            "source": text_of("source") if "source" in roles else None,
            "doc_type": text_of("doc_type") if "doc_type" in roles else None,
            "sub_doc_no": text_of("sub_doc_no") if "sub_doc_no" in roles else None,
            "transfer_id": text_of("transfer_id") if "transfer_id" in roles else None,
            "biz_type": text_of("biz_type") if "biz_type" in roles else None,
            "payer_name": text_of("payer_name") if "payer_name" in roles else None,
            "payer_account_tail": tail_of("payer_account") if "payer_account" in roles else None,
            "payee_name": text_of("payee_name") if "payee_name" in roles else None,
            "payee_account_tail": tail_of("payee_account") if "payee_account" in roles else None,
            "txn_type": text_of("txn_type") if "txn_type" in roles else None,
            "purpose": text_of("purpose") if "purpose" in roles else None,
            "txn_time": txn_time,
            "memo": text_of("memo") if "memo" in roles else None,
            "status": text_of("status") if "status" in roles else None,
            "claim_time": (
                _parse_ts_cell(
                    row[roles["claim_time"] - 1],
                    _ctx_for(file, sheet, row_idx, roles["claim_time"], "认领时间"),
                    formulas,
                    sheet,
                )
                if "claim_time" in roles
                else None
            ),
            "claimant": text_of("claimant") if "claimant" in roles else None,
            "audit_time": (
                _parse_ts_cell(
                    row[roles["audit_time"] - 1],
                    _ctx_for(file, sheet, row_idx, roles["audit_time"], "审核时间"),
                    formulas,
                    sheet,
                )
                if "audit_time" in roles
                else None
            ),
            "auditor": text_of("auditor") if "auditor" in roles else None,
        },
    )
    return NormalizedRecord(
        kind="claim",
        row_no=row_idx,
        doc_no=doc_no,
        occur_date=occur_date,
        amount_1=amount_recv,
        amount_2=amount_claim,
        amount_3=amount_fee,
        details=details,
    )


def _cell_present(value) -> bool:
    """宽松非空判断（仅用于行分类）：None/空白文本为空，其余视为有值。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


# 欠费表底部说明行标识（内容特征，不依赖行号；命中即视为非数据行跳过）
_ARREAR_NOTE_MARKERS = ("制表", "查询", "打印", "备注：")


def _build_arrear_record(row, row_idx, roles, specs, formulas, file, sheet):
    """欠费行构建；聚合/说明行返回 None（跳过），明细缺合同报错。"""
    contract_cell = _text_at(row, roles["contract"], file, sheet, row_idx, "合同号")
    if contract_cell and any(marker in contract_cell for marker in _ARREAR_NOTE_MARKERS):
        return None  # 底部制表/查询说明等非数据行（含合并单元格把说明展开进合同列的形态）
    if not contract_cell:
        period_hint = any(
            _cell_present(row[roles[role] - 1])
            for role in ("fee_start", "fee_end")
            if role in roles
        )
        if period_hint:
            # 有费用期间 = 明细行缺身份，不能当聚合行吞掉
            raise ParseSourceError(
                f"{_ctx_for(file, sheet, row_idx, roles['contract'], '合同号')}"
                "：缺少必填「合同号」（该行存在费用期间，属明细行，不能静默跳过）"
            )
        return None  # 无合同且无费用期 → 聚合/非数据行（总计行等），跳过
    contract = contract_cell

    def amount(role: str) -> Decimal | None:
        col = roles[role]
        ctx = _ctx_for(file, sheet, row_idx, col, _spec_of(specs, role).label)
        return _parse_amount_cell(row[col - 1], ctx, formulas, sheet)

    amount_total = amount("amount_total")
    amount_rent = amount("amount_rent") if "amount_rent" in roles else None
    amount_mgmt = amount("amount_mgmt") if "amount_mgmt" in roles else None
    if amount_total is None:
        col = roles["amount_total"]
        raise ParseSourceError(
            f"{_ctx_for(file, sheet, row_idx, col, '合计')}：缺少必填「合计」"
        )

    def date_of(role: str) -> date | None:
        col = roles[role]
        return _parse_date_cell(
            row[col - 1],
            _ctx_for(file, sheet, row_idx, col, _spec_of(specs, role).label),
            formulas,
            sheet,
        )

    details = _build_details(
        "arrear",
        file,
        sheet,
        row_idx,
        {
            "bill_month": (
                _text_at(row, roles["bill_month"], file, sheet, row_idx, "账单月")
                if "bill_month" in roles
                else None
            )
        },
    )
    return NormalizedRecord(
        kind="arrear",
        row_no=row_idx,
        contract_no=contract,
        due_date=date_of("due_date") if "due_date" in roles else None,
        fee_period_start=date_of("fee_start") if "fee_start" in roles else None,
        fee_period_end=date_of("fee_end") if "fee_end" in roles else None,
        amount_1=amount_total,
        amount_2=amount_rent,
        amount_3=amount_mgmt,
        details=details,
    )


_BUILDERS = {
    "writeoff": _build_writeoff_record,
    "claim": _build_claim_record,
    "arrear": _build_arrear_record,
}


def parse_xlsx_source(path: Path, kind: str) -> list[NormalizedRecord]:
    """xlsx 源解析入口（writeoff/claim/arrear）：返回标准化事实行列表。"""
    if kind not in XLSX_KINDS:
        raise ParseSourceError(f"未知源类型 {kind!r}（xlsx 解析仅支持：{'、'.join(XLSX_KINDS)}）")
    if path.suffix.lower() != ".xlsx":
        raise ParseSourceError(
            f"文件「{path.name}」不是 .xlsx（{kind} 源仅支持 .xlsx；请用 Excel 另存为 .xlsx）"
        )
    if not path.is_file():
        raise ParseSourceError(f"文件不存在：{path.name}")
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseSourceError(f"无法打开 xlsx 文件「{path.name}」：{exc}") from exc
    try:
        sheet, grid, header_row, roles = _locate_sheet(wb, kind)
    finally:
        wb.close()
    specs = SPECS_BY_KIND[kind]
    file_name = path.name
    formulas = _FormulaLookup(path)
    # arrear 构建器对聚合/说明行返回 None（跳过）；writeoff/claim 恒返回记录
    records = [
        record
        for record in (
            _BUILDERS[kind](row, row_idx, roles, specs, formulas, file_name, sheet)
            for row_idx, row in _iter_data_rows(grid, header_row)
        )
        if record is not None
    ]
    if not records:
        raise ParseSourceError(
            f"文件「{file_name}」工作表「{sheet}」未找到任何数据行（仅表头或全部为空/合计行）"
        )
    return records
