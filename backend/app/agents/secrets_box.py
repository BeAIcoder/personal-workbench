"""API Key 静态加密（DPAPI，绑定本机+当前用户）。

存储格式：
- ``dpapi:<base64>`` —— DPAPI 加密值（新写入一律走此格式）
- 无前缀明文 —— 历史遗留值，读取时原样返回（向后兼容），
  可由 migrate_seal_keys() 一次性迁移为 dpapi 格式。

安全约束（N-1）：pywin32/DPAPI 不可用时**绝不静默降级明文**——
seal/unseal 一律 fail-fast 抛错；应用启动时强制校验，缺 pywin32 时
拒绝启动并提示 pip install pywin32。

SQLite 不强制 VARCHAR 长度，密封后超出 String(300) 声明无影响。
换机/还原备份后 DPAPI 值无法解密（返回 ""），需重新录入 Key——这是预期行为。
"""
from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)

_PREFIX = "dpapi:"

try:
    import win32crypt  # type: ignore

    _DPAPI_OK = True
except ImportError:  # 非 Windows 或未装 pywin32：仅探测可用性，绝不据此改变行为
    _DPAPI_OK = False


class DpapiUnavailableError(RuntimeError):
    """DPAPI 不可用（未安装 pywin32 或非 Windows 平台）——fail-fast 专用异常。"""


def ensure_dpapi_available() -> None:
    """强制校验 DPAPI 可用性；不可用时抛出明确异常，调用方必须失败而非降级。

    供应用启动与每次 seal/unseal 前调用（N-1：杜绝"缺 pywin32 密钥静默变明文"）。
    """
    if not _DPAPI_OK:
        raise DpapiUnavailableError(
            "DPAPI 不可用：未检测到 pywin32（或当前不是 Windows 平台）。"
            "API Key 静态加密以 pywin32 为硬依赖，请先执行 "
            "pip install pywin32 再启动；本应用拒绝在缺少 DPAPI 时以降级明文运行。"
        )


def seal(plain: str) -> str:
    """加密明文 Key 为可落盘格式；DPAPI 不可用时 fail-fast（抛错，绝不降级明文）。"""
    if not plain:
        return ""
    if plain.startswith(_PREFIX):  # 幂等：已密封不重复加密
        return plain
    ensure_dpapi_available()
    blob = win32crypt.CryptProtectData(plain.encode("utf-8"), "workbench-llm-key")
    return _PREFIX + base64.b64encode(blob).decode("ascii")


def unseal(stored: str) -> str:
    """还原 Key；明文历史值原样返回；DPAPI 缺失时 fail-fast；解密失败（如换机）返回空串。"""
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        return stored  # 历史明文，向后兼容
    ensure_dpapi_available()
    try:
        blob = base64.b64decode(stored[len(_PREFIX):])
        _desc, data = win32crypt.CryptUnprotectData(blob)
        return data.decode("utf-8")
    except Exception:
        logger.warning("API Key 解密失败（密钥绑定原机器+账户，换机需重新录入）", exc_info=True)
        return ""


def migrate_seal_keys() -> int:
    """把数据库中存量明文 api_key 一次性迁移为 DPAPI 格式，返回迁移条数。

    DPAPI 不可用时由 seal() fail-fast 抛出，不会写出半迁移状态（整事务回滚）。
    """
    # 延迟导入避免循环依赖
    from ..database import SessionLocal
    from ..models import ModelProvider

    count = 0
    with SessionLocal() as db:
        for prov in db.query(ModelProvider).all():
            if prov.api_key and not prov.api_key.startswith(_PREFIX):
                prov.api_key = seal(prov.api_key)
                count += 1
        if count:
            db.commit()
    return count
