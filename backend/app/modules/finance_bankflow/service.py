"""finance_bankflow：银行流水 xlsx → BIP「银行对账单」导入模板转换服务。

输入：建行银企直联导出的流水 xlsx（单 sheet，表头含「交易日期/账号/银行交易流水号」等 26 列）。
输出：BIP 银行流水导入模板 xlsx——结构以用户提供的官方样例为蓝本，静态内容（ReadMe 使用指南、
币种参照表、下拉配置、importTemplateInfo JSON）以常量内置，均无敏感信息；
真实「银行账号 → H 开头账套编码」映射放模块内 config.local.yaml（gitignore，不入仓库）。
"""
from __future__ import annotations

import hashlib
import json
from copy import copy
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl
import yaml

# 模板结构与全部格式由官方骨架 template_skeleton.xlsx 提供（表头/样式/列宽/数据验证/辅助 sheet）。


# ---------------- 账户 → H 开头账套编码映射 ----------------

_CONFIG_PATH = Path(__file__).resolve().parent / "config.local.yaml"

# 官方模板骨架：保留全部格式（表头样式/列宽/数据验证下拉/文本格式@/4 个辅助 sheet）。
# 骨架由官方样例清空数据行（r10 起）制成，含官方示例行（假数据），无真实业务数据。
_SKELETON_PATH = Path(__file__).resolve().parent / "template_skeleton.xlsx"
_SKELETON_LAST_ROW = 9  # 骨架最后一行（官方示例行：其样式即数据行样式模板）


def _load_skeleton_bytes() -> bytes:
    if not _SKELETON_PATH.exists():
        raise ValueError("模板骨架文件缺失（template_skeleton.xlsx），无法生成保留官方格式的模板")
    return _SKELETON_PATH.read_bytes()


def load_account_map(path: Path | None = None) -> dict[str, dict[str, str]]:
    """读「银行账号 → {ent: H 开头账套编码, name: 项目名}」映射；配置缺失返回空 dict。"""
    cfg_path = path or _CONFIG_PATH
    if not cfg_path.exists():
        return {}
    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    accounts = data.get("bank_accounts") or {}
    out: dict[str, dict[str, str]] = {}
    for account, info in accounts.items():
        if not account or not isinstance(info, dict):
            continue
        ent = str(info.get("ent") or "").strip()
        if not ent:
            continue
        out[str(account).strip()] = {"ent": ent, "name": str(info.get("name") or "").strip()}
    return out


def resolve_account(acc_map: dict[str, dict[str, str]], account: str) -> dict[str, str] | None:
    """账号 → 映射：全号精确优先，未命中再按尾号后缀匹配（最长后缀优先）。"""
    meta = acc_map.get(account)
    if meta:
        return meta
    candidates = [
        (len(key), info)
        for key, info in acc_map.items()
        if len(key) >= 4 and account.endswith(key)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


# ---------------- 流水解析 ----------------

# 核心必需列（建行/中行银企直联导出共有）。流水号列两家不同：建行有「银行交易流水号」，
# 中行没有（退化为「对账批号/资金系统单据号/单证号码」候选，全空时按行内容合成稳定唯一值）。
_FLOW_REQUIRED = ("交易日期", "账号", "收入", "支出", "用途")
_SEQ_CANDIDATES = ("银行交易流水号", "交易流水号", "对账批号", "资金系统单据号", "单证号码")


def _locate_header_rows(wb):
    """返回全部含表头的工作表 [(worksheet, 1-based 行号)]。

    首张可能是封面/说明页；同一工作簿也可能按账户拆成多张数据 sheet，
    必须全部遍历（M7：旧实现只看第一张，第二张及以后静默丢失）。
    """
    found = []
    for ws in wb.worksheets:
        for row_no, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), start=1):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if "交易日期" in cells and "账号" in cells:
                found.append((ws, row_no))
                break
    if not found:
        raise ValueError("未找到流水表头行（需同时含「交易日期」与「账号」列；已检查全部工作表前 10 行）")
    return found


def _synthetic_seq(row: dict[str, Any]) -> str:
    """无银行流水号（中行等）时合成稳定唯一值：同一笔流水反复生成结果一致（幂等），
    不同流水因余额/金额/对方/用途参与哈希而几乎不重复。"""
    raw = "|".join(
        "" if row.get(k) is None else str(row.get(k))
        for k in ("tran_date", "account", "income", "expense", "balance", "counter_name", "use_name")
    )
    return "SYN" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20].upper()


def _norm_date(v: Any) -> str | None:
    """交易日期归一为 YYYYMMDD；解析失败返回 None。"""
    if v is None or v == "":
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y%m%d")
    text = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return None


def _norm_amount(v: Any) -> float | None:
    """金额归一：'-' / '' / None → None；其余转 float（容千分位逗号），失败返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    text = str(v).strip()
    if text in ("", "-"):
        return None
    try:
        return round(float(text.replace(",", "")), 2)
    except ValueError:
        return None


def _norm_currency(v: Any) -> str:
    """'CNY-人民币' → '人民币##CNY'；已含 ## 原样返回；无法解析时返回原文。"""
    text = str(v or "").strip()
    if not text:
        return "人民币##CNY"
    if "##" in text:
        return text
    if "-" in text:
        code, _, name = text.partition("-")
        code, name = code.strip(), name.strip()
        if code and name:
            return f"{name}##{code}"
    return text


def mask_account(account: str) -> str:
    """账号脱敏：仅保留尾 4 位（告警/错误文案不得暴露完整卡号）。"""
    text = str(account or "")
    return f"{'*' * max(0, len(text) - 4)}{text[-4:]}" if text else "****"


def row_problems(row: dict[str, Any]) -> list[str]:
    """单行硬性问题（会导必填空白或金额归属不明）：双金额、无金额、空用途、坏日期。"""
    problems: list[str] = []
    if row["income"] is not None and row["expense"] is not None:
        problems.append("收入与支出同时有值，无法判断收付方向")
    elif row["income"] is None and row["expense"] is None:
        problems.append("收入与支出均为空，金额必填不能为空")
    if not row.get("use_name"):
        problems.append("用途为空，BIP 模板用途列必填")
    if not row["tran_date"]:
        problems.append("交易日期无法解析（支持 YYYY-MM-DD / YYYYMMDD / 日期单元格）")
    return problems


def _non_date_problems(row: dict[str, Any]) -> list[str]:
    return [p for p in (row.get("problems") or []) if not p.startswith("交易日期无法解析")]


def parse_flow(data: bytes) -> list[dict[str, Any]]:
    """解析银行流水 xlsx 为规范化行列表；结构异常抛 ValueError（中文）。

    每行带 ``row_no``（Excel 行号）与 ``problems``（硬性问题），供预览提示与
    模板生成时明确拒绝——旧实现把双金额/无金额/坏日期静默导出成必填空白。
    """
    try:
        wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"流水文件无法解析：{type(exc).__name__}") from exc
    try:
        sheets = _locate_header_rows(wb)
        rows: list[dict[str, Any]] = []
        for ws, header_row in sheets:
            headers = [
                str(c).strip() if c is not None else ""
                for c in next(ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True))
            ]
            missing = [k for k in _FLOW_REQUIRED if k not in headers]
            if missing:
                detected = "、".join(h for h in headers if h)[:300] or "（空）"
                raise ValueError(
                    f"工作表「{ws.title}」缺少必需列：{'、'.join(missing)}；检测到的列：{detected}"
                )
            idx = {name: i for i, name in enumerate(headers)}

            def cell(raw, name, idx=idx):
                i = idx.get(name)
                if i is None or len(raw) <= i:
                    return None
                return raw[i]

            seq_col = next((c for c in _SEQ_CANDIDATES if c in idx), None)
            for row_no, raw in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
                account = str(cell(raw, "账号") or "").strip()
                if not account:
                    continue
                item = {
                    "sheet": ws.title,
                    "row_no": row_no,
                    "account": account,
                    "bank": str(cell(raw, "银行") or "").strip(),
                    "tran_date": _norm_date(cell(raw, "交易日期")),
                    "income": _norm_amount(cell(raw, "收入")),
                    "expense": _norm_amount(cell(raw, "支出")),
                    "seq": str(cell(raw, seq_col) or "").strip() if seq_col else "",
                    "use_name": str(cell(raw, "用途") or "").strip(),
                    "remark": str(cell(raw, "附言(对账码)") or "").strip(),
                    "balance": _norm_amount(cell(raw, "当前余额")),
                    "counter_name": str(cell(raw, "对方户名") or "").strip(),
                    "counter_account": str(cell(raw, "对方账号") or "").strip(),
                    "currency": _norm_currency(cell(raw, "币种")),
                }
                if not item["seq"]:
                    item["seq"] = _synthetic_seq(item)
                item["problems"] = row_problems(item)
                rows.append(item)
        return rows
    finally:
        wb.close()


# ---------------- 汇总与模板生成 ----------------

def summarize_flow(data: bytes) -> dict[str, Any]:
    """解析并按账号汇总：{total, valid_total, warnings, accounts: [...]}。

    accounts[] 额外给出 invalid_rows 与 invalid 分类计数（双金额/无金额/坏日期），
    让预览页明确提示哪些行无法生成模板，而不是静默丢行（L-081/L-082）。
    """
    rows = parse_flow(data)
    accounts: dict[str, dict[str, Any]] = {}
    for row in rows:
        info = accounts.setdefault(
            row["account"],
            {"rows": 0, "invalid_rows": 0,
             "invalid": {"amount_conflict": 0, "missing_amount": 0, "missing_use": 0, "bad_date": 0},
             "date_min": None, "date_max": None, "bank": ""},
        )
        if row.get("bank") and not info["bank"]:
            info["bank"] = row["bank"]
        problems = row.get("problems") or []
        if problems:
            info["invalid_rows"] += 1
            if "收入与支出同时有值，无法判断收付方向" in problems:
                info["invalid"]["amount_conflict"] += 1
            if "收入与支出均为空，金额必填不能为空" in problems:
                info["invalid"]["missing_amount"] += 1
            if any(p.startswith("用途为空") for p in problems):
                info["invalid"]["missing_use"] += 1
            if any(p.startswith("交易日期无法解析") for p in problems):
                info["invalid"]["bad_date"] += 1
            continue
        info["rows"] += 1
        d = row["tran_date"]
        if d:
            info["date_min"] = min(info["date_min"], d) if info["date_min"] else d
            info["date_max"] = max(info["date_max"], d) if info["date_max"] else d
    acc_map = load_account_map()
    out = []
    warnings: list[str] = []
    for account, info in accounts.items():
        meta = resolve_account(acc_map, account) or {}
        out.append(
            {
                "account": account,
                "ent": meta.get("ent") or "",
                "name": meta.get("name") or "",
                "bank": info["bank"],
                "bank_label": bank_label(info["bank"]),
                "rows": info["rows"],
                "invalid_rows": info["invalid_rows"],
                "invalid": info["invalid"],
                "date_min": info["date_min"],
                "date_max": info["date_max"],
            }
        )
        if info["invalid_rows"]:
            warnings.append(
                f"账号 {mask_account(account)} 有 {info['invalid_rows']} 行无法转换："
                f"双金额 {info['invalid']['amount_conflict']}、"
                f"无金额 {info['invalid']['missing_amount']}、"
                f"空用途 {info['invalid']['missing_use']}、"
                f"坏日期 {info['invalid']['bad_date']}；这些行不会进入模板，请先修正源文件"
            )
    valid_total = sum(item["rows"] for item in out)
    return {"total": len(rows), "valid_total": valid_total, "warnings": warnings, "accounts": out}


def _format_bad_rows(bad: list[dict[str, Any]]) -> str:
    """异常行摘要：工作表/行号 + 全部原因（最多 10 行，防止提示过长）。"""
    def describe(r: dict[str, Any]) -> str:
        sheet = str(r.get("sheet") or "").strip()
        prefix = f"{sheet}!" if sheet else ""
        reasons = "、".join(r.get("problems") or ["无法转换"])
        return f"第 {r['row_no']} 行（{prefix}）：{reasons}" if prefix else f"第 {r['row_no']} 行：{reasons}"

    shown = "；".join(describe(r) for r in bad[:10])
    extra = f"（另有 {len(bad) - 10} 行）" if len(bad) > 10 else ""
    return f"{shown}{extra}"


def build_template(
    rows: list[dict[str, Any]],
    account: str,
    ent_code: str,
    date_start: str | None,
    date_end: str | None,
) -> tuple[bytes, int]:
    """按账号+日期区间过滤流水并生成模板 xlsx；返回 (bytes, 导出行数)。

    先按日期区间筛选，再校验选中行的必填列（收入/支出金额、用途）；区间外的
    空值行不阻塞本月导出。坏日期无法参与区间筛选，单独列出并要求修正（M5）。
    硬性拒绝（ValueError，路由层 400）时不会生成任何必填空白行（L-081/L-082/I3）。
    """
    lo = date_start or "00000000"
    hi = date_end or "99999999"
    masked = mask_account(account)
    account_rows = [r for r in rows if r["account"] == account]
    bad_date = [r for r in account_rows if not r["tran_date"]]
    selected = [r for r in account_rows if r["tran_date"] and lo <= r["tran_date"] <= hi]
    invalid_selected = [r for r in selected if _non_date_problems(r)]
    if invalid_selected:
        raise ValueError(
            f"账号 {masked} 所选日期区间内有 {len(invalid_selected)} 行缺少/无法识别必填信息"
            f"（金额或用途）：{_format_bad_rows(invalid_selected)}。请先修正源文件后重试"
        )
    if bad_date:
        raise ValueError(
            f"账号 {masked} 有 {len(bad_date)} 行交易日期无法解析，无法按区间筛选："
            f"{_format_bad_rows(bad_date)}。请先修正源文件后重试"
        )
    # 以官方骨架为基底：表头样式/列宽/数据验证下拉/文本格式 @/辅助 sheet 全部原样保留，
    # 仅从骨架末行（官方示例行）之后追加数据；每列样式复制示例行，确保导入端可解析。
    wb = openpyxl.load_workbook(BytesIO(_load_skeleton_bytes()))
    ws = wb["银行对账单"]
    style_row = [
        ws.cell(row=_SKELETON_LAST_ROW, column=c) for c in range(1, 18)
    ]
    r = _SKELETON_LAST_ROW + 1
    for row in selected:
        income, expense = row["income"], row["expense"]
        if income is not None:
            dc, amt = ("收入", income)
        else:
            dc, amt = ("支出", expense)
        values = [
            "", "", "", ent_code, row["tran_date"], account, row["currency"], dc,
            amt, row["use_name"], row["remark"] or row["use_name"], row["seq"], row["balance"],
            row["counter_account"], "", row["counter_name"], "",
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=value)
            cell._style = copy(style_row[col - 1]._style)
        r += 1
    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue(), r - _SKELETON_LAST_ROW - 1


def bank_label(bank: str) -> str:
    """银行简称（用于下载文件名前缀）：BOC→中行、CCB→建行、ICBC→工行、ABC→农行。"""
    b = (bank or "").upper()
    if "BOC" in b or "中国银行" in b:
        return "中行"
    if "CCB" in b or "建设银行" in b:
        return "建行"
    if "ICBC" in b or "工商银行" in b:
        return "工行"
    if "ABC" in b or "农业银行" in b:
        return "农行"
    return "银行"


def export_filename(account: str, date_start: str | None, date_end: str | None, bank: str = "") -> str:
    """下载文件名：{银行简称}{尾4}_银行流水导入模板_{start}-{end}.xlsx（照用户样本口径）。"""
    tail = account[-4:] if len(account) >= 4 else account
    start = (date_start or "").replace("-", "") or "00000000"
    end = (date_end or "").replace("-", "") or "99999999"
    return f"{bank_label(bank)}{tail}_银行流水导入模板_{start}-{end}.xlsx"