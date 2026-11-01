"""OpenCode Go 配置更新替换（2026-11-01）。

把真实库中旧的 OpenCode Go 供应商（id=2）更新替换为 provider_presets.json 的
opencode_go 预设：protocol/base_url/models_url/preset_key 对齐预设并启用供应商；
然后在线同步模型（在线失败自动按预设目录兜底）；最后激活 glm-5.3-flash。
api_key 不经本脚本（不传即保留库中现值），全程不打印任何密钥。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.agents import config_store as cs  # noqa: E402


def main():
    preset = cs.preset_by_key("opencode_go")
    if not preset:
        raise SystemExit("opencode_go 预设缺失")
    target = next((p for p in cs.list_providers() if p["id"] == 2), None)
    if target is None:
        raise SystemExit("供应商 id=2 不存在（OpenCode Go）")
    cs.save_provider({
        "provider_id": 2,
        "name": "OpenCode Go",
        "protocol": preset["protocol"],
        "base_url": preset["base_url"],
        "models_url": preset["models_url"],
        "preset_key": preset["preset_key"],
        "enabled": True,
    })
    res = cs.sync_provider_models(2)
    res.pop("providers", None)
    print("sync:", json.dumps(res, ensure_ascii=False))
    provs = cs.list_providers()
    models = [p for p in provs if p["id"] == 2][0].get("models", [])
    pick = [m for m in models if m.get("model") == "glm-5.3-flash" and m.get("enabled")]
    if pick:
        print("activated:", cs.activate_model(pick[0]["id"]))
    else:
        print("glm-5.3-flash 未启用，跳过激活")


if __name__ == "__main__":
    main()
