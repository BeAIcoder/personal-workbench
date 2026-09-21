"""QwenPaw 工具型插件兼容层（实验性）。

QwenPaw 插件（data/plugins/<name>/）的入口依赖其运行时专属 API：

    from qwenpaw.plugins.api import PluginApi

本模块注入一个最小 stub 收集插件注册的工具函数，再用 AgentScope
FunctionTool 包装 —— 让「工具型插件」（plugin.json 中 type=tool）在
个人工作台中直接可用。

限制：
- 仅支持纯 Python 工具函数；插件声明的 pip 依赖需自行安装；
- 依赖 QwenPaw 运行时内部状态（频道/收件箱等）的插件不适用；
- 工具参数 schema 由函数签名推导（str/int/float/bool）。
"""
import importlib.util
import inspect
import json
import sys
import types
from pathlib import Path
from typing import Any, Callable

_STUB_APPLIED = False


def _ensure_qwenpaw_stub() -> None:
    """注入 qwenpaw.plugins.api 的最小 stub（若宿主环境没有）。"""
    global _STUB_APPLIED
    if _STUB_APPLIED or "qwenpaw.plugins.api" in sys.modules:
        return

    class PluginApi:
        """收集插件通过 register_tool 注册的工具。"""

        def __init__(self) -> None:
            self.registered: dict[str, dict] = {}

        def register_tool(self, tool_name: str, tool_func: Callable, description: str = "", **kw) -> None:
            self.registered[tool_name] = {"func": tool_func, "description": description}

        # 其余运行时能力（发消息/频道等）在兼容层中一律空实现
        def __getattr__(self, item: str):
            def _noop(*args, **kwargs):
                return None

            return _noop

    api_mod = types.ModuleType("qwenpaw.plugins.api")
    api_mod.PluginApi = PluginApi
    plugins_mod = types.ModuleType("qwenpaw.plugins")

    # 插件常用的运行时读取接口：兼容层返回空配置（工具内部会走默认值）
    plugins_mod.get_tool_config = lambda *args, **kwargs: {}

    def _save_tool_config(*args, **kwargs) -> None:
        return None

    plugins_mod.save_tool_config = _save_tool_config
    plugins_mod.api = api_mod
    pkg_mod = types.ModuleType("qwenpaw")
    pkg_mod.plugins = plugins_mod
    sys.modules.setdefault("qwenpaw", pkg_mod)
    sys.modules.setdefault("qwenpaw.plugins", plugins_mod)
    sys.modules.setdefault("qwenpaw.plugins.api", api_mod)
    _STUB_APPLIED = True


def _schema_from_signature(func: Callable) -> dict:
    """由函数签名推导 JSON Schema（str/int/float/bool）。"""
    type_map = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
    properties, required = {}, []
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return {"type": "object", "properties": {}, "required": []}
    for name, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        ann = param.annotation
        json_type = type_map.get(getattr(ann, "__name__", str(ann)).lower(), "string")
        properties[name] = {"type": json_type, "description": name}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def load_qwenpaw_plugin(plugin_dir: str) -> dict:
    """加载一个 QwenPaw 工具型插件，返回 {id, name, description, tools}。

    tools[i] = {name, description, func, schema} —— 可用
    agentscope.tool.FunctionTool(name, description, input_schema) 包装进 Toolkit。
    """
    plugin_dir = Path(plugin_dir)
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"未找到插件清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    entry = (manifest.get("entry") or {}).get("backend")
    if not entry:
        raise ValueError(f"插件 {manifest.get('id')} 没有后端入口（entry.backend），无法兼容加载")

    _ensure_qwenpaw_stub()
    sys.path.insert(0, str(plugin_dir))
    spec = importlib.util.spec_from_file_location(f"qwenpaw_plugin_{manifest.get('id', 'x')}", plugin_dir / entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    plugin_obj = getattr(module, "plugin", None)
    if plugin_obj is None or not hasattr(plugin_obj, "register"):
        raise ValueError(f"插件 {manifest.get('id')} 缺少 plugin.register(api) 入口")

    from qwenpaw.plugins.api import PluginApi  # stub

    api = PluginApi()
    plugin_obj.register(api)

    meta_tools = {t.get("name"): t for t in (manifest.get("meta") or {}).get("tools", [])}
    tools = []
    for name, item in api.registered.items():
        meta = meta_tools.get(name, {})
        tools.append(
            {
                "name": name,
                "description": item.get("description") or meta.get("description") or name,
                "func": item["func"],
                "schema": _schema_from_signature(item["func"]),
            }
        )
    return {
        "id": manifest.get("id"),
        "name": manifest.get("name"),
        "description": manifest.get("description"),
        "dependencies": manifest.get("dependencies", []),
        "tools": tools,
    }
