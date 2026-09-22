"""LLM 模型工厂：按配置的协议构造 AgentScope 模型实例。

- openai 协议 → OpenAIChatModel（ModelScope / 硅基流动 / DeepSeek / 火山方舟 / DashScope 兼容 / Ollama）
- anthropic 协议 → AnthropicChatModel（ZCode 的 CodingPlan 网关等）

配置来源优先级：激活模型（model_providers + provider_models，界面可管理）> backend/.env 兜底。
"""
import logging

from . import config_store

logger = logging.getLogger(__name__)


def llm_status(cfg: dict | None = None) -> dict:
    """模型接入状态（不回传密钥内容）。"""
    cfg = cfg or config_store.load()
    configured = bool(cfg.get("api_key") and cfg.get("base_url") and cfg.get("model"))
    return {
        "configured": configured,
        "protocol": cfg.get("protocol") or "openai",
        "provider": cfg.get("provider_name") or cfg.get("provider") or "custom",
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
        level = effort if effort in EFFORT_OPENAI else "medium"
        if level == "max":
            level = "xhigh"
        return {**kwargs, "thinking_enable": effort != "off", "reasoning_effort": level}
    except Exception:
        logger.warning("思考深度参数映射失败，忽略该设置：effort=%s", effort, exc_info=True)
        return kwargs


def build_model(cfg: dict | None = None):
    """构造 AgentScope 模型实例；配置不完整返回 None。agentscope 惰性导入。"""
    cfg = cfg or config_store.load()
    if not (cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")):
        return None

    if cfg.get("protocol") == "anthropic":
        from agentscope.credential import AnthropicCredential
        from agentscope.model import AnthropicChatModel

        base_url = cfg["base_url"].strip()
        # anthropic SDK 会在 base_url 后追加 /v1/messages；去掉多余的 /v1 尾巴
        if base_url.rstrip("/").lower().endswith("/v1"):
            base_url = base_url.rstrip("/").lower()[: -len("/v1")]

        try:
            credential = AnthropicCredential(api_key=cfg["api_key"], base_url=base_url)
        except Exception:
            logger.warning("AnthropicCredential 关键字构造失败，改用 data 构造", exc_info=True)
            credential = AnthropicCredential(data={"api_key": cfg["api_key"], "base_url": base_url})
        param_kwargs = _apply_reasoning(AnthropicChatModel.Parameters, cfg, "anthropic", {"max_tokens": cfg.get("max_tokens") or None})
        try:
            parameters = AnthropicChatModel.Parameters(**param_kwargs)
        except Exception:
            logger.warning("Anthropic Parameters 构造失败，回退仅 max_tokens：%s", param_kwargs, exc_info=True)
            parameters = AnthropicChatModel.Parameters(max_tokens=cfg.get("max_tokens") or None)
        return AnthropicChatModel(
            credential=credential,
            model=cfg["model"],
            stream=True,  # anthropic 网关要求流式；Agent 层自动聚合
            max_retries=2,
            context_size=cfg.get("context_size") or 200000,
            parameters=parameters,
        )

    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel

    try:
        credential = OpenAICredential(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
        )
    except Exception:
        logger.warning("OpenAICredential 关键字构造失败，改用 data 构造", exc_info=True)
        credential = OpenAICredential(
            data={"api_key": cfg["api_key"], "base_url": cfg["base_url"]}
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
        max_retries=2,
        context_size=cfg.get("context_size") or 128000,
        parameters=parameters,
    )
