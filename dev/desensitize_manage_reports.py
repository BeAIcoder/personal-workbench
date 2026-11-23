"""经营管理报表脱敏工具（四份区域报表 → 脱敏样本 + 数据模板）。

输入：会话附件目录里的四份 xlsx（内控管理表格 / 云南项目周报 / 曲靖项目周报 / 月度工作情况表）。
输出：
- `脱敏数据/经营管理/脱敏数据<原文件名>.xlsx`（gitignore，样本留档）
- `数据模板/经营管理-*.xlsx`（入库模板：脱敏骨架 + 首个「填写说明」工作表）
- `脱敏数据/经营管理/mapping.local.json`（映射表，含真实值，只在本地）
- 真实映射键从 `dev/desensitize.local.json`（gitignore）的 `manage_reports` 段加载；
  代码内只保留合成示例，绝不入库。

脱敏口径（只替换身份信息，**保留金额、日期、比率、面积、铺位号、业态、诉讼阶段**，
分析口径与勾稽关系不变）：
- 公司/业主/被告单位 → 区域商业管理公司 / 业主公司N / 被告公司NNN；
- 项目名 → 项目A/B/C/D、区域项目；
- 品牌商户 → 品牌NNN（一对一共通映射，正文与表格同一套）；
- 人员按角色 → 员工NN / 被告NN / 律师NN；律所 → 律所A…；法院 → 某法院；
- 地址、院校、展名 → 泛化名；11 位手机号 → 等长掩码。

用法：
    python dev/desensitize_manage_reports.py            # 生成样本与模板
    python dev/desensitize_manage_reports.py --check    # 只做残留扫描（非零退出=有残留）
    python dev/desensitize_manage_reports.py --in <dir> # 指定附件目录
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desensitize_finance_data import load_local_config  # noqa: E402

try:
    from pii_terms import load_pii_terms  # noqa: E402
except Exception:  # noqa: BLE001
    def load_pii_terms() -> list[str]:  # noqa: D401
        return []

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "脱敏数据" / "经营管理"
TPL_DIR = ROOT / "数据模板"
MAPPING_FILE = OUT_DIR / "mapping.local.json"

# 模板文件名：源文件关键词 → (模板名, 报表类型, 解析锚点说明)
TEMPLATES = (
    ("内控", "经营管理-内控管理表格-模板.xlsx", "control",
     "六个工作表：表1 重点工作周进度、表2 年度经营业绩责任书、表3 项目收入、"
     "表4 招商完成、表5 诉讼案件、表6 拟报总办会。请勿改工作表名前缀与表头文字。"),
    ("曲靖", "经营管理-曲靖项目周报-模板.xlsx", "weekly",
     "单表：一、招商；二、经营（收缴/预警商户/品牌宣发/竞品）；三、品质提升与维修基金；"
     "四、本周重点及困难事项；五、下周重点工作计划。请勿改「实际（期末值）」「总租赁面积㎡」"
     "「续签品牌数量」「1-10月累计应收款」等标签行。"),
    ("周报", "经营管理-云南项目周报-模板.xlsx", "weekly",
     "同一周报模板（另一项目），另含品牌宣发与竞品小节。请勿改标签行与表头文字。"),
    ("月度", "经营管理-月度工作情况表-模板.xlsx", "monthly",
     "单表：一、问题线索处置及案件办理；二、重点工作推进；三、自身建设和队伍建设；四、亮点工作。"
     "每节内容写在一个单元格内，节内用「（一）」「1.」「（1）」分层。"),
)

# 代码只保留合成示例；真实公司/项目/人员/品牌从 dev/desensitize.local.json
# （gitignore）的 manage_reports 段加载，绝不入库。
PROJECT_COMPANY = (
    ("示例机构甲", "合成机构甲"),
    ("示例机构乙", "合成机构乙"),
    ("示例机构丙", "合成机构丙"),
    ("示例项目甲", "合成项目甲"),
)

LAW_FIRMS = (
    ("示例律所甲", "合成律所甲"),
)


def _local_manage_config() -> dict:
    section = load_local_config().get("manage_reports")
    return section if isinstance(section, dict) else {}


def _pairs(key: str, default: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    value = _local_manage_config().get(key)
    if not isinstance(value, list) or not value:
        return default
    pairs = [
        (str(item[0]), str(item[1]))
        for item in value
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    return tuple(pairs) or default


def _terms(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = _local_manage_config().get(key)
    if not isinstance(value, list) or not value:
        return default
    terms = tuple(str(item) for item in value if str(item).strip())
    return terms or default


REQUIRED_PAIR_KEYS = ("project_company", "law_firms")
REQUIRED_TERM_KEYS = ("staff", "defendants", "lawyers", "others", "brands")


def _valid_pairs(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and all(isinstance(part, str) and part.strip() for part in item[:2])
            for item in value
        )
    )


def _valid_terms(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def require_manage_config() -> dict:
    """真实生成前校验全部本地映射键；缺失/类型错误时中文拒绝，避免 fail-open。"""
    section = _local_manage_config()
    problems: list[str] = []
    for key in REQUIRED_PAIR_KEYS:
        if key not in section:
            problems.append(f"{key} 缺失")
        elif not _valid_pairs(section[key]):
            problems.append(f"{key} 必须是非空二元组列表")
    for key in REQUIRED_TERM_KEYS:
        if key not in section:
            problems.append(f"{key} 缺失")
        elif not _valid_terms(section[key]):
            problems.append(f"{key} 必须是非空字符串列表")
    if problems:
        raise SystemExit(
            "dev/desensitize.local.json 的 manage_reports 配置无效："
            + "；".join(problems)
            + "；拒绝生成脱敏样本"
        )
    return section


STAFF = ("合成人员甲", "合成人员乙")
DEFENDANTS = ("合成被告甲",)
LAWYERS = ("合成律师甲",)
OTHERS = ("合成其他甲",)
BRANDS = ("合成品牌甲", "合成品牌乙")

MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
GENERIC_COMPANY = re.compile(r"[\u4e00-\u9fa5]{2,12}(?:有限公司|有限责任公司|股份有限公司)")
ROAD = re.compile(
    r"(?:(?![于某位在区市县镇村])[\u4e00-\u9fa5]){2,8}(?:路|街|大道)\s*\d+\s*号"
)
DISTRICT = re.compile(r"(?:官渡|五华|西山|盘龙|呈贡|安宁|嵩明|宜良)区")
BUILDING = re.compile(r"[\u4e00-\u9fa5]{2,6}(?:小区|大厦|写字楼)")
CLEANUP = (
    ("被告被告公司", "被告公司"),
    ("被告被告", "被告"),
    ("原告原告", "原告"),
    ("某市市", "某市"),
    ("项目A项目", "项目A"),
    ("项目B项目", "项目B"),
    ("项目C项目", "项目C"),
    ("项目D项目", "项目D"),
)


def _ordered(mapping: dict[str, str]) -> list[tuple[str, str]]:
    return sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)


def build_mapping() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for real, fake in _pairs("project_company", PROJECT_COMPANY):
        mapping[real] = fake
    for real, fake in _pairs("law_firms", LAW_FIRMS):
        mapping[real] = fake
    for idx, real in enumerate(_terms("staff", STAFF), start=1):
        mapping[real] = f"员工{idx:02d}"
    for idx, real in enumerate(_terms("defendants", DEFENDANTS), start=1):
        mapping[real] = f"被告{idx:02d}"
    for idx, real in enumerate(_terms("lawyers", LAWYERS), start=1):
        mapping[real] = f"律师{idx:02d}"
    for idx, real in enumerate(_terms("others", OTHERS), start=1):
        mapping[real] = f"人员{idx:02d}"
    for idx, real in enumerate(_terms("brands", BRANDS), start=1):
        mapping.setdefault(real, f"品牌{idx:03d}")
    return mapping


def build_replacer(mapping: dict[str, str]):
    pairs = _ordered(mapping)
    # 空白容忍：源表长文本里公司名常被空格/换行拆开（「示例商 业管理有限公司」）
    pattern = re.compile(
        "|".join(r"\s*".join(re.escape(ch) for ch in real) for real, _ in pairs)
    )
    table = {real: fake for real, fake in pairs}
    generic_companies: dict[str, str] = {}

    def generic_company(match: re.Match) -> str:
        real = match.group(0)
        if real not in generic_companies:
            generic_companies[real] = f"关联公司{len(generic_companies) + 1:02d}"
        return generic_companies[real]

    def replace(text: str) -> str:
        text = pattern.sub(lambda m: table[re.sub(r"\s+", "", m.group(0))], text)
        text = GENERIC_COMPANY.sub(generic_company, text)
        text = DISTRICT.sub("某区", text)
        text = ROAD.sub("某路", text)
        text = BUILDING.sub("某建筑", text)
        text = MOBILE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)
        for old, new in CLEANUP:
            text = text.replace(old, new)
        return text

    return replace


def _source_files(base: Path) -> list[Path]:
    if not base.is_dir():
        raise SystemExit(f"附件目录不存在：{base}")
    files = [p for p in sorted(base.glob("*.xlsx")) if not p.name.startswith("~$")]
    if len(files) < 4:
        raise SystemExit(f"附件目录只有 {len(files)} 个 xlsx，预期 4 个：{base}")
    return files


def _guess_base() -> Path:
    env = os.environ.get("MANAGE_ATTACH_DIR") or load_local_config().get("manage_attach_dir")
    if env and Path(str(env)).is_dir():
        return Path(str(env))
    sessions = os.environ.get("KIMI_SESSIONS")
    if sessions:
        base = Path(sessions)
        if base.is_dir():
            for path in base.rglob("attachments"):
                if not path.is_dir():
                    continue
                names = [p.name for p in path.glob("*.xlsx")]
                if any("内控" in n for n in names) and any("月度" in n for n in names):
                    return path
    raise SystemExit(
        "找不到附件目录，请用 --in 指定，或设置 MANAGE_ATTACH_DIR / KIMI_SESSIONS，"
        "或在 dev/desensitize.local.json 配置 manage_attach_dir"
    )


def _replace_workbook(src: Path, dst: Path, replace) -> int:
    wb = openpyxl.load_workbook(src, data_only=False)
    cached = openpyxl.load_workbook(src, data_only=True)
    changed = 0
    for ws in wb.worksheets:
        values_ws = cached[ws.title] if ws.title in cached.sheetnames else None
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                # 公式单元格落成计算值：openpyxl 另存会丢缓存值，模板/样本必须自带数值，
                # 否则导入时 data_only 读到空、图表少一块（如招商「区域合计」=F+E+D）
                if isinstance(value, str) and value.startswith("=") and values_ws is not None:
                    computed = values_ws[cell.coordinate].value
                    if computed is not None:
                        value = computed
                        cell.value = computed
                        changed += 1
                if not isinstance(value, str):
                    continue
                new = replace(value)
                if new != value:
                    cell.value = new
                    changed += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
    wb.close()
    cached.close()
    return changed


def _drop_empty_sheets(wb) -> None:
    for ws in list(wb.worksheets):
        if ws.max_row <= 1 and ws.max_column <= 1 and not any(
            c.value not in (None, "") for row in ws.iter_rows() for c in row
        ):
            wb.remove(ws)


def _add_instruction_sheet(dst: Path, report_type: str, note: str) -> None:
    wb = openpyxl.load_workbook(dst)
    _drop_empty_sheets(wb)
    if "填写说明" in wb.sheetnames:
        wb.remove(wb["填写说明"])
    ws = wb.create_sheet("填写说明", 0)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 96
    rows = [
        ("模板", f"经营管理 · {report_type}"),
        ("说明", "本表为脱敏示例数据（项目A/品牌001/员工01），请整体替换为自有数据后再导入。"),
        ("解析方式", note),
        ("单位口径", "金额按表内标注（万元/元）填写；比率填 0–1 小数或带 % 的百分数；日期用 Excel 日期或 YYYY-MM-DD。"),
        ("必填", "报表期间（YYYY-MM）、项目名称、各标签行对应的数值行；长文本小节可留空。"),
        ("注意", "不要改工作表名前缀与标签行文字；不要删除或合并标签行；模板首列「填写说明」可保留。"),
        ("限额", "单文件 ≤10MB；单次导入 ≤200,000 行；同项目同期间重复导入会整批覆盖。"),
    ]
    for r, (a, b) in enumerate(rows, start=1):
        ws.cell(row=r, column=1, value=a).font = Font(bold=True)
        cell = ws.cell(row=r, column=2, value=b)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(dst)
    wb.close()


def _template_for(name: str):
    for key, tpl, report_type, note in TEMPLATES:
        if key in name:
            return tpl, report_type, note
    return None


def generate(base: Path) -> dict:
    require_manage_config()
    mapping = build_mapping()
    replace = build_replacer(mapping)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = []
    for index, src in enumerate(_source_files(base), start=1):
        dst = OUT_DIR / f"脱敏数据{replace(src.name)}"
        changed = _replace_workbook(src, dst, replace)
        stats.append({"index": index, "cells": changed})
        tpl = _template_for(src.name)
        if tpl:
            tpl_name, report_type, note = tpl
            tpl_path = TPL_DIR / tpl_name
            _replace_workbook(src, tpl_path, replace)
            _add_instruction_sheet(tpl_path, report_type, note)
    MAPPING_FILE.write_text(
        json.dumps({"mapping": mapping}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"files": stats, "mapping": str(MAPPING_FILE)}


def check(paths: list[Path] | None = None) -> int:
    """独立残留扫描：check_pii 通用规则 + 本地词表，不复用 build_mapping()。"""
    targets = paths or (
        sorted(OUT_DIR.glob("*.xlsx")) + sorted(TPL_DIR.glob("经营管理-*.xlsx"))
    )
    if not targets:
        print("没有可检查的脱敏文件，先运行生成模式", file=sys.stderr)
        return 2

    try:
        from check_pii import ScanError, scan_paths, scan_text
    except Exception as exc:  # noqa: BLE001
        print(f"残留扫描失败：无法加载 dev/check_pii.py（{type(exc).__name__}）", file=sys.stderr)
        return 2

    try:
        terms = load_pii_terms()
    except Exception:  # noqa: BLE001 词表缺失不阻断通用规则
        terms = []
    if not terms:
        print("警告：未加载本地词表（dev/pii-terms.local.txt 或映射表），仅执行通用规则", file=sys.stderr)

    findings = []
    try:
        for path in targets:
            findings.extend(scan_paths([path], terms=terms))
            findings.extend(scan_text(path.name, path=str(path), terms=terms))
    except ScanError as exc:
        print(f"残留扫描失败：{exc}", file=sys.stderr)
        return 2

    unique = sorted(set(findings))
    if unique:
        print(f"残留 {len(unique)} 处（仅位置与规则）：", file=sys.stderr)
        for finding in unique[:50]:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print(f"残留扫描通过：{len(targets)} 个文件、{len(terms)} 个词表项，0 命中")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="经营管理报表脱敏")
    parser.add_argument("--in", dest="src", type=Path, default=None, help="附件目录")
    parser.add_argument("--check", action="store_true", help="只做残留扫描")
    args = parser.parse_args()
    if args.check:
        return check()
    base = args.src or _guess_base()
    result = generate(base)
    for item in result["files"]:
        print(f"文件 {item['index']} 已脱敏（{item['cells']} 个单元格）")
    print(f"映射表：{result['mapping']}")
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
