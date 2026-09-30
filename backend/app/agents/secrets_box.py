"""API Key 静态加密（DPAPI，绑定本机+当前用户）。

存储格式：
- ``dpapi:<base64>`` —— DPAPI 加密值（新写入一律走此格式）
- 无前缀明文 —— 历史遗留值，读取时原样返回（向后兼容），
  可由 migrate_seal_keys() 一次性迁移为 dpapi 格式。

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
except ImportError:  # 非 Windows 或未装 pywin32
    _DPAPI_OK = False


def seal(plain: str) -> str:
    """加密明文 Key 为可落盘格式；DPAPI 不可用时降级明文并告警。"""
    if not plain:
        return ""
    if plain.startswith(_PREFIX):  # 幂等：已密封不重复加密
        return plain
    if not _DPAPI_OK:
        logger.warning("pywin32 不可用，API Key 将以明文落盘（仅应在非 Windows 开发环境出现）")
        return plain
    blob = win32crypt.CryptProtectData(plain.encode("utf-8"), "workbench-llm-key")
    return _PREFIX + base64.b64encode(blob).decode("ascii")


def unseal(stored: str) -> str:
    """还原 Key；明文历史值原样返回；解密失败（如换机）返回空串。"""
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        return stored  # 历史明文，向后兼容
    if not _DPAPI_OK:
        logger.error("发现 DPAPI 加密 Key 但 pywin32 不可用，无法解密")
        return ""
    try:
        blob = base64.b64decode(stored[len(_PREFIX):])
        _desc, data = win32crypt.CryptUnprotectData(blob)
        return data.decode("utf-8")
    except Exception:
        logger.warning("API Key 解密失败（密钥绑定原机器+账户，换机需重新录入）", exc_info=True)
        return ""


def migrate_seal_keys() -> int:
    """把数据库中存量明文 api_key 一次性迁移为 DPAPI 格式，返回迁移条数。"""
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
