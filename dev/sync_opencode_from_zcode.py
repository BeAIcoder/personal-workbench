# -*- coding: utf-8 -*-
"""从 ZCode 本地配置同步 OpenCode Go 供应商（2026-11-02）。

读取 ~/.zcode/v2/config.json 中第一个启用的、带 apiKey 的 OpenCode 供应商（zen/go + anthropic），
把 apiKey/baseURL/协议同步进工作台真实库供应商 id=2；随后在线同步模型目录（失败按官方目录兜底）、
确保 deepseek-v4.1-flash 存在并激活为默认模型。Key 全程不打印、只进库（密封存储）。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.agents import config_store as cs  # noqa: E402

ZCODE_CFG = Path.home() / ".zcode" / "v2" / "config.json"
TARGET_MODEL = "deepseek-v4.1-flash"


def _zcode_provider():
    data = json.loads(ZCODE_CFG.read_text(encoding="utf-8"))
    providers = data.get("provider", {})
    for pid, p in providers.items():
        name = str(p.get("name", ""))
        opts = p.get("options", {}) or {}
        if "opencode" in (pid + name).lower() and opts.get("apiKey"):
            return p
    raise SystemExit("ZCode 配置中未找到带 Key 的 OpenCode 供应商")


def main():
    p = _zcode_provider()
    opts = p.get("options", {}) or {}
    kind = str(p.get("kind", "anthropic"))
    print("zcode provider:", p.get("name"), "| kind:", kind, "| base:", opts.get("baseURL"))
    cs.save_provider({
        "provider_id": 2,
        "name": "OpenCode Go",
        "protocol": "anthropic" if kind == "anthropic" else "openai",
        "base_url": str(opts.get("baseURL", "")).rstrip("/"),
        "api_key": str(opts.get("apiKey", "")),
        "enabled": True,
    })
    res = cs.sync_provider_models(2)
    res.pop("providers", None)
    print("sync:", json.dumps(res, ensure_ascii=False)[:600])
    prov = next(p for p in cs.list_providers() if p["id"] == 2)
    models = prov.get("models", [])
    hit = [m for m in models if m.get("model") == TARGET_MODEL]
    if not hit:
        cs.save_provider_model({"provider_id": 2, "model": TARGET_MODEL, "enabled": True})
        prov = next(p for p in cs.list_providers() if p["id"] == 2)
        models = prov.get("models", [])
        hit = [m for m in models if m.get("model") == TARGET_MODEL]
    print("target model rows:", [(m.get("model"), m.get("id"), m.get("enabled")) for m in models if m.get("model") == TARGET_MODEL])
    if hit and hit[0].get("enabled"):
        print("activated:", cs.activate_model(hit[0]["id"]))
    else:
        print("目标模型未启用，未激活")


if __name__ == "__main__":
    main()
