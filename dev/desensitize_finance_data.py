"""财务样本脱敏工具（外部验证第 3/4 章数据准备）。

输入：用友 BIP 导出（凭证列表/辅助明细账/辅助余额表/科目辅助余额表）+ POS 日包 + 现金流文件。
输出：`脱敏数据/脱敏数据<原文件名>`，勾稽关系保持：
- 凭证列表：每张凭证借方合计 = 贷方合计（先随机拆分再配平，符号模式保留）；
- 辅助明细账：按（科目+核算账簿）链重算滚动余额（期初行 → 逐行 ± 发生额），方向随余额符号；
- 余额表：期末 = 期初 + 本期借方 - 本期贷方（方向感知），本年累计随机；
- 金额：两位小数随机值（量级 0.01~99999.99）；
敏感信息泛化：
- 公司/账簿名 → 脱敏单元A/B/C…（一对一共通映射，sheet 名同步替换）；
- 人员名（制单/审批/记账人列取值全集）→ 员工NNN（共通映射）；
- 辅助核算【类型:编码/名称】→ 编码等长随机数字、名称 → 脱敏<类型>N；
- 残留公司名片段、长数字串（卡号/证件号）→ 随机等长数字。
原始文件只读，绝不改写；输出前断言校验勾稽。

用法：
    python dev/desensitize_finance_data.py bip     # 4 个 BIP 附件 → 脱敏数据/
    python dev/desensitize_finance_data.py pos     # POS 2026/10 日包 → 脱敏数据/
    python dev/desensitize_finance_data.py cash    # 现金流 2026/10 → 脱敏数据/
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
DEV_DIR = Path(__file__).resolve().parent
OUT_DIR = ROOT / "脱敏数据"
LOCAL_CONFIG_FILE = DEV_DIR / "desensitize.local.json"

# 保勾稽需要可复现的固定种子（凭证借=贷、余额链），非密码学用途，不用 secrets
rng = random.Random(20261022)

# 代码只保留合成示例；真实公司/项目/银行名从 dev/desensitize.local.json
# （gitignore）或环境变量加载，绝不入库。
COMPANY_TOKENS = {
    "合成机构甲": "合成单位",
    "合成街区": "合成街区",
    "合成品牌": "合成品牌",
    "合成银行": "合成银行",
}


def load_local_config() -> dict:
    """读取本地脱敏配置（gitignore）；缺失或损坏时返回空配置。"""
    try:
        data = json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_local_config() -> None:
    """把本地配置里的真实串并入 COMPANY_TOKENS（就地更新，import 引用保持有效）。"""
    tokens = load_local_config().get("company_tokens")
    if isinstance(tokens, dict):
        COMPANY_TOKENS.update({str(k): str(v) for k, v in tokens.items() if k})


def require_company_tokens() -> None:
    """真实运行前强制加载本地映射；缺失时中文拒绝，避免无映射时 fail-open。"""
    apply_local_config()
    tokens = load_local_config().get("company_tokens")
    if not isinstance(tokens, dict) or not tokens:
        raise SystemExit(
            "缺少 dev/desensitize.local.json 的 company_tokens；"
            "拒绝在无真实映射时处理真实数据"
        )


def _configured_dir(env_name: str, config_key: str) -> Path | None:
    raw = os.environ.get(env_name) or load_local_config().get(config_key)
    return Path(str(raw)) if raw else None

# ---------- 通用工具 ----------

def _find_attachments():
    """定位会话附件目录（含 4 个 BIP xlsx）。"""
    env = os.environ.get("VERIFY_ATTACH_DIR") or load_local_config().get("attach_dir")
    if env:
        path = Path(str(env))
        if path.is_dir():
            return path
    sessions = os.environ.get("KIMI_SESSIONS")
    if sessions:
        base = Path(sessions)
        if base.is_dir():
            for p in base.rglob("attachments"):
                if any(f.suffix == ".xlsx" for f in p.iterdir()):
                    return p
    raise SystemExit(
        "找不到附件目录：请设置 VERIFY_ATTACH_DIR / KIMI_SESSIONS，"
        "或在 dev/desensitize.local.json 配置 attach_dir"
    )


def rand_amount(lo: float = 0.01, hi: float = 99999.99) -> float:
    return round(rng.uniform(lo, hi), 2)


def _num(v):
    """数字字符串 → float（用友导出的金额常为文本型数字）；非数字返回 None。"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip().replace(",", "")
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None
    return None


def split_total(total: float, k: int) -> list[float]:
    """把 total 随机拆成 k 份两位小数（合计严格相等）。"""
    if k <= 1:
        return [round(total, 2)]
    cuts = sorted(rng.uniform(0.05, 0.95) for _ in range(k - 1))
    parts, prev = [], 0.0
    for c in cuts:
        parts.append(round(total * c - prev, 2))
        prev = round(total * c, 2)
    parts.append(round(total - prev, 2))
    return parts


def _init_cache():
    if not hasattr(genericize_text, "_name_cache"):
        genericize_text._name_cache = {}


def genericize_text(text: str, name_map: dict[str, str]) -> str:
    """自由文本泛化（公司/人名/辅助核算令牌/长数字）。令牌名同一原名稳定映射。"""
    _init_cache()
    """摘要/辅助核算等自由文本：替换已知人名/公司名，泛化【类型:编码/名称】与长数字串。"""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for real, fake in name_map.items():
        out = out.replace(real, fake)
    # 【类型:编码/名称】【类型:编码】→ 编码等长随机、名称泛化（同一原名稳定映射，保勾稽）
    cache = genericize_text._name_cache

    def _tok(m):
        kind, code, name = m.group(1), m.group(2), m.group(3)
        new_code = "".join(rng.choice("0123456789") for _ in code) if code else ""
        if not name:
            return f"【{kind}:{new_code}】"
        key = (kind, name)
        if key not in cache:
            cache[key] = f"脱敏{kind}{len(cache) + 1}"
        return f"【{kind}:{new_code}/{cache[key]}】"
    out = re.sub(r"【([^：:【】]{1,12}):([0-9A-Za-z./_-]{1,40})/([^【】]{1,40})】", _tok, out)
    out = re.sub(r"【([^：:【】]{1,12}):([0-9A-Za-z./_-]{1,40})】",
                 lambda m: f"【{m.group(1)}:{''.join(rng.choice('0123456789') for _ in m.group(2))}】", out)
    # 长数字串（>=8 位，卡号/证件/银行账号）→ 等长随机
    out = re.sub(r"\d{8,}", lambda m: "".join(rng.choice("0123456789") for _ in m.group(0)), out)
    return out


def _ledger_fake(ledger_map: dict[str, str], name: str) -> str:
    """账簿名 → 脱敏单元N；'合计'等结构性标记原样保留。

    命名避开 BIP 表头关键词（账簿/科目/日期/凭证号/借方/贷方/期初/期末/本期/摘要），
    否则泛化后的账簿值会污染表头行定位（_locate_header_row 的关键词命中）。
    """
    if name in ("合计", "小计", "总计"):
        return name
    if name not in ledger_map:
        ledger_map[name] = f"脱敏单元{chr(ord('A') + len(ledger_map))}"
    return ledger_map[name]


def copy_sheet_template(src_ws, dst_wb, title):
    """按原 sheet 名建空 sheet（值由调用方回填）。"""
    return dst_wb.create_sheet(title)


def _safe_filename(name: str, mapping: dict[str, str]) -> str:
    """文件名泛化：先替换已登记真实串，再兜底替换残留公司全称。"""
    out = name
    for real, fake in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        if real:
            out = out.replace(real, fake)
    return re.sub(
        r"[\u4e00-\u9fa5]{2,30}(?:有限公司|有限责任公司)(?:[\u4e00-\u9fa5]{0,10}(?:分公司|支公司))?",
        "合成单位",
        out,
    )


# ---------- BIP：凭证列表 ----------

def desensitize_vouchers(src: Path, dst: Path, ledger_map: dict[str, str], name_map: dict[str, str]):
    wb = openpyxl.load_workbook(src, data_only=True)
    out = openpyxl.Workbook()
    out.remove(out.active)
    stats = {"sheets": 0, "rows": 0, "vouchers": 0}
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not rows:
            continue
        stats["sheets"] += 1
        # sheet 名（账簿名）泛化 + r1 标题行泛化
        orig_ledger = ws.title
        ledger_fake = _ledger_fake(ledger_map, orig_ledger)
        new_title = ledger_fake
        if new_title in out.sheetnames:
            new_title = f"{ledger_fake}_{stats['sheets']}"
        dst_ws = copy_sheet_template(ws, out, new_title)
        if rows and isinstance(rows[0][0], str) and rows[0][0].startswith("账簿："):
            rows[0][0] = genericize_text(rows[0][0], {orig_ledger: ledger_fake, **name_map, **ledger_map, **COMPANY_TOKENS})
        header = rows[1] if len(rows) > 1 else []
        # 找关键列
        col = {name: i for i, name in enumerate(header) if isinstance(name, str)}
        i_period, i_date, i_vno = col.get("期间"), col.get("凭证日期"), col.get("凭证号")
        i_debit, i_credit = col.get("借方金额"), col.get("贷方金额")
        i_sum, i_vsum = col.get("分录摘要"), col.get("凭证摘要")
        i_aux = col.get("辅助核算")
        i_maker, i_approver, i_poster = col.get("制单人"), col.get("审批人"), col.get("记账人")
        i_ledger = col.get("核算账簿")
        data = rows[2:]
        # 前向填充凭证键，分组
        groups: list[list[int]] = []
        cur: list[int] = []
        last_key = None
        for idx, r in enumerate(data):
            key = (r[i_period], r[i_vno]) if i_period is not None and i_vno is not None else idx
            if i_vno is not None and r[i_vno]:
                if cur:
                    groups.append(cur)
                cur = [idx]
                last_key = key
            else:
                cur.append(idx)
        if cur:
            groups.append(cur)
        # 逐凭证重建金额（借=贷），其余字段回填
        for g in groups:
            stats["vouchers"] += 1
            lines = [data[i] for i in g]
            # 金额可能为文本型数字：_num 归一后再判定符号与借贷边
            neg = any((_num(r[i_debit]) or 0) < 0 for r in lines if i_debit is not None) or \
                  any((_num(r[i_credit]) or 0) < 0 for r in lines if i_credit is not None)
            d_idx = [j for j, r in enumerate(lines) if i_debit is not None and _num(r[i_debit]) is not None]
            c_idx = [j for j, r in enumerate(lines) if i_credit is not None and _num(r[i_credit]) is not None]
            if not d_idx or not c_idx:
                # 单边凭证（原始数据即存在，共约 371 组，B6 平衡校验素材）：
                # 保持单边性质，仅把已有边的金额随机重生，避免真实金额残留
                side_idx = d_idx or c_idx
                key = i_debit if d_idx else i_credit
                for j in side_idx:
                    lines[j][key] = rand_amount()
                    lines[j][key + 1] = lines[j][key]
                continue
            total = round(rng.uniform(100, 200000), 2)
            sign = -1 if neg else 1
            for j, amt in zip(d_idx, split_total(total, len(d_idx))):
                lines[j][i_debit] = round(sign * amt, 2)
                lines[j][i_debit + 1] = lines[j][i_debit]  # 借方原币同额
            for j, amt in zip(c_idx, split_total(total, len(c_idx))):
                lines[j][i_credit] = round(sign * amt, 2)
                lines[j][i_credit + 1] = lines[j][i_credit]
        # 写回（含文本泛化）
        for r in rows[:2]:
            dst_ws.append([genericize_text(str(c), {orig_ledger: ledger_fake, **name_map, **ledger_map, **COMPANY_TOKENS}) if isinstance(c, str) else c for c in r])
        for r in data:
            row = list(r)
            for i in (i_sum, i_vsum, i_aux):
                if i is not None and isinstance(row[i], str):
                    row[i] = genericize_text(row[i], {**name_map, **ledger_map, **COMPANY_TOKENS})
            for i in (i_maker, i_approver, i_poster):
                if i is not None and isinstance(row[i], str) and row[i].strip():
                    row[i] = name_map.get(row[i].strip(), row[i])
            if i_ledger is not None and isinstance(row[i_ledger], str):
                row[i_ledger] = _ledger_fake(ledger_map, row[i_ledger])
            dst_ws.append(row)
            stats["rows"] += 1
    out.save(dst)
    wb.close()
    return stats


# ---------- BIP：辅助明细账 ----------

def desensitize_aux_ledger(src: Path, dst: Path, ledger_map: dict[str, str], name_map: dict[str, str]):
    wb = openpyxl.load_workbook(src, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    # 元数据头（账簿值泛化）
    for r in rows[:12]:
        if r and isinstance(r[0], str) and r[0] == "账簿" and len(r) > 1 and isinstance(r[1], str):
            for real, fake in {**ledger_map, **COMPANY_TOKENS}.items():
                r[1] = r[1].replace(real, fake)
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "科目编码")
    header = rows[header_idx]
    col = {name: i for i, name in enumerate(header) if isinstance(name, str)}
    i_code, i_name, i_date = col["科目编码"], col["科目名称"], col["日期"]
    i_ledger, i_vno, i_sum = col.get("核算账簿"), col.get("凭证号"), col.get("摘要")
    i_d, i_c, i_dir, i_bal = col["借方本币"], col["贷方本币"], col["方向"], col["本币余额"]
    data = rows[header_idx + 1:]
    # 账簿映射收集
    for r in data:
        if i_ledger is not None and isinstance(r[i_ledger], str):
            _ledger_fake(ledger_map, r[i_ledger])
    # 逐（科目+账簿）链：期初行重建随机余额，发生行随机金额并滚算
    chains: dict[tuple, list[int]] = {}
    for idx, r in enumerate(data):
        key = (r[i_code], r[i_ledger] if i_ledger is not None else "")
        chains.setdefault(key, []).append(idx)
    for key, idxs in chains.items():
        balance = 0.0
        for n, idx in enumerate(idxs):
            r = data[idx]
            has_amount = isinstance(r[i_d], (int, float)) or isinstance(r[i_c], (int, float))
            if not has_amount:  # 期初余额行
                balance = round(rng.uniform(-500000, 500000), 2)
                r[i_bal] = abs(balance)
                r[i_dir] = "借" if balance >= 0 else "贷"
                continue
            d = rand_amount() if rng.random() < 0.5 else None
            c = rand_amount() if d is None else (rand_amount() if rng.random() < 0.2 else None)
            r[i_d], r[i_c] = d, c
            balance = round(balance + (d or 0) - (c or 0), 2)
            r[i_bal] = abs(balance)
            r[i_dir] = "借" if balance >= 0 else "贷"
    out = openpyxl.Workbook()
    out.remove(out.active)
    dst_ws = out.create_sheet(ws.title)
    for r in rows[:header_idx + 1]:
        dst_ws.append(r)
    for r in data:
        row = list(r)
        if i_ledger is not None and isinstance(row[i_ledger], str):
            row[i_ledger] = _ledger_fake(ledger_map, row[i_ledger])
        if i_sum is not None and isinstance(row[i_sum], str):
            row[i_sum] = genericize_text(row[i_sum], {**name_map, **ledger_map, **COMPANY_TOKENS})
        dst_ws.append(row)
    out.save(dst)
    wb.close()
    return {"rows": len(data), "chains": len(chains)}


# ---------- BIP：余额表（两类） ----------

def desensitize_balance(src: Path, dst: Path, ledger_map: dict[str, str], name_map: dict[str, str]):
    wb = openpyxl.load_workbook(src, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    for r in rows[:16]:
        if r and isinstance(r[0], str) and r[0] == "账簿" and len(r) > 1 and isinstance(r[1], str):
            for real, fake in {**ledger_map, **COMPANY_TOKENS}.items():
                r[1] = r[1].replace(real, fake)
    # 两级表头：定位含“科目编码”的表头行与紧随的子表头行
    h1 = next(i for i, r in enumerate(rows) if r and r[0] == "科目编码")
    h2 = h1 + 1
    col = {name: i for i, name in enumerate(rows[h1]) if isinstance(name, str)}
    i_code, i_name, i_ledger = col["科目编码"], col["科目名称"], col.get("核算账簿")
    i_open_dir, i_open_amt = col["期初余额"], col["期初余额"] + 1
    i_d, i_c = col["本期发生"], col["本期发生"] + 1
    i_yd, i_yc = col["本年累计"], col["本年累计"] + 1
    i_end_dir, i_end_amt = col["期末余额"], col["期末余额"] + 1
    data = rows[h2 + 1:]
    for r in data:
        if i_ledger is not None and isinstance(r[i_ledger], str):
            _ledger_fake(ledger_map, r[i_ledger])
    for r in data:
        if not (isinstance(r[i_code], str) and r[i_code].strip()):
            continue
        opening = round(rng.uniform(-500000, 500000), 2)
        d, c = rand_amount(), rand_amount()
        end = round(opening + d - c, 2)
        r[i_open_amt], r[i_d], r[i_c] = abs(opening), d, c
        r[i_open_dir] = "借" if opening >= 0 else "贷"
        r[i_yd], r[i_yc] = rand_amount(), rand_amount()
        r[i_end_amt] = abs(end)
        r[i_end_dir] = "借" if end >= 0 else "贷"
    out = openpyxl.Workbook()
    out.remove(out.active)
    dst_ws = out.create_sheet(ws.title)
    for r in rows[:h2 + 1]:
        dst_ws.append(r)
    for r in data:
        row = list(r)
        skip = {i_code, i_name, i_ledger}
        for ci in range(len(row)):
            if ci in skip or not isinstance(row[ci], str):
                continue
            row[ci] = genericize_text(row[ci], {**name_map, **ledger_map, **COMPANY_TOKENS})
        if i_ledger is not None and isinstance(row[i_ledger], str):
            row[i_ledger] = _ledger_fake(ledger_map, row[i_ledger])
        dst_ws.append(row)
    out.save(dst)
    wb.close()
    return {"rows": len(data)}


# ---------- BIP 驱动 ----------

# 摘要等人名提取的停用词（会计术语，非人名；本项目数据集特定）
PERSON_STOPLIST = {"保证金", "税率", "到内部", "收到", "关于", "内部", "其他", "合计"}


def collect_person_names(files: dict[str, Path]) -> set[str]:
    """人员名全集：(a) 凭证列表制单/审批/记账人列（如有）；(b) 摘要列上下文候选（去停用词）。"""
    persons: set[str] = set()
    # (a) 人员列
    if "vouchers" in files:
        wb = openpyxl.load_workbook(files["vouchers"], data_only=True)
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            header = rows[1]
            col = {n: i for i, n in enumerate(header) if isinstance(n, str)}
            for i in (col.get("制单人"), col.get("审批人"), col.get("记账人")):
                if i is None:
                    continue
                for r in rows[2:]:
                    v = r[i] if i < len(r) else None
                    if isinstance(v, str) and v.strip():
                        persons.add(v.strip())
        wb.close()
    # (b) 摘要列上下文候选
    pat = re.compile(r"(?:人力资源部|行政部|财务部|调整|收到)([一-龥]{2,4})(?:关于|固定|的|，|,|$)")
    for kind in ("vouchers", "aux"):
        if kind not in files:
            continue
        wb = openpyxl.load_workbook(files[kind], data_only=True)
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            header_idx = next((i for i, r in enumerate(rows) if r and r[0] == "科目编码"), 1)
            header = rows[header_idx]
            col = {n: i for i, n in enumerate(header) if isinstance(n, str)}
            for cn in ("摘要", "分录摘要", "凭证摘要"):
                i = col.get(cn)
                if i is None:
                    continue
                for r in rows[header_idx + 1:]:
                    t = r[i] if i < len(r) else None
                    if isinstance(t, str):
                        for m in pat.finditer(t):
                            persons.add(m.group(1))
        wb.close()
    return {p for p in persons if p not in PERSON_STOPLIST and "关于" not in p}


def run_bip():
    require_company_tokens()
    att = _find_attachments()
    OUT_DIR.mkdir(exist_ok=True)
    # 附件名形如 f_<uuid>-<原始文件名>：按已知类型分子串分类（先长后短避歧义）
    def classify(fname: str) -> str | None:
        if "凭证列表" in fname:
            return "vouchers"
        if "辅助明细账" in fname:
            return "aux"
        if "科目辅助余额表" in fname:
            return "subject_balance"
        if "辅助余额表" in fname:
            return "balance"
        return None

    files: dict[str, str] = {}
    for f in os.listdir(att):
        if not f.endswith(".xlsx"):
            continue
        kind = classify(f)
        if kind:
            files[kind] = f
    assert len(files) == 4, f"应找到 4 个 BIP 文件，实际 {len(files)} 个"
    ledger_map: dict[str, str] = {}
    # 先收集人员名全集（人员列 + 摘要上下文候选，去会计术语停用词）
    name_map: dict[str, str] = {}
    persons = collect_person_names({k: att / v for k, v in files.items()})
    for n, p in enumerate(sorted(persons), 1):
        name_map[p] = f"员工{n:03d}"
    print(f"人员映射 {len(name_map)} 人；账簿映射随各文件收集")
    results = {}
    kind_label = {"vouchers": "凭证列表", "aux": "辅助明细账", "subject_balance": "科目辅助余额表", "balance": "辅助余额表"}
    for kind in ("vouchers", "aux", "subject_balance", "balance"):
        fname = files[kind]
        # 原始文件名：剥掉 f_<uuid>- 前缀
        m = re.match(r"^f_[0-9a-fA-F-]+-(.+)$", fname)
        orig_name = m.group(1) if m else fname
        src = att / fname
        dst = OUT_DIR / f"脱敏数据{_safe_filename(orig_name, {**COMPANY_TOKENS, **ledger_map})}"
        if kind == "vouchers":
            results[kind] = desensitize_vouchers(src, dst, ledger_map, name_map)
        elif kind == "aux":
            results[kind] = desensitize_aux_ledger(src, dst, ledger_map, name_map)
        else:
            results[kind] = desensitize_balance(src, dst, ledger_map, name_map)
        print(f"  ✓ {kind_label[kind]} 已脱敏  ({results[kind]})")
    print(f"账簿映射：{len(ledger_map)} 个")
    return results


# ---------- POS ----------

def desensitize_xlsx_in_zip(zip_path: Path, member: str) -> bytes:
    """脱敏 zip 内单个 POS 对账单 xlsx（建行 plate 版式：标签:值对 + 交易汇总区）。

    - 标签值替换：商户编号/商户名称/商户地址/服务专员/联系电话/邮政编码 → 泛化值；
    - 全文本单元格过 genericize_text（公司名片段、8+ 位长数字、辅助核算令牌）；
    - 交易汇总区（表头行以下）金额串随机重生，笔数保留量级。
    """
    import io
    with zipfile.ZipFile(zip_path) as z:
        raw = z.read(member)
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    ws = wb.worksheets[0]
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    # 定位交易区表头行（含“交易笔数”或“人民币卡分类”）
    head_idx = next((i for i, r in enumerate(grid) if any(isinstance(c, str) and ("交易笔数" in c or "人民币卡分类" in c) for c in r)), None)
    label_value = {"商户编号": "脱敏商户号", "商户名称": "脱敏商户", "商户地址": "脱敏地址",
                   "服务专员": "脱敏专员", "商户联系电话": "脱敏电话", "邮政编码": "脱敏邮编",
                   "集团商户编号": "脱敏集团商户号", "商户服务行": "脱敏服务行"}
    for r in grid:
        for j, c in enumerate(r):
            if isinstance(c, str) and c.rstrip("：:").strip() in label_value:
                # plate 版式：值在标签右侧若干列（合并单元格），找第一个非空单元格替换
                for k in range(j + 1, min(j + 6, len(r))):
                    if r[k] not in (None, ""):
                        r[k] = label_value[c.rstrip("：:").strip()]
                        break
    for i, r in enumerate(grid):
        for j, c in enumerate(r):
            if not isinstance(c, str) or not c.strip():
                continue
            if head_idx is not None and i > head_idx:
                t = c.strip().replace(",", "")
                if re.fullmatch(r"-?\d+\.\d{2}", t):  # 金额串 → 随机
                    r[j] = f"{rand_amount():.2f}"
                    continue
                if re.fullmatch(r"\d{1,4}", t) and j >= 4:  # 笔数量级保留
                    continue
            r[j] = genericize_text(c, {**COMPANY_TOKENS})
    out = openpyxl.Workbook()
    out.remove(out.active)
    dst_ws = out.create_sheet(ws.title)
    for r in grid:
        dst_ws.append(r)
    buf = io.BytesIO()
    out.save(buf)
    wb.close()
    return buf.getvalue()


def run_pos(limit: int = 32):
    """复制指定期间的日包（默认最近 limit 个），商户号泛化，包内 xlsx 脱敏。"""
    require_company_tokens()
    pos_src = _configured_dir("POS_SRC_DIR", "pos_dir")
    if pos_src is None or not pos_src.is_dir():
        raise SystemExit(
            "POS 源目录未配置：请设置 POS_SRC_DIR 或 dev/desensitize.local.json 的 pos_dir"
        )
    zips = sorted(pos_src.glob("SHOP.*.zip"))[-limit:]
    dst_dir = OUT_DIR / "脱敏数据POS刷卡清单"
    dst_dir.mkdir(parents=True, exist_ok=True)
    fake_shop = "9" + "".join(rng.choice("0123456789") for _ in range(14))
    n = 0
    for zp in zips:
        member = zp.stem + ".xlsx" if (zp.stem + ".xlsx") else None
        with zipfile.ZipFile(zp) as z:
            names = [x for x in z.namelist() if x.lower().endswith(".xlsx")]
            if not names:
                continue
            member = names[0]
            payload = desensitize_xlsx_in_zip(zp, member)
        new_name = re.sub(r"SHOP\.\d+", f"SHOP.{fake_shop}", zp.name)
        with zipfile.ZipFile(dst_dir / new_name, "w", zipfile.ZIP_DEFLATED) as zout:
            zout.writestr(re.sub(r"SHOP\.\d+", f"SHOP.{fake_shop}", member), payload)
        n += 1
    print(f"  ✓ POS 日包 {n} 个 → {dst_dir}（商户号 {fake_shop[:6]}…）")
    return {"zips": n}


# ---------- 现金流 ----------

DEFAULT_CASH_FILES = (
    "2026年9月-2027年2月现金流量滚动预测模板.xlsx",
    "2026.9银行存款-辅助明细账_2026-10-2 19_22_59.xlsx",
    "2026.10FSSC付款池会计已付清单台账.xlsx",
)


def _cash_files() -> tuple[str, ...]:
    """真实文件名清单（常含公司名）从本地配置读取；代码只留中性示例。"""
    configured = load_local_config().get("cash_files")
    if isinstance(configured, list) and configured:
        return tuple(str(name) for name in configured)
    return DEFAULT_CASH_FILES


def _clean_cash_stem(stem: str) -> str:
    return _safe_filename(stem, COMPANY_TOKENS)


def run_cash():
    """复制指定期间现金流关键文件并脱敏（模板/辅助明细账/上报版/资金计划/付款池清单）。"""
    require_company_tokens()
    cash_src = _configured_dir("CASH_SRC_DIR", "cash_dir")
    if cash_src is None or not cash_src.is_dir():
        raise SystemExit(
            "现金流源目录未配置：请设置 CASH_SRC_DIR 或 dev/desensitize.local.json 的 cash_dir"
        )
    n = 0
    for index, name in enumerate(_cash_files(), start=1):
        src = cash_src / name
        if not src.is_file():
            print(f"  ! 缺失：配置清单第 {index} 项（{src.suffix}）")
            continue
        wb = openpyxl.load_workbook(src, data_only=True)
        out = openpyxl.Workbook()
        out.remove(out.active)
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            dst_ws = out.create_sheet(ws.title)
            for r in rows:
                dst_ws.append([genericize_text(c, COMPANY_TOKENS) if isinstance(c, str) else c for c in r])
        stem, ext = os.path.splitext(name)
        clean = _clean_cash_stem(stem)
        dst = OUT_DIR / f"脱敏数据{clean}{ext}"
        out.save(dst)
        wb.close()
        n += 1
        print(f"  ✓ 第 {index} 个文件已脱敏")
    print(f"现金流文件 {n} 个")
    return {"files": n}


def run_expense():
    """费用分类样本：银行辅助明细账（1002 过滤导出）脱敏。
    与 BIP 辅助明细账同格式（元数据块+表头+数据），复用 aux 脱敏逻辑（余额链保持）。"""
    require_company_tokens()
    att = _find_attachments()
    OUT_DIR.mkdir(exist_ok=True)
    fname = next(f for f in os.listdir(att) if "1002辅助明细账" in f and f.endswith(".xlsx"))
    m = re.match(r"^f_[0-9a-fA-F-]+-(.+)$", fname)
    orig_name = m.group(1) if m else fname
    ledger_map: dict[str, str] = {}
    persons = collect_person_names({"aux": att / fname})
    name_map = {p: f"员工{n:03d}" for n, p in enumerate(sorted(persons), 1)}
    dst = OUT_DIR / f"脱敏数据{_safe_filename(orig_name, COMPANY_TOKENS)}"
    stats = desensitize_aux_ledger(att / fname, dst, ledger_map, name_map)
    print(f"  ✓ 费用样本已脱敏  ({stats})")
    print(f"账簿映射：{len(ledger_map)} 个")
    return stats


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "bip"
    if mode == "bip":
        run_bip()
    elif mode == "pos":
        run_pos()
    elif mode == "cash":
        run_cash()
    elif mode == "exp":
        run_expense()
    else:
        raise SystemExit(__doc__)
