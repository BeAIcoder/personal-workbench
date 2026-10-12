"""模型能力探测接口与 LLM 厂商预设接口。

- 探测：POST /api/models/{model_id}/probe/{reasoning,context,output,all}（同步执行，
  单端点超时由前端控制）；GET /api/models/{model_id}/probe 只读当前状态；
- 预设：GET /api/providers/presets、POST /api/providers/presets/reload。

约定：探测失败（厂商不可达/参数被拒）是正常结果，以 200 + error 字段返回；
仅「模型不存在」返回 404。
"""
import logging

from fastapi import APIRouter, HTTPException

from ..agents import config_store, model_probe
from ..database import SessionLocal
from ..models import ProviderModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/models", tags=["模型能力探测"])
# 预设管理按契约挂 /api/providers 下（不套 /models 前缀）；main.py 另挂 /api/models 别名
presets_router = APIRouter(tags=["模型能力探测"])


def _run_probe(model_id: int, fn) -> dict:
    """取模型行 → 同步执行探测 → 返回结果与最新状态；模型不存在 404。"""
    with SessionLocal() as db:
        row = db.get(ProviderModel, int(model_id))
        if row is None:
            raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在")
        try:
            result = fn(db, row)
        except Exception as exc:
            # 探测内部异常不应逃逸成 5xx：记录日志，按「探测失败」正常返回
            logger.warning("模型探测执行失败：model_id=%s", model_id, exc_info=True)
            result = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        state = model_probe.probe_state(row)
    return {"model_id": model_id, "result": result, "probe": state}


@router.post("/{model_id}/probe/reasoning", summary="探测思考能力（reasoning_effort 分级，仅 openai 协议）")
def probe_reasoning(model_id: int):
    return _run_probe(model_id, model_probe.probe_reasoning)


@router.post("/{model_id}/probe/context", summary="探测有效上下文 tokens（二分查找，最多 6 次请求）")
def probe_context(model_id: int):
    return _run_probe(model_id, model_probe.probe_context)


@router.post("/{model_id}/probe/output", summary="探测最大输出 tokens（阶梯 1024→131072）")
def probe_output(model_id: int):
    return _run_probe(model_id, model_probe.probe_output)


@router.post("/{model_id}/probe/all", summary="全量探测（思考→上下文→输出，单步失败不中断）")
def probe_all(model_id: int):
    return _run_probe(model_id, model_probe.probe_all)


@router.get("/{model_id}/probe", summary="读取模型当前探测状态（不触发探测）")
def probe_status(model_id: int):
    with SessionLocal() as db:
        row = db.get(ProviderModel, int(model_id))
        if row is None:
            raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在")
        state = model_probe.probe_state(row)
    return {"model_id": model_id, "probe": state}


@presets_router.get("/providers/presets", summary="LLM 厂商预设列表（纯数据，无密钥概念）")
def get_presets():
    presets = config_store.PRESETS
    return {"presets": presets, "count": len(presets)}


@presets_router.post("/providers/presets/reload", summary="重新加载 provider_presets.json 预设")
def reload_presets():
    presets = config_store.reload_presets()
    return {"presets": presets, "count": len(presets)}
