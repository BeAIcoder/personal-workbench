"""Agent 供应商 / 模型两张表的 CRUD、能力视图与实测结论（自 config_store.py 机械拆出）。

职责边界：供应商与模型的读写、激活、在线列表同步入口、工具/图片实测结论落库、
能力契约视图、测试目标模型定位。生效配置组装在 config_settings.py，密钥掩码与
地址校验在 config_secrets.py；预设文件与连通性测试仍在 config_store.py
（PRESETS 可被 reload 重绑、_probe_urlopen 是测试注入点，必须与 facade 同命名空间）。
"""
import json
import logging
from datetime import datetime

from sqlalchemy.exc import OperationalError

from ..database import SessionLocal
from ..models import AgentSettings, ModelProvider, ProviderModel
from . import model_catalog
from .config_secrets import MASK, MASK_PREFIX, _read_sync_credentials, validate_base_url
from .secrets_box import seal, unseal

logger = logging.getLogger(__name__)

def _parse_temperature(raw) -> float | None:
    """模型级温度解析：空/非法 → None（不设置）；合法 → float（0-2 夹取）。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        t = float(str(raw).strip())
    except ValueError:
        return None
    return t if 0.0 <= t <= 2.0 else None


def _model_capability_view(m) -> dict:
    """模型能力视图：能力列 + capability_meta（字段来源/手动字段/最近同步快照）。"""
    cap = model_catalog.capability_meta_of(m)
    return {
        "max_input_tokens": m.max_input_tokens,
        "supports_tools": m.supports_tools,
        "input_modalities": cap["input_modalities"],
        "output_modalities": cap["output_modalities"],
        "default_thinking_level": cap["default_thinking_level"],
        "thinking_can_disable": cap["thinking_can_disable"],
        "reasoning_style": cap["reasoning_style"],
        "api_family": cap["api_family"],
        "remote_status": cap["remote_status"],
        "synced_at": m.synced_at,
        "capability": cap,
    }


def _row() -> AgentSettings | None:
    with SessionLocal() as db:
        return db.get(AgentSettings, 1)


def _active_model():
    """当前激活的 (provider, provider_model)；无激活或已停用返回 None。"""
    try:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            if row is None or not row.active_model_id:
                return None
            pm = db.get(ProviderModel, row.active_model_id)
            if pm is None or not pm.enabled:
                return None
            prov = db.get(ModelProvider, pm.provider_id)
            if prov is None or not prov.enabled:
                return None
            return prov, pm
    except OperationalError:
        logger.warning("读取激活模型失败（数据库暂不可用）", exc_info=True)
        return None


# ---------------- 供应商 / 模型 CRUD（ZCode 式） ----------------

def _json_list(raw: str) -> list:
    """解析存为 JSON 文本的列表字段（如 thinking_levels）；失败返回空列表。"""
    try:
        val = json.loads(raw or "[]")
    except Exception:
        return []
    return val if isinstance(val, list) else []


def list_providers() -> list[dict]:
    from .config_store import preset_by_key  # 预设唯一源在 facade（PRESETS 可被 reload 与测试重绑）

    try:
        row = _row()
        active_model_id = row.active_model_id if row else None
        with SessionLocal() as db:
            provs = db.query(ModelProvider).order_by(ModelProvider.id.asc()).all()
            models = db.query(ProviderModel).order_by(ProviderModel.id.asc()).all()
    except OperationalError:
        logger.warning("供应商列表查询失败（数据库暂不可用）", exc_info=True)
        return []

    by_prov: dict[int, list] = {}
    for m in models:
        by_prov.setdefault(m.provider_id, []).append(m)

    return [
        {
            "id": p.id,
            "name": p.name,
            "protocol": p.protocol,
            "base_url": p.base_url,
            "api_key_set": bool(p.api_key),
            "enabled": p.enabled,
            # 预设标识与在线列表地址（适配器只认 preset_key；usage_scope 等套餐边界取自预设文件）
            "preset_key": p.preset_key or "",
            "models_url": p.models_url or "",
            "usage_scope": (preset_by_key(p.preset_key) or {}).get("usage_scope", "general"),
            "auth_scheme": (preset_by_key(p.preset_key) or {}).get("auth_scheme", "bearer"),
            "sync_adapter": (preset_by_key(p.preset_key) or {}).get("sync_adapter", "none"),
            # 管控凭证只暴露「是否已配置」布尔量，绝不回传凭证内容（含掩码）
            "sync_credentials_set": bool(p.sync_credentials),
            "models": [
                {
                    "id": m.id,
                    "model": m.model,
                    "context_size": m.context_size,
                    "max_tokens": m.max_tokens,
                    "multimodal": m.multimodal,
                    # 视觉（图片理解）能力：聊天传图需开启
                    "supports_vision": bool(m.supports_vision),
                    "enabled": m.enabled,
                    "active": m.id == active_model_id,
                    # 采样温度（空 = 跟随系统默认）与备注
                    "temperature": m.temperature,
                    "note": m.note,
                    # 思考能力探测：-1 未探测 / 0 否 / 1 是 + 可用档位
                    "supports_thinking": m.supports_thinking,
                    "thinking_levels": _json_list(m.thinking_levels),
                    # 探测出的有效上下文 / 最大输出（0 = 未探测）与探测时间
                    "probed_context": m.probed_context,
                    "probed_max_output": m.probed_max_output,
                    "probed_at": m.probed_at,
                    # 能力契约字段（Task 3）：工具/模态/思考默认档/api_family/来源/同步时间
                    **_model_capability_view(m),
                }
                for m in by_prov.get(p.id, [])
            ],
        }
        for p in provs
    ]


def models_flat() -> list[dict]:
    """所有启用中的（供应商, 模型）扁平列表，供快速切换下拉。"""
    out = []
    for p in list_providers():
        if not p["enabled"]:
            continue
        for m in p["models"]:
            if m["enabled"]:
                out.append(
                    {
                        "id": m["id"],
                        "label": f"{p['name']} · {m['model']}",
                        "provider": p["name"],
                        "model": m["model"],
                        "multimodal": m["multimodal"],
                        "supports_vision": m["supports_vision"],
                        "active": m["active"],
                        # 思考能力（Task 6B：与 providers 视图同口径，供前端按 Agent 生效模型生成档位）
                        "supports_thinking": m["supports_thinking"],
                        "thinking_levels": m["thinking_levels"],
                        "reasoning_style": m["reasoning_style"],
                        # 能力契约字段（Task 3，与 providers 视图同口径）
                        "max_input_tokens": m["max_input_tokens"],
                        "supports_tools": m["supports_tools"],
                        "input_modalities": m["input_modalities"],
                        "output_modalities": m["output_modalities"],
                        "default_thinking_level": m["default_thinking_level"],
                        "thinking_can_disable": m["thinking_can_disable"],
                        "api_family": m["api_family"],
                        "capability": m["capability"],
                    }
                )
    return out


def save_provider(data: dict) -> dict:
    """新增/更新供应商；api_key 传空或掩码（MASK 哨兵 / MASK_PREFIX 回显形态）保留现值。

    preset_key/models_url 供同步适配器定位官方列表；sync_access_key/sync_secret_key
    为可选管控面凭证（火山 AK/SK），密封 JSON 存 sync_credentials：空/掩码保留旧值，
    任何 GET 不回传。
    """
    pid = data.get("provider_id")
    name = str(data.get("name") or "").strip()[:50] or "未命名供应商"
    with SessionLocal() as db:
        prov = db.get(ModelProvider, int(pid)) if pid else None
        if prov is None:
            prov = ModelProvider(name=name)
            db.add(prov)
            db.flush()
        else:
            prov.name = name
        protocol = str(data.get("protocol") or prov.protocol or "openai").strip().lower()
        if protocol not in ("openai", "anthropic"):
            raise ValueError("供应商协议仅支持 openai / anthropic")
        prov.protocol = protocol
        if data.get("base_url") is not None:
            url = str(data["base_url"]).strip().rstrip("/")
            if len(url) > 300:
                raise ValueError("供应商地址过长（最多 300 字符）")
            prov.base_url = validate_base_url(url) if url else ""
        if data.get("preset_key") is not None:
            prov.preset_key = str(data["preset_key"]).strip()[:50]
        if data.get("models_url") is not None:
            url = str(data["models_url"]).strip().rstrip("/")
            if len(url) > 300:
                raise ValueError("模型列表地址过长（最多 300 字符）")
            prov.models_url = validate_base_url(url) if url else ""
        key_in = str(data.get("api_key") or "").strip()
        if len(key_in) > 300:
            raise ValueError("API Key 过长（最多 300 字符），未保存")
        # 两种掩码形态均视为「未修改保留原值」：哨兵 ******** 与 GET 回显的 ••••••+后四位
        if key_in and key_in != MASK and not key_in.startswith(MASK_PREFIX):
            prov.api_key = seal(key_in)
        # 管控凭证：与 api_key 同口径（空/掩码保留旧值），逐字段合并后整体重新密封
        ak_in = str(data.get("sync_access_key") or "").strip()
        sk_in = str(data.get("sync_secret_key") or "").strip()
        if len(ak_in) > 300 or len(sk_in) > 300:
            raise ValueError("管控凭证过长（每项最多 300 字符），未保存")
        if ak_in or sk_in:
            creds = _read_sync_credentials(prov.sync_credentials)
            changed = False
            if ak_in and ak_in != MASK and not ak_in.startswith(MASK_PREFIX):
                creds["access_key"] = ak_in
                changed = True
            if sk_in and sk_in != MASK and not sk_in.startswith(MASK_PREFIX):
                creds["secret_key"] = sk_in
                changed = True
            if changed:
                prov.sync_credentials = seal(json.dumps(creds, ensure_ascii=False))
        if data.get("enabled") is not None:
            prov.enabled = bool(data["enabled"])
        prov.updated_at = datetime.now()
        db.commit()
        return {"id": prov.id, "name": prov.name, "protocol": prov.protocol}


def delete_provider(pid: int) -> None:
    """删除供应商，同事务级联删除其全部模型；激活模型属于该供应商时清理激活态。

    两表无数据库级联：不删 ProviderModel 会留孤儿数据，SQLite 复用 rowid 后
    旧模型还会挂到新供应商名下。
    """
    with SessionLocal() as db:
        pid = int(pid)
        models = db.query(ProviderModel).filter(ProviderModel.provider_id == pid).all()
        model_ids = {m.id for m in models}
        row = db.get(AgentSettings, 1)
        if row is not None and row.active_model_id in model_ids:
            row.active_model_id = None
        for m in models:
            db.delete(m)
        prov = db.get(ModelProvider, pid)
        if prov is not None:
            db.delete(prov)
        db.commit()


def save_provider_model(data: dict) -> dict:
    """新增/更新供应商下的模型。

    手动填写的能力字段（上下文/输出/输入上限/工具/模态/思考档位/思考默认档/api_family 等）
    记入 capability_meta.manual_fields，目录/在线同步默认不覆盖它们；
    probe_* 与 note/temperature/enabled 由用户单独管理，同步永不触碰。
    """
    provider_id = int(data["provider_id"])
    model = str(data.get("model") or "").strip()[:120]
    if not model:
        raise ValueError("模型名不能为空")
    mid = data.get("model_id")
    with SessionLocal() as db:
        prov = db.get(ModelProvider, provider_id)
        if prov is None:
            raise ValueError(f"供应商 {provider_id} 不存在")
        pm = db.get(ProviderModel, int(mid)) if mid else None
        if pm is not None and pm.provider_id != provider_id:
            # 归属校验：路径里的 provider_id 必须与模型实际所属一致，
            # 否则 POST /providers/{B}/models 带 A 的 model_id 会改到 A 的模型。
            raise ValueError("该模型不属于此供应商")
        if pm is None:
            pm = ProviderModel(provider_id=provider_id, model=model)
            db.add(pm)
            db.flush()
        else:
            pm.model = model
        pm.context_size = max(4096, int(data.get("context_size") or pm.context_size or 128000))
        pm.max_tokens = max(64, int(data.get("max_tokens") or pm.max_tokens or 2048))
        if data.get("multimodal") is not None:
            pm.multimodal = bool(data["multimodal"])
        if data.get("supports_vision") is not None:
            pm.supports_vision = bool(data["supports_vision"])
        if data.get("enabled") is not None:
            pm.enabled = bool(data["enabled"])
        # 采样温度：空串 = 跟随系统默认（TEXT 存储，兼容手动填写）
        if data.get("temperature") is not None:
            t = data["temperature"]
            pm.temperature = str(t).strip()[:16] if t != "" else ""
        if data.get("note") is not None:
            pm.note = str(data.get("note") or "")[:500]
        # 思考能力：探测接口会自动写回，此处也允许手动标记（-1/0/1）
        if data.get("supports_thinking") is not None:
            pm.supports_thinking = max(-1, min(1, int(data["supports_thinking"])))
        if data.get("thinking_levels") is not None:
            levels = [str(x)[:20] for x in (data["thinking_levels"] or [])][:12]
            pm.thinking_levels = json.dumps(levels, ensure_ascii=False)
        # 能力契约字段（Task 3）：手动填写 → 记 manual_fields，同步默认不覆盖
        if data.get("max_input_tokens") is not None:
            pm.max_input_tokens = max(0, int(data["max_input_tokens"]))
        if data.get("supports_tools") is not None:
            pm.supports_tools = max(-1, min(1, int(data["supports_tools"])))
        meta = model_catalog.capability_meta_of(pm)
        manual_fields = set(meta["manual_fields"])
        sources = dict(meta["sources"])
        for key in ("max_input_tokens", "supports_tools"):
            if data.get(key) is not None:
                manual_fields.add(key)
                sources[key] = "manual"
        if data.get("input_modalities") is not None:
            meta["input_modalities"] = [str(x)[:20] for x in (data["input_modalities"] or [])][:8]
            manual_fields.add("input_modalities")
            sources["input_modalities"] = "manual"
            # 手动模态驱动派生能力（未显式传布尔值时），派生值同步标记 manual：
            # 模态是用户的权威声明，目录/同步不得把 vision/multimodal 翻转成与模态矛盾的值
            if data.get("multimodal") is None:
                pm.multimodal = bool(model_catalog.derive_multimodal(meta["input_modalities"]))
                manual_fields.add("multimodal")
                sources["multimodal"] = "manual"
            if data.get("supports_vision") is None:
                pm.supports_vision = bool(model_catalog.derive_supports_vision(meta["input_modalities"]))
                manual_fields.add("supports_vision")
                sources["supports_vision"] = "manual"
        if data.get("output_modalities") is not None:
            meta["output_modalities"] = [str(x)[:20] for x in (data["output_modalities"] or [])][:8]
            manual_fields.add("output_modalities")
            sources["output_modalities"] = "manual"
        if data.get("default_thinking_level") is not None:
            meta["default_thinking_level"] = str(data["default_thinking_level"]).strip()[:20] or None
            manual_fields.add("default_thinking_level")
            sources["default_thinking_level"] = "manual"
        if "thinking_can_disable" in data:
            v = data["thinking_can_disable"]
            meta["thinking_can_disable"] = None if v is None else bool(v)
            manual_fields.add("thinking_can_disable")
            sources["thinking_can_disable"] = "manual"
        if data.get("reasoning_style") is not None:
            meta["reasoning_style"] = str(data["reasoning_style"]).strip()[:20]
            manual_fields.add("reasoning_style")
            sources["reasoning_style"] = "manual"
        if data.get("api_family") is not None:
            fam = str(data["api_family"]).strip().lower()[:30]
            meta["api_family"] = fam if fam in ("chat_completions", "messages", "responses", "other") else ""
            manual_fields.add("api_family")
            sources["api_family"] = "manual"
        # 既有字段的手动标记（同步默认同样不覆盖这些字段）
        for key, col in (
            ("context_size", "context_size"),
            ("max_tokens", "max_tokens"),
            ("multimodal", "multimodal"),
            ("supports_vision", "supports_vision"),
            ("supports_thinking", "supports_thinking"),
            ("thinking_levels", "thinking_levels"),
        ):
            if data.get(key) is not None:
                manual_fields.add(col)
                sources[col] = "manual"
        meta["manual_fields"] = sorted(manual_fields)
        meta["sources"] = sources
        pm.capability_meta = json.dumps(meta, ensure_ascii=False)
        pm.updated_at = datetime.now()
        db.commit()
        return {"id": pm.id, "model": pm.model}


def delete_provider_model(mid: int, provider_id: int | None = None) -> None:
    """删除模型；传入 provider_id 时校验归属，不匹配则拒绝（防跨供应商误删）。"""
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is not None and provider_id is not None and pm.provider_id != int(provider_id):
            raise ValueError("该模型不属于此供应商")
        if pm is not None:
            db.delete(pm)
        row = db.get(AgentSettings, 1)
        if row is not None and row.active_model_id == int(mid):
            row.active_model_id = None
        db.commit()


def activate_model(mid: int) -> dict:
    """激活某个模型（跨供应商），立即生效。"""
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is None or not pm.enabled:
            raise ValueError(f"模型 {mid} 不存在或未启用")
        prov = db.get(ModelProvider, pm.provider_id)
        if prov is None or not prov.enabled:
            raise ValueError(f"供应商未启用：{pm.provider_id}")
        row = db.get(AgentSettings, 1)
        if row is None:
            row = AgentSettings(id=1)
            db.add(row)
            db.flush()
        row.active_model_id = int(mid)
        db.commit()
        return {"model": pm.model, "provider": prov.name}


def reset_provider_model_overrides(mid: int) -> dict:
    """清除模型的手动能力覆盖并按最近一次同步快照恢复；模型不存在抛 ValueError。"""
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is None:
            raise ValueError(f"模型 {mid} 不存在")
        result = model_catalog.reset_manual_overrides(pm)
        db.commit()
        return {"model_id": pm.id, **result}


def sync_provider_models(provider_id: int, include_disabled: bool = False, override_manual: bool = False) -> dict:
    """同步供应商在线模型列表（Task 4）：在线适配器优先，失败/无来源时能力目录兜底。

    在线获取只发目录/管控请求（火山官方管控面为只读列表 Action 的只读 POST，body 仅 {} 或
    PageNumber/PageSize 分页字段）、绝不发推理请求；
    单事务幂等 upsert，manual/probe/note/temperature/enabled/active 不被覆盖。实现在 model_sync
    （延迟导入避免循环依赖）。
    """
    from . import model_sync  # 延迟导入：model_sync 依赖本模块的校验/预设/凭证读取

    with SessionLocal() as db:
        return model_sync.sync_provider_models(
            db, provider_id, include_disabled=include_disabled, override_manual=override_manual
        )


# 工具/图片实测结论的三态判定（落库口径与界面展示一致）
_PROBE_VERDICT_FIELDS = ("supports_tools", "supports_vision")


def record_probe_verdict(model_id: int, field: str, verdict: str) -> dict | None:
    """把工具/图片实测结论写回模型行（来源 probe）。

    field: supports_tools / supports_vision；verdict: supported / rejected / unknown。
    只有明确结论才写列与来源（supported→1、rejected→0）；unknown（网络错误等
    未获得明确证据）不改动任何值——不把「没测到」当成「不支持」。

    probe 结论同时记入 manual_fields 作为**本地覆盖**：默认目录同步不覆盖它
    （实测优先于目录声明），`override_manual=True` 的同步或 reset-overrides
    可清除覆盖并恢复同步值。
    """
    if field not in _PROBE_VERDICT_FIELDS or verdict not in ("supported", "rejected"):
        return None
    value = 1 if verdict == "supported" else 0
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(model_id))
        if pm is None:
            return None
        setattr(pm, field, value)
        meta = model_catalog.capability_meta_of(pm)
        sources = dict(meta["sources"])
        sources[field] = "probe"
        meta["sources"] = sources
        # 实测结论作为本地覆盖：默认同步不再用目录值覆盖它（override_manual/reset 可清除）
        if field not in meta["manual_fields"]:
            meta["manual_fields"] = sorted([*meta["manual_fields"], field])
        pm.capability_meta = json.dumps(meta, ensure_ascii=False)
        try:
            result = json.loads(pm.probe_result or "{}")
        except Exception:
            result = {}
        if not isinstance(result, dict):
            result = {}
        result[field] = {
            "verdict": verdict,
            "value": value,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        pm.probe_result = json.dumps(result, ensure_ascii=False)
        pm.probed_at = datetime.now().isoformat(timespec="seconds")
        db.commit()
        return {"model_id": pm.id, "field": field, "value": value}


def resolve_test_model_id(model_id: int | None = None, provider_id: int | None = None) -> int | None:
    """定位工具/图片实测要回写的模型行：显式 model_id > 供应商首个启用模型 > 激活模型。"""
    if model_id:
        return int(model_id)
    if provider_id:
        for p in list_providers():
            if p["id"] == provider_id:
                for m in p["models"]:
                    if m["enabled"]:
                        return m["id"]
        return None
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        if row is None or not row.active_model_id:
            return None
        return row.active_model_id


def provider_test_cfg(pid: int) -> dict:
    """取某供应商第一个启用模型的测试配置。"""
    for p in list_providers():
        if p["id"] == pid:
            models = [m for m in p["models"] if m["enabled"]]
            if not models:
                raise ValueError(f"供应商「{p['name']}」下没有启用中的模型")
            key = "sk-unknown"
            pm = None
            try:
                with SessionLocal() as db:
                    prov = db.get(ModelProvider, pid)
                    key = unseal(prov.api_key) if prov else key
                    pm = db.get(ProviderModel, models[0]["id"])
            except OperationalError:
                logger.warning("读取供应商密钥失败（数据库暂不可用），使用占位 key", exc_info=True)
            m = models[0]
            cfg = {
                "protocol": p["protocol"],
                "base_url": p["base_url"],
                "api_key": key,
                "model": m["model"],
                "context_size": m["context_size"],
                "max_tokens": m["max_tokens"],
                # 套餐边界与会话头（Task 5）：供应商测试同样按 preset 生成请求头/受套餐约束
                "preset_key": p["preset_key"],
                "usage_scope": p["usage_scope"],
            }
            if pm is not None:
                # 能力契约（Task 3/5）：测试路径同样按模型的思考风格/档位生成请求参数
                cfg.update(_model_capability_view(pm))
            return cfg
    raise ValueError(f"供应商 {pid} 不存在")
