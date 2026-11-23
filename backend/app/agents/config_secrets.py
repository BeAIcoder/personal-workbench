"""Agent 配置的密钥、掩码与地址安全边界（自 config_store.py 机械拆出）。

职责边界：API Key 的掩码哨兵与回显形态、回传前脱敏、网关/base_url 安全校验、
密封管控凭证的读取。表结构与生效配置组装分别在 config_providers.py /
config_settings.py；预设与连通性测试仍在 config_store.py。
"""
import json
import logging
import urllib.parse

# seal/unseal 在 DPAPI 不可用时 fail-fast 抛 RuntimeError（配合启动校验，N-1），绝不降级明文
from .secrets_box import unseal

logger = logging.getLogger(__name__)

# 前端回显密钥时使用的掩码哨兵：界面"未改动"传回该值 → 保留现值
MASK = "********"
# GET 回显的掩码形态：MASK_PREFIX + 原密钥后四位（见 masked()）；
# 保存侧对该形态同样视为「未修改保留原值」，防止回显掩码被当成新密钥覆盖真实密钥
MASK_PREFIX = "••••••"

def _check_http_url(url: str) -> str:
    """http(s) 地址安全校验（SDK 构造 / 网关探测 / httpx 测试共用口径）。

    本应用为本机单用户场景，允许 127.0.0.1/内网地址（如 Ollama 本地网关），
    因此只做协议白名单与字符合法性校验，不封禁私网段。
    错误信息不回显 URL 主体：地址可能内嵌 userinfo 等敏感片段，不进日志/响应。
    """
    if any(ord(ch) < 0x21 or ch == "\x7f" for ch in url):
        raise ValueError("网关地址含非法字符")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("网关地址仅允许 http(s)")
    if parts.username or parts.password:
        raise ValueError("网关地址不允许内嵌用户名/密码")
    return url


def _safe_gateway_url(base_url: str, path: str) -> str:
    """连通性/多模态测试的网关地址校验：仅 http(s)、拒绝内嵌凭据与控制字符。

    口径与 SDK 构造侧（validate_base_url）同源：本机单用户场景允许
    127.0.0.1/内网地址（如 Ollama 本地网关），不封禁私网段。
    """
    return _check_http_url(f"{str(base_url).rstrip('/')}{path}")


def validate_base_url(base_url) -> str:
    """供应商/自定义模型 base_url 安全校验（SDK 构造与探测共用入口）。

    与 _safe_gateway_url 同一套口径：仅 http(s)、需有主机名、拒绝内嵌凭据与
    控制字符；允许 Ollama 等本机/内网 http 地址。返回去首尾空白与尾部斜杠后
    的归一化地址，供 OpenAICredential/AnthropicCredential 与 httpx 测试使用。
    """
    return _check_http_url(str(base_url or "").strip().rstrip("/"))

def _read_sync_credentials(stored: str) -> dict:
    """解析密封的管控凭证 JSON；空/损坏返回空 dict。"""
    if not stored:
        return {}
    try:
        data = json.loads(unseal(stored) or "{}")
    except Exception:
        logger.warning("管控凭证解析失败，按未配置处理", exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def masked(cfg: dict) -> dict:
    """回传给前端的配置：api_key 脱敏。"""
    out = {**cfg}
    key = (cfg.get("api_key") or "").strip()
    if not key:
        out["api_key"] = ""
    elif len(key) <= 4:
        out["api_key"] = MASK_PREFIX  # 短密钥整体掩码：后缀回显会把全文明文泄露给前端
    else:
        out["api_key"] = MASK_PREFIX + key[-4:]
    out["api_key_set"] = bool(key)
    return out
