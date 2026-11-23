# -*- coding: utf-8 -*-
"""经营管理报表解析器：三类报表（内控 / 周报 / 月度）统一解析成
`{metrics, records, texts, warnings}`。

解析口径：一律按**标签文本锚点**定位表头与数据行，不锁死行号——模板加减行、
合并单元格位置变化都不影响；缺小节只记 warning，不整体失败。数值为空、
`#DIV/0!`、`—`、`/` 一律记 None（不补 0）；月报只从单位明确的句式抽数，
抽不到就留空并记 warning，绝不猜。
"""
from __future__ import annotations

import io
import re
from bisect import bisect_left
from datetime import date, datetime, timedelta
from typing import Any

import openpyxl

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 200_000

_NULL_TEXTS = {"", "-", "--", "—", "/", "／", "无", "#DIV/0!", "#N/A", "#VALUE!"}
_DATE_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
_PERIOD_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
_SCOPE_WORDS = (("截至本月末", "ytd"), ("当月", "month"), ("周", "week"), ("年度", "year"))


# ---------------- 基础取值 ----------------

def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _flat(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value))


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    text = _flat(value).replace(",", "").replace("，", "")
    if text in _NULL_TEXTS:
        return None
    percent = text.endswith("%")
    text = text.rstrip("%")
    if text in _NULL_TEXTS:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return round(number / 100 if percent else number, 6)


def _rate(value: Any) -> float | None:
    """比率统一成小数：73.5% → 0.735；大于 5 的裸数按百分数处理。"""
    number = _num(value)
    if number is None:
        return None
    if isinstance(value, str) and "%" in value:
        return round(number, 6)
    if number > 5:
        return round(number / 100, 6)
    return round(number, 6)


def _day(value: Any, default_year: int | None = None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    text = _text(value)
    match = _DATE_RE.search(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    match = re.match(r"^(\d{1,2})[./月](\d{1,2})日?$", text)
    if match and default_year:
        try:
            return date(default_year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _sheet(wb, prefix: str):
    for ws in wb.worksheets:
        if ws.title.startswith(prefix):
            return ws
    return None


class _CellValue:
    """只读单元格：解析器只读取 .value。"""

    __slots__ = ("value",)

    def __init__(self, value=None):
        self.value = value


_CACHE_COLUMNS = 60


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


class _SheetView:
    """只读工作表视图：按真实行号缓存非空行，列数截到解析器实际读取上限。

    解析器按标签锚点做随机坐标读取，read_only 工作表的 `cell()` 每次都会从头
    重扫 XML（行数大时退化为 O(n²)）；这里只读迭代一次并缓存行值。

    - 行数上限按**非空数据行**计数：Excel 把格式刷到 25 万行不会误拒正常文件，
      真实数据行超过上限仍整体拒绝；
    - 尾部全空行不进入缓存，`max_row` 是最后一个非空行的真实行号；内部空行
      不缓存，`cell()` 返回 None，锚点解析语义不变；
    - 列缓存截到 `_CACHE_COLUMNS`（解析器最大读取 60 列），避免宽表内存放大；
    - `used_rows` 在所有页签间共享，20 万行上限按整个文件合计。
    """

    def __init__(self, ws, max_rows: int, used_rows: list[int]):
        self.title = str(getattr(ws, "title", "") or "")
        self._row_numbers: list[int] = []
        self._rows: list[tuple] = []
        real_rows = 0
        for index, row in enumerate(ws.iter_rows(values_only=True), start=1):
            values = tuple(row[:_CACHE_COLUMNS])
            if all(_is_blank(value) for value in values):
                continue
            real_rows += 1
            if used_rows[0] + real_rows > max_rows:
                raise ValueError(f"文件行数超过 {max_rows} 行上限（所有工作表合计）")
            self._row_numbers.append(index)
            self._rows.append(values)
        used_rows[0] += real_rows
        self.max_row = self._row_numbers[-1] if self._row_numbers else 0
        self.max_column = max((len(row) for row in self._rows), default=0)

    def cell(self, row: int = 1, column: int = 1, value=None):
        if value is not None:
            raise TypeError("只读工作表不支持写入")
        if row < 1 or column < 1:
            raise ValueError("Row or column values must be at least 1")
        if column > _CACHE_COLUMNS:
            return _CellValue(None)
        position = bisect_left(self._row_numbers, row)
        if position >= len(self._row_numbers) or self._row_numbers[position] != row:
            return _CellValue(None)
        data = self._rows[position]
        if column > len(data):
            return _CellValue(None)
        return _CellValue(data[column - 1])


class _WorkbookView:
    """只读 workbook 视图：只构建需要的页签，所有页签合计共用 20 万非空行上限。"""

    def __init__(self, workbook, max_rows: int, prefixes: tuple[str, ...] = ()):
        views = []
        used_rows = [0]
        for ws in workbook.worksheets:
            if prefixes and not any(ws.title.startswith(prefix) for prefix in prefixes):
                continue
            # 部分生成器写入的 dimension 元数据不可靠；read_only 会按它截断数据，
            # 复位后按实际行迭代（行数上限由 _SheetView 在迭代中兜底）
            ws.reset_dimensions()
            views.append(_SheetView(ws, max_rows, used_rows))
        self.worksheets = views


def _header_row(ws, *needles: str, max_col: int = 40) -> int | None:
    for row in range(1, ws.max_row + 1):
        joined = "".join(_flat(ws.cell(row=row, column=col).value) for col in range(1, max_col + 1))
        if all(needle in joined for needle in needles):
            return row
    return None


def _columns(ws, row: int, max_col: int = 40) -> list[tuple[int, str]]:
    return [(col, _flat(ws.cell(row=row, column=col).value)) for col in range(1, max_col + 1)]


def _col(columns: list[tuple[int, str]], *needles: str, last: bool = False) -> int | None:
    hits = [col for col, text in columns if all(needle in text for needle in needles)]
    if not hits:
        return None
    return hits[-1] if last else hits[0]


def _row_text(ws, row: int, max_col: int = 40) -> str:
    return "".join(_flat(ws.cell(row=row, column=col).value) for col in range(1, max_col + 1))


def _add_metric(bucket: list[dict], section: str, key: str, label: str, value: Any, unit: str = "") -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        bucket.append({
            "section": section, "metric_key": key, "label": label,
            "value_num": round(float(value), 6), "value_text": "", "unit": unit,
        })
    elif isinstance(value, str) and value.strip():
        bucket.append({
            "section": section, "metric_key": key, "label": label,
            "value_num": None, "value_text": value.strip(), "unit": unit,
        })


def _add_record(bucket: list[dict], section: str, payload: dict, **fields) -> None:
    item = {"section": section, "payload": payload, "sort": len(bucket)}
    item.update({key: value for key, value in fields.items() if value not in (None, "")})
    bucket.append(item)


def _add_text(bucket: list[dict], key: str, content: str) -> None:
    content = (content or "").strip()
    if not content:
        return
    for item in bucket:
        if item["section"] == key:
            item["content"] = item["content"] + "\n" + content
            return
    bucket.append({"section": key, "subsection": "", "content": content, "sort": len(bucket) * 10})


def _period_from(text: str) -> tuple[str, date, date] | None:
    match = _PERIOD_RE.search(text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    days = re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    period = f"{year:04d}-{month:02d}"
    if len(days) >= 2:
        try:
            start = date(year, int(days[0][0]), int(days[0][1]))
            end = date(year, int(days[1][0]), int(days[1][1]))
            return period, start, end
        except ValueError:
            pass
    end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    return period, date(year, month, 1), end


# ---------------- 内控管理 ----------------

def _parse_control(wb) -> dict:
    metrics: list[dict] = []
    records: list[dict] = []
    texts: list[dict] = []
    warnings: list[str] = []

    _parse_key_work(_sheet(wb, "表1"), records, warnings)
    _parse_kpi(_sheet(wb, "表2"), records, warnings)
    _parse_revenue(_sheet(wb, "表3"), metrics, texts, warnings)
    _parse_leasing(_sheet(wb, "表4"), metrics, warnings)
    _parse_litigation(_sheet(wb, "表5"), records, warnings)
    _parse_board(_sheet(wb, "表6"), records, warnings)

    period_text = ""
    for prefix in ("表3", "表2"):
        ws = _sheet(wb, prefix)
        if ws is not None:
            period_text = " ".join(
                _text(ws.cell(row=row, column=col).value)
                for row in range(1, 4) for col in range(1, 8)
            )
            if _PERIOD_RE.search(period_text):
                break
    period = _period_from(period_text) or _period_from("")
    return {
        "metrics": metrics, "records": records, "texts": texts, "warnings": warnings,
        "period": period[0] if period else "",
        "period_start": period[1] if period else None,
        "period_end": period[2] if period else None,
        "project": "云南区域",
    }


def _parse_key_work(ws, records: list[dict], warnings: list[str]) -> None:
    if ws is None:
        warnings.append("内控表1（重点工作）缺失")
        return
    header = _header_row(ws, "序号", "工作内容")
    if header is None:
        warnings.append("内控表1 未找到表头（序号/工作内容）")
        return
    columns = _columns(ws, header)
    seq_col = _col(columns, "序号")
    name_col = _col(columns, "工作内容")
    strategy_col = _col(columns, "策略") or _col(columns, "待决")
    progress_cols = [col for col, text in columns if "进度" in text]
    first_col = _col(columns, "第一次录入")
    remain_col = _col(columns, "剩余时间")
    due_col = _col(columns, "预计完成")
    owner_col = _col(columns, "主责人")
    co_col = _col(columns, "协办人")
    note_col = _col(columns, "备注")

    empty = 0
    for row in range(header + 1, ws.max_row + 1):
        seq = _int(ws.cell(row=row, column=seq_col).value) if seq_col else None
        name = _text(ws.cell(row=row, column=name_col).value) if name_col else ""
        if not name:
            empty += 1
            if empty >= 2 or (seq_col and _flat(ws.cell(row=row, column=seq_col).value) == "备注"):
                break
            continue
        empty = 0
        lines = [line.strip() for line in name.splitlines() if line.strip()]
        category = lines[0] if len(lines) > 1 else ""
        payload: dict[str, Any] = {}
        if len(progress_cols) >= 2:
            payload["prev_progress"] = _text(ws.cell(row=row, column=progress_cols[0]).value)
            progress = _text(ws.cell(row=row, column=progress_cols[-1]).value)
        else:
            progress = _text(ws.cell(row=row, column=progress_cols[0]).value) if progress_cols else ""
        if first_col:
            payload["first_entry"] = _text(ws.cell(row=row, column=first_col).value)
        if remain_col:
            payload["remaining_days"] = _int(ws.cell(row=row, column=remain_col).value)
        _add_record(
            records, "key_work", payload,
            seq=seq or 0, name=name if len(lines) == 1 else lines[-1], category=category,
            content=_text(ws.cell(row=row, column=strategy_col).value) if strategy_col else "",
            progress=progress,
            due_on=_day(ws.cell(row=row, column=due_col).value) if due_col else None,
            owner=_text(ws.cell(row=row, column=owner_col).value) if owner_col else "",
            co_owner=_text(ws.cell(row=row, column=co_col).value) if co_col else "",
            note=_text(ws.cell(row=row, column=note_col).value) if note_col else "",
        )


def _parse_kpi(ws, records: list[dict], warnings: list[str]) -> None:
    if ws is None:
        warnings.append("内控表2（责任书）缺失")
        return
    header = _header_row(ws, "指标类型", "指标名称")
    if header is None:
        warnings.append("内控表2 未找到表头（指标类型/指标名称）")
        return
    columns = _columns(ws, header)
    category_col = _col(columns, "指标类型")
    name_col = _col(columns, "指标名称")
    weight_col = _col(columns, "考核权重")
    range_col = _col(columns, "分值区间")
    target_col = _col(columns, "年度目标值")
    rate_col = _col(columns, "完成率")
    actual_col = _col(columns, "完成情况")
    issue_col = _col(columns, "难点")
    due_col = _col(columns, "预计完成")
    owner_col = _col(columns, "主责人")
    note_col = _col(columns, "备注")

    category = ""
    for row in range(header + 1, ws.max_row + 1):
        first = _flat(ws.cell(row=row, column=1).value)
        if first.startswith("合计"):
            break
        row_category = _text(ws.cell(row=row, column=category_col).value) if category_col else ""
        if row_category:
            category = row_category
        name = _text(ws.cell(row=row, column=name_col).value) if name_col else ""
        target = ws.cell(row=row, column=target_col).value if target_col else None
        if not name:
            if target and records:
                records[-1]["payload"]["sub_items"] = (
                    records[-1]["payload"].get("sub_items", []) + [{"target": _text(target),
                                                                   "actual": _text(ws.cell(row=row, column=actual_col).value) if actual_col else ""}]
                )
            continue
        rate = _rate(ws.cell(row=row, column=rate_col).value) if rate_col else None
        actual_value = ws.cell(row=row, column=actual_col).value if actual_col else None
        target_num = _num(target)
        actual_num = _num(actual_value)
        payload: dict[str, Any] = {
            "weight": _text(ws.cell(row=row, column=weight_col).value) if weight_col else "",
            "score_range": _text(ws.cell(row=row, column=range_col).value) if range_col else "",
            "target_text": _text(target),
            "issue": _text(ws.cell(row=row, column=issue_col).value) if issue_col else "",
        }
        if rate is None and target_num and actual_num is not None and target_num > 0:
            derived = actual_num / target_num
            if 0 <= derived <= 5:
                rate = round(derived, 4)
                payload["rate_derived"] = True
        _add_record(
            records, "kpi", payload,
            name=name, category=category,
            amount=actual_num,
            rate=rate,
            due_on=_day(ws.cell(row=row, column=due_col).value) if due_col else None,
            owner=_text(ws.cell(row=row, column=owner_col).value) if owner_col else "",
            note=_text(ws.cell(row=row, column=note_col).value) if note_col else "",
        )


def _parse_revenue(ws, metrics: list[dict], texts: list[dict], warnings: list[str]) -> None:
    if ws is None:
        warnings.append("内控表3（项目收入）缺失")
        return
    blocks: list[tuple[str, int, int]] = []
    for row in range(1, ws.max_row + 1):
        name = _text(ws.cell(row=row, column=1).value)
        if name and "科目" in _flat(ws.cell(row=row, column=2).value):
            blocks.append((name, row, ws.max_row))
    for index, (project, start, _) in enumerate(blocks):
        end = blocks[index + 1][1] if index + 1 < len(blocks) else ws.max_row
        scope_key, scope_label = "", ""
        for row in range(start, end):
            label_b = _text(ws.cell(row=row, column=2).value)
            label_c = _flat(ws.cell(row=row, column=3).value)
            if label_b:
                scope_label = label_b
                scope_key = next((key for word, key in _SCOPE_WORDS if word in label_b), "")
            if not scope_key or not label_c:
                continue
            row_key = "target" if label_c.startswith("指标") else "actual" if "已执行" in label_c else "rate" if "完成率" in label_c else ""
            if not row_key:
                continue
            for offset, col_key, col_label in ((1, "current", "当期应收"), (2, "arrears", "历欠催缴"), (3, "accrual", "权责收入")):
                raw = ws.cell(row=row, column=3 + offset).value
                value = _rate(raw) if row_key == "rate" else _num(raw)
                if value is None:
                    continue
                key = f"revenue.{project}.{scope_key}.{row_key}.{col_key}"
                _add_metric(metrics, "revenue", key,
                            f"{project}·{scope_label}·{col_label}·{'完成率' if row_key == 'rate' else '指标' if row_key == 'target' else '已执行'}",
                            value, "" if row_key == "rate" else "万元")
        note_col = 7
        note = "\n".join(
            _text(ws.cell(row=row, column=note_col).value)
            for row in range(start + 1, end)
            if _text(ws.cell(row=row, column=note_col).value)
        )
        if note:
            texts.append({"section": "revenue_note", "subsection": project, "content": note,
                          "sort": len(texts) * 10})


_LEASING_ITEMS = (
    ("1.1", "plan", "计划总体指标"), ("1.2", "change", "变动指标"),
    ("1.3", "task", "实际总体任务"), ("1.4", "cumulative", "累计指标"),
    ("2.1", "done_area", "已完成面积"), ("2.2", "done_rate", "总体指标完成率"),
    ("2.3", "accrual_rate", "累计指标完成率"),
)


def _parse_leasing(ws, metrics: list[dict], warnings: list[str]) -> None:
    if ws is None:
        warnings.append("内控表4（招商完成）缺失")
        return
    header = _header_row(ws, "区域合计")
    if header is None:
        warnings.append("内控表4 未找到表头（区域合计）")
        return
    group_row = header - 1
    columns = _columns(ws, header)
    groups: list[tuple[int, int, str]] = []
    starts = [(col, _flat(ws.cell(row=group_row, column=col).value)) for col, _ in columns
              if _flat(ws.cell(row=group_row, column=col).value)]
    for index, (col, label) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else ws.max_column + 1
        scope = next((key for word, key in _SCOPE_WORDS if word in label), "")
        if scope:
            groups.append((col, end, scope))
    for row in range(header + 1, ws.max_row + 1):
        label = _flat(ws.cell(row=row, column=2).value) or _flat(ws.cell(row=row, column=1).value)
        item = next((key for prefix, key, _ in _LEASING_ITEMS if label.startswith(prefix)), None)
        if not item:
            continue
        label_text = next((text for prefix, _, text in _LEASING_ITEMS if label.startswith(prefix)), label)
        for start, end, scope in groups:
            for col in range(start, end):
                project = _text(ws.cell(row=header, column=col).value)
                if not project and col != start:
                    continue
                project = project or "区域合计"
                raw = ws.cell(row=row, column=col).value
                value = _rate(raw) if "率" in label_text else _num(raw)
                if value is None:
                    continue
                _add_metric(metrics, "leasing", f"leasing.{scope}.{project}.{item}",
                            f"招商·{scope}·{project}·{label_text}", value,
                            "" if "率" in label_text else "㎡")


_LITIGATION_FIELDS = {
    "当前阶段": ("stage", "text"),
    "商户品牌": ("name", "text"),
    "诉讼案件纠纷类型": ("category", "text"),
    "欠费金额": ("amount", "number"),
    "诉讼金额": ("amount2", "number"),
    "回款总额": ("recovery", "number"),
    "还欠金额": ("balance", "number"),
    "起诉申请通过日期": ("occurred_on", "date"),
    "一审判决或调和解日期": ("due_on", "date"),
    "代理律师": ("owner", "text"),
    "诉讼请求内容": ("content", "text"),
    "进度计划": ("progress", "text"),
    "执行情况说明": ("note", "text"),
    "代理律所": ("law_firm", "payload"),
    "诉讼代理费": ("agent_fee", "payload"),
    "判决金额": ("judgment_amount", "payload"),
    "生效判决/调解/和解金额": ("effective_amount", "payload"),
    "一审判决或调和解内容": ("judgment_content", "payload"),
    "二审判决内容": ("second_content", "payload"),
    "备注": ("remark", "payload"),
}


def _parse_litigation(ws, records: list[dict], warnings: list[str]) -> None:
    if ws is None:
        warnings.append("内控表5（诉讼案件）缺失")
        return
    header = _header_row(ws, "当前阶段", "商户品牌")
    if header is None:
        warnings.append("内控表5 未找到表头（当前阶段/商户品牌）")
        return
    columns = _columns(ws, header, max_col=min(ws.max_column, 60))
    headers = {col: text for col, text in columns if text}
    empty = 0
    for row in range(header + 1, ws.max_row + 1):
        if not any(_text(ws.cell(row=row, column=col).value) for col in (1, 2, 3)):
            empty += 1
            if empty >= 3:
                break
            continue
        empty = 0
        fields: dict[str, Any] = {}
        payload: dict[str, Any] = {}
        for col, header_text in headers.items():
            raw = ws.cell(row=row, column=col).value
            mapped = next((value for key, value in _LITIGATION_FIELDS.items() if key in header_text), None)
            if mapped:
                name, kind = mapped
                if kind == "number":
                    fields[name] = _num(raw)
                elif kind == "date":
                    fields[name] = _day(raw)
                elif kind == "payload":
                    payload[name] = _num(raw) if any(word in header_text for word in ("金额", "费")) else _text(raw)
                else:
                    fields[name] = _text(raw)
            else:
                text = _text(raw)
                if text:
                    payload[header_text] = text
        _add_record(records, "litigation", payload, **fields)


def _parse_board(ws, records: list[dict], warnings: list[str]) -> None:
    if ws is None:
        warnings.append("内控表6（总办会）缺失")
        return
    header = _header_row(ws, "序号", "待上会决策事项")
    if header is None:
        warnings.append("内控表6 未找到表头（序号/待上会决策事项）")
        return
    columns = _columns(ws, header)
    seq_col = _col(columns, "序号")
    name_col = _col(columns, "待上会决策事项")
    content_col = _col(columns, "内部初步决策")
    progress_col = _col(columns, "上会流程")
    plan_col = _col(columns, "后续工作")
    first_col = _col(columns, "第一次录入")
    remain_col = _col(columns, "剩余时间")
    due_col = _col(columns, "预计完成")
    owner_col = _col(columns, "主责人")
    co_col = _col(columns, "协办人")
    note_col = _col(columns, "备注")
    for row in range(header + 1, ws.max_row + 1):
        first = _flat(ws.cell(row=row, column=1).value)
        if first.startswith("备注"):
            break
        name = _text(ws.cell(row=row, column=name_col).value) if name_col else ""
        if not name or first == "示例":
            continue
        payload = {}
        if plan_col:
            payload["next_plan"] = _text(ws.cell(row=row, column=plan_col).value)
        if first_col:
            payload["first_entry"] = _text(ws.cell(row=row, column=first_col).value)
        if remain_col:
            payload["remaining_days"] = _int(ws.cell(row=row, column=remain_col).value)
        _add_record(
            records, "board_item", payload,
            seq=_int(ws.cell(row=row, column=seq_col).value) if seq_col else 0,
            name=name,
            content=_text(ws.cell(row=row, column=content_col).value) if content_col else "",
            progress=_text(ws.cell(row=row, column=progress_col).value) if progress_col else "",
            due_on=_day(ws.cell(row=row, column=due_col).value) if due_col else None,
            owner=_text(ws.cell(row=row, column=owner_col).value) if owner_col else "",
            co_owner=_text(ws.cell(row=row, column=co_col).value) if co_col else "",
            note=_text(ws.cell(row=row, column=note_col).value) if note_col else "",
        )


# ---------------- 项目周报 ----------------

_WEEKLY_SIMPLE = (
    ("可出租面积", "weekly.lease.rentable_area", "期末可出租面积", "㎡", "num"),
    ("出租率", "weekly.lease.occupancy_rate", "期末出租率", "", "rate"),
    ("环比增幅", "weekly.lease.mom_change", "出租率环比增幅", "", "rate"),
    ("开业率", "weekly.lease.opening_rate", "开业率", "", "rate"),
)

_VACANCY = (
    ("总租赁面积", "weekly.vacancy.total_area", "总租赁面积", "㎡"),
    ("总铺位数", "weekly.vacancy.shop_count", "总铺位数", "家"),
    ("空铺数", "weekly.vacancy.vacant_count", "空铺数", "家"),
    ("总空铺面积", "weekly.vacancy.vacant_area", "空铺面积", "㎡"),
    ("空铺率", "weekly.vacancy.vacancy_rate", "空铺率", ""),
    ("新签品牌数量", "weekly.leasing.week_new_count", "本周新签品牌数", "家"),
    ("新签品牌面积", "weekly.leasing.week_new_area", "本周新签面积", "㎡"),
    ("流程中品牌数量", "weekly.leasing.week_pipeline_count", "流程中品牌数", "家"),
    ("流程中品牌面积", "weekly.leasing.week_pipeline_area", "流程中品牌面积", "㎡"),
    ("新签品牌数量", "weekly.leasing.ytd_new_count", "年内新签品牌数", "家"),
    ("新签品牌面积", "weekly.leasing.ytd_new_area", "年内新签面积", "㎡"),
)

_RENEWAL = (
    ("续签品牌数量", "weekly.renewal.expiring_count", "到期续签品牌数", "家"),
    ("续签品牌面积", "weekly.renewal.expiring_area", "到期续签面积", "㎡"),
    ("已完成续签品牌数量", "weekly.renewal.done_count", "已完成续签数", "家"),
    ("已完成续签品牌面积", "weekly.renewal.done_area", "已完成续签面积", "㎡"),
    ("续签撤铺品牌数量", "weekly.renewal.vacate_count", "续签撤铺品牌数", "家"),
    ("续签撤铺品牌面积", "weekly.renewal.vacate_area", "续签撤铺面积", "㎡"),
    ("续签撤铺补招完成数量", "weekly.renewal.backfill_done_count", "撤铺补招完成数", "家"),
    ("续签撤铺补招完成面积", "weekly.renewal.backfill_done_area", "撤铺补招完成面积", "㎡"),
    ("未完成补招数量", "weekly.renewal.backfill_open_count", "未完成补招数", "家"),
    ("未完成补招面积", "weekly.renewal.backfill_open_area", "未完成补招面积", "㎡"),
)

_COLLECTION = (
    ("累计应收款", "weekly.collection.receivable", "累计应收", "万元"),
    ("累计已收款", "weekly.collection.received", "累计已收", "万元"),
    ("累计未收款", "weekly.collection.unreceived", "累计未收", "万元"),
    ("本周收回", "weekly.collection.week_received", "本周收回", "万元"),
    ("累计收缴率", "weekly.collection.rate", "累计收缴率", ""),
)

_FUND_STAGES = ("annual", "project", "design", "construction", "ongoing", "settled")
_FUND_STAGE_LABELS = {
    "annual": "年度", "project": "立项", "design": "设计合同",
    "construction": "施工合同", "ongoing": "施工中", "settled": "竣工待结算",
}
_MONTHLY_UNITS = {
    "receivable": "万元", "received": "万元", "rate": "", "area": "㎡",
    "new_area": "㎡", "renew_area": "㎡", "new_brands": "家",
    "renew_brands": "家", "tenants": "人", "total": "户", "done": "户",
}


def _parse_weekly(ws, project_hint: str) -> dict:
    metrics: list[dict] = []
    records: list[dict] = []
    texts: list[dict] = []
    warnings: list[str] = []

    title = _text(ws.cell(row=1, column=1).value)
    stats = ""
    for row in range(1, min(ws.max_row, 4) + 1):
        for col in range(1, 12):
            text = _text(ws.cell(row=row, column=col).value)
            if "统计时间" in text:
                stats = text
    period = _period_from(stats) or _period_from(title)
    project = project_hint
    for row in range(1, 3):
        text = _text(ws.cell(row=row, column=1).value)
        if text.startswith("项目："):
            project = text.split("：", 1)[1].strip() or project_hint
    if period is None:
        warnings.append("周报未识别到统计时间（统计时间：YYYY年M月D日-M月D日）")

    header = _header_row(ws, "可出租面积", "出租率")
    if header is not None and header < ws.max_row:
        columns = _columns(ws, header)
        data_row = header + 1
        for needle, key, label, unit, kind in _WEEKLY_SIMPLE:
            col = _col(columns, needle)
            if not col:
                continue
            raw = ws.cell(row=data_row, column=col).value
            value = _rate(raw) if kind == "rate" else _num(raw)
            _add_metric(metrics, key.rsplit(".", 1)[0], key, label, value, unit)
    else:
        warnings.append("周报未找到招商表头（可出租面积/出租率）")

    vacancy_header = _header_row(ws, "总租赁面积", "空铺")
    if vacancy_header is not None:
        columns = _columns(ws, vacancy_header)
        data_row = vacancy_header + 1
        used: dict[str, int] = {}
        for needle, key, label, unit in _VACANCY:
            hits = [col for col, text in columns if needle in text]
            index = used.get(needle, 0)
            if index >= len(hits):
                continue
            used[needle] = index + 1
            value = _rate(ws.cell(row=data_row, column=hits[index]).value) if key.endswith("rate") else _num(ws.cell(row=data_row, column=hits[index]).value)
            _add_metric(metrics, key.rsplit(".", 1)[0], key, label, value, unit)
    else:
        warnings.append("周报未找到空铺招商表（总租赁面积/空铺）")

    renewal_header = _header_row(ws, "续签品牌数量")
    if renewal_header is not None:
        columns = _columns(ws, renewal_header)
        data_row = renewal_header + 1
        used = {}
        for needle, key, label, unit in _RENEWAL:
            hits = [col for col, text in columns if needle in text]
            index = used.get(needle, 0)
            if index >= len(hits):
                continue
            used[needle] = index + 1
            _add_metric(metrics, "weekly.renewal", key, label, _num(ws.cell(row=data_row, column=hits[index]).value), unit)
    else:
        warnings.append("周报未找到续签表（续签品牌数量）")

    collection_header = _header_row(ws, "累计应收款")
    if collection_header is not None:
        columns = _columns(ws, collection_header)
        data_row = collection_header + 1
        for needle, key, label, unit in _COLLECTION:
            col = _col(columns, needle)
            if not col:
                continue
            raw = ws.cell(row=data_row, column=col).value
            value = _rate(raw) if key.endswith("rate") else _num(raw)
            _add_metric(metrics, "weekly.collection", key, label, value, unit)
    else:
        warnings.append("周报未找到收缴表（累计应收款）")

    _parse_warning(ws, records, warnings)
    _parse_promo(ws, records, warnings)
    _parse_competitor(ws, metrics, warnings)
    _parse_fund(ws, metrics, warnings)
    _parse_weekly_texts(ws, texts, warnings)

    return {
        "metrics": metrics, "records": records, "texts": texts, "warnings": warnings,
        "period": period[0] if period else "",
        "period_start": period[1] if period else None,
        "period_end": period[2] if period else None,
        "project": project,
    }


def _parse_warning(ws, records: list[dict], warnings: list[str]) -> None:
    header = _header_row(ws, "业态", "品牌名称")
    if header is None:
        warnings.append("周报未找到预警商户表（业态/品牌名称）")
        return
    columns = _columns(ws, header)
    cols = {needle: _col(columns, needle) for needle in ("业态", "品牌名称", "面积", "预警原因", "解决措施", "本周进度")}
    for row in range(header + 1, ws.max_row + 1):
        first = _flat(ws.cell(row=row, column=1).value)
        if first.startswith("备注"):
            break
        name = _text(ws.cell(row=row, column=cols["品牌名称"]).value) if cols["品牌名称"] else ""
        if not name:
            continue
        _add_record(
            records, "warning_tenant", {},
            name=name,
            category=_text(ws.cell(row=row, column=cols["业态"]).value) if cols["业态"] else "",
            area=_num(ws.cell(row=row, column=cols["面积"]).value) if cols["面积"] else None,
            content=_text(ws.cell(row=row, column=cols["预警原因"]).value) if cols["预警原因"] else "",
            note=_text(ws.cell(row=row, column=cols["解决措施"]).value) if cols["解决措施"] else "",
            progress=_text(ws.cell(row=row, column=cols["本周进度"]).value) if cols["本周进度"] else "",
        )


def _parse_promo(ws, records: list[dict], warnings: list[str]) -> None:
    header = _header_row(ws, "新品牌", "开业时间")
    if header is None:
        warnings.append("周报未找到品牌宣发表（新品牌/开业时间）")
        return
    columns = _columns(ws, header)
    name_col = _col(columns, "新品牌")
    open_col = _col(columns, "开业时间")
    sales_col = _col(columns, "月均销售")
    pre_col = _col(columns, "预热期")
    open_phase = _col(columns, "开业期")
    re_col = _col(columns, "复推期")
    # 重点品牌表头是「新品牌」区块的结束边界；必须在循环前定位，否则会一路吞到表尾
    key_header = _header_row(ws, "重点品牌", "宣发总次数")
    if key_header is None:
        key_header = _header_row(ws, "重点品牌", "6个月")
    stop = key_header if key_header is not None else ws.max_row + 1
    for row in range(header + 1, stop):
        name = _text(ws.cell(row=row, column=name_col).value) if name_col else ""
        if not name or name.startswith("备注"):
            continue
        payload = {
            "pre": _text(ws.cell(row=row, column=pre_col).value) if pre_col else "",
            "open": _text(ws.cell(row=row, column=open_phase).value) if open_phase else "",
            "re": _text(ws.cell(row=row, column=re_col).value) if re_col else "",
        }
        _add_record(
            records, "brand_promo", payload,
            name=name, category="new",
            occurred_on=_day(ws.cell(row=row, column=open_col).value) if open_col else None,
            amount=_num(ws.cell(row=row, column=sales_col).value) if sales_col else None,
        )
    if key_header is None:
        return
    columns = _columns(ws, key_header)
    name_col = _col(columns, "重点品牌") or _col(columns, "品牌")
    sales_col = _col(columns, "月均销售")
    times_col = _col(columns, "宣发总次数") or _col(columns, "6个月")
    channels_col = _col(columns, "渠道")
    last_col = _col(columns, "最近一次")
    for row in range(key_header + 1, ws.max_row + 1):
        if _flat(ws.cell(row=row, column=1).value).startswith("备注"):
            break
        name = _text(ws.cell(row=row, column=name_col).value) if name_col else ""
        if not name:
            continue
        payload = {
            "times": _text(ws.cell(row=row, column=times_col).value) if times_col else "",
            "channels": _text(ws.cell(row=row, column=channels_col).value) if channels_col else "",
        }
        _add_record(
            records, "brand_promo", payload,
            name=name, category="key",
            amount=_num(ws.cell(row=row, column=sales_col).value) if sales_col else None,
            occurred_on=_day(ws.cell(row=row, column=last_col).value) if last_col else None,
        )


_COMPETITOR = (
    ("商业面积", "commercial_area", "商业面积", "text"),
    ("铺位数", "shop_count", "铺位数", "num"),
    ("出租率", "occupancy_rate", "出租率", "rate"),
    ("客流", "traffic", "客流", "text"),
    ("车流", "car_flow", "车流", "text"),
    ("本周新进品牌", "new_brands", "本周新进品牌", "text"),
    ("本周营销活动", "campaign", "本周营销活动", "text"),
)


def _parse_competitor(ws, metrics: list[dict], warnings: list[str]) -> None:
    start = None
    for row in range(1, ws.max_row + 1):
        if _flat(ws.cell(row=row, column=1).value).startswith("商业面积"):
            start = row
            break
    if start is None:
        warnings.append("周报未找到竞品表（商业面积/铺位数）")
        return
    for row in range(start, min(start + 8, ws.max_row + 1)):
        label = _flat(ws.cell(row=row, column=1).value)
        item = next((entry for entry in _COMPETITOR if label.startswith(entry[0])), None)
        if not item:
            continue
        _, key, label_text, kind = item
        for col, side in ((2, "self"), (3, "rival")):
            raw = ws.cell(row=row, column=col).value
            value = _rate(raw) if kind == "rate" else _num(raw) if kind == "num" else _text(raw)
            if value in (None, ""):
                continue
            _add_metric(metrics, "weekly.competitor", f"weekly.competitor.{key}.{side}",
                        f"竞品对比·{label_text}·{'我项目' if side == 'self' else '竞品'}", value,
                        "" if kind in ("rate", "text") else "")


def _parse_fund(ws, metrics: list[dict], warnings: list[str]) -> None:
    headers = [row for row in range(1, ws.max_row + 1) if "年度情况" in _row_text(ws, row, 12)]
    if not headers:
        warnings.append("周报未找到维修基金表（年度情况）")
        return
    for header in headers:
        year_match = re.search(r"(20\d{2})", _row_text(ws, header, 6))
        year = year_match.group(1) if year_match else "未标注"
        columns = _columns(ws, header)
        labels = [text for _, text in columns]
        for row in range(header + 1, min(header + 15, ws.max_row + 1)):
            if "年度情况" in _row_text(ws, row, 3):
                break
            category = _text(ws.cell(row=row, column=1).value)
            if not category:
                continue  # 「项 / 预算」子表头行
            if category.startswith("备注"):
                break
            for index, stage in enumerate(_FUND_STAGES):
                stage_label = _FUND_STAGE_LABELS.get(stage, stage)
                count = _num(ws.cell(row=row, column=2 + index * 2).value)
                amount = _num(ws.cell(row=row, column=3 + index * 2).value)
                _add_metric(metrics, "weekly.fund", f"weekly.fund.{year}.{category}.{stage}_count",
                            f"维修基金·{year}·{category}·{stage_label}·项数", count, "项")
                _add_metric(metrics, "weekly.fund", f"weekly.fund.{year}.{category}.{stage}_amount",
                            f"维修基金·{year}·{category}·{stage_label}·金额", amount, "元")


def _parse_weekly_texts(ws, texts: list[dict], warnings: list[str]) -> None:
    for row in range(1, ws.max_row + 1):
        text = _flat(ws.cell(row=row, column=1).value)
        if not text:
            continue
        if "重点品牌招商进度" in text:
            content = _text(ws.cell(row=row, column=1).value)
            if content.strip() == "重点品牌招商进度：":
                content = ""
            if not content:
                content = _next_text(ws, row + 1)
            _add_text(texts, "progress", content)
        elif text.startswith("缺口分析"):
            _add_text(texts, "gap_analysis", _next_text(ws, row + 1))
        elif "本周重点及困难事项" in text:
            _add_text(texts, "key_issues", _collect_until(ws, row + 1, ("下周重点工作计划",)))
        elif "下周重点工作计划" in text:
            _add_text(texts, "next_week", _collect_until(ws, row + 1, ()))
        elif "品质提升改造进度" in text:
            _add_text(texts, "renovation_progress", _next_text(ws, row + 1))
        elif text.startswith("2.3品牌宣发"):
            _add_text(texts, "promo", _collect_until(ws, row + 1, ("新品牌", "宣发周期明细")))


def _next_text(ws, row: int) -> str:
    for index in range(row, min(row + 4, ws.max_row + 1)):
        text = _text(ws.cell(row=index, column=1).value)
        if text:
            return text
    return ""


def _collect_until(ws, row: int, stops: tuple[str, ...]) -> str:
    parts: list[str] = []
    for index in range(row, ws.max_row + 1):
        text = _text(ws.cell(row=index, column=1).value)
        if not text:
            continue
        flat = _flat(text)
        if any(flat.startswith(stop) for stop in stops):
            break
        parts.append(text)
    return "\n".join(parts)


# ---------------- 月度工作情况 ----------------

_MONTHLY_SECTIONS = (("一、", "issues"), ("二、", "key_work"), ("三、", "team"), ("四、", "highlights"))
_MONTHLY_PATTERNS = (
    (re.compile(r"合同口径累计应收([\d.]+)万元[，,]\s*实收([\d.]+)万元[，,]\s*收缴率([\d.]+)%"),
     "collection", ("receivable", "received", "rate"), ("num", "num", "rate")),
    (re.compile(r"项目目前整体出租率[：:]\s*([\d.]+)\s*%"), "occupancy", ("rate",), ("rate",)),
    (re.compile(r"原存量商户\s*(\d+)\s*户[，,]\s*现已完成签约\s*(\d+)\s*户"), "signing", ("total", "done"), ("num", "num")),
    (re.compile(r"总体在营商户\s*(\d+)\s*户[，,]\s*经营面积约\s*([\d.]+)\s*平方米"), "occupancy", ("tenants", "area"), ("num", "num")),
)
_MONTHLY_REGION = (
    (re.compile(r"新签品牌\s*(\d+)\s*家[，,]\s*面积合计\s*([\d.]+)\s*㎡"), "leasing", ("new_brands", "new_area")),
    (re.compile(r"续签品牌\s*(\d+)\s*家[，,]\s*面积合计\s*([\d.]+)\s*㎡"), "leasing", ("renew_brands", "renew_area")),
)
_PROJECT_RATE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,10})出租率\s*([\d.]+)\s*%")


_MONTHLY_LABELS = {
    ("collection", "receivable"): "合同口径累计应收（万元）",
    ("collection", "received"): "累计实收（万元）",
    ("collection", "rate"): "合同口径收缴率",
    ("occupancy", "rate"): "整体出租率",
    ("occupancy", "tenants"): "在营商户数",
    ("occupancy", "area"): "经营面积（㎡）",
    ("signing", "total"): "存量商户数",
    ("signing", "done"): "已完成签约数",
    ("leasing", "new_brands"): "新签品牌数",
    ("leasing", "new_area"): "新签面积（㎡）",
    ("leasing", "renew_brands"): "续签品牌数",
    ("leasing", "renew_area"): "续签面积（㎡）",
}


def _clean_project(name: str) -> str:
    return re.sub(r"^(其中|以及|其中：|、|，|及|和)", "", (name or "").strip()).strip()


def _parse_monthly(ws) -> dict:
    metrics: list[dict] = []
    texts: list[dict] = []
    warnings: list[str] = []
    title = _text(ws.cell(row=1, column=1).value)
    report_date = ""
    for row in range(1, min(ws.max_row, 6) + 1):
        text = _text(ws.cell(row=row, column=1).value)
        if text.startswith("时间"):
            report_date = text
    period = _period_from(title) or _period_from(report_date)
    if period is None:
        warnings.append("月报未识别到期间（标题或时间行）")

    sections: dict[str, list[str]] = {}
    current = ""
    for row in range(1, ws.max_row + 1):
        text = _text(ws.cell(row=row, column=1).value)
        if not text:
            continue
        head = next((key for prefix, key in _MONTHLY_SECTIONS if text.startswith(prefix)), None)
        if head:
            current = head
            sections.setdefault(current, []).append(text)
        elif current:
            sections[current].append(text)
    for key, parts in sections.items():
        texts.append({"section": key, "subsection": "", "content": "\n".join(parts), "sort": len(texts) * 10})

    body = "\n".join(item["content"] for item in texts)
    seen: set[str] = set()

    def put(project: str, domain: str, field_name: str, raw: str, kind: str) -> None:
        value = _rate(raw) if kind == "rate" else _num(raw)
        if value is None:
            return
        prefix = f"monthly.{project}" if project else "monthly"
        key = f"{prefix}.{domain}.{field_name}"
        if key in seen:
            return
        seen.add(key)
        unit = _MONTHLY_UNITS.get(field_name, "")
        label = _MONTHLY_LABELS.get((domain, field_name), f"{domain}·{field_name}")
        _add_metric(metrics, f"monthly.{domain}", key, label, value, unit)

    blocks = re.split(r"\n(?=（[一二三四五六七八九十]+）)", body)
    for block in blocks:
        head = re.match(r"（[一二三四五六七八九十]+）\s*([^\n（]+)", block)
        project = _clean_project(head.group(1)) if head else ""
        for pattern, domain, names, kinds in _MONTHLY_PATTERNS:
            for match in pattern.finditer(block):
                for name, raw, kind in zip(names, match.groups(), kinds):
                    put(project, domain, name, raw, kind)
    for pattern, domain, names in _MONTHLY_REGION:
        for match in pattern.finditer(body):
            for name, raw in zip(names, match.groups()):
                put("", domain, name, raw, "num")
    for match in _PROJECT_RATE.finditer(body):
        project = _clean_project(match.group(1))
        if any(word in project for word in ("整体", "平均", "期末", "项目目前")):
            continue
        put(project, "occupancy", "rate", match.group(2), "rate")
    if not metrics:
        warnings.append("月报未抽到可量化指标（只保留叙述文本）")
    return {
        "metrics": metrics, "records": [], "texts": texts, "warnings": warnings,
        "period": period[0] if period else "",
        "period_start": period[1] if period else None,
        "period_end": period[2] if period else None,
        "project": "云南区域",
        "title": title,
    }


# ---------------- 入口 ----------------

def _sheet_weight(ws, limit: int) -> int:
    """候选页排序分：有 dimension 用 max_row×max_column；缺失时按实际非空行数。

    缺失 `<dimension>` 的第三方文件在 read_only 下 max_row/max_column 为 None，
    若直接按 0 比较会退化为「第一页」。这里先 reset_dimensions() 再按非空行计数，
    计数受 limit 兜底（超限只记 limit+1），避免为辅助页做无界扫描。
    """
    rows = ws.max_row or 0
    columns = ws.max_column or 0
    if rows > 0:
        return rows * max(columns, 1)
    ws.reset_dimensions()
    count = 0
    width = 0
    for row in ws.iter_rows(values_only=True):
        values = tuple(row[:_CACHE_COLUMNS])
        width = max(width, len(values))
        if all(_is_blank(value) for value in values):
            continue
        count += 1
        if count > limit:
            count = limit + 1
            break
    return count * max(width, 1)


def _data_sheet(workbook):
    """取数据工作表：跳过「填写说明」，选行数最多的页；缺 dimension 时按实际非空行数比较。"""
    candidates = [ws for ws in workbook.worksheets if ws.title != "填写说明"] or list(workbook.worksheets)
    if not candidates:
        raise ValueError("文件没有可读取的工作表")
    best = candidates[0]
    best_score = -1
    for ws in candidates:
        score = _sheet_weight(ws, MAX_ROWS)
        if score > best_score:
            best, best_score = ws, score
    return best


def _single_sheet_view(workbook) -> _SheetView:
    ws = _data_sheet(workbook)
    ws.reset_dimensions()
    return _SheetView(ws, MAX_ROWS, [0])


def parse(report_type: str, filename: str, content: bytes) -> dict:
    """解析上传文件；返回 {metrics, records, texts, warnings, period…, project, title}。"""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(content), data_only=True, read_only=True,
        )
    except Exception as exc:  # noqa: BLE001 Excel 解析失败统一转中文
        raise ValueError("文件不是可读取的 xlsx（请用 Excel 另存为 .xlsx 后重试）") from exc
    try:
        # 只构建该类报表真正解析的页签：周报/月报仅数据页，内控仅表1..表6；
        # 「填写说明」等辅助页不参与解析，也不占用 20 万行预算。
        if report_type == "control":
            wb = _WorkbookView(
                workbook, MAX_ROWS,
                prefixes=("表1", "表2", "表3", "表4", "表5", "表6"),
            )
            parsed = _parse_control(wb)
        elif report_type == "weekly":
            parsed = _parse_weekly(
                _single_sheet_view(workbook),
                "曲靖项目" if "曲靖" in filename else "云南项目",
            )
        elif report_type == "monthly":
            parsed = _parse_monthly(_single_sheet_view(workbook))
        else:
            raise ValueError("报表类型应为 control / weekly / monthly")
    finally:
        workbook.close()
    parsed.setdefault("title", filename.rsplit(".", 1)[0])
    parsed["source_file"] = filename
    parsed["report_type"] = report_type
    return parsed
