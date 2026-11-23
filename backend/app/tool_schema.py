"""Agent 工具参数 schema 的唯一事实源（内置 TOOL_SPECS 与模块 manifest 共用）。

约定（2026-11-17 起只有这一种写法）：
- ``params = {参数名: (JSON 类型, 中文说明)}``，JSON 类型限定在白名单内；
- 必填参数**只**写进 spec 的 ``required`` 列表（与 JSON Schema 语义一致），
  ``params`` 里没有第三位；
- ``required`` 必须是 ``params`` 的子集，且不可重复。

为什么收紧：历史上 ``params`` 出现过 3 元组 ``(type, description, required)``、2 元组和
dict 三种写法，``_param_schema`` 只读前两位、**静默忽略**第三位——于是「必填」有两个
来源（三元组第三位 / required 列表）且可能互相矛盾，模型拿到的 schema 与声明不一致。
现在非二元组一律判为非法定义，由调用方决定降级粒度：
manifest 校验失败 → 跳过该模块；``build_tools`` → 跳过该工具并记 ERROR。

本模块刻意不 import 任何业务代码：``app.modules.spec`` 与 ``app.agents.tools`` 都依赖它，
放进来才能既共享同一份校验、又不制造 import 环。
"""
from __future__ import annotations

from typing import Any

# JSON Schema 允许的参数类型白名单：与 MCP inputSchema 的映射保持一致
JSON_PARAM_TYPES: frozenset[str] = frozenset(
    {"string", "integer", "number", "boolean", "array", "object"}
)


def param_schema(pname: str, pval: Any) -> dict[str, str]:
    """把单个参数定义转成 JSON Schema 片段；定义非法时抛 ValueError。"""
    if not isinstance(pval, tuple) or len(pval) != 2:
        raise ValueError(
            f"参数 {pname} 必须是 (JSON 类型, 说明) 二元组，当前为 "
            f"{type(pval).__name__} {pval!r}；必填写进 required 列表"
        )
    ptype, desc = pval
    if ptype not in JSON_PARAM_TYPES:
        raise ValueError(
            f"参数 {pname} 的 JSON 类型 {ptype!r} 不在白名单 {sorted(JSON_PARAM_TYPES)} 内"
        )
    if not isinstance(desc, str):
        raise ValueError(f"参数 {pname} 的说明必须是字符串，当前为 {type(desc).__name__}")
    return {"type": ptype, "description": desc}


def params_errors(label: str, params: Any, required: Any = None) -> list[str]:
    """收集参数定义错误（空列表 = 合法）。

    ``required`` 传 None 时只校验 ``params`` 本身（用于只关心形状的场景）。
    """
    if not isinstance(params, dict):
        return [f"{label} 的 params 必须是 dict，当前为 {type(params).__name__}"]
    errors: list[str] = []
    for pname, pval in params.items():
        if not isinstance(pname, str) or not pname:
            errors.append(f"{label} 存在非法参数名 {pname!r}")
            continue
        try:
            param_schema(pname, pval)
        except ValueError as exc:
            errors.append(f"{label} {exc}")
    if required is None:
        return errors
    if not isinstance(required, list):
        return errors + [f"{label} 的 required 必须是 list，当前为 {type(required).__name__}"]
    bad = [r for r in required if not isinstance(r, str) or not r]
    if bad:
        errors.append(f"{label} 的 required 含非字符串/空名称：{bad!r}")
    missing = [r for r in required if isinstance(r, str) and r not in params]
    if missing:
        errors.append(f"{label} 的 required 含未声明的参数：{missing}")
    dupes = sorted({r for r in required if isinstance(r, str) and required.count(r) > 1})
    if dupes:
        errors.append(f"{label} 的 required 有重复项：{dupes}")
    return errors


def input_schema(label: str, params: Any, required: Any = None) -> dict:
    """按 label 构造工具的 JSON Schema；任何非法定义抛 ValueError（不做静默兜底）。"""
    errors = params_errors(label, params, [] if required is None else required)
    if errors:
        raise ValueError("；".join(errors))
    names = list(required or [])
    return {
        "type": "object",
        "properties": {pname: param_schema(pname, pval) for pname, pval in params.items()},
        "required": names,
    }
