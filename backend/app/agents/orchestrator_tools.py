"""编排器的工具规划与只读门控（自 orchestrator.py 机械拆出）。

职责边界：按专家白名单 / 工作模式 / 全局配置计算「本轮放哪些工具、是否允许
Bash 与插件、工具轮次上限」，以及技能池 loader 的构造。执行、路由、持久化
仍在 orchestrator.py；本模块不持有进程级缓存，也不导入 orchestrator。
"""
import logging
from pathlib import Path

from .team import QUERY_TOOLS, AgentSpec
from .tools import TOOL_SPECS

logger = logging.getLogger(__name__)

# Agent 工作模式
WORK_MODES = {
    "standard": {"label": "标准执行", "hint": "全部工具，自动执行"},
    "readonly": {"label": "只读咨询", "hint": "仅查询分析，不修改任何数据"},
    "deep": {"label": "深度研究", "hint": "更高工具轮次，多角度深入分析"},
}


def _resolve_mode(mode: str | None) -> str:
    """工作模式归一化：未知/历史脏值一律 fail-closed 回退 readonly。

    定时任务等历史行可能残留非法 mode。旧实现回退 standard（可写 + Bash），
    等于把脏数据当成最高权限执行；这里改为只读，任何非白名单值都不得写数据。
    """
    return mode if mode in WORK_MODES else "readonly"


async def _resolve_skill_loaders(cfg: dict, spec: AgentSpec):
    """构造注入 Agent 的技能 loader 列表（None = 不注入技能池）。

    全局未启用技能池或未配置技能池目录时不注入；专家级 skills 为空列表时
    显式关闭（None 跟随全局），非空列表为专家白名单——只注入名单内的技能包。
    已停用的技能包（_store.json 的 disabled 列表）与专家白名单由过滤 loader
    在注入侧统一剔除——LocalSkillLoader 本身不做过滤，停用与白名单由此对
    Agent 注入真实生效。
    """
    if not (cfg.get("enable_skills") and cfg.get("skills_dir")):
        return None
    if spec.skills is not None and not spec.skills:
        return None  # 专家配置显式关闭技能池
    skills_dir = Path(cfg["skills_dir"])
    if not skills_dir.is_dir():
        return None
    try:
        from agentscope.skill import LocalSkillLoader

        from . import skill_store

        disabled = set(skill_store.disabled_packs(skills_dir))
        whitelist = set(spec.skills) if spec.skills else None  # 专家级非空白名单

        class _EnabledSkillLoader(LocalSkillLoader):
            """只注入启用中（且在专家白名单内）技能包的 loader。"""

            async def list_skills(self):
                skills = await super().list_skills()
                if disabled:
                    skills = [s for s in skills if Path(s.dir).name not in disabled and s.name not in disabled]
                if whitelist is not None:
                    skills = [s for s in skills if s.name in whitelist or Path(s.dir).name in whitelist]
                return skills

        return [_EnabledSkillLoader(directory=str(skills_dir), scan_subdir=True)]
    except Exception:
        logger.warning("技能池加载失败，本次不注入技能：%s", skills_dir, exc_info=True)
        return None


def _readonly_tool_names() -> set[str]:
    """只读模式放行的工具名：内置查询工具 + 注册表中带 readonly=True 标记的工具。

    readonly 标记由模块 manifest（modules/spec.py 契约）与 MCP 工具注册（mcp.py）
    写入 TOOL_SPECS；构建时实时汇总，模块/MCP 注册变化即时生效。
    """
    # 快照迭代：MCP 连接/断开会在请求线程里增删 TOOL_SPECS
    return set(QUERY_TOOLS) | {name for name, spec in list(TOOL_SPECS.items()) if spec.get("readonly")}


def _tooling_plan(spec: AgentSpec, mode: str, cfg: dict) -> tuple[list[str], bool, bool, int]:
    """纯函数：按专家白名单/工作模式/全局配置计算本轮工具与执行能力。

    只读模式只放行 readonly=True 的工具；Bash/插件在只读模式恒关闭。
    read_context 是只读资料读取工具，自动补进所有专家（不破坏写工具白名单）。
    入口先归一化 mode：历史脏值 fail-closed 为 readonly（R11-016 执行侧兜底）。
    """
    mode = _resolve_mode(mode)
    tool_names = list(spec.tools)
    if mode == "readonly":
        readonly_names = _readonly_tool_names()
        tool_names = [t for t in tool_names if t in readonly_names]
    # 只读资料/知识工具自动补进所有专家：不破坏写工具白名单，
    # 也让「公司制度/软件手册」对所有专家立即可查（与 read_context 同一先例）
    for extra in ("read_context", "search_knowledge", "read_knowledge_doc"):
        spec = TOOL_SPECS.get(extra)
        if spec and spec.get("readonly") and extra not in tool_names:
            tool_names.append(extra)
    allow_bash = bool(cfg.get("enable_bash")) and mode != "readonly"
    allow_plugins = bool(cfg.get("enable_plugins") and cfg.get("plugins_dir")) and mode != "readonly"
    max_iters = cfg.get("max_iters", 5)
    if mode == "deep":
        max_iters = max(max_iters, 10)
    return tool_names, allow_bash, allow_plugins, max_iters


async def _agent_bash_available(agent) -> bool:
    """Agent 实际是否加载了 Bash 工具。

    `_tooling_plan` 只表示配置意愿；`toolkit.add_tool(Bash(...))` 可能失败（import/
    构造异常），此时本轮并没有 Bash。附件提示必须按实际工具集生成，否则会对模型
    与用户承诺不存在的执行能力（M7）。探测失败按不可用处理（fail-closed）。
    """
    toolkit = getattr(agent, "toolkit", None)
    if toolkit is None:
        return False
    try:
        schemas = await toolkit.get_tool_schemas()
    except Exception:
        logger.warning("读取 Agent 工具清单失败，按未启用 Bash 处理", exc_info=True)
        return False
    for schema in schemas or []:
        if not isinstance(schema, dict):
            continue
        function = schema.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").lower()
        if "bash" in name:
            return True
    return False
