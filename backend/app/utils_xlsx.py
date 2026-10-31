# -*- coding: utf-8 -*-
"""xlsx 导出的公式注入防护（单一实现，供各模块复用）。

以 ``= + - @`` 开头的文本会被 Excel 当作公式执行；写入前加前导单引号使其按
文本处理。经营/职能模块各自实现了 ``_cell``（leasing/operations/planning/
legal/hr/facility 共 6 份拷贝），本工具是收敛起点；新增导出一律用这里。
"""
from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def safe_cell(value: Any) -> Any:
    """文本以 ``= + - @`` 开头时加前导单引号，防止 openpyxl 写成活公式。"""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
