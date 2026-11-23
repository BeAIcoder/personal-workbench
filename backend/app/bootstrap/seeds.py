"""启动种子与幂等补齐（从 `main.lifespan` 拆出）。

全部步骤都是幂等的，可以重复执行（启动、脚本、测试夹具都走同一份实现）。
失败策略与拆分前一致：
- 可降级项（知识库索引、专家包、技能包、调试中心种子、定时任务中断恢复）只告警；
- 必需项（模块工具预置 apply_pending_presets）不吞异常，fail-fast。
"""
from __future__ import annotations

import logging

from ..config import settings
from ..database import SessionLocal

logger = logging.getLogger(__name__)


def ensure_knowledge_index() -> None:
    """知识库索引（FTS5 / sqlite-vec 虚拟表）不在 ORM metadata 里，单独幂等创建。

    失败只降级检索能力，不影响启动（缺 FTS5 时关键词检索退化为 LIKE）。
    """
    try:
        from ..knowledge.index import ensure_schema

        ensure_schema()
    except Exception:
        logger.warning("知识库索引初始化跳过（不影响启动）", exc_info=True)


def normalize_jobs_and_experts() -> None:
    """历史脏任务先禁用/归一化；内置专家增量补齐但尊重删除墓碑。"""
    from ..scheduler import normalize_legacy_jobs
    from ..seed import seed_agent_specs

    with SessionLocal() as db:
        normalize_legacy_jobs(db)
        seed_agent_specs(db)


def ensure_expert_packs() -> None:
    """专家团文件化：确保专家包目录存在并完成种子复制（失败仅记日志）。"""
    try:
        from ..agents import expert_packs

        expert_packs.packs_dir()
    except Exception:
        logger.warning("专家包目录初始化跳过（不影响启动）", exc_info=True)


def ensure_skill_packs() -> None:
    """技能包种子：首次启动把 seed_skills 复制进 data/skills（幂等；失败仅记日志）。"""
    try:
        from ..agents.skill_store import ensure_seed_packs

        ensure_seed_packs()
    except Exception:
        logger.warning("技能包种子初始化跳过", exc_info=True)


def ensure_debug_issues() -> None:
    """调试中心种子问题：首次启动写入 data/debug/issues.json（幂等；失败仅记日志）。"""
    try:
        from ..modules.debug_center import service as debug_center_svc

        debug_center_svc.ensure_seed_issues()
    except Exception:
        logger.warning("调试中心种子初始化跳过", exc_info=True)


def apply_module_presets() -> None:
    """模块工具预置补齐：load_modules 早于本步骤，新库场景下暂存的预置在此生效。

    必需项：模块工具预置缺失会让专家工具集静默不全，因此不吞异常（fail-fast）。
    """
    from ..modules import apply_pending_presets

    apply_pending_presets()


def seed_sample_data() -> None:
    """空库且允许时写入示例数据（设置项 seed_on_startup 控制）。"""
    if not settings.seed_on_startup:
        return
    from ..seed import seed_if_empty

    with SessionLocal() as db:
        seed_if_empty(db)


def recover_scheduler_runs() -> None:
    """上次进程退出时仍在执行的定时任务：标为 unknown（结果未知），不自动重试。"""
    try:
        from ..scheduler import recover_interrupted_runs

        recovered = recover_interrupted_runs()
        if recovered:
            logger.warning("定时任务中断恢复：%d 轮标记为 unknown（未自动重试）", recovered)
    except Exception:
        logger.warning("定时任务中断恢复跳过（不影响启动）", exc_info=True)


def run_startup_seeds() -> None:
    """按拆分前的顺序执行全部幂等启动种子。"""
    ensure_knowledge_index()
    normalize_jobs_and_experts()
    ensure_expert_packs()
    ensure_skill_packs()
    ensure_debug_issues()
    apply_module_presets()
    seed_sample_data()
    recover_scheduler_runs()
