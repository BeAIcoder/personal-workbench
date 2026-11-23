"""编排器的路由判定辅助（自 orchestrator.py 机械拆出）。

职责边界：LLM 仲裁回复的解析、路由候选载荷、续聊判定。真正发起仲裁与
缓存/模型选择仍在 orchestrator.py（`_route_by_llm` / `_resolve_route`），
因为测试与配置变更需要按模块属性失效它们的缓存。
"""
import json
import re

from .team import AgentSpec, normalize_route_text


def parse_route_reply(text: str, team: dict[str, AgentSpec]) -> str | None:
    """从纯文本回复中解析名册内唯一专家；接受精确 name/label 与 JSON 代码块。"""
    normalized = normalize_route_text(text or "").strip()
    if not normalized:
        return None

    aliases: dict[str, set[str]] = {}
    for name, spec in team.items():
        for value in (name, spec.label):
            alias = normalize_route_text(value).strip()
            if alias:
                aliases.setdefault(alias, set()).add(name)

    def _exact_names(value: str) -> set[str]:
        return aliases.get(normalize_route_text(value).strip(), set())

    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", normalized, re.DOTALL)
    candidate_text = (fenced.group(1) if fenced else normalized).strip()
    exact = _exact_names(candidate_text)
    if len(exact) == 1:
        return next(iter(exact))

    try:
        payload = json.loads(candidate_text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        selected: set[str] = set()
        identity_values = [payload[key] for key in ("agent", "name", "label") if isinstance(payload.get(key), str)]
        if not identity_values:
            return None
        for value in identity_values:
            names = _exact_names(value)
            if len(names) != 1:
                return None
            selected.update(names)
        return next(iter(selected)) if len(selected) == 1 else None
    if isinstance(payload, str):
        names = _exact_names(payload)
        return next(iter(names)) if len(names) == 1 else None

    selected = set()
    for alias, names in aliases.items():
        pattern = re.escape(alias)
        if alias[0].isascii() and alias[0].isalnum():
            pattern = rf"(?<![a-z0-9_]){pattern}"
        if alias[-1].isascii() and alias[-1].isalnum():
            pattern = rf"{pattern}(?![a-z0-9_])"
        if re.search(pattern, candidate_text):
            selected.update(names)
    return next(iter(selected)) if len(selected) == 1 else None


def _route_candidate_payload(matches: list[dict]) -> list[dict]:
    return [
        {"name": match["name"], "score": match["score"], "confidence": match["confidence"]}
        for match in matches
    ]


# 续聊判定：这些表达强依赖上一轮上下文，不应作为新问题重新路由
_CONTINUATION_MARKERS = (
    "继续",
    "接着",
    "接着说",
    "按照上面",
    "如上",
    "同上",
    "刚才",
    "刚刚",
    "上面",
)


def _is_continuation(question: str) -> bool:
    """判断用户是否在要求延续当前会话上下文。"""
    normalized = normalize_route_text(question or "")
    return any(marker in normalized for marker in _CONTINUATION_MARKERS)
