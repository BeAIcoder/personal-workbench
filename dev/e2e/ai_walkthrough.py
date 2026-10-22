# -*- coding: utf-8 -*-
"""AI 浏览器走查验收：把一次"人工级"点击验收归档为调试中心的一条 run（kind=ai-verify）。

定位（与 Playwright 脚本分工）：
  - dev/e2e/e2e_*.py     确定性回归主干：逐页/逐用例自动跑，全绿才算过；
  - 本脚本配合 AI 操作    人工验收层：AI 控制真实浏览器（desktop browser MCP /
    （Kimi WebBridge 等）  WebBridge）像人一样点菜单、开抽屉、悬停、看确认框，
                          验收"脚本断言覆盖不了的交互与文案"，结果同样落 debug 模块。

用法（AI 或人工走查完后执行）：
    python ai_walkthrough.py                          # 全部通过
    python ai_walkthrough.py --fail AI-VERIFY-004:tooltip 没弹出 --note "重测"
    python ai_walkthrough.py --fail AI-VERIFY-005     # 可重复传多个 --fail
    python ai_walkthrough.py --debug-dir <隔离目录>    # 显式指定归档目录

失败项会经 save_run 自动建/更新问题（source=e2e、P1），全绿不写 issues.json。
验收集（CHECKLIST）随功能演进维护：新增验收点加条目即可。

归档隔离（二轮 e2e-artifact-safety 加固）：未显式指定目录时，归档落到 dev/e2e 下
专有的测试调试目录 debug_data_ai_verify（每次运行前重置，与三旧脚本的 debug_data_*
两两不同），绝不默认写真实 backend/data/debug；需要归档到别处时显式给出隔离路径
（--debug-dir 优先于环境变量 DEBUG_DATA_DIR），调用方目录只写不重置。
"""
import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E_DIR))
from debug_reporter import save_run

BASE = "http://127.0.0.1:8000"

# 本脚本专有的默认调试归档目录（dev/e2e 下，已被 .gitignore 的 debug_data*/ 覆盖）
DEFAULT_DEBUG_DIR = E2E_DIR / "debug_data_ai_verify"

# 当前验收集：覆盖核心页面 + 近期修复的关键交互（2026-10-13 首轮走查基线）
CHECKLIST = [
    ("AI-VERIFY-001", "菜单结构：一级菜单 5 项，「AI 配置」子菜单含 知识与记忆/MCP工具/技能包管理/专家团文件", "点击展开/跳转正常"),
    ("AI-VERIFY-002", "菜单与页面标题一致（技能包管理/专家团文件等）", "无命名漂移"),
    ("AI-VERIFY-003", "技能包页提示文案「技能池目录」+ 路径 backend/data/skills", "指引与设置项同名同路径"),
    ("AI-VERIFY-004", "专家团文件「有差异」徽标悬停展示具体差异字段", "tooltip 列出字段名"),
    ("AI-VERIFY-005", "导入有差异/内置专家前弹覆盖确认框", "文案明示覆盖范围"),
    ("AI-VERIFY-006", "导出覆盖已修改文件前弹确认框", "同名且有差异时确认"),
    ("AI-VERIFY-007", "从种子重置提示「仅重置文件，需再点全部导入才生效」", "确认框含引导"),
    ("AI-VERIFY-008", "AI 助手设置页显示「当前生效模型（来源）」", "provider/env 兜底可辨"),
    ("AI-VERIFY-009", "停用供应商显示「已停用」而非「已配置」", "徽标随开关状态"),
    ("AI-VERIFY-010", "供应商卡片有「探测全部模型」批量入口", "按钮在位"),
    ("AI-VERIFY-011", "停用技能包不注入 Agent（后端实测）", "注入清单不含停用包"),
    ("AI-VERIFY-012", "调试中心三页渲染正常（问题记录/测试运行/前端报错）", "列表/抽屉/截图链路正常"),
]


def parse_fail(specs):
    """--fail ID[:reason] → {id: reason}"""
    out = {}
    for s in specs or []:
        if ":" in s:
            tid, reason = s.split(":", 1)
        else:
            tid, reason = s, "走查未通过（未注明原因）"
        out[tid.strip()] = reason.strip()
    return out


def resolve_debug_dir(cli_dir=None):
    """解析本次归档目录：--debug-dir > 环境变量 DEBUG_DATA_DIR > 默认测试调试目录。

    默认目录是 dev/e2e 下专供本脚本的调试归档目录，未显式指定时绝不写真实
    backend/data/debug（与三旧脚本的 debug_data_* 各自独立、互不重置）。
    """
    for candidate in (cli_dir, os.environ.get("DEBUG_DATA_DIR", "").strip()):
        if candidate:
            return Path(candidate)
    return DEFAULT_DEBUG_DIR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail", action="append", default=[], help="标记失败项：ID[:原因]，可重复")
    ap.add_argument("--note", default="", help="附加说明，写入 run 的 extra")
    ap.add_argument("--debug-dir", default="",
                    help="归档调试目录（显式隔离路径，只写不重置）；缺省用 DEBUG_DATA_DIR，"
                         "再缺省用 dev/e2e/debug_data_ai_verify")
    args = ap.parse_args()

    fails = parse_fail(args.fail)
    unknown = set(fails) - {i for i, _, _ in CHECKLIST}
    if unknown:
        print(f"警告：未知的验收项 ID（已忽略）：{sorted(unknown)}；可用 ID 见 CHECKLIST")

    tests = [
        {"id": tid, "name": name, "passed": tid not in fails,
         "detail": fails.get(tid, detail)}
        for tid, name, detail in CHECKLIST
    ]

    # 归档目录隔离：仅重置本脚本自有的默认目录；调用方显式路径绝不触碰
    debug_dir = resolve_debug_dir(args.debug_dir)
    if debug_dir == DEFAULT_DEBUG_DIR and debug_dir.exists():
        shutil.rmtree(debug_dir)
    os.environ["DEBUG_DATA_DIR"] = str(debug_dir)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id = save_run(
        "ai-verify",
        base=BASE,
        started_at=now,
        finished_at=now,
        tests=tests,
        extra={
            "verifier": "AI 浏览器走查（desktop browser MCP / Kimi WebBridge，AI 控制真实浏览器点击）",
            "note": args.note or "AI 人工级点击验收",
        },
    )

    failed = [t for t in tests if not t["passed"]]
    print(f"run_id={run_id}")
    print(f"共 {len(tests)} 项，通过 {len(tests) - len(failed)}，失败 {len(failed)}")
    for t in failed:
        print(f"  [FAIL] {t['id']} {t['name']} —— {t['detail']}")
    if failed:
        print("失败项已自动建/更新问题（source=e2e, P1），请在「问题记录」跟进")
    else:
        print("全部通过；未触碰 issues.json")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
