"""模块注册机制（Module Registry）：宿主按目录发现模块，挂载路由、注册表与 Agent 工具。"""
from .registry import (
    apply_pending_presets,
    collect_column_patches,
    discover_module_names,
    get_loaded_modules,
    load_modules,
)
from .spec import ModuleManifest

__all__ = [
    "ModuleManifest",
    "apply_pending_presets",
    "collect_column_patches",
    "discover_module_names",
    "get_loaded_modules",
    "load_modules",
]
