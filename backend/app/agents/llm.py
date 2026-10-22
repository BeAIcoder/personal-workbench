"""LLM 模型工厂：按配置的协议构造 AgentScope 模型实例。

- openai 协议 → OpenAIChatModel（ModelScope / 硅基流动 / DeepSeek / 火山方舟 / DashScope 兼容 / Ollama）
- anthropic 协议 → AnthropicChatModel（ZCode 的 CodingPlan 网关等）

配置来源优先级：激活模型（model_providers + provider_models，界面可管理）> backend/.env 兜底。

两种协议共用 120 秒显式 SDK 超时（client_kwargs 透传）；base_url 统一经
config_store.validate_base_url 安全校验（拒绝内嵌凭据/非 http(s)/控制字符，
允许 Ollama 等私网 http），不合法时拒绝构造（返回 None）。
"""
import logging

from . import config_store

logger = logging.getLogger(__name__)

# 两种协议 SDK 客户端的统一显式超时（秒）：agentscope 的 client_kwargs 会透传给
# openai.AsyncClient / anthropic.AsyncAnthropic；两者默认超时均为 600 秒（10 分钟），
# 单用户工作台场景下显式收紧到 120 秒，避免一次挂起长时间占住请求。
LLM_TIMEOUT_SECONDS = 120.0


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


EFFORT_ANTHROPIC = ("low", "medium", "high", "xhigh", "max")
EFFORT_OPENAI = ("none", "minimal", "low", "medium", "high", "xhigh")


def _apply_reasoning(parameters_cls, cfg: dict, protocol: str, kwargs: dict) -> dict:
    """把「思考深度」设置映射到对应协议的 Parameters。

    cfg['reasoning_effort'] ∈ ''（跟随模型）/ off / low / medium / high / max。
    """
    effort = (cfg.get("reasoning_effort") or "").strip().lower()
    if not effort or effort == "default":
        return kwargs
    try:
        if protocol == "anthropic":
            if effort == "off":
                return {**kwargs, "thinking_enable": False, "thinking_mode": "disabled"}
            level = effort if effort in EFFORT_ANTHROPIC else "medium"
            return {**kwargs, "thinking_enable": True, "reasoning_effort": level}
        # openai
        if effort == "off":
            return {**kwargs, "thinking_enable": False, "reasoning_effort": "none"}
        if effort == "max":
            # openai 协议无 max 档：映射到 xhigh（对齐 anthropic 分支的 max 语义）
            level = "xhigh"
        else:
            level = effort if effort in EFFORT_OPENAI else "medium"
        return {**kwargs, "thinking_enable": effort != "off", "reasoning_effort": level}
    except Exception:
        logger.warning("思考深度参数映射失败，忽略该设置：effort=%s", effort, exc_info=True)
        return kwargs


def build_model(cfg: dict | None = None):
    """构造 AgentScope 模型实例；配置不完整或 base_url 未通过安全校验时返回 None。

    agentscope 惰性导入；默认带 120 秒显式 SDK 超时（client_kwargs 透传），
    cfg 传 client_timeout（秒，数字）可覆盖——连通性/多模态测试路径用 30s；
    cfg 传 client_max_retries 可覆盖重试次数——测试路径用 0 次（快速失败），
    聊天路径保留默认 2 次（网络抖动韧性）。
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
        param_kwargs = _apply_reasoning(AnthropicChatModel.Parameters, cfg, "anthropic", {"max_tokens": cfg.get("max_tokens") or None})
        if cfg.get("temperature") is not None:
            param_kwargs.setdefault("temperature", cfg.get("temperature"))
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
            client_kwargs={"timeout": timeout, "max_retries": max_retries},
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

    param_kwargs = _apply_reasoning(OpenAIChatModel.Parameters, cfg, "openai", {"max_tokens": cfg.get("max_tokens") or None})
    if cfg.get("temperature") is not None:
        param_kwargs.setdefault("temperature", cfg.get("temperature"))
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
        client_kwargs={"timeout": timeout, "max_retries": max_retries},
    )
