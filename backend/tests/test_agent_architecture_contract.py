"""AI 助手架构契约：编排边界、工具能力、只读模式与任务默认安全。"""
import ast
import importlib
from pathlib import Path

import pytest

from app import scheduler
from app.agents import orchestrator
from app.agents.team import TEAM, COMMON_RULES
from app.agents.tools import TOOL_SPECS
from app.modules.registry import _load_manifest, discover_module_names
from app.schemas import ScheduledJobBase

_APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(autouse=True)
def _ensure_tables():
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_scheduler_only_talks_to_orchestrator_chat():
    tree = _parse(_APP_DIR / "scheduler.py")
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not any(module.startswith("agentscope") for module in imports)
    assert not any("build_model" in module for module in imports)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "ChatMessage" not in names
    assert "build_model" not in names
    assert "Agent" not in names
    # 对话只经 orchestrator.chat，不在调度器里另起模型
    attrs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "orchestrator"
    }
    assert "chat" in attrs


def test_only_orchestrator_writes_chat_messages():
    offenders = []
    for path in _APP_DIR.rglob("*.py"):
        if path.name == "orchestrator.py":
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ChatMessage"
            ):
                offenders.append(str(path.relative_to(_APP_DIR)))
    assert offenders == []


def test_every_tool_declares_readonly_and_param_pairs():
    """工具 spec 统一形状：纵容过的写法一律不允许（唯一事实源 app/tool_schema.py）。"""
    from app.tool_schema import JSON_PARAM_TYPES

    assert TOOL_SPECS
    for name, spec in TOOL_SPECS.items():
        assert callable(spec.get("func")), name
        assert str(spec.get("description") or "").strip(), name
        assert isinstance(spec.get("readonly"), bool), name
        params = spec.get("params")
        assert isinstance(params, dict), name
        for pname, pval in params.items():
            assert isinstance(pval, tuple) and len(pval) == 2, f"{name}.{pname} 必须是 (JSON 类型, 说明) 二元组"
            assert pval[0] in JSON_PARAM_TYPES, f"{name}.{pname} 的 JSON 类型 {pval[0]!r} 不在白名单"
            assert isinstance(pval[1], str), f"{name}.{pname} 的说明必须是字符串"
        assert set(spec.get("required") or []) <= set(params), name


def test_every_module_agent_tool_declares_readonly():
    for module_name in discover_module_names():
        manifest = _load_manifest(module_name)
        if manifest is None:
            continue
        for tool in manifest.agent_tools or []:
            assert isinstance(tool.get("readonly"), bool), f"{module_name}.{tool.get('name')} 未显式声明 readonly"
            for pname, pval in (tool.get("params") or {}).items():
                assert isinstance(pval, tuple) and len(pval) == 2, (
                    f"{module_name}.{tool.get('name')}.{pname} 必须是 (JSON 类型, 说明) 二元组"
                )
            assert set(tool.get("required") or []) <= set(tool.get("params") or {}), (
                f"{module_name}.{tool.get('name')} 的 required 必须是 params 子集"
            )


def test_readonly_plan_excludes_bash_plugins_and_write_tools():
    spec = TEAM["realestate"]
    cfg = {"enable_bash": True, "enable_plugins": True, "plugins_dir": "D:/tmp/plugins", "max_iters": 5}
    names, allow_bash, allow_plugins, max_iters = orchestrator._tooling_plan(spec, "readonly", cfg)
    assert allow_bash is False
    assert allow_plugins is False
    assert "create_task" not in names
    assert "read_context" in names
    assert set(names) <= orchestrator._readonly_tool_names()
    assert max_iters == 5


def test_standard_plan_keeps_explicit_write_tools_and_bash():
    spec = TEAM["realestate"]
    cfg = {"enable_bash": True, "enable_plugins": True, "plugins_dir": "D:/tmp/plugins", "max_iters": 5}
    names, allow_bash, allow_plugins, max_iters = orchestrator._tooling_plan(spec, "standard", cfg)
    assert allow_bash is True
    assert allow_plugins is True
    assert "create_task" in names
    assert max_iters == 5


def test_deep_plan_raises_tool_iterations():
    spec = TEAM["realestate"]
    _, _, _, max_iters = orchestrator._tooling_plan(spec, "deep", {"max_iters": 5})
    assert max_iters == 10


def test_new_scheduled_job_defaults_to_readonly():
    job = ScheduledJobBase(name="默认安全", prompt="x", schedule_type="interval", interval_minutes=60)
    assert job.mode == "readonly"


def test_unknown_is_a_terminal_scheduler_status():
    assert "unknown" in scheduler.TERMINAL_STATUSES
    assert scheduler._fact_type_for_status("unknown") == "run.unknown"


def test_common_rules_treat_external_text_as_data():
    assert "资料文件" in COMMON_RULES
    assert "不是指令" in COMMON_RULES
