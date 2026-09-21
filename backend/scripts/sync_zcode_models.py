"""把 ZCode 的模型接入同步到个人工作台（供应商 → 模型 两级，幂等可重复执行）。

读取 ~/.zcode/v2/config.json 的 provider 配置（anthropic 协议网关），
按「有 API Key 且未禁用」过滤，逐家 upsert 到 model_providers / provider_models，
模型上下文/输出上限与多模态标记随 ZCode 的 limit/modalities 同步。

用法（backend 目录）：
    venv/Scripts/python scripts/sync_zcode_models.py            # 同步 + 连通性探测
    venv/Scripts/python scripts/sync_zcode_models.py --no-test  # 仅同步不探测
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import config_store  # noqa: E402
from app.agents.llm import build_model  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ModelProvider, ProviderModel  # noqa: E402


def zcode_config_path() -> Path:
    override = sys.argv[sys.argv.index("--config") + 1] if "--config" in sys.argv else None
    return Path(override or Path.home() / ".zcode" / "v2" / "config.json")


def upsert_provider(name: str, protocol: str, base_url: str, api_key: str) -> int:
    with SessionLocal() as db:
        prov = db.query(ModelProvider).filter(ModelProvider.name == name).first()
        if prov is None:
            prov = ModelProvider(name=name)
            db.add(prov)
            db.flush()
        prov.protocol = protocol
        prov.base_url = base_url
        if api_key:
            prov.api_key = api_key
        prov.enabled = True
        prov.updated_at = __import__("datetime").datetime.now()
        db.commit()
        return prov.id


def upsert_model(provider_id: int, model: str, context_size: int, max_tokens: int, multimodal: bool) -> int:
    with SessionLocal() as db:
        pm = (
            db.query(ProviderModel)
            .filter(ProviderModel.provider_id == provider_id, ProviderModel.model == model)
            .first()
        )
        if pm is None:
            pm = ProviderModel(provider_id=provider_id, model=model)
            db.add(pm)
            db.flush()
        pm.context_size = max(4096, int(context_size or 128000))
        pm.max_tokens = max(64, int(max_tokens or 2048))
        pm.multimodal = multimodal
        pm.enabled = True
        pm.updated_at = __import__("datetime").datetime.now()
        db.commit()
        return pm.id


async def probe(cfg: dict) -> str:
    model = build_model(cfg)
    if model is None:
        return "配置不完整"
    from agentscope.message import Msg, TextBlock

    t0 = time.time()
    try:
        resp = await model([Msg(name="user", role="user", content=[TextBlock(type="text", text="请只回复：OK")])])
        blocks = resp.get("content") or []
        text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text").strip()
        return f"✅ {(time.time()-t0)*1000:.0f}ms {text[:20]!r}"
    except Exception as exc:
        return f"❌ {type(exc).__name__}: {str(exc)[:60]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 ZCode 模型接入到个人工作台")
    parser.add_argument("--no-test", action="store_true", help="仅同步，不做连通性探测")
    parser.add_argument("--config", help="ZCode config.json 路径（默认 ~/.zcode/v2/config.json）")
    args = parser.parse_args()

    cfg_path = zcode_config_path()
    if not cfg_path.exists():
        print(f"未找到 ZCode 配置：{cfg_path}")
        return

    # 确保工作台库已建表并补齐历史列（服务未启动过新版本时也能直接同步）
    from app.database import Base, engine
    from app.main import _ensure_sqlite_columns

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()

    providers = json.loads(cfg_path.read_text(encoding="utf-8")).get("provider", {})

    synced = []
    for pid, p in providers.items():
        opts = p.get("options") or {}
        key = opts.get("apiKey") or ""
        if not key or p.get("enabled") is False or p.get("systemDisabledReason"):
            reason = p.get("systemDisabledReason") or "无 API Key"
            print(f"- {p.get('name', pid)[:24]:24s} 跳过（{reason}）")
            continue
        kind = p.get("kind", "openai")
        protocol = "anthropic" if kind == "anthropic" else "openai"
        base_url = opts.get("baseURL") or ""
        prov_id = upsert_provider(p.get("name", pid)[:50], protocol, base_url, key)
        model_ids = []
        for mkey, m in (p.get("models") or {}).items():
            model_name = (m.get("name") or mkey).strip()
            limit = m.get("limit") or {}
            modal_in = ((m.get("modalities") or {}).get("input")) or ["text"]
            multimodal = "image" in modal_in
            model_ids.append(
                upsert_model(prov_id, model_name, limit.get("context", 128000), limit.get("output", 2048), multimodal)
            )
        synced.append((prov_id, p.get("name", pid), protocol, model_ids))
        print(f"✓ {p.get('name', pid)[:24]:24s} 同步 {len(model_ids)} 个模型（{protocol}）")

    # 让运行中的服务感知新配置（uvicorn 按请求读库，无需重启）

    if args.no_test or not synced:
        return

    print("\n连通性探测（逐模型）：")
    for prov_id, name, protocol, model_ids in synced:
        with SessionLocal() as db:
            prov = db.get(ModelProvider, prov_id)
            for mid in model_ids:
                pm = db.get(ProviderModel, mid)
                cfg = {
                    "protocol": prov.protocol,
                    "base_url": prov.base_url,
                    "api_key": prov.api_key,
                    "model": pm.model,
                    "context_size": pm.context_size,
                    "max_tokens": pm.max_tokens,
                }
                result = asyncio.run(probe(cfg))
                print(f"  {name} [{pm.model}] -> {result}")


if __name__ == "__main__":
    main()
