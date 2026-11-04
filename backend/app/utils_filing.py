# -*- coding: utf-8 -*-
"""税收填报记录的导出产物清理（删除记录时同步删除底稿，避免 exports/ 累积孤儿文件）。

台账 L-065：四个填报模块（印花税/增值税/个税/残保金）原先删除记录只删库行，
`exports/` 里的 xlsx 永久残留。这里收口为单一实现：只按**文件名**解析目标路径，
传入的 `export_path` 即使被污染成 `../../x` 也只会落到导出目录内的同名文件上。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def unlink_export_artifact(export_path: str | None, exports_dir: Path | str) -> bool:
    """删除导出产物；返回是否真的删除了文件。路径非法或文件不存在都不报错。"""
    if not export_path:
        return False
    name = str(export_path).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", ".."):
        return False
    target = Path(exports_dir) / name
    try:
        if not target.is_file():
            return False
        target.unlink()
        return True
    except OSError:
        # 文件被 Excel 占用等场景不阻断记录删除，但必须留痕便于排查
        logger.warning("导出产物清理失败（文件可能被占用）：%s", target, exc_info=True)
        return False
