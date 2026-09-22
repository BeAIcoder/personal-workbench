"""把本机 QwenPaw 的 Agent 定义整合进工作台专家团队。

QwenPaw 的每个 Agent 工作区 = 一个角色定义（SOUL.md 灵魂 / AGENTS.md 岗位手册 /
00_系统总则.md 等全局规则）。本脚本读取这些工作区，经脱敏后：

1. 领域重叠的合并进内置专家：cre-leasing→招商专员 leasing、cre-ops→运营专员、
   cre-mkt→企划策划、cre-fm→工程物业工程师 property、cre-finance→信息化财务分析师
   it_finance、cre-it→网络安全工程师 security（追加角色定义 + 并集关键词）；
2. 全新职责的新建专家：cre_gm 总经理、cloud_orchestrator/executor/verifier
   CloudPaw 三件套、datapaw 数据分析。

用法（在项目任意目录均可）：
    python backend/scripts/import_qwenpaw_agents.py --dry-run   # 预览不写入
    python backend/scripts/import_qwenpaw_agents.py             # 执行导入
    python backend/scripts/import_qwenpaw_agents.py --overwrite # 覆盖历史导入内容再跑
    python backend/scripts/import_qwenpaw_agents.py --from "D:/AI_Service/QwenPaw/data/workspaces"

- 源目录定位顺序：--from 参数 > 环境变量 QWENPAW_WORKSPACES > 本机默认路径；都没有则退出。
- 幂等：角色提示词带导入标记，重复执行不会叠加；--overwrite 会剥掉旧导入内容重写。
- 脱敏：本脚本会把「老头子」等称呼、本机绝对路径替换为通用占位。
- 导入后 60 秒内自动生效（团队列表有 TTL 缓存），也可重启服务立即生效。
"""
import argparse
import os
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models import AgentSpecModel  # noqa: E402
from app.agents.team import QUERY_TOOLS  # noqa: E402

MARKER = "【QwenPaw 整合】"

# 合并进既有内置专家的：workbench 专家名 ← QwenPaw 工作区 + 补充关键词
MERGE_MAP: dict[str, dict] = {
    "leasing": {"src": "cre-leasing", "add_keywords": ["招商", "租约", "租赁", "租金", "品牌", "签约", "免租", "装补", "业态", "商务条件", "主力店"]},
    "operations": {"src": "cre-ops", "add_keywords": ["运营", "营运", "客流", "销售额", "收缴率", "坪效", "提袋率", "商户经营", "培育"]},
    "marketing": {"src": "cre-mkt", "add_keywords": ["企划", "营销", "活动方案", "推广", "美陈", "会员", "造节", "市集", "ROI"]},
    "property": {"src": "cre-fm", "add_keywords": ["工程", "物业", "维保", "维修", "能耗", "巡检", "空调", "电梯", "外包"]},
    "it_finance": {"src": "cre-finance", "add_keywords": ["财务", "报表", "凭证", "分录", "报销", "预算", "费用", "税", "RPA", "业财"]},
    "security": {"src": "cre-it", "add_keywords": ["运维", "安全", "漏洞", "等保", "终端", "数据安全", "权限"]},
}

# 全新创建的专家：name ← (工作区, 显示名, emoji, color, 关键词, sort)
NEW_AGENTS: dict[str, tuple] = {
    "cre_gm": ("cre-gm", "商业地产总经理", "🎯", "#F56C6C",
               ["战略", "决策", "估值", "NOI", "Cap Rate", "IRR", "调改", "资产处置", "拍板", "授权"], 7),
    "cloud_orchestrator": ("cloud-orchestrator", "CloudPaw 主控编排", "☁️", "#409EFF",
                           ["云编排", "编排", "阿里云", "云资源", "部署编排", "IaC", "Mission"], 8),
    "cloud_executor": ("cloud-executor", "CloudPaw 执行器", "⚙️", "#67C23A",
                       ["执行", "脚本", "CLI", "部署", "配置", "代码"], 10),
    "cloud_verifier": ("cloud-verifier", "CloudPaw 验证器", "✅", "#E6A23C",
                       ["验证", "验收", "合规", "检查", "审计", "核查"], 9),
    "datapaw": ("datapaw", "DataPaw 数据分析", "📈", "#9C27B0",
                ["数据分析", "指标", "口径", "取数", "SQL", "图表", "对账"], 11),
}

# 角色提示词的来源文件（按顺序拼接，存在才读）
ROLE_FILES = ["SOUL.md", "PROFILE.md", "AGENTS.md", "00_系统总则.md", "06_协同规则.md"]


def sanitize(text: str) -> str:
    """脱敏：称呼、本机绝对路径。"""
    text = text.replace("老头子", "用户")
    # 各种盘符的绝对路径
    text = re.sub(r"[A-Za-z]:[\\/][^\s`'\"，。；）\]】]+", "[本地路径]", text)
    return text.strip()


def ws_dir(workspace: str) -> Path:
    d = WORKSPACES_DIR / workspace
    if not d.is_dir():
        raise FileNotFoundError(d)
    return d


def read_role(workspace: str) -> tuple[str, str]:
    """返回 (agent.json 描述, 拼接脱敏后的角色提示词)。"""
    d = ws_dir(workspace)
    desc = ""
    agent_json = d / "agent.json"
    if agent_json.exists():
        import json
        try:
            desc = sanitize(str(json.loads(agent_json.read_text(encoding="utf-8")).get("description", "")))
        except Exception:
            desc = ""
    parts = [f"{MARKER}以下角色定义导入自本机 QwenPaw（{workspace}），用于界定职责边界。"]
    for fname in ROLE_FILES:
        fp = d / fname
        if fp.exists():
            parts.append(f"\n===== {fname} =====\n" + sanitize(fp.read_text(encoding="utf-8")))
    prompt = "\n".join(parts)
    return desc, prompt[:60000]  # 硬上限，防止异常文件撑爆上下文


def strip_imported(old: str) -> str:
    """去掉上次导入追加的内容（幂等重导）。"""
    return old.split(MARKER, 1)[0].rstrip()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    global WORKSPACES_DIR
    ap = argparse.ArgumentParser(description="把 QwenPaw Agent 定义整合进工作台专家团队")
    ap.add_argument("--from", dest="src", default="", help="QwenPaw workspaces 目录")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    ap.add_argument("--overwrite", action="store_true", help="剥掉旧导入内容后重写（默认追加/更新）")
    args = ap.parse_args()

    src = args.src or os.environ.get("QWENPAW_WORKSPACES") or r"D:/AI_Service/QwenPaw/data/workspaces"
    WORKSPACES_DIR = Path(src)
    if not WORKSPACES_DIR.is_dir():
        print(f"未找到 QwenPaw workspaces 目录：{src}")
        print("请用 --from 指定目录，或设置环境变量 QWENPAW_WORKSPACES。")
        return 1

    # 校验源工作区齐全
    missing = [m["src"] for m in MERGE_MAP.values()] + [v[0] for v in NEW_AGENTS.values()]
    missing = [m for m in missing if not (WORKSPACES_DIR / m).is_dir()]
    if missing:
        print(f"目录下缺少这些 QwenPaw 工作区：{missing}，终止。")
        return 1

    plan: list[str] = []
    with SessionLocal() as db:
        # 1) 合并进内置专家
        for name, cfg in MERGE_MAP.items():
            row = db.query(AgentSpecModel).filter_by(name=name).first()
            desc, prompt = read_role(cfg["src"])
            if row is None:
                plan.append(f"[跳过] {name}（工作台里不存在，请先启动一次服务完成内置专家 seed）")
                continue
            already = MARKER in (row.role_prompt or "")
            if already and not args.overwrite:
                plan.append(f"[已导入] {name}：角色提示词已含 QwenPaw 定义，跳过（--overwrite 可重导）")
                continue
            base = strip_imported(row.role_prompt or "") if args.overwrite else (row.role_prompt or "").rstrip()
            kw = list(dict.fromkeys((row.keywords or []) + cfg["add_keywords"]))
            plan.append(f"[合并] {name} ← {cfg['src']}（角色定义 {len(prompt)} 字符，关键词 +{len(cfg['add_keywords'])}）")
            if not args.dry_run:
                row.role_prompt = base + ("\n\n" if base else "") + prompt
                row.keywords = kw

        # 2) 新建专家
        for name, (src_ws, label, emoji, color, keywords, sort) in NEW_AGENTS.items():
            row = db.query(AgentSpecModel).filter_by(name=name).first()
            desc, prompt = read_role(src_ws)
            if row is not None:
                if MARKER in (row.role_prompt or "") and not args.overwrite:
                    plan.append(f"[已导入] {name}：已存在，跳过（--overwrite 可重导）")
                    continue
                base = strip_imported(row.role_prompt or "")
                plan.append(f"[更新] {name}：按源工作区重写角色定义")
                if not args.dry_run:
                    row.role_prompt = base + ("\n\n" if base else "") + prompt
                    row.keywords = keywords
                    row.sort = sort
                continue
            plan.append(f"[新建] {name} {label}（{emoji}，关键词 {len(keywords)} 个，sort={sort}）")
            if not args.dry_run:
                db.add(AgentSpecModel(
                    name=name, display_name=label, emoji=emoji, color=color,
                    description=desc or f"来自 QwenPaw 的 {src_ws} 智能体",
                    role_prompt=prompt, keywords=keywords,
                    tools=list(QUERY_TOOLS), enabled=True, sort=sort, is_builtin=False,
                ))

        if not args.dry_run:
            db.commit()

    print("\n".join(plan))
    action = "预览（未写入）" if args.dry_run else "导入完成"
    print(f"\n{action}。团队列表 60 秒内自动生效，或重启后端服务立即生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
