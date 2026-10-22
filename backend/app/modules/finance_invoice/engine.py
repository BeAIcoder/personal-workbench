"""发票解析引擎接入（L0 直挂）：sys.path 动态挂载「发票批量重命名增加项目」，导入 invoice_processor 包。

- 引擎路径：settings.finance_invoice_engine_path 非空时优先；为空时按默认相对位置推导
  （personal-workbench 与「发票工具」同级，均在 01-财税自动化 目录下）；
- 引擎配置链：引擎自带 ConfigManager 读取其 config.yaml（存在时），并自动合并其
  config.local.yaml（本机真值，运行时只读引用，本模块不复制其任何内容）；
- 故障隔离：引擎缺失 / 导入失败只标记不可用，调用方收到 EngineUnavailableError（路由层转 503），
  绝不把异常抛穿宿主；
- 句柄按解析后的引擎根路径缓存；reset_engine_cache() 供测试与配置变更后隔离。
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import settings
from ...utils import resolve_engine_root

logger = logging.getLogger(__name__)


class EngineUnavailableError(RuntimeError):
    """发票解析引擎不可用（路径缺失 / 导入失败）。"""


@dataclass
class EngineHandle:
    """引擎句柄：缓存导入的类与配置，避免每次请求重复 import。"""

    root: Path
    processor_cls: Any  # invoice_processor.src.pdf_processor.PDFInvoiceProcessor
    config: Any  # invoice_processor.src.config_manager.ConfigManager


_cache: dict[str, EngineHandle] = {}
_errors: dict[str, str] = {}


def default_engine_root() -> Path:
    """默认引擎位置：<01-财税自动化>/发票工具/发票批量重命名增加项目。"""
    # 本文件位于 backend/app/modules/finance_invoice/engine.py
    # parents: [0]finance_invoice [1]modules [2]app [3]backend [4]personal-workbench [5]01-财税自动化
    return Path(__file__).resolve().parents[5] / "发票工具" / "发票批量重命名增加项目"


def engine_root() -> Path:
    """引擎根目录：配置优先，空则默认推导。"""
    raw = (settings.finance_invoice_engine_path or "").strip()
    # 支持目录或单一文件（文件取其父目录）——见 utils.resolve_engine_root
    return resolve_engine_root(raw) if raw else default_engine_root()


def _load(root: Path) -> EngineHandle:
    """实际导入引擎包；任何失败统一转为 EngineUnavailableError。"""
    marker = root / "invoice_processor" / "src" / "pdf_processor.py"
    if not marker.is_file():
        raise EngineUnavailableError(f"引擎路径无效：{root}（缺少 invoice_processor/src/pdf_processor.py）")
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        from invoice_processor.src.config_manager import ConfigManager
        from invoice_processor.src.pdf_processor import PDFInvoiceProcessor
    except Exception as exc:  # 依赖缺失 / 语法错误等一律按引擎不可用处理
        raise EngineUnavailableError(f"引擎导入失败：{type(exc).__name__}: {exc}") from exc
    config_path = root / "invoice_processor" / "config.yaml"
    # 引擎 ConfigManager 会自动合并同目录 config.local.yaml（本机真值，只读）
    config = ConfigManager(str(config_path)) if config_path.is_file() else ConfigManager()
    return EngineHandle(root=root, processor_cls=PDFInvoiceProcessor, config=config)


def get_engine() -> EngineHandle:
    """获取引擎句柄（按根路径缓存）；不可用时抛 EngineUnavailableError。"""
    root = engine_root()
    key = str(root)
    if key in _cache:
        return _cache[key]
    if key in _errors:
        raise EngineUnavailableError(_errors[key])
    try:
        handle = _load(root)
    except EngineUnavailableError as exc:
        _errors[key] = str(exc)
        logger.warning("finance_invoice 引擎不可用：%s", exc)
        raise
    _cache[key] = handle
    logger.info("finance_invoice 引擎已挂载：%s", root)
    return handle


def engine_status() -> dict:
    """引擎可用性探测（不抛异常），供路由层自检接口与前端降级提示。"""
    root = engine_root()
    try:
        handle = get_engine()
    except EngineUnavailableError as exc:
        return {"available": False, "root": str(root), "error": str(exc)}
    return {"available": True, "root": str(handle.root), "error": ""}


def reset_engine_cache() -> None:
    """清空引擎缓存（测试隔离 / 配置变更后调用）。"""
    _cache.clear()
    _errors.clear()
