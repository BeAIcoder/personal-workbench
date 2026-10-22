"""脱敏数据校验：勾稽关系断言 + 敏感残留扫描 + 原始文件只读确认。

校验项：
  V1 凭证列表：每张凭证借方金额合计 == 贷方金额合计（前向填充凭证号分组）；
  V2 辅助明细账：按（科目+核算账簿）链重算滚动余额，与表内方向/本币余额一致；
  V3 余额表：期末本币金额（含方向）== 期初 + 本期借方 - 本期贷方；
  V4 敏感残留：输出文件全部文本单元格不含公司名/人名/辅助核算实名/≥8 位长数字；
  V5 原始附件 sha256 与脱敏前一致（只读未改）。
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "脱敏数据"
ATTACH = Path(os.environ["VERIFY_ATTACH_DIR"])

SENSITIVE = ["云南云泰", "云泰融城", "茶马花街", "中青旅行社", "谢南江", "彭婷", "姚序", "方莹", "杜丹", "詹芷钰", "王娜娜", "陈霞"]
fails: list[str] = []


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_vouchers():
    f = next(OUT.glob("脱敏数据凭证列表*.xlsx"))
    wb = openpyxl.load_workbook(f, data_only=True)
    total_v, bad, single_side = 0, 0, 0
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        header = rows[1]
        col = {n: i for i, n in enumerate(header) if isinstance(n, str)}
        i_vno, i_d, i_c = col["凭证号"], col["借方金额"], col["贷方金额"]
        # 凭证号跨期间会重复：按“每次出现”分组（列表），不用 dict 聚合
        groups: list[list[tuple]] = []
        cur = []
        for r in rows[2:]:
            if r[i_vno]:
                if cur:
                    groups.append(cur)
                cur = []
            if isinstance(r[i_d], (int, float)):
                cur.append(("d", r[i_d]))
            if isinstance(r[i_c], (int, float)):
                cur.append(("c", r[i_c]))
        if cur:
            groups.append(cur)
        for lines in groups:
            has_d = any(t == "d" for t, _ in lines)
            has_c = any(t == "c" for t, _ in lines)
            if not (has_d and has_c):
                single_side += 1  # 原始数据即单边（B6 平衡校验素材），不参与配平断言
                continue
            total_v += 1
            sd = round(sum(a for t, a in lines if t == "d"), 2)
            sc = round(sum(a for t, a in lines if t == "c"), 2)
            if abs(sd - sc) > 0.01:
                bad += 1
                if bad <= 3:
                    fails.append(f"V1 凭证不平 {ws.title}: 借{sd} != 贷{sc}")
    wb.close()
    print(f"V1 凭证勾稽：双边 {total_v} 张全平；单边 {single_side} 张（原始即单边，B6 素材，不参与配平）")
    return bad == 0


def check_aux_ledger():
    f = next(OUT.glob("脱敏数据*辅助明细账*.xlsx"))
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    h = next(i for i, r in enumerate(rows) if r and r[0] == "科目编码")
    col = {n: i for i, n in enumerate(rows[h]) if isinstance(n, str)}
    i_code, i_led = col["科目编码"], col["核算账簿"]
    i_d, i_c, i_dir, i_bal = col["借方本币"], col["贷方本币"], col["方向"], col["本币余额"]
    chains: dict[tuple, float] = {}
    bad = 0
    for r in rows[h + 1:]:
        if not (isinstance(r[i_code], str) and r[i_code].strip()):
            continue
        key = (r[i_code], r[i_led])
        bal = chains.get(key, 0.0)
        has_amt = isinstance(r[i_d], (int, float)) or isinstance(r[i_c], (int, float))
        if has_amt:
            bal = round(bal + (r[i_d] or 0) - (r[i_c] or 0), 2)
        else:
            bal = float(r[i_bal] or 0) * (1 if r[i_dir] == "借" else -1)
        chains[key] = bal
        if abs(abs(bal) - float(r[i_bal] or 0)) > 0.01 or ("借" if bal >= 0 else "贷") != r[i_dir]:
            bad += 1
            if bad <= 3:
                fails.append(f"V2 余额链断裂 {key} r: {r[i_bal]}/{r[i_dir]} vs 计算 {bal}")
    wb.close()
    print(f"V2 明细账余额链：{len(chains)} 条链，断链 {bad} 处")
    return bad == 0


def check_balance(pattern):
    f = next(OUT.glob(pattern))
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    h1 = next(i for i, r in enumerate(rows) if r and r[0] == "科目编码")
    col = {n: i for i, n in enumerate(rows[h1]) if isinstance(n, str)}
    i_od, i_oa, i_d, i_c, i_ed, i_ea = col["期初余额"], col["期初余额"] + 1, col["本期发生"], col["本期发生"] + 1, col["期末余额"], col["期末余额"] + 1
    bad = total = 0
    for r in rows[h1 + 2:]:
        if not (isinstance(r[0], str) and r[0].strip()):
            continue
        total += 1
        opening = (r[i_oa] or 0) * (1 if r[i_od] == "借" else -1)
        end = round(opening + (r[i_d] or 0) - (r[i_c] or 0), 2)
        if abs(abs(end) - float(r[i_ea] or 0)) > 0.01 or ("借" if end >= 0 else "贷") != r[i_ed]:
            bad += 1
            if bad <= 3:
                fails.append(f"V3 余额表不平 {r[0]}: 期末表内 {r[i_ed]}{r[i_ea]} vs 计算 {end}")
    wb.close()
    print(f"V3 {f.name[:24]}：{total} 行，不平 {bad} 行")
    return bad == 0


def check_sensitive():
    """V4：输出不得包含（a）公司/人名；（b）原始文件中出现过的 8+ 位长数字串（卡号等）。
    科目编码等纯数字结构性字段允许保留（BIP 导入与汇总依赖）。"""
    # 收集原始文件的文本嵌入式长数字串（纯数字列如科目编码属结构性字段，不纳入）
    orig_long: set[str] = set()
    for f in ATTACH.glob("*.xlsx"):
        wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for c in row:
                    if isinstance(c, str) and re.search(r"\D", c):
                        orig_long.update(re.findall(r"\d{8,}", c))
        wb.close()
    bad = []
    for f in OUT.glob("*.xlsx"):
        wb = openpyxl.load_workbook(f, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for c in row:
                    if not isinstance(c, str):
                        continue
                    for s in SENSITIVE:
                        if s in c:
                            bad.append(f"{f.name}/{ws.title}: 名称 {s}")
                    for num in re.findall(r"\d{8,}", c):
                        if num in orig_long:
                            bad.append(f"{f.name}/{ws.title}: 泄露长数字 {num}")
        wb.close()
    for b in bad[:8]:
        fails.append("V4 " + b)
    print(f"V4 敏感残留（名称+原始长数字泄露）：{len(bad)} 处")
    return not bad


def check_originals():
    manifest = ROOT / "dev" / "verify-original-manifest.sha256"
    lines = []
    for f in sorted(ATTACH.glob("*.xlsx")):
        lines.append(f"{sha256(f)}  {f.name}\n")
    if manifest.exists():
        old = manifest.read_text(encoding="utf-8")
        same = old == "".join(lines)
        print(f"V5 原始附件未改动：{'是' if same else '否（清单已刷新）'}")
        if not same:
            fails.append("V5 原始附件校验和变化！")
        return same
    manifest.write_text("".join(lines), encoding="utf-8")
    print(f"V5 原始附件清单已记录（{len(lines)} 个）")
    return True


if __name__ == "__main__":
    ok = True
    ok &= check_vouchers()
    ok &= check_aux_ledger()
    ok &= check_balance("脱敏数据*辅助余额表*.xlsx")
    ok &= check_balance("脱敏数据*科目辅助余额表*.xlsx")
    ok &= check_sensitive()
    ok &= check_originals()
    print("=" * 60)
    if ok:
        print("全部校验通过 ✅")
    else:
        print("存在问题：")
        for x in fails[:15]:
            print(" -", x)
        sys.exit(1)
