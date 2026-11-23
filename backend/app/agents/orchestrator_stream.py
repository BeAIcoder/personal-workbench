"""编排器的流式辅助与轨迹治理（自 orchestrator.py 机械拆出）。

职责边界：思考块剥离/提取、中断前已流式内容的整理、工具轨迹的落库体积治理
（纯函数，不碰数据库）。落库与流式主循环仍在 orchestrator.py——ChatMessage 的
构造必须留在该文件（架构契约 test_only_orchestrator_writes_chat_messages）。
"""
import json
import re

_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# 工具轨迹落库大小治理（演进第一批）：单条 result 截到 4k 字符、整条轨迹上限 100KB，
# 超预算时从最旧条目开始丢弃并留一条说明标记。前端按 {tool,args,result} 列表渲染
# （ChatPanel trace-collapse），治理只做截断与计数，不改列表结构。
_TRACE_RESULT_MAX = 4_000
_TRACE_TOTAL_MAX = 100_000


def _govern_trace(trace: list) -> list:
    """限制工具轨迹的落库体积：单条 result 截断、总额超限丢弃最旧条目并留说明标记。"""
    if not trace:
        return trace
    governed: list = []
    dropped = 0
    total = 0
    for item in reversed(trace):
        if not isinstance(item, dict):
            dropped += 1
            continue
        result = str(item.get("result") or "")
        if len(result) > _TRACE_RESULT_MAX:
            result = result[:_TRACE_RESULT_MAX] + f"…（截断，原文 {len(result)} 字符）"
        args_size = len(json.dumps(item.get("args") or {}, ensure_ascii=False, default=str))
        size = len(result) + args_size
        if total + size > _TRACE_TOTAL_MAX and governed:
            dropped += 1
            continue
        total += size
        governed.append({**item, "result": result})
    governed.reverse()
    if dropped:
        governed.insert(0, {
            "tool": "trace",
            "args": {},
            "result": f"（工具轨迹已按大小治理：省略 {dropped} 条更早条目，单条 result 上限 {_TRACE_RESULT_MAX} 字符）",
        })
    return governed


def _split_inline_think(text: str) -> tuple[str, str]:
    """剥离正文中的 <think>…</think>（部分思考型模型会把它混进正文）。"""
    m = _THINK_TAG_RE.search(text)
    if m:
        think = m.group(1).strip()
        rest = (text[:m.start()] + text[m.end():]).strip()
    else:
        think, rest = "", text
    # 孤立的 think 结束标签也清掉（如 Qwen 正文开头的 "</think>"）
    rest = rest.replace("<think>", "").replace("</think>", "").strip()
    return rest, think


def _thinking_of(msg) -> str:
    """从最终消息中提取思考块文本。"""
    blocks = msg.get_content_blocks() if hasattr(msg, "get_content_blocks") else []
    parts = []
    for b in blocks or []:
        if getattr(b, "type", None) == "thinking":
            parts.append(getattr(b, "text", ""))
    return "\n".join(p for p in parts if p).strip()


def _interrupted_stream_message(delta_parts: list[str], thinking_parts: list[str]) -> tuple[str, str]:
    """把中断前已流式产出的正文/思考整理为可落库内容，避免历史缺片。"""
    text, inline_think = _split_inline_think("".join(delta_parts))
    thinking = "\n".join(part for part in (inline_think, "".join(thinking_parts)) if part).strip()
    return (text.strip() or "（已停止生成）"), thinking
