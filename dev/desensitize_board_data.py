"""报表监控管线源数据脱敏（微信财务文件，通用电子表格）。

规则：格式与结构保持；数值逐格随机（整数→0-9999，金额→两位小数）；
文本按 COMPANY_TOKENS 泛化；**合计行重算**（标签含 合计/总计/小计/累计 的行，
其数值格 = 上方同列数据区之和）以保持勾稽；保留 sheet 名与行列尺寸；
文件名公司名泛化；旧版 .xls 用 xlrd 读取后按 xlsx 写出（内容脱敏不变）。

用法：
    python dev/desensitize_board_data.py [--src <微信file目录>] [--out <输出目录>]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desensitize_finance_data import COMPANY_TOKENS, OUT_DIR, genericize_text, rand_amount, rng  # noqa: E402

_TOTAL_LABELS = ("合计", "总计", "小计", "本日合计", "本月合计", "累计", "总额")
_KWS = ("税金底稿", "资金计划", "付款清单", "周报", "经营", "利润", "资产负债", "现金流量")


def _read_any(path: Path):
    """读取 xlsx/xls：xlsx 用 openpyxl；旧版 .xls 用 xlrd 转行列网格。"""
    if path.suffix.lower() == ".xls":
        import xlrd
        book = xlrd.open_workbook(str(path))
        return [(sh.name[:31], [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)])
                for sh in book.sheets()]
    wb = openpyxl.load_workbook(path, data_only=True)
    sheets = [(ws.title[:31], [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    wb.close()
    return sheets


def _desens_grid(grid):
    """逐格脱敏 + 合计行重算（保勾稽）。"""
    for r in grid:
        for j, c in enumerate(r):
            if isinstance(c, bool):
                continue
            if isinstance(c, (int, float)):
                if float(c).is_integer() and abs(c) < 100000:
                    r[j] = rng.randint(0, 9999)
                else:
                    r[j] = rand_amount()
            elif isinstance(c, str) and c.strip():
                r[j] = genericize_text(c, dict(COMPANY_TOKENS))
    for i, r in enumerate(grid):
        label = " ".join(str(c) for c in r[:3] if isinstance(c, str))
        if not any(t in label for t in _TOTAL_LABELS):
            continue
        for j, c in enumerate(r):
            if j < 3 or isinstance(c, bool) or not isinstance(c, (int, float)):
                continue
            vals = [grid[k][j] for k in range(max(0, i - 60), i)
                    if isinstance(grid[k][j], (int, float)) and not isinstance(grid[k][j], bool)]
            if vals:
                r[j] = round(sum(vals), 2)


def _fake_name(name: str) -> str:
    """文件名泛化：公司名片段替换，保留期间/类别结构信息。"""
    return re.sub(r"云南云泰[^，,。（(]*", "脱敏单位", name)


def main() -> int:
    src_dir = Path(os.environ.get(
        "VERIFY_WECHAT_DIR",
        r"D:\Documents\xwechat_files\wxid_a6mdshqqj7r521_1c1d\msg\file"))
    out_dir = OUT_DIR / "脱敏数据报表监控源数据"
    out_dir.mkdir(parents=True, exist_ok=True)
    picks: list[Path] = []
    for month in ("2026-10", "2026-09"):
        d = src_dir / month
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*")):
            if f.is_file() and f.suffix.lower() in (".xlsx", ".xls") and any(k in f.name for k in _KWS):
                picks.append(f)
    picks = picks[:14]
    manifest = {}
    for src in picks:
        fake = "脱敏数据" + _fake_name(src.name)
        dst = out_dir / (Path(fake).stem + ".xlsx")  # .xls 统一按 xlsx 写出（内容脱敏不变）
        try:
            sheets = _read_any(src)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! 跳过（读取失败）：{src.name}（{type(exc).__name__}）")
            continue
        out = openpyxl.Workbook()
        out.remove(out.active)
        for title, grid in sheets:
            ws = out.create_sheet(title)
            _desens_grid(grid)
            for r in grid:
                ws.append(r)
        out.save(dst)
        manifest[dst.name] = {"sha256": hashlib.sha256(dst.read_bytes()).hexdigest()[:16], "source": src.name}
        print(f"  ok {dst.name[:46]}")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报表监控源数据 {len(manifest)} 个文件 -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
