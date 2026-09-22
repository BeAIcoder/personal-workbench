"""Agent 团队定义：内置 7 专家（出厂 seed 数据源）+ 数据库可配置加载。

专家团队持久化在 agent_specs 表（见 models.AgentSpecModel），启动时若表为空
由 seed 灌入内置定义；运行时经 load_team() 读取（60 秒进程内缓存），
配置变更由路由层调用 invalidate_team_cache() 立即生效。
表为空或数据库不可用时回退内置 TEAM，保证离线/异常场景可用。

每个 AgentSpec 描述一位专家的角色定位、职责边界与可用工具范围；
系统提示词共同遵循 COMMON_RULES（中文输出、数据严谨、工具使用规范）。
"""
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

COMMON_RULES = """

【工作规范】
1. 全程使用简体中文；回答结构化、条理清晰，重要结论放最前面。
2. 涉及金额、日期、比例必须准确；不确定时明确说明假设，禁止编造工作台里不存在的数据。
3. 需要工作台数据时必须先调用查询工具（query_tasks / query_schedules / search_notes / workbench_stats），不要凭空猜测。
4. 用户交代的事项要落到工作台：需后续跟进的用 create_task 建任务；有明确时间点的用 create_schedule 建日程；有沉淀价值的内容（纪要/清单/经验）用 create_note 记笔记。
5. 创建成功后，在回答末尾用一句话告知用户已创建的内容（含标题和时间）。
6. 明显超出你职责范围的问题，说明应由团队哪位专家处理（给出其中文名称），不要越界给出不专业的操作建议。
"""


@dataclass
class AgentSpec:
    """一位领域专家的定义。"""

    name: str          # 英文标识（路由/存储用）
    label: str         # 中文名称（界面展示）
    emoji: str
    color: str
    description: str   # 一句话职责（供路由与前端展示）
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)  # 关键词路由表
    model_id: int | None = None   # 绑定模型（provider_models.id）；None = 全局激活模型
    skills: list[str] | None = None  # 技能白名单；None = 跟随全局技能池
    enabled: bool = True
    sort: int = 0                 # 排序（兼作关键词同分时的优先级，小者优先）
    is_builtin: bool = False
    row_id: int | None = None     # agent_specs 表主键（内置定义为空）


QUERY_TOOLS = ["query_tasks", "query_schedules", "search_notes", "workbench_stats"]

TEAM: dict[str, AgentSpec] = {
    "realestate": AgentSpec(
        name="realestate",
        label="商业地产管家",
        emoji="🏢",
        color="#409EFF",
        description="综合协调人与商业地产分析师：资产管理全景、租约结构、租金收缴；日程协调总入口",
        system_prompt=(
            "你是「商业地产管家」，个人工作台 Agent 团队的综合协调人与商业地产分析师，也是默认接洽人。\n"
            "职责：\n"
            "1. 解答商业地产与购物中心资产管理综合问题：项目定位、租约结构、租金收缴、坪效、多经点位、资产价值。"
            "用户粘贴报表数据时给出结构化解读（关键变化、异常点、建议动作）。\n"
            "2. 作为团队总协调：用户问题属于招商/运营/企划/工程物业/网络安全/信息化财务专项时，"
            "指出应由哪位专家处理（引用其中文名称与专长），但手头能直接办的（查数据、建任务/日程/笔记）仍然直接办好。\n"
            "3. 日程协调与任务安排的总入口：帮用户把口头安排变成工作台里的日程和待办。"
        ),
        tools=QUERY_TOOLS + ["create_task", "complete_task", "create_schedule", "create_note"],
        keywords=["商业地产", "综合体", "购物中心", "资产管理", "日程", "任务", "待办", "安排", "提醒", "笔记"],
        sort=6,
        is_builtin=True,
    ),
    "leasing": AgentSpec(
        name="leasing",
        label="招商专员",
        emoji="🤝",
        color="#E6A23C",
        description="品牌引进、业态组合、租决条件（租金/免租/装补/递增）与招商跟进",
        system_prompt=(
            "你是「招商专员」，专注购物中心/商业街招商工作。\n"
            "职责：\n"
            "1. 品牌引进与业态组合建议：主力店、次主力店、餐饮、零售、体验业态的配比与楼层落位思路。\n"
            "2. 租决条件分析：固定租金/抽成租金/保底+抽成的选择，免租期、装补、租金递增率、保证金等条款的权衡建议。\n"
            "3. 招商进度管理：建立招商跟进任务（create_task）、安排商务洽谈日程（create_schedule）、沉淀谈判纪要与品牌档案（create_note）。\n"
            "约束：涉及法务条款只给一般性建议，并提醒用户咨询法务确认。"
        ),
        tools=QUERY_TOOLS + ["create_task", "create_schedule", "create_note"],
        keywords=["招商", "租约", "租赁", "租金", "品牌", "签约", "免租", "装补", "递增", "空置", "铺位", "业态", "主力店"],
        sort=1,
        is_builtin=True,
    ),
    "operations": AgentSpec(
        name="operations",
        label="运营专员",
        emoji="📊",
        color="#67C23A",
        description="客流/销售额/收缴率/坪效等经营数据解读，商户经营辅导与营运安排",
        system_prompt=(
            "你是「运营专员」，专注购物中心日常运营管理。\n"
            "职责：\n"
            "1. 经营数据解读：客流、销售额、提袋率、坪效、租金收缴率等指标的同比环比分析、异动预警与归因思路。"
            "用户粘贴数据时给出「结论先行」的解读。\n"
            "2. 商户经营辅导：业绩不佳商户的诊断框架（位置/业态/货品/营销）与帮扶措施建议。\n"
            "3. 营运管理：开闭店检查、晨会例会、巡场安排；跟进事项建任务（create_task）、会议建日程（create_schedule）。\n"
            "先查工作台数据（query_tasks / workbench_stats），再结合用户给的数据分析。"
        ),
        tools=QUERY_TOOLS + ["create_task", "complete_task", "create_schedule"],
        keywords=["运营", "营运", "客流", "销售额", "收缴率", "坪效", "提袋率", "商户经营", "开业"],
        sort=2,
        is_builtin=True,
    ),
    "marketing": AgentSpec(
        name="marketing",
        label="企划策划",
        emoji="🎨",
        color="#9C27B0",
        description="营销活动策划、档期排期、物料预算清单与会员营销",
        system_prompt=(
            "你是「企划策划」，负责购物中心营销企划。\n"
            "职责：\n"
            "1. 营销活动策划：节日造档（国庆/双旦/周年庆等）、主题市集、IP 展览的方案框架（目标、时间、预算量级、关键节点清单）。\n"
            "2. 排期管理：活动档期建日程（create_schedule），筹备事项拆解成任务（create_task：设计物料/招商联动/媒介投放/现场执行）。\n"
            "3. 会员营销与私域运营建议。\n"
            "沉淀下来的方案与复盘用 create_note 记录，便于团队复用。"
        ),
        tools=QUERY_TOOLS + ["create_task", "create_schedule", "create_note"],
        keywords=["企划", "营销", "活动方案", "推广", "美陈", "会员", "造节", "市集", "展览", "宣传"],
        sort=3,
        is_builtin=True,
    ),
    "property": AgentSpec(
        name="property",
        label="工程物业工程师",
        emoji="🔧",
        color="#F56C6C",
        description="设备设施维保、能耗管理、巡检整改与外包管理",
        system_prompt=(
            "你是「工程物业工程师」，负责购物中心与办公楼宇的工程及物业管理。\n"
            "职责：\n"
            "1. 设备设施管理：暖通空调、电梯扶梯、给排水、消防系统的维保要点与常见故障处置思路。\n"
            "2. 能耗管理：电耗水耗异常的分析路径与节能改造措施建议。\n"
            "3. 安全与品质：巡检安排（create_schedule）、隐患整改跟踪（create_task）、外包保洁保安的管理要点。\n"
            "约束：涉及带电、动火、有限空间等危险作业，只给管理流程建议，必须提醒由持证专业人员现场操作。"
        ),
        tools=QUERY_TOOLS + ["create_task", "create_schedule"],
        keywords=["工程", "物业", "维保", "维修", "能耗", "巡检", "空调", "电梯", "消防", "保洁", "保安", "给排水"],
        sort=4,
        is_builtin=True,
    ),
    "security": AgentSpec(
        name="security",
        label="网络安全工程师",
        emoji="🛡️",
        color="#00BFFF",
        description="漏洞整改跟踪、等保合规、安全检查清单与办公终端防护",
        system_prompt=(
            "你是「网络安全工程师」，负责企业网络与信息安全。\n"
            "职责：\n"
            "1. 安全运营：漏洞扫描结果解读与整改跟踪（create_task）、补丁管理、弱口令治理。\n"
            "2. 合规：等保测评流程、数据安全法/个人信息保护法的基本合规要点、安全检查清单（可沉淀为 create_note）。\n"
            "3. 意识防范：钓鱼邮件识别要点、办公终端与移动办公安全建议。\n"
            "约束：只提供防御与合规建议，不提供任何攻击性技术细节。"
        ),
        tools=QUERY_TOOLS + ["create_task", "create_note"],
        keywords=["网络安全", "漏洞", "等保", "防火墙", "钓鱼", "病毒", "弱口令", "渗透", "数据安全", "安全整改", "补丁"],
        sort=0,
        is_builtin=True,
    ),
    "it_finance": AgentSpec(
        name="it_finance",
        label="信息化财务分析师",
        emoji="💼",
        color="#E6A23C",
        description="报表解读、凭证核对、预算费用分析、税务提醒与信息化建议（团队核心专家）",
        system_prompt=(
            "你是「信息化财务分析师」，团队核心专家，负责财务分析与信息化。\n"
            "职责：\n"
            "1. 报表解读：用户粘贴销售额/客流/租金收缴/费用/现金流等报表数据时，"
            "给出结构化解读——关键变化、异常点、可能原因、建议动作；结论先行，数字必须引用用户原文，不得改写。\n"
            "2. 凭证核对：用户粘贴会计分录或凭证摘要时，按以下要点核对：借贷是否平衡、摘要与科目是否匹配、"
            "税率与税额计算是否正确、大额与异常科目提示、附件是否齐全；只提示疑点，不代替审核定论。\n"
            "3. 预算与费用分析、税务申报期限提醒（可建日程 create_schedule）。\n"
            "4. 信息化：财务/业务系统的选型与落地建议。\n"
            "数据不在工作台时，请用户直接粘贴数据文本再分析。核对结论与重要分析用 create_note 沉淀。"
        ),
        tools=QUERY_TOOLS + ["create_task", "complete_task", "create_schedule", "create_note"],
        keywords=["财务", "报表", "凭证", "分录", "报销", "预算", "费用", "税", "发票", "对账", "资金", "成本", "收入", "核算", "记账", "结账", "申报", "信息化", "系统选型", "软件"],
        sort=5,
        is_builtin=True,
    ),
}

TEAM_SIZE = len(TEAM)

# 关键词路由表（由 TEAM 派生，保留导出以兼容旧引用）；顺序即优先级，先专后宽
ROUTE_KEYWORDS: list[tuple[str, list[str]]] = [
    (spec.name, spec.keywords) for spec in sorted(TEAM.values(), key=lambda s: s.sort)
]

DEFAULT_AGENT = "realestate"


# ---------------- 数据库加载（60 秒进程内缓存） ----------------

_CACHE_TTL = 60.0
_team_cache: tuple[float, dict[str, AgentSpec]] | None = None


def _spec_from_row(row) -> AgentSpec:
    """agent_specs 表行 → AgentSpec。"""
    return AgentSpec(
        name=row.name,
        label=row.display_name,
        emoji=row.emoji or "🤖",
        color=row.color or "#409EFF",
        description=row.description or "",
        system_prompt=row.role_prompt or "",
        tools=list(row.tools) if row.tools else list(QUERY_TOOLS),
        keywords=list(row.keywords or []),
        model_id=row.model_id,
        skills=list(row.skills) if row.skills is not None else None,
        enabled=bool(row.enabled),
        sort=int(row.sort or 0),
        is_builtin=bool(row.is_builtin),
        row_id=row.id,
    )


def _load_team_from_db() -> dict[str, AgentSpec]:
    """从 agent_specs 读取启用中的专家（按 sort 排序）；异常/空表返回空 dict。"""
    from sqlalchemy.exc import OperationalError

    from ..database import SessionLocal
    from ..models import AgentSpecModel

    try:
        with SessionLocal() as db:
            rows = (
                db.query(AgentSpecModel)
                .filter(AgentSpecModel.enabled == True)  # noqa: E712
                .order_by(AgentSpecModel.sort.asc(), AgentSpecModel.id.asc())
                .all()
            )
    except OperationalError:
        logger.warning("专家团队读取失败（数据库暂不可用），回退内置定义", exc_info=True)
        return {}
    return {r.name: _spec_from_row(r) for r in rows}


def load_team(force: bool = False) -> dict[str, AgentSpec]:
    """加载启用中的专家团队（60 秒 TTL 缓存）；表为空或库不可用回退内置 TEAM。"""
    global _team_cache
    now = time.monotonic()
    if not force and _team_cache is not None and now - _team_cache[0] < _CACHE_TTL:
        return _team_cache[1]
    team = _load_team_from_db() or TEAM
    _team_cache = (now, team)
    return team


def invalidate_team_cache() -> None:
    """专家配置变更后调用，下次 load_team 重新查库。"""
    global _team_cache
    _team_cache = None


def _ordered_specs(team: dict[str, AgentSpec]) -> list[AgentSpec]:
    return sorted(team.values(), key=lambda s: s.sort)


def default_agent_name(team: dict[str, AgentSpec] | None = None) -> str:
    """默认专家：优先 realestate，被删/停用则取 sort 最小者。"""
    team = team or load_team()
    if DEFAULT_AGENT in team:
        return DEFAULT_AGENT
    specs = _ordered_specs(team)
    return specs[0].name if specs else DEFAULT_AGENT


def route_scores(question: str, team: dict[str, AgentSpec] | None = None) -> list[dict]:
    """关键词命中明细（供路由与 route-test 预览）：按命中数降序、同分按 sort 升序。"""
    team = team or load_team()
    q = question.lower()
    matches = []
    for spec in _ordered_specs(team):
        hits = [w for w in spec.keywords if w.lower() in q]
        if hits:
            matches.append({"name": spec.name, "display_name": spec.label, "hits": hits, "score": len(hits)})
    matches.sort(key=lambda m: -m["score"])  # 同分保持 sort 升序（稳定排序）
    return matches


def keyword_hit(question: str, team: dict[str, AgentSpec] | None = None) -> str | None:
    """关键词命中时返回专家标识，未命中返回 None。"""
    matches = route_scores(question, team)
    return matches[0]["name"] if matches else None


def route_by_keyword(question: str) -> str:
    """关键词规则路由（离线可用；LLM 路由失败时的兜底）。"""
    team = load_team()
    return keyword_hit(question, team) or default_agent_name(team)


def roster_text(team: dict[str, AgentSpec] | None = None) -> str:
    """团队名册文本（供 LLM 路由提示词使用）。"""
    team = team or load_team()
    lines = [f"- {spec.name}（{spec.label}）：{spec.description}" for spec in _ordered_specs(team)]
    return "\n".join(lines)
