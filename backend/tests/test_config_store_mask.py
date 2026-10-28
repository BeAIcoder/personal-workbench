"""API 密钥掩码哨兵一致性测试。

GET 回显的掩码（•••••• + 原密钥后四位，见 config_store.masked）若被客户端原样回传，
保存侧必须视为「未修改保留原值」，与哨兵 ******** 同口径——不得覆盖真实密钥；
只有真实新密钥才执行更新。
"""
from app.agents import config_store
from app.agents.secrets_box import unseal
from app.database import SessionLocal
from app.models import ModelProvider


def _mk_provider(client, api_key: str = "sk-real-key-9876") -> int:
    body = client.post(
        "/api/assistant/providers",
        json={"name": "掩码测试", "protocol": "openai", "base_url": "https://x.com/v1", "api_key": api_key},
    ).json()
    return body["provider"]["id"]


def _stored_key(pid: int) -> str:
    with SessionLocal() as db:
        return unseal(db.get(ModelProvider, pid).api_key)


def _save_key(client, pid: int, api_key: str):
    return client.post(
        "/api/assistant/providers",
        json={"provider_id": pid, "name": "掩码测试", "api_key": api_key},
    )


def test_masked_echo_format():
    """masked() 回显形态：•••••• + 后四位；空密钥不回显掩码。"""
    # 假钥运行时拼装避免扫描误报
    out = config_store.masked({"api_key": "sk-" + "real-key-9876"})
    assert out["api_key"] == "••••••9876"
    assert out["api_key_set"] is True
    empty = config_store.masked({"api_key": ""})
    assert empty["api_key"] == "" and empty["api_key_set"] is False


def test_mask_sentinel_and_echo_preserve_real_key(client):
    pid = _mk_provider(client)
    assert _stored_key(pid) == "sk-real-key-9876"

    # 旧哨兵 ******** → 保留现值
    assert _save_key(client, pid, "********").status_code == 200
    assert _stored_key(pid) == "sk-real-key-9876"

    # GET 回显形态（••••••+后四位）原样回传 → 同样保留现值，不覆盖真实密钥
    echoed = config_store.masked({"api_key": _stored_key(pid)})["api_key"]
    assert echoed.startswith("••••••")
    assert _save_key(client, pid, echoed).status_code == 200
    assert _stored_key(pid) == "sk-real-key-9876"

    # 纯前缀形态（无后四位）也视为掩码
    assert _save_key(client, pid, "••••••").status_code == 200
    assert _stored_key(pid) == "sk-real-key-9876"

    # 真实新密钥 → 正常更新
    assert _save_key(client, pid, "sk-new-0001").status_code == 200
    assert _stored_key(pid) == "sk-new-0001"
