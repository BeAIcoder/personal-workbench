"""LLM 模型工厂：按配置的协议构造 AgentScope 模型实例。

- openai 协议 → OpenAIChatModel（ModelScope / 硅基流动 / DeepSeek / 火山方舟 / DashScope 兼容 / Ollama）
- anthropic 协议 → AnthropicChatModel（ZCode 的 CodingPlan 网关等）

配置来源优先级：激活模型（model_providers + provider_models，界面可管理）> backend/.env 兜底。

思考参数（Task 5）不写死档位表：按模型的能力契约（capability_meta 的
reasoning_style / thinking_levels / default_thinking_level / thinking_can_disable）
生成对应协议的请求参数——AgentScope 参数字典不认识的档位（如 openai 侧的 max）
经 extra_body 原样发送，不擅改 medium/xhigh；强制思考模型不可被关成 off；
非法已存档档位回退「跟随模型」，不猜不编。

两种协议共用 120 秒显式 SDK 超时（client_kwargs 透传）；base_url 统一经
config_store.validate_base_url 安全校验（拒绝内嵌凭据/非 http(s)/控制字符，
允许 Ollama 等私网 http），不合法时拒绝构造（返回 None）。
官方要求会话头的供应商（OpenCode Go：User-Agent + x-opencode-session）经
provider_headers 生成默认请求头，密钥只走 SDK 鉴权、绝不进自定义头。
"""
import hashlib
import logging

from . import config_store

logger = logging.getLogger(__name__)

# 两种协议 SDK 客户端的统一显式超时（秒）：agentscope 的 client_kwargs 会透传给
# openai.AsyncClient / anthropic.AsyncAnthropic；两者默认超时均为 600 秒（10 分钟），
# 单用户工作台场景下显式收紧到 120 秒，避免一次挂起长时间占住请求。
LLM_TIMEOUT_SECONDS = 120.0

# 思考档位的全集（UI 可选 + 目录/同步可能声明的取值）。
# off/none = 显式关闭思考；minimal/low/medium/high/xhigh/max = 由低到高。
_THINKING_LEVELS = frozenset({"off", "none", "minimal", "low", "medium", "high", "xhigh", "max"})
# AgentScope OpenAIChatModel.Parameters.reasoning_effort 的 Literal 白名单（无 max）
_OPENAI_EFFORT_LEVELS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
# AgentScope AnthropicChatModel.Parameters.reasoning_effort 的 Literal 白名单（原生含 max）
_ANTHROPIC_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
# 思考参数风格（capability_meta.reasoning_style；缺省按通用 effort 形态）
_REASONING_STYLES = ("effort", "budget", "toggle")
# budget 风格（enable_thinking + thinking_budget）的档位→预算映射（tokens）
_THINKING_BUDGET_BY_LEVEL = {
    "minimal": 1024,
    "low": 4096,
    "medium": 16384,
    "high": 32768,
    "xhigh": 49152,
    "max": 65536,
}


def thinking_budget_for(level: str) -> int:
    """budget 风格的档位→思考预算（tokens）；未知档位按 medium 档（不猜更极端值）。"""
    return _THINKING_BUDGET_BY_LEVEL.get(str(level or "").strip().lower(), _THINKING_BUDGET_BY_LEVEL["medium"])


def _reasoning_style_of(cfg: dict) -> str:
    style = str(cfg.get("reasoning_style") or "").strip().lower()
    return style if style in _REASONING_STYLES else "effort"


def _resolve_thinking_level(cfg: dict, effort) -> str | None:
    """把用户选择解析为最终档位。

    返回 None = 跟随模型（不发送任何思考参数）；"off" = 显式关闭；其余 = 档位名。
    - `supports_thinking == 0`（明确不支持思考）→ 一律 None：任何档位都不发思考参数
      （目录/手动字段可能与实测结论不一致，此处兜底；未知 -1/缺省不受阻）；
    - 空/default → 用模型声明的默认档；无声明则真正跟随模型；
    - off/none → 强制思考模型（thinking_can_disable=False）不可关闭，回退跟随模型
     （声明默认档；无声明则不发参数）；off 只受 can_disable 约束，不受声明集门控；
    - 模型已声明档位（thinking_levels 非空）时，声明集之外的 canonical 档位视为不支持，
      回退声明默认档/跟随模型——不原样发给供应商；未声明档位时保持兼容（原样发送）；
    - 非法已存档档位 → 回退跟随模型，不擅改 medium/xhigh。
    """
    raw = str(effort if effort is not None else cfg.get("reasoning_effort") or "").strip().lower()
    # 明确不支持思考（supports_thinking == 0）：任何档位都不发思考参数。
    # 目录/手动字段可能与探测结论不一致（目录标了档位但实测不支持），此处兜底；
    # 未知（-1）与缺省不受阻，保持既有兼容行为
    st = cfg.get("supports_thinking")
    if st is not None:
        try:
            if int(st) == 0:
                return None
        except (TypeError, ValueError):
            pass
    can_disable = cfg.get("thinking_can_disable") is not False
    default = str(cfg.get("default_thinking_level") or "").strip().lower()
    if raw in ("", "default"):
        raw = default
    if raw in ("off", "none"):
        if can_disable:
            return "off"
        # 强制思考不可 off：回退跟随模型（声明默认档；无声明/默认也是关闭档则不发参数）
        raw = default
        if raw in ("off", "none") or not raw:
            return None
    elif raw not in _THINKING_LEVELS:
        # 非法已存档档位：回退跟随模型，不擅改 medium/xhigh
        raw = default
        if raw in ("off", "none"):
            return "off" if can_disable else None
        if not raw:
            return None
    elif not _level_declared(cfg, raw):
        # 已声明档位集之外的 canonical 档位：视为该模型不支持，回退声明默认/跟随
        raw = default
        if raw in ("off", "none"):
            return "off" if can_disable else None
        if not raw:
            return None
    return raw if raw in _THINKING_LEVELS else None


def _level_declared(cfg: dict, level: str) -> bool:
    """档位是否被模型声明支持；未声明档位集（空/缺失）时不做门控（兼容旧模型）。"""
    declared = cfg.get("thinking_levels")
    if not isinstance(declared, (list, tuple)) or not declared:
        return True
    return level in {str(x).strip().lower() for x in declared}


def reasoning_request(cfg: dict, effort) -> tuple[dict, dict]:
    """按模型能力契约生成思考参数：返回 (Parameters kwargs, OpenAI extra_body)。

    - effort 风格（openai）：档位进 Parameters.reasoning_effort；AgentScope 不认识的档位
     （如 max）只经 extra_body 原样发送，不与顶层字段重复；
    - toggle 风格：thinking{type} 开关（DeepSeek 形态）；
    - budget 风格：enable_thinking + thinking_budget 数值档（Qwen/SiliconFlow 形态），
     不伪装成 effort 字符串档；
    - anthropic 协议：原生 reasoning_effort（内部转 output_config.effort，含 max）；
     toggle 风格走 thinking_mode（enabled/disabled）；off 走 thinking 关闭；
     协议无法表达的档位回退跟随模型，不编不猜。
    """
    protocol = str(cfg.get("protocol") or "openai").strip().lower()
    level = _resolve_thinking_level(cfg, effort)
    if level is None:
        return {}, {}
    if protocol == "anthropic":
        if level == "off":
            return {"thinking_enable": False, "thinking_mode": "disabled"}, {}
        style = _reasoning_style_of(cfg)
        if style == "toggle":
            # Anthropic 侧开关形态：thinking_mode（优先于 thinking_enable）
            return {"thinking_enable": True, "thinking_mode": "enabled"}, {}
        if style == "budget":
            return {"thinking_enable": True, "thinking_budget": thinking_budget_for(level)}, {}
        if level in _ANTHROPIC_EFFORT_LEVELS:
            return {"thinking_enable": True, "reasoning_effort": level}, {}
        return {}, {}  # Anthropic 无法表达的档位：跟随模型
    style = _reasoning_style_of(cfg)
    if style == "toggle":
        return {}, {"thinking": {"type": "disabled" if level == "off" else "enabled"}}
    if style == "budget":
        if level == "off":
            return {}, {"enable_thinking": False}
        return {}, {"enable_thinking": True, "thinking_budget": thinking_budget_for(level)}
    if level == "off":
        return {"thinking_enable": False}, {}
    if level in _OPENAI_EFFORT_LEVELS:
        return {"thinking_enable": True, "reasoning_effort": level}, {}
    # AgentScope 不认识的档位（如 max）：经 extra_body 原样发送，不与顶层重复
    return {"thinking_enable": True}, {"reasoning_effort": level}


# 无会话上下文时的会话头取值：进程稳定（路由等内部调用同样稳定，可缓存）
_DEFAULT_SESSION_VALUE = "wb-" + hashlib.sha256(b"personal-workbench/default-session").hexdigest()[:24]


def session_header_value(session_id) -> str:
    """会话头取值：同一 session_id 恒定（供应商侧会话稳定、实例可缓存）。

    session_id 缺省时返回进程稳定默认值；但实际请求用的值取决于模型实例首次构造时
    传入的 session——无会话调用可能复用首个会话实例的头值（稳定，不保证是默认值）。
    """
    sid = str(session_id or "").strip()
    if not sid:
        return _DEFAULT_SESSION_VALUE
    return "wb-" + hashlib.sha256(sid.encode("utf-8")).hexdigest()[:24]


def provider_headers(cfg: dict, session_id=None) -> dict:
    """供应商自定义请求头（经 SDK client_kwargs.default_headers 透传）。

    仅官方明确要求会话头的套餐（OpenCode Go：User-Agent + x-opencode-session）会生成头；
    其余供应商返回空 dict。密钥只走 SDK 鉴权，绝不放进自定义头。
    """
    preset = config_store.preset_by_key(str(cfg.get("preset_key") or "").strip()) or {}
    headers: dict[str, str] = {}
    ua = str(preset.get("user_agent") or "").strip()
    if ua:
        headers["User-Agent"] = ua
    session_header = str(preset.get("session_header") or "").strip()
    if session_header:
        headers[session_header] = session_header_value(session_id)
    return headers


def needs_session_headers(cfg: dict) -> bool:
    """该供应商是否要求会话头（是 → 模型实例按 session 维度缓存，其余沿用共享缓存）。"""
    preset = config_store.preset_by_key(str(cfg.get("preset_key") or "").strip()) or {}
    return bool(str(preset.get("session_header") or "").strip())


def llm_status(cfg: dict | None = None) -> dict:
    """模型接入状态（不回传密钥内容）。"""
    cfg = cfg or config_store.load()
    configured = bool(cfg.get("api_key") and cfg.get("base_url") and cfg.get("model"))
    provider_name = cfg.get("provider_name") or ""
    return {
        "configured": configured,
        "protocol": cfg.get("protocol") or "openai",
        "provider": provider_name or cfg.get("provider") or "custom",
        # 来源标识：provider=界面配置的供应商激活模型；env=.env 兜底
        # （供应商全关/未配置时生效，模型不在快速切换下拉里，界面需明示）
        "source": "env" if provider_name == "env兜底" else "provider",
        "base_url": cfg.get("base_url") or "",
        "model": cfg.get("model") or "",
    }


def build_model(cfg: dict | None = None, session_id: str | None = None):
    """构造 AgentScope 模型实例；配置不完整或 base_url 未通过安全校验时返回 None。

    agentscope 惰性导入；默认带 120 秒显式 SDK 超时（client_kwargs 透传），
    cfg 传 client_timeout（秒，数字）可覆盖——连通性/多模态测试路径用 30s；
    cfg 传 client_max_retries 可覆盖重试次数——测试路径用 0 次（快速失败），
    聊天路径保留默认 2 次（网络抖动韧性）。

    session_id：需要会话头的供应商（OpenCode Go 等）据此生成稳定的会话请求头，
    模型实例由调用方按 session 维度缓存（见 orchestrator._get_spec_model）。
    """
    cfg = cfg or config_store.load()
    if not (cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")):
        return None

    try:
        timeout = float(cfg.get("client_timeout") or LLM_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = LLM_TIMEOUT_SECONDS
    try:
        max_retries = int(cfg.get("client_max_retries", 2))
    except (TypeError, ValueError):
        max_retries = 2

    # 官方要求会话头的套餐：UA + 会话头经 default_headers 透传（密钥只走 SDK 鉴权）
    headers = provider_headers(cfg, session_id)
    client_kwargs = {"timeout": timeout, "max_retries": max_retries}
    if headers:
        client_kwargs["default_headers"] = headers
    param_kwargs, extra_body = reasoning_request(cfg, cfg.get("reasoning_effort"))
    if cfg.get("temperature") is not None:
        param_kwargs.setdefault("temperature", cfg.get("temperature"))
    param_kwargs.setdefault("max_tokens", cfg.get("max_tokens") or None)

    if cfg.get("protocol") == "anthropic":
        from agentscope.credential import AnthropicCredential
        from agentscope.model import AnthropicChatModel

        try:
            # 与网关探测/多模态测试同一套 base_url 安全校验（拒绝内嵌凭据/非 http(s)/控制字符）
            base_url = config_store.validate_base_url(cfg["base_url"])
        except ValueError:
            logger.warning("Anthropic 协议 base_url 未通过 URL 安全校验，拒绝构造模型（不记录地址与密钥）")
            return None
        # anthropic SDK 会在 base_url 后追加 /v1/messages；去掉多余的 /v1 尾巴
        # （仅剥结尾 /v1，大小写不敏感；URL 其余部分保持原样，不做小写化；
        #  validate_base_url 已做去首尾空白与去尾斜杠归一化）
        if base_url.lower().endswith("/v1"):
            base_url = base_url[: -len("/v1")]

        try:
            credential = AnthropicCredential(api_key=cfg["api_key"], base_url=base_url)
        except Exception:
            logger.warning("AnthropicCredential 关键字构造失败，改用 data 构造", exc_info=True)
            credential = AnthropicCredential(data={"api_key": cfg["api_key"], "base_url": base_url})
        try:
            parameters = AnthropicChatModel.Parameters(**param_kwargs)
        except Exception:
            logger.warning("Anthropic Parameters 构造失败，回退仅 max_tokens：%s", param_kwargs, exc_info=True)
            parameters = AnthropicChatModel.Parameters(max_tokens=cfg.get("max_tokens") or None)
        return AnthropicChatModel(
            credential=credential,
            model=cfg["model"],
            stream=True,  # anthropic 网关要求流式；Agent 层自动聚合
            max_retries=max_retries,
            context_size=cfg.get("context_size") or 200000,
            parameters=parameters,
            client_kwargs=client_kwargs,
        )

    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel

    try:
        # 与 anthropic 分支/网关探测同一套 base_url 安全校验
        base_url = config_store.validate_base_url(cfg["base_url"])
    except ValueError:
        logger.warning("OpenAI 协议 base_url 未通过 URL 安全校验，拒绝构造模型（不记录地址与密钥）")
        return None

    try:
        credential = OpenAICredential(
            api_key=cfg["api_key"],
            base_url=base_url,
        )
    except Exception:
        logger.warning("OpenAICredential 关键字构造失败，改用 data 构造", exc_info=True)
        credential = OpenAICredential(
            data={"api_key": cfg["api_key"], "base_url": base_url}
        )

    try:
        parameters = OpenAIChatModel.Parameters(**param_kwargs)
    except Exception:
        logger.warning("OpenAI Parameters 构造失败，回退仅 max_tokens：%s", param_kwargs, exc_info=True)
        parameters = OpenAIChatModel.Parameters(max_tokens=cfg.get("max_tokens") or None)
    return OpenAIChatModel(
        credential=credential,
        model=cfg["model"],
        stream=False,
        max_retries=max_retries,
        context_size=cfg.get("context_size") or 128000,
        parameters=parameters,
        # AgentScope 不认识的档位（如 max）经 extra_body 原样发送，不与顶层重复
        extra_body=extra_body or None,
        client_kwargs=client_kwargs,
    )
