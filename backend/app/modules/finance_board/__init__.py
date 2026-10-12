"""finance_board 报表看板模块：微信报表监控管线触发 + HTML 快照归档与嵌入。

模块加载时向调度器注册定时动作 `finance_board.run`：定时任务页可创建
「模块动作」任务（schedule_type=cron/interval/daily + action=finance_board.run），
到点自动刷新看板，无需 LLM 参与。
"""
from . import models  # noqa: F401
from .manifest import manifest  # noqa: F401
from . import service as _service
from ...scheduler import register_action as _register_action

__all__ = ["manifest", "models"]


def _run_board_action() -> dict:
    """调度器动作入口：跑一次管线并归档快照；失败时返回 error 状态（不抛出，避免调度循环异常）。"""
    try:
        snap = _service.run_pipeline()
        return {"status": "ok", "message": f"看板已刷新（快照 {snap['stamp']}，{snap['elapsed_sec']}s）"}
    except Exception as exc:  # noqa: BLE001  动作失败转为状态记录，不影响其他任务
        return {"status": "error", "message": f"看板刷新失败：{exc}"}


_register_action("finance_board.run", _run_board_action)
