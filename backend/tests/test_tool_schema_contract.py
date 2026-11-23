"""工具参数 schema 契约（2026-11-17 清理 #9）。

唯一事实源是 `app/tool_schema.py`：

- ``params = {参数名: (JSON 类型, 说明)}``，JSON 类型限定白名单；
- 必填参数只写进 ``required`` 列表，``required`` 必须是 ``params`` 的子集且不重复。

本文件把「内置 TOOL_SPECS / 模块 manifest / build_tools 产物 / ModuleManifest 校验」
四处对齐，防止再出现「3 元组第三位 required 被静默忽略」或「required 越界」的漂移；
同时锁定 manifest 已删除的 menu/tables（菜单唯一源是前端 manifest.js，表名以
``models.__tablename__`` 为准）。
"""
import importlib
import pkgutil
from dataclasses import fields

import pytest

from app.agents.tools import TOOL_SPECS, build_tools
from app.modules.registry import _load_manifest, discover_module_names
from app.modules.spec import ModuleManifest
from app.tool_schema import JSON_PARAM_TYPES, param_schema, params_errors


def test_builtin_tool_specs_follow_the_single_schema():
    assert TOOL_SPECS
    for name, spec in TOOL_SPECS.items():
        assert params_errors(f"内置工具 {name!r}", spec.get("params"), spec.get("required")) == []
        assert isinstance(spec.get("readonly"), bool), name


def test_all_module_manifests_follow_the_single_schema():
    for module_name in discover_module_names():
        manifest = _load_manifest(module_name)
        assert manifest is not None, f"{module_name} 未能加载"
        assert manifest.validate() == [], f"{module_name} manifest 校验失败"
        for tool in manifest.agent_tools:
            assert isinstance(tool.get("required"), list), f"{module_name}.{tool.get('name')}"
            assert params_errors(
                f"{module_name}.{tool.get('name')}", tool.get("params"), tool["required"]
            ) == []


def test_build_tools_schema_matches_declaration():
    """build_tools 产出的 schema 必须与声明逐字段一致（不补 0、不猜类型、不吞 required）。"""
    names = list(TOOL_SPECS)
    tools = build_tools(names)
    assert len(tools) == len(names)
    for name, tool in zip(names, tools):
        spec = TOOL_SPECS[name]
        schema = tool.input_schema
        assert schema["type"] == "object", name
        assert list(schema["properties"]) == list(spec["params"]), name
        assert schema["required"] == list(spec["required"]), name
        for pname, pval in spec["params"].items():
            assert schema["properties"][pname] == {"type": pval[0], "description": pval[1]}, (name, pname)


@pytest.mark.parametrize(
    "bad",
    [
        ("string", "说明", True),          # 历史三元组：第三位曾被静默忽略
        ["string", "说明"],                # list 不是二元组
        {"type": "string", "description": "字典写法"},
        123,
        ("string",),                       # 只有类型
        ("str", "非白名单类型"),
    ],
)
def test_param_schema_rejects_legacy_shapes(bad):
    with pytest.raises(ValueError):
        param_schema("p", bad)


def test_params_errors_reports_both_required_drift_directions():
    assert params_errors("t", {"a": ("string", "x")}, ["b"])  # required 含未声明参数
    assert params_errors("t", {"a": ("string", "x")}, ["a", "a"])  # required 重复
    assert params_errors("t", {"a": ("string", "x")}, "a")  # required 不是 list
    assert params_errors("t", {"a": ("string", "x")}, ["a"]) == []


def test_manifest_drops_menu_and_tables_fields():
    """menu/tables 已删除（无消费者）：title/version 保留（仅日志用）。"""
    names = {item.name for item in fields(ModuleManifest)}
    assert "menu" not in names and "tables" not in names
    assert {"name", "prefix", "title", "version", "agent_tools", "column_patches",
            "requires", "preset_experts", "enabled"} <= names


def test_module_tables_are_mod_prefixed():
    """表名契约从 manifest.tables 移到 models.__tablename__（建表的真实来源）。"""
    import app.modules as modules_pkg

    root = list(modules_pkg.__path__)[0]
    offenders: list[str] = []
    for info in pkgutil.iter_modules([root]):
        if not info.name.startswith(("finance_", "business_")):
            continue
        try:
            models = importlib.import_module(f"app.modules.{info.name}.models")
        except ModuleNotFoundError:
            continue  # 无自有表的模块（debug_center / business_gm 等）
        for attr in vars(models).values():
            table_name = getattr(attr, "__tablename__", None)
            if isinstance(table_name, str) and not table_name.startswith("mod_"):
                offenders.append(f"{info.name}:{table_name}")
    assert not offenders, offenders


def test_json_param_type_whitelist_is_closed():
    assert JSON_PARAM_TYPES == {"string", "integer", "number", "boolean", "array", "object"}
