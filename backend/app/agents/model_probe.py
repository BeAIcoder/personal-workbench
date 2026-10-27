"""LLM 模型能力自动探测：直连厂商 API（httpx，不经 agentscope）。

四个维度：
- 思考能力（reasoning_effort 分级）：按模型已同步/手动的 thinking_levels 与
  capability_meta.reasoning_style 生成对应风格的请求参数（effort / thinking{type} /
  enable_thinking+thinking_budget），逐档发送，按「是否产出思考内容 / 是否接受该参数」
  分级判定；accepted（接受参数但无思考输出）不等于 supported；
- 有效上下文：二分查找最大可接受输入（重复中文语料填充，1 token≈1.5 汉字，
  每次请求 max_tokens=1）；
- 最大输出：阶梯 [1024, 4096, 16384, 32768, 65536, 131072] 逐档请求，
  取厂商接受的最高档。

约定：
- 探测失败（厂商不可达/参数被拒/超时/base_url 不合法）是正常结果，一律以 error 字段返回，
  不抛异常逃逸到 HTTP 层；网络与 HTTP 错误在 _chat_once/_messages_once 内就地转换；
  base_url 发出前经 config_store.validate_base_url 校验（与 SDK 构造/网关探测同口径，
  允许 Ollama 等私网 http），非法地址拒绝发起请求，已保存密钥不会发往别处；
- anthropic 协议（/v1/messages）按声明的档位与风格实测（Task 5 起不再一律跳过）：
  effort 风格走 output_config.effort，budget 风格走 thinking.budget_tokens；
- 每个探测函数写回模型行字段并 commit，分步详情并入 probe_result JSON，
  并刷新 probed_at。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import httpx

from ..models import ModelProvider
from . import llm, model_catalog
from .config_store import validate_base_url
from .secrets_box import unseal

logger = logging.getLogger(__name__)

# 思考能力探测：固定 prompt 与默认档位（模型未声明档位时的兜底：openai 四档常用值）
_REASONING_PROMPT = "9.11 和 9.9 哪个大？只输出数字。"
_REASONING_LEVELS = ["none", "low", "medium", "high"]
# 单次思考探测最多档位数（声明档位过多时截断，防止异常厂商耗光时间）
_REASONING_MAX_LEVELS = 6
# 可被探测的档位全集（含 off/none 关闭档与 max）
_KNOWN_LEVELS = frozenset({"off", "none", "minimal", "low", "medium", "high", "xhigh", "max"})
# Anthropic Messages 探测的最小 max_tokens：思考预算上限受 max_tokens 约束，
# 太小会直接触发 budget>max_tokens 报错，把探测条件问题误判成「不支持思考」
_ANTHROPIC_MIN_OUTPUT_TOKENS = 2048
# 最大输出探测：阶梯与 prompt
_OUTPUT_LADDER = [1024, 4096, 16384, 32768, 65536, 131072]
_OUTPUT_PROMPT = "请输出一篇尽可能长的中文散文"

# 上下文二分查找边界：最小 2000 tokens；上限取模型声明上下文与 100 万的较小值
_CONTEXT_LO = 2000
_CONTEXT_HI_CAP = 1000000

# 限流（429）退避重试：厂商 TPM/RPM 窗口通常以分钟计，命中 429 后等待再重试，
# 避免一次限流就把整个探测判成部分完成（外部验证实测：硅基 TPM 会截断二分与阶梯）。
_RATE_LIMIT_MAX_RETRIES = 2
_RATE_LIMIT_WAIT_SECONDS = 30.0
# 单维探测最多请求次数（防止异常厂商耗光时间）
_PROBE_MAX_REQUESTS = 6
# 请求间隔（秒）：避免对厂商造成压力
_PROBE_INTERVAL = 0.3

# 上下文超限错误特征（错误信息小写包含任一即判定为「输入太大」）
_CONTEXT_ERROR_MARKERS = (
    "context_length",
    "context length",
    "maximum context",
    "context window",
    "reduce the length",
    "too long",
    "too many tokens",
    "tokens exceed",
)
# max_tokens 超限错误特征
_MAX_TOKENS_ERROR_MARKERS = (
    "max_tokens",
    "max tokens",
    "max_completion_tokens",
    "max output",
    "output token",
    "generation length",
    "too large",
)
# reasoning_effort 参数被拒的错误特征（4xx 且含这些词 → 该档 unsupported；
# output_config 为 Anthropic 侧 effort 的载体）
_EFFORT_ERROR_MARKERS = (
    "reasoning_effort",
    "reasoning",
    "thinking",
    "effort",
    "think",
    "output_config",
    "enable_thinking",
    "budget",
)

# 上下文探测填充语料（约 500 字/段，按目标 token 数重复截取）
_CORPUS_SEGMENT = (
    "人工智能技术的发展正在深刻改变社会的运行方式。从工业生产到日常生活，智能系统正在承担越来越多的复杂任务。"
    "机器学习算法能够从海量数据中提取规律，帮助人们做出更加科学的决策。自然语言处理让计算机理解了人类的语言，"
    "人机交互变得更加自然流畅。计算机视觉技术赋予了机器观察世界的能力，自动驾驶和智能安防都离不开它的支持。"
    "在医疗领域，智能诊断系统可以辅助医生分析影像资料，提高诊断的准确性和效率。金融行业利用智能风控模型识别异常交易，"
    "保护用户的资金安全。教育行业通过个性化学习系统，为每个学生制定合适的学习计划。制造业引入智能机器人，"
    "实现了生产过程的自动化和智能化。农业生产也开始使用传感器和无人机，实时监测作物生长情况。"
    "与此同时，数据隐私和算法安全也成为社会关注的焦点。如何在促进技术创新和保护个人权益之间取得平衡，"
    "是摆在所有人面前的课题。据统计，全球算力规模近年来保持高速增长，数据中心成为新的基础设施。"
    "各国纷纷将人工智能纳入国家战略，加大基础研究投入，培养跨学科人才，力求在下一轮科技竞争中占据有利位置。"
    "未来，人工智能将继续向通用智能方向演进，与人类形成更加紧密的协作关系。"
    "只有坚持科技向善的原则，才能让这项技术真正造福全社会，推动文明不断向前发展。"
)


def _chat_once(
    base_url: str,
    api_key: str,
    model: str,
    messages: list,
    max_tokens: int,
    reasoning_effort: str | None = None,
    timeout: int = 30,
    extra: dict | None = None,
) -> dict:
    """向 OpenAI 兼容 /chat/completions 发一条请求；网络/HTTP/URL 校验错误绝不抛异常。

    返回 {ok, status_code, error, content, reasoning_content, finish_reason, usage}：
    ok=True 仅表示 HTTP 2xx 且解析出响应体；错误一律进 error 字段。
    extra：按模型思考风格附加的请求体字段（thinking{type} / enable_thinking+thinking_budget）。
    base_url 先经 config_store.validate_base_url 校验（与 SDK 构造/网关探测同口径：
    仅 http(s)、需主机名、拒绝内嵌凭据与控制字符，允许 Ollama 等私网 http）；
    不合法则就地转为 error 字段，绝不带密钥向该地址发起请求。
    """
    out = {
        "ok": False,
        "status_code": None,
        "error": None,
        "content": "",
        "reasoning_content": "",
        "finish_reason": None,
        "usage": None,
    }
    try:
        base = validate_base_url(base_url)
    except ValueError as exc:
        logger.warning("模型探测 base_url 未通过 URL 安全校验，已拒绝发起请求（不记录地址与密钥）")
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if extra:
        payload.update(extra)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp, error = _post_with_retry(url, payload, headers, timeout, out)
    if resp is None:
        return out

    out["status_code"] = resp.status_code
    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code >= 400:
        out["error"] = (_extract_error(data) or resp.text or f"HTTP {resp.status_code}")[:300]
        return out

    choices = data.get("choices") if isinstance(data, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else {}
    msg = choice.get("message") if isinstance(choice, dict) else None
    msg = msg if isinstance(msg, dict) else {}
    usage = data.get("usage") if isinstance(data, dict) else None
    out["ok"] = True
    out["content"] = str(msg.get("content") or "")
    out["reasoning_content"] = str(msg.get("reasoning_content") or msg.get("reasoning") or "")
    out["finish_reason"] = choice.get("finish_reason") if isinstance(choice, dict) else None
    out["usage"] = usage if isinstance(usage, dict) else None
    return out


def _post_with_retry(url: str, payload: dict, headers: dict, timeout: int, out: dict):
    """POST JSON（429 有界退避重试）；返回 (response, None) 或 (None, error 已写入 out)。

    网络错误与退避 sleep 被打断等异常就地转换为 out['error']，绝不逃逸。
    """
    try:
        retries = 0
        while True:
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
                return None, out["error"]
            if resp.status_code == 429 and retries < _RATE_LIMIT_MAX_RETRIES:
                retries += 1
                logger.info("探测命中限流（429），第 %d/%d 次退避 %.0fs 后重试", retries, _RATE_LIMIT_MAX_RETRIES, _RATE_LIMIT_WAIT_SECONDS)
                time.sleep(_RATE_LIMIT_WAIT_SECONDS)
                continue
            break
    except Exception as exc:  # 退避 sleep 被打断等异常：就地转 error，不逃逸
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return None, out["error"]
    return resp, None


def _messages_once(
    base_url: str,
    api_key: str,
    model: str,
    messages: list,
    max_tokens: int,
    extra: dict | None = None,
    timeout: int = 30,
) -> dict:
    """向 Anthropic /v1/messages 发一条请求（Task 5：anthropic 协议按 Messages 实测）。

    返回与 _chat_once 同构的结果字典：思考痕迹取 content 里 type=thinking 的块，
    finish_reason 取 stop_reason；错误同样就地转换、绝不抛异常。
    extra：output_config.effort（effort 风格）或 thinking（budget/toggle 风格）。
    """
    out = {
        "ok": False,
        "status_code": None,
        "error": None,
        "content": "",
        "reasoning_content": "",
        "finish_reason": None,
        "usage": None,
    }
    try:
        base = validate_base_url(base_url)
    except ValueError as exc:
        logger.warning("模型探测 base_url 未通过 URL 安全校验，已拒绝发起请求（不记录地址与密钥）")
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    # anthropic SDK 同款处理：base_url 末尾多余的 /v1 会拼成 /v1/v1/messages
    if base.lower().endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/v1/messages"
    payload = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if extra:
        payload.update(extra)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    headers["anthropic-version"] = "2023-06-01"
    resp, error = _post_with_retry(url, payload, headers, timeout, out)
    if resp is None:
        return out

    out["status_code"] = resp.status_code
    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code >= 400:
        out["error"] = (_extract_error(data) or resp.text or f"HTTP {resp.status_code}")[:300]
        return out

    blocks = data.get("content") if isinstance(data, dict) else None
    blocks = blocks if isinstance(blocks, list) else []
    usage = data.get("usage") if isinstance(data, dict) else None
    out["ok"] = True
    out["content"] = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    out["reasoning_content"] = "".join(
        b.get("thinking", "") for b in blocks if isinstance(b, dict) and b.get("type") == "thinking"
    )
    out["finish_reason"] = data.get("stop_reason") if isinstance(data, dict) else None
    out["usage"] = usage if isinstance(usage, dict) else None
    return out


def _extract_error(data) -> str:
    """从 OpenAI 兼容错误响应体提取可读错误信息（error 可能是对象或字符串）。"""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "")
    if isinstance(err, str):
        return err
    return str(data.get("message") or "")


def _is_4xx(result: dict) -> bool:
    """是否为 4xx（参数类拒绝）。"""
    code = result.get("status_code")
    return isinstance(code, int) and 400 <= code < 500


def _matches(markers: tuple, text) -> bool:
    """错误信息（小写）是否包含任一特征词。"""
    t = str(text or "").lower()
    return any(m in t for m in markers)


def _has_reasoning_trace(result: dict) -> bool:
    """响应是否带思考痕迹：reasoning_content 非空，或 usage/finish_reason 含 reasoning 信息。"""
    if (result.get("reasoning_content") or "").strip():
        return True
    usage = result.get("usage")
    if isinstance(usage, dict):
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict) and (details.get("reasoning_tokens") or 0) > 0:
            return True
        if (usage.get("reasoning_tokens") or 0) > 0:
            return True
    finish = str(result.get("finish_reason") or "").lower()
    return ("reason" in finish) or ("think" in finish)


def _filler_messages(tokens: int) -> list[dict]:
    """按目标 token 数生成填充 messages（1 token≈1.5 汉字，重复中文语料）。"""
    chars = max(1, int(tokens * 1.5))
    filler = (_CORPUS_SEGMENT * (chars // len(_CORPUS_SEGMENT) + 1))[:chars]
    return [{"role": "user", "content": filler}]


def _provider_of(db, model_row) -> ModelProvider | None:
    """模型行所属供应商（同会话内查询）。"""
    return db.get(ModelProvider, model_row.provider_id)


def _reasoning_style_of(model_row) -> str:
    """模型的思考参数风格（capability_meta.reasoning_style；缺省按通用 effort 形态）。"""
    meta = model_catalog.capability_meta_of(model_row)
    style = str(meta.get("reasoning_style") or "").strip().lower()
    return style if style in ("effort", "budget", "toggle") else "effort"


def _reasoning_levels_of(model_row) -> list[str]:
    """探测档位：已同步/手动的 thinking_levels 优先（Task 3/4 成果），否则默认四档。"""
    try:
        declared = json.loads(model_row.thinking_levels or "[]")
    except Exception:
        declared = []
    raw = [str(x).strip().lower() for x in declared if str(x).strip()] if isinstance(declared, list) else []
    ordered: list[str] = []
    for lv in raw:
        if lv in _KNOWN_LEVELS and lv not in ordered:
            ordered.append(lv)
    return (ordered or list(_REASONING_LEVELS))[:_REASONING_MAX_LEVELS]


def _probe_request(protocol: str, style: str, level: str) -> tuple[str | None, dict | None, int]:
    """按协议与思考风格生成单档探测请求：(reasoning_effort, extra, max_tokens)。

    openai 协议：effort 风格档位原样进 reasoning_effort（含 AgentScope 不认识的 max）；
    toggle 风格带 thinking{type}；budget 风格带 enable_thinking+thinking_budget。
    anthropic 协议（Messages）：effort 风格进 output_config.effort；budget 风格进
    thinking.budget_tokens（max_tokens 需大于预算）；off/none 显式关闭思考。
    """
    lv = str(level or "").strip().lower()
    if protocol == "anthropic":
        if lv in ("off", "none"):
            return None, {"thinking": {"type": "disabled"}}, _ANTHROPIC_MIN_OUTPUT_TOKENS
        if style == "budget":
            budget = llm.thinking_budget_for(lv)
            return None, {"thinking": {"type": "enabled", "budget_tokens": budget}}, budget + 1024
        return None, {"output_config": {"effort": lv}}, _ANTHROPIC_MIN_OUTPUT_TOKENS
    if style == "toggle":
        return None, {"thinking": {"type": "disabled" if lv in ("off", "none") else "enabled"}}, 64
    if style == "budget":
        if lv in ("off", "none"):
            return None, {"enable_thinking": False}, 64
        return None, {"enable_thinking": True, "thinking_budget": llm.thinking_budget_for(lv)}, 64
    if lv in ("off", "none"):
        return "none", None, 64
    return lv, None, 64


def _send(prov: ModelProvider, api_key: str, model: str, messages: list, max_tokens: int, reasoning_effort=None, extra=None) -> dict:
    """按协议分发探测请求：anthropic 走 /v1/messages，其余走 /chat/completions。

    只传有值的参数（effort/extra 为空时不传），保持与既有探测桩的签名兼容。
    """
    if prov.protocol == "anthropic":
        kwargs: dict = {"max_tokens": max_tokens}
        if extra:
            kwargs["extra"] = extra
        return _messages_once(prov.base_url, api_key, model, messages, **kwargs)
    kwargs = {"max_tokens": max_tokens}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if extra:
        kwargs["extra"] = extra
    return _chat_once(prov.base_url, api_key, model, messages, **kwargs)


def _api_key_of(prov: ModelProvider) -> str:
    """解密供应商密钥；失败返回空串（探测会以鉴权错误正常收尾）。"""
    try:
        return unseal(prov.api_key or "")
    except Exception:
        logger.warning("模型探测读取密钥失败（provider_id=%s）", prov.id, exc_info=True)
        return ""


def _merge_step(db, model_row, key: str, detail: dict) -> None:
    """把单步探测详情并入 probe_result JSON、刷新探测时间并 commit。"""
    try:
        result = json.loads(model_row.probe_result or "{}")
    except Exception:
        result = {}
    if not isinstance(result, dict):
        result = {}
    result[key] = detail
    model_row.probe_result = json.dumps(result, ensure_ascii=False)
    model_row.probed_at = datetime.now().isoformat(timespec="seconds")
    db.commit()


def _mark_probe_override(model_row, fields: tuple[str, ...]) -> None:
    """把明确的探测结论记为 probe 本地覆盖（与 config_store.record_probe_verdict 同口径）。

    sources[field]='probe' 且字段进入 capability_meta.manual_fields：默认目录/在线
    同步不覆盖它们（实测优先于目录声明），override_manual=True 的同步或 reset-overrides
    可清除覆盖并按同步值恢复。调用方负责随后 commit（探测各步统一走 _merge_step）。
    """
    meta = model_catalog.capability_meta_of(model_row)
    sources = dict(meta["sources"])
    manual = list(meta["manual_fields"])
    for field in fields:
        sources[field] = "probe"
        if field not in manual:
            manual.append(field)
    meta["sources"] = sources
    meta["manual_fields"] = sorted(manual)
    model_row.capability_meta = json.dumps(meta, ensure_ascii=False)


def probe_reasoning(db, model_row) -> dict:
    """思考能力探测：按模型已同步的档位与思考参数风格逐档实测。

    分级判定：
    - supported：响应 ok 且带思考痕迹（reasoning_content 非空或 usage/finish_reason 有 reasoning 信息）；
    - accepted：响应 ok 但不产生思考内容（接受参数、无思考输出）——不等于 supported；
    - unsupported：4xx 且错误信息指向思考参数不被支持；
    - error：网络错误或其他 HTTP 错误（不影响其他档位判定）。

    汇总写 supports_thinking（任一 supported → 1，否则 0；全部请求均失败时保持 -1 未探测）
    与 thinking_levels（supported + accepted 的档位）。明确的结论同时记为 probe 本地覆盖
    （sources=probe + manual_fields）：默认目录/在线同步不覆盖实测值，override_manual=True
    或 reset-overrides 可清除恢复。anthropic 协议按 Messages 实测，不再一律跳过。
    """
    prov = _provider_of(db, model_row)
    if prov is None:
        return {"error": "供应商不存在，无法探测"}

    api_key = _api_key_of(prov)
    style = _reasoning_style_of(model_row)
    levels = _reasoning_levels_of(model_row)
    messages = [{"role": "user", "content": _REASONING_PROMPT}]

    verdicts: dict[str, str] = {}
    steps: list[dict] = []
    for level in levels:
        effort, extra, max_tokens = _probe_request(prov.protocol, style, level)
        r = _send(prov, api_key, model_row.model, messages, max_tokens, reasoning_effort=effort, extra=extra)
        if r["ok"]:
            verdict = "supported" if _has_reasoning_trace(r) else "accepted"
        elif _is_4xx(r) and _matches(_MAX_TOKENS_ERROR_MARKERS, r.get("error")):
            # 输出/思考预算超限：探测条件问题（如 budget>max_tokens），不是「不支持思考」
            verdict = "error"
        elif _is_4xx(r) and _matches(_EFFORT_ERROR_MARKERS, r.get("error")):
            verdict = "unsupported"
        else:
            verdict = "error"
        verdicts[level] = verdict
        steps.append(
            {
                "level": level,
                "verdict": verdict,
                "status_code": r["status_code"],
                "reasoning_len": len(r.get("reasoning_content") or ""),
                "error": r.get("error"),
            }
        )

    supported = [lv for lv, v in verdicts.items() if v == "supported"]
    accepted = [lv for lv, v in verdicts.items() if v == "accepted"]
    definitive = [lv for lv, v in verdicts.items() if v in ("supported", "accepted", "unsupported")]
    # supported + accepted 的档位，按本次探测顺序输出（供界面下拉）
    chosen = set(supported) | set(accepted)
    ordered_levels = [lv for lv in levels if lv in chosen]
    detail = {
        "protocol": prov.protocol,
        "style": style,
        "levels": verdicts,
        "steps": steps,
        "supported": supported,
        "accepted": accepted,
        "thinking_levels": ordered_levels,
        "requests": len(steps),
    }
    if definitive:
        model_row.supports_thinking = 1 if supported else 0
        model_row.thinking_levels = json.dumps(ordered_levels, ensure_ascii=False)
        # 实测结论是本地覆盖：默认同步不得把实测「不支持」翻回目录「支持」
        _mark_probe_override(model_row, ("supports_thinking", "thinking_levels"))
    else:
        detail["error"] = "厂商不可达或全部请求失败，未更新探测结论"
    _merge_step(db, model_row, "reasoning", detail)
    return detail


def probe_context(db, model_row) -> dict:
    """有效上下文探测：二分查找最大可接受输入 tokens。

    填充语料为重复中文文本（1 token≈1.5 汉字），每次请求 max_tokens=1：
    - 报上下文超限类错误（或 413）→ 缩小上界；
    - 成功 → 记录该值并放大下界；
    - 其他错误（网络/5xx/未知 4xx）→ 停止并记录 error，保留已获得的最佳值。
    lo=2000、hi=min(model.context_size, 1000000)，最多 6 次请求，请求间隔 0.3s；
    结果写 probed_context（0 = 未探测到可用值）。anthropic 协议走 /v1/messages 实测。
    """
    prov = _provider_of(db, model_row)
    if prov is None:
        return {"error": "供应商不存在，无法探测"}

    api_key = _api_key_of(prov)
    cap = min(int(model_row.context_size or 128000), _CONTEXT_HI_CAP)
    lo, hi = _CONTEXT_LO, cap
    best = 0
    steps: list[dict] = []
    error = None
    requests = 0
    while lo <= hi and requests < _PROBE_MAX_REQUESTS:
        mid = (lo + hi) // 2
        requests += 1
        r = _send(prov, api_key, model_row.model, _filler_messages(mid), max_tokens=1)
        if r["ok"]:
            best = mid
            lo = mid + 1
            verdict = "ok"
        elif r["status_code"] == 413 or (_is_4xx(r) and _matches(_CONTEXT_ERROR_MARKERS, r.get("error"))):
            hi = mid - 1
            verdict = "too_long"
        else:
            verdict = "error"
            error = r.get("error") or f"HTTP {r['status_code']}"
        steps.append(
            {"tokens": mid, "verdict": verdict, "status_code": r["status_code"], "error": r.get("error")}
        )
        if verdict == "error":
            break
        if lo <= hi and requests < _PROBE_MAX_REQUESTS:
            time.sleep(_PROBE_INTERVAL)

    model_row.probed_context = best
    detail = {
        "effective_context": best,
        "requests": requests,
        "lo": _CONTEXT_LO,
        "hi_cap": cap,
        "steps": steps,
    }
    if error:
        detail["error"] = error
    _merge_step(db, model_row, "context", detail)
    return detail


def probe_output(db, model_row) -> dict:
    """最大输出探测：按阶梯 [1024..131072] 逐档请求中文散文。

    - ok 且 finish_reason 为 length/stop/max_tokens/end_turn（或缺失）→ 该档可用，继续上一档；
    - 4xx 且错误指向 max_tokens → 停止，取前一档为 effective；
    - 其他错误（网络/5xx/未知 4xx）→ 停止并记录 error；
    全部档位可用 → 取最高档。最多 6 次请求；结果写 probed_max_output（0 = 未探测到可用值）。
    anthropic 协议走 /v1/messages 实测（stop_reason=max_tokens/end_turn）。
    """
    prov = _provider_of(db, model_row)
    if prov is None:
        return {"error": "供应商不存在，无法探测"}

    api_key = _api_key_of(prov)
    messages = [{"role": "user", "content": _OUTPUT_PROMPT}]

    effective = 0
    steps: list[dict] = []
    error = None
    requests = 0
    for rung in _OUTPUT_LADDER:
        if requests >= _PROBE_MAX_REQUESTS:
            break
        requests += 1
        r = _send(prov, api_key, model_row.model, messages, max_tokens=rung)
        finish = r.get("finish_reason")
        if r["ok"] and (finish in ("length", "stop", "max_tokens", "end_turn") or finish is None):
            effective = rung
            steps.append({"max_tokens": rung, "verdict": "ok", "finish_reason": finish})
            continue
        if not r["ok"] and _is_4xx(r) and _matches(_MAX_TOKENS_ERROR_MARKERS, r.get("error")):
            steps.append({"max_tokens": rung, "verdict": "rejected", "error": r.get("error")})
            break
        error = r.get("error") or f"HTTP {r['status_code']}"
        steps.append({"max_tokens": rung, "verdict": "error", "error": error})
        break

    model_row.probed_max_output = effective
    detail = {"effective_max_output": effective, "requests": requests, "steps": steps}
    if error:
        detail["error"] = error
    _merge_step(db, model_row, "output", detail)
    return detail


def probe_all(db, model_row) -> dict:
    """全量探测：按 reasoning → context → output 顺序执行。

    任何单步异常只记录不中断其余步骤；分步详情已在各步内并入 probe_result，
    此处汇总刷新 probed_at。返回 {"reasoning": ..., "context": ..., "output": ..., "probed_at": ...}。
    """
    out: dict = {}
    for key, fn in (
        ("reasoning", probe_reasoning),
        ("context", probe_context),
        ("output", probe_output),
    ):
        try:
            out[key] = fn(db, model_row)
        except Exception as exc:
            logger.warning("探测步骤异常（不影响其余步骤）：%s", key, exc_info=True)
            entry = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            out[key] = entry
            try:
                _merge_step(db, model_row, key, entry)  # 失败详情也落库，便于回溯
            except Exception:
                logger.warning("探测步骤失败详情落库失败：%s", key, exc_info=True)
    model_row.probed_at = datetime.now().isoformat(timespec="seconds")
    db.commit()
    out["probed_at"] = model_row.probed_at
    return out


def probe_state(model_row) -> dict:
    """模型行当前探测状态（GET /models/{id}/probe 用，不触发探测）。"""
    try:
        result = json.loads(model_row.probe_result or "{}")
    except Exception:
        result = {}
    if not isinstance(result, dict):
        result = {}
    try:
        levels = json.loads(model_row.thinking_levels or "[]")
    except Exception:
        levels = []
    return {
        "model": model_row.model,
        "provider_id": model_row.provider_id,
        "context_size": model_row.context_size,
        "max_tokens": model_row.max_tokens,
        "temperature": model_row.temperature,
        "note": model_row.note,
        "supports_thinking": model_row.supports_thinking,
        "thinking_levels": levels if isinstance(levels, list) else [],
        "probed_context": model_row.probed_context,
        "probed_max_output": model_row.probed_max_output,
        "probe_result": result,
        "probed_at": model_row.probed_at,
    }
