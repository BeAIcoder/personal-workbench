# -*- coding: utf-8 -*-
"""术语表覆盖测试：`docs/GLOSSARY.md` 的显示名/别名必须与后端 manifest 一致。

只读文档与 manifest 文本，不启动服务、不读写 `backend/data`。目的是防止
“文档里叫数据池、菜单里叫数据仓、路由又是 vault”这类显示名漂移。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GLOSSARY = REPO / "docs" / "GLOSSARY.md"
MODULES = REPO / "backend" / "app" / "modules"

DISPLAY_NAMES = {
    "finance_vault": "数据池清洗",
    "finance_dsf": "残保金填报",
    "finance_receipt": "银行回单整理",
    "finance_pos": "POS 对账",
}

REQUIRED_ALIASES = (
    "残保金",
    "残疾人就业保障金",
    "disability security fund",
    "数据池",
    "数据仓",
    "银行回单",
    "刷卡凭证",
    "vouchers_service",
    "mod_pos_voucher_batch",
    "mod_receipt_batch",
)


def _glossary() -> str:
    assert GLOSSARY.is_file(), f"缺少术语表 {GLOSSARY}"
    return GLOSSARY.read_text(encoding="utf-8")


def _manifest_field(module: str, field: str) -> str:
    text = (MODULES / module / "manifest.py").read_text(encoding="utf-8")
    match = re.search(rf'{field}="([^"]+)"', text)
    assert match, f"{module} manifest 缺少 {field}"
    return match.group(1)


def test_glossary_has_display_name_section():
    assert "## 1.1 显示名与别名" in _glossary()


def test_display_names_match_module_manifest_titles():
    text = _glossary()
    for module, title in DISPLAY_NAMES.items():
        assert title == _manifest_field(module, "title"), f"{module} 显示名与 manifest title 不一致"
        assert module in text, f"术语表缺少 {module}"
        assert title in text, f"术语表缺少显示名 {title}"


def test_alias_and_identity_terms_are_documented():
    text = _glossary()
    for alias in REQUIRED_ALIASES:
        assert alias in text, f"术语表缺少别名/代码名 {alias}"


def test_route_prefixes_are_not_renamed_by_glossary():
    """术语表写的是显示名，路由前缀必须与 manifest 一致（改名要另开迁移）。"""
    text = _glossary()
    for module in DISPLAY_NAMES:
        prefix = _manifest_field(module, "prefix")
        assert prefix in text, f"术语表缺少 {module} 的路由前缀 {prefix}"


def test_gm_provider_registry_is_documented_and_present():
    text = _glossary()
    assert "DomainProvider" in text
    assert "register_provider" in text

    from app.modules.business_gm.service import DomainProvider, register_provider, registered_providers

    assert callable(register_provider)
    assert DomainProvider.__dataclass_fields__["builder"]
    assert [item.domain for item in registered_providers()][0] == "leasing"
