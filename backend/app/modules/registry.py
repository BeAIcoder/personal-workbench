"""模块注册器：扫描 app/modules/*/manifest.py，校验、挂载路由、注册模型与 Agent 工具。

设计要点：
- `_` 开头的目录（如 _template 模板）不视为模块，直接跳过；
- 任何模块加载/校验/挂载失败仅记日志并跳过该模块，绝不抛异常拖垮宿主；
- load_modules(app) 必须在 main.py 模块级作用域调用（静态 include_router 之后、
  lifespan 之前）：导入 manifest 时连带导入模块 models，把模块表注册进
  Base.metadata，lifespan 里的 create_all 才能建出模块表；
- collect_column_patches() 汇总各模块声明的列补丁，供 main 的轻量迁移合并执行。
"""
import importlib
import logging
from pathlib import Path

from fastapi import FastAPI

from ..agents.tools import register_module_tools
from .spec import ModuleManifest

logger = logging.getLogger(__name__)

# app/modules 目录
MODULES_DIR = Path(__file__).resolve().parent

# 本次成功加载的模块清单（load_modules 时填充）
_loaded: list[ModuleManifest] = []


def discover_module_names() -> list[str]:
    """扫描模块目录名：含 manifest.py 且目录名不以 _ 开头。"""
    names: list[str] = []
    for child in sorted(MODULES_DIR.iterdir()):
        if child.is_dir() and not child.name.startswith("_") and (child / "manifest.py").is_file():
            names.append(child.name)
    return names


def _load_manifest(name: str) -> ModuleManifest | None:
    """导入并校验单个模块的 manifest；任何失败记日志返回 None。"""
    try:
        mod = importlib.import_module(f"app.modules.{name}.manifest")
    except Exception:
        logger.exception("模块 %s 导入失败，已跳过（不影响宿主）", name)
        return None
    manifest = getattr(mod, "manifest", None)
    if not isinstance(manifest, ModuleManifest):
        logger.warning("模块 %s 的 manifest.py 未定义 ModuleManifest 变量 manifest，已跳过", name)
        return None
    errors = manifest.validate()
    if manifest.name != name:
        errors.append(f"目录名 {name!r} 与 manifest.name {manifest.name!r} 不一致")
    if errors:
        logger.warning("模块 %s manifest 校验失败：%s；已跳过", name, "；".join(errors))
        return None
    return manifest


def load_modules(app: FastAPI) -> list[ModuleManifest]:
    """发现、校验并挂载全部启用模块，返回成功加载的 manifest 列表。

    分两遍执行：先加载校验全部 manifest（含 requires 依赖检查），再统一挂载，
    保证依赖缺失的模块在挂载前就被跳过。单模块任何异常只跳过该模块。
    """
    global _loaded
    _loaded = []
    manifests: dict[str, ModuleManifest] = {}
    for name in discover_module_names():
        manifest = _load_manifest(name)
        if manifest is None:
            continue
        if not manifest.enabled:
            logger.info("模块 %s 已禁用（enabled=False），跳过", name)
            continue
        if name in manifests:
            logger.warning("模块 %s 重复定义，已跳过", name)
            continue
        manifests[name] = manifest

    for name, manifest in manifests.items():
        missing = [dep for dep in manifest.requires if dep not in manifests]
        if missing:
            logger.warning("模块 %s 依赖未加载：%s，已跳过", name, ", ".join(missing))
            continue
        try:
            router_mod = importlib.import_module(f"app.modules.{name}.router")
            app.include_router(router_mod.router, prefix=manifest.prefix)
        except Exception:
            logger.exception("模块 %s 路由挂载失败，已跳过", name)
            continue
        if manifest.agent_tools:
            register_module_tools({t["name"]: t for t in manifest.agent_tools})
        _loaded.append(manifest)
        logger.info("模块已加载：%s v%s（prefix=%s）", manifest.name, manifest.version, manifest.prefix)
    return _loaded


def get_loaded_modules() -> list[ModuleManifest]:
    """返回本次成功加载的模块清单（副本）。"""
    return list(_loaded)


def collect_column_patches() -> dict[str, dict]:
    """汇总各模块声明的轻量迁移列补丁（{表名: {列名: DDL}}），供宿主合并执行。"""
    merged: dict[str, dict] = {}
    for manifest in _loaded:
        for table, columns in (manifest.column_patches or {}).items():
            merged.setdefault(table, {}).update(columns)
    return merged
