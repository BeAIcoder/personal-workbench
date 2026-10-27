"""批次 agent-store 的 bugfix 回归测试（B1-B10）。

- B1 llm.reasoning_request：按能力契约生成思考参数，max/未知档不擅改，强制思考不可 off
- B2 llm.build_model：anthropic base_url 仅剥结尾 /v1，不得整体小写化
- B3 config_store.masked：长度 ≤4 的 api_key 整体掩码，不得明文回显
- B4 config_store.delete_provider：同事务删除 ProviderModel 行并清理激活态
- B5 secrets_box.seal：明文以 "dpapi:" 开头时也照常加密（可 unseal 还原）
- B6 expert_packs：_quote/_unquote 转义闭环，export→parse 逐字节相等
- B7 expert_packs.import_all：单文件任意异常计入 errors，不中断整批
- B8 expert_packs._to_list：同时按中英文逗号切分
- B9 skill_store：_store.json 原子写 + set_enabled 读-改-写加锁（并发不丢状态）
- B10 expert_packs.export_pack 原子写（写中断不毁旧文件）；
      skill_store.ensure_seed_packs 复制失败清理半成品目录并抛出，重跑可补全

专家包目录统一 monkeypatch 到 tmp_path，不写真实 backend/data 目录；
数据库用 conftest 的临时 SQLite；skill_store 的 store_dir 在用例级指向 tmp_path。
"""
import json
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from app.agents import config_store, expert_packs, llm, secrets_box, skill_store
from app.database import SessionLocal
from app.models import AgentSettings, AgentSpecModel, ModelProvider, ProviderModel


@pytest.fixture(autouse=True, scope="session")
def _isolate_packs_dir_for_startup():
    """会话级隔离：应用启动（lifespan）时的专家包种子复制也落在临时目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="bugfix_agent_store_packs_"))
    original = expert_packs.packs_dir
    expert_packs.packs_dir = lambda: tmp
    yield tmp
    expert_packs.packs_dir = original
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _tmp_packs_dir(tmp_path, monkeypatch):
    """每个用例的专家包目录指向独立 tmp_path。"""
    monkeypatch.setattr(expert_packs, "packs_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def skill_store_dir(tmp_path, monkeypatch):
    """把 skill_store.store_dir 指到 tmp_path/skills。"""
    root = tmp_path / "skills"

    def _store_dir():
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(skill_store, "store_dir", _store_dir)
    return root


# ---------- B1：按模型能力生成思考参数（Task 5 重写：不再 max→xhigh / 未知→medium） ----------

def _reasoning_cfg(**over):
    """能力契约配置：Task 3/4 同步/手写后经 load()/model_cfg_by_id() 进入 cfg 的字段。"""
    base = {
        "protocol": "openai",
        "reasoning_style": "effort",
        "thinking_levels": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        "default_thinking_level": None,
        "thinking_can_disable": True,
    }
    base.update(over)
    return base


def test_b1_reasoning_request_openai_effort_sends_levels_verbatim():
    """openai effort 风格：已知档位原样进 Parameters；max 不经擅改降成 xhigh。"""
    cfg = _reasoning_cfg()
    for level in ("minimal", "low", "medium", "high", "xhigh"):
        kwargs, extra = llm.reasoning_request(cfg, level)
        assert kwargs == {"thinking_enable": True, "reasoning_effort": level}
        assert extra == {}
    # max 不在 AgentScope 的 Literal 白名单：经 extra_body 原样发送，且不与顶层字段重复
    kwargs, extra = llm.reasoning_request(cfg, "max")
    assert kwargs.get("reasoning_effort") is None
    assert extra == {"reasoning_effort": "max"}


def test_b1_reasoning_request_off_and_none_disable_thinking():
    cfg = _reasoning_cfg()
    for off in ("off", "none"):
        kwargs, extra = llm.reasoning_request(cfg, off)
        assert kwargs.get("thinking_enable") is False
        assert kwargs.get("reasoning_effort") is None
        assert extra == {}


def test_b1_reasoning_request_forced_thinking_cannot_be_off():
    """强制思考（thinking_can_disable=False）：off 不被执行，回退跟随模型（声明默认档）。"""
    cfg = _reasoning_cfg(thinking_can_disable=False, default_thinking_level="high")
    assert llm.reasoning_request(cfg, "off") == (
        {"thinking_enable": True, "reasoning_effort": "high"},
        {},
    )
    # 无声明默认档时真正跟随模型：不发任何思考参数（厂商默认即开启）
    assert llm.reasoning_request(_reasoning_cfg(thinking_can_disable=False), "off") == ({}, {})


def test_b1_reasoning_request_invalid_level_falls_back_to_follow_model():
    """非法已存档档位（如 turbo）：不擅改 medium/xhigh，回退跟随模型。"""
    cfg = _reasoning_cfg(default_thinking_level="low")
    assert llm.reasoning_request(cfg, "turbo") == (
        {"thinking_enable": True, "reasoning_effort": "low"},
        {},
    )
    assert llm.reasoning_request(_reasoning_cfg(), "turbo") == ({}, {})


def test_b1_reasoning_request_follow_model_uses_declared_default():
    cfg = _reasoning_cfg(default_thinking_level="high")
    assert llm.reasoning_request(cfg, "") == ({"thinking_enable": True, "reasoning_effort": "high"}, {})
    # 无声明默认档：跟随模型 = 不发送任何思考参数
    assert llm.reasoning_request(_reasoning_cfg(), "") == ({}, {})
    assert llm.reasoning_request(_reasoning_cfg(default_thinking_level="off"), "") == (
        {"thinking_enable": False},
        {},
    )


def test_b1_reasoning_request_toggle_style_sends_thinking_type():
    """toggle 风格（DeepSeek 形态）：thinking{type} 开关，档位不伪装成 effort。"""
    cfg = _reasoning_cfg(reasoning_style="toggle")
    assert llm.reasoning_request(cfg, "high") == ({}, {"thinking": {"type": "enabled"}})
    assert llm.reasoning_request(cfg, "off") == ({}, {"thinking": {"type": "disabled"}})


def test_b1_reasoning_request_budget_style_not_masquerading_as_effort():
    """budget 风格（Qwen/SiliconFlow 形态）：enable_thinking+thinking_budget，不当成 effort 档发送。"""
    cfg = _reasoning_cfg(reasoning_style="budget")
    kwargs, extra = llm.reasoning_request(cfg, "high")
    assert kwargs == {}
    assert "reasoning_effort" not in extra
    assert extra == {"enable_thinking": True, "thinking_budget": 32768}
    assert llm.reasoning_request(cfg, "off") == ({}, {"enable_thinking": False})


def test_b1_reasoning_request_anthropic_effort_native_max():
    """Anthropic 原生支持 max（内部转 output_config.effort）；off 走 thinking 关闭；无法表达的档位不编不猜。"""
    cfg = _reasoning_cfg(protocol="anthropic")
    assert llm.reasoning_request(cfg, "max") == (
        {"thinking_enable": True, "reasoning_effort": "max"},
        {},
    )
    assert llm.reasoning_request(cfg, "off") == (
        {"thinking_enable": False, "thinking_mode": "disabled"},
        {},
    )
    assert llm.reasoning_request(cfg, "minimal") == ({}, {})


def test_b1_build_model_unknown_effort_goes_through_extra_body():
    """build_model：AgentScope 不认识的 max 只出现在 extra_body，顶层 reasoning_effort 不重复。"""
    cfg = {
        "protocol": "openai",
        "api_key": "sk-test",
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        "reasoning_style": "effort",
        "reasoning_effort": "max",
    }
    model = llm.build_model(cfg)
    assert model is not None
    assert model.extra_body == {"reasoning_effort": "max"}
    assert model.parameters.reasoning_effort is None


def test_b1_build_model_passes_session_headers_via_client_kwargs():
    """OpenCode Go 官方要求 UA + x-opencode-session：经 client_kwargs.default_headers 透传。"""
    cfg = {
        "protocol": "openai",
        "api_key": "sk-test",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "model": "kimi-k3.1-pro",
        "preset_key": "opencode_go",
    }
    model = llm.build_model(cfg, session_id="job-7")
    headers = model.client_kwargs["default_headers"]
    assert headers["User-Agent"]
    assert headers["x-opencode-session"] == llm.session_header_value("job-7")
    assert "sk-test" not in str(headers)  # 密钥只走 SDK 鉴权，绝不进自定义头


def test_b1_provider_headers_session_stable_and_keyless():
    """会话头对同一 session 稳定、不同 session 不同；不含 Key；非会话头供应商无自定义头。"""
    cfg = {"preset_key": "opencode_go", "api_key": "sk-synthetic-0001"}
    h1 = llm.provider_headers(cfg, session_id="job-7")
    h2 = llm.provider_headers(cfg, session_id="job-7")
    assert h1 == h2
    assert h1["x-opencode-session"] and h1["User-Agent"]
    assert llm.provider_headers(cfg, session_id="job-8")["x-opencode-session"] != h1["x-opencode-session"]
    assert "sk-synthetic-0001" not in str(h1) and "Authorization" not in h1
    # 无会话上下文：进程级稳定默认值（路由等内部调用同样稳定）
    assert llm.provider_headers(cfg) == llm.provider_headers(cfg)
    assert llm.provider_headers({"preset_key": "deepseek"}, session_id="job-7") == {}
    assert llm.provider_headers({}, session_id="job-7") == {}


def test_b1_needs_session_headers_only_for_session_header_presets():
    assert llm.needs_session_headers({"preset_key": "opencode_go"}) is True
    assert llm.needs_session_headers({"preset_key": "deepseek"}) is False
    assert llm.needs_session_headers({}) is False


# ---------- B1 fix round 1：声明集门控 / anthropic toggle ----------

def test_b1_reasoning_request_undeclared_level_falls_back_to_declared_default():
    """模型已声明档位时，声明集之外的 canonical 档位（如 max）不原样发送，回退声明默认/跟随。"""
    cfg = _reasoning_cfg(thinking_levels=["low", "medium", "high"], default_thinking_level="medium")
    assert llm.reasoning_request(cfg, "max") == (
        {"thinking_enable": True, "reasoning_effort": "medium"},
        {},
    )
    # 无声明默认档：真正跟随模型，不得把 max 原样发给供应商
    assert llm.reasoning_request(_reasoning_cfg(thinking_levels=["low", "medium", "high"]), "max") == ({}, {})
    # 未声明档位（空列表）：保持既有兼容行为，max 原样走 extra_body
    assert llm.reasoning_request(_reasoning_cfg(thinking_levels=[]), "max") == (
        {"thinking_enable": True},
        {"reasoning_effort": "max"},
    )
    # off 不受声明集门控（由 thinking_can_disable 单独约束）
    assert llm.reasoning_request(cfg, "off") == ({"thinking_enable": False}, {})


def test_b1_step_model_saving_max_falls_back(client):
    """Step（step-3.7-flash 声明 low/medium/high）下把思考深度存成 max：不原样发送。"""
    pid = client.post(
        "/api/assistant/providers",
        json={
            "name": "阶跃",
            "protocol": "openai",
            "base_url": "https://api.stepfun.com/v1",
            "api_key": "sk-step-test",
            "preset_key": "stepfun",
        },
    ).json()["provider"]["id"]
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "step-3.7-flash", "thinking_levels": ["low", "medium", "high"]},
    ).json()["model"]["id"]
    client.put("/api/assistant/config", json={"reasoning_effort": "max"})
    client.post(f"/api/assistant/models/{mid}/activate")

    cfg = config_store.load()
    assert cfg["reasoning_effort"] == "max"
    assert cfg["thinking_levels"] == ["low", "medium", "high"]
    # 未声明默认档 → 跟随模型（不发任何思考参数），max 不原样出厂
    assert llm.reasoning_request(cfg, cfg["reasoning_effort"]) == ({}, {})

    # 声明默认档 high 后：回退到声明默认档，仍不发送 max
    client.post(
        f"/api/assistant/providers/{pid}/models",
        json={
            "model_id": mid,
            "model": "step-3.7-flash",
            "thinking_levels": ["low", "medium", "high"],
            "default_thinking_level": "high",
        },
    )
    cfg2 = config_store.load()
    assert llm.reasoning_request(cfg2, cfg2["reasoning_effort"]) == (
        {"thinking_enable": True, "reasoning_effort": "high"},
        {},
    )


def test_b1_reasoning_request_anthropic_toggle_style_uses_thinking_mode():
    """anthropic + toggle 风格：映射 thinking_enable/thinking_mode，不得误走 effort。"""
    cfg = _reasoning_cfg(protocol="anthropic", reasoning_style="toggle")
    assert llm.reasoning_request(cfg, "high") == (
        {"thinking_enable": True, "thinking_mode": "enabled"},
        {},
    )
    assert llm.reasoning_request(cfg, "off") == (
        {"thinking_enable": False, "thinking_mode": "disabled"},
        {},
    )


# ---------- B2：anthropic base_url 小写化 ----------

def test_b2_anthropic_base_url_strips_v1_preserving_case():
    cfg = {
        "protocol": "anthropic",
        "api_key": "sk-test",
        "base_url": "https://Gateway.Example.COM:8443/Anthropic/v1",
        "model": "claude-test",
        "max_tokens": 100,
    }
    model = llm.build_model(cfg)
    assert model.credential.base_url == "https://Gateway.Example.COM:8443/Anthropic"
    # 大写 /V1 尾巴同样剥离（大小写不敏感），其余部分原样保留
    cfg2 = {**cfg, "base_url": "https://Gw.Example.COM/API/V1/"}
    assert llm.build_model(cfg2).credential.base_url == "https://Gw.Example.COM/API"
    # 非 /v1 结尾原样不动
    cfg3 = {**cfg, "base_url": "https://Gw.Example.COM/API"}
    assert llm.build_model(cfg3).credential.base_url == "https://Gw.Example.COM/API"


# ---------- B3：masked() 短 key 明文 ----------

def test_b3_masked_short_api_key_fully_masked():
    out = config_store.masked({"api_key": "abc"})
    assert "abc" not in out["api_key"]
    assert out["api_key"] == config_store.MASK_PREFIX
    out4 = config_store.masked({"api_key": "abcd"})
    assert "abcd" not in out4["api_key"]
    assert out4["api_key"] == config_store.MASK_PREFIX
    # 对照：长密钥保持既有行为（掩码前缀 + 后四位）
    assert config_store.masked({"api_key": "sk-real-key-9876"})["api_key"] == config_store.MASK_PREFIX + "9876"


# ---------- B4：delete_provider 不删 ProviderModel 行 ----------

def test_b4_delete_provider_removes_models_and_clears_active(client):
    pid = config_store.save_provider({"name": "P1", "protocol": "openai", "base_url": "https://x.com/v1"})["id"]
    mid = config_store.save_provider_model({"provider_id": pid, "model": "m-1"})["id"]
    config_store.save_provider_model({"provider_id": pid, "model": "m-2"})
    config_store.activate_model(mid)

    config_store.delete_provider(pid)

    with SessionLocal() as db:
        assert db.get(ModelProvider, pid) is None
        # 供应商下的模型行必须一并删除（不得留下孤儿数据）
        assert db.query(ProviderModel).filter(ProviderModel.provider_id == pid).all() == []
        # 激活模型属于被删供应商：激活态清理
        assert db.get(AgentSettings, 1).active_model_id is None


def test_b4_delete_provider_no_orphan_models_after_rowid_reuse(client):
    """SQLite rowid 复用场景：删供应商后新建供应商不得挂上旧供应商的模型。"""
    pid1 = config_store.save_provider({"name": "P1"})["id"]
    config_store.save_provider_model({"provider_id": pid1, "model": "legacy-model"})
    config_store.delete_provider(pid1)

    pid2 = config_store.save_provider({"name": "P2"})["id"]
    providers = {p["id"]: p for p in config_store.list_providers()}
    assert providers[pid2]["models"] == []


# ---------- B5：seal() 前缀幂等 bug ----------

def test_b5_seal_dpapi_prefixed_plaintext_roundtrip():
    plain = "dpapi:realkey"
    sealed = secrets_box.seal(plain)
    assert secrets_box.unseal(sealed) == plain


def test_b5_seal_normal_key_roundtrip():
    """对照组：常规 key 往返（修复前后都应通过）。"""
    assert secrets_box.unseal(secrets_box.seal("sk-normal-key-123")) == "sk-normal-key-123"


# ---------- B6：专家包导出→导入转义不闭环 ----------

TRICKY_DESCRIPTION = '描述含 "双引号" 与 \\反斜杠\\ 混合，结尾再跟一个 \\'


def test_b6_export_import_escaping_roundtrip_byte_identical(client, tmp_path):
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "leasing").one()
        row.description = TRICKY_DESCRIPTION
        db.commit()
        expert_packs.export_pack("leasing", db)

    # 导出后再解析：description 逐字节相等
    parsed = expert_packs.parse_pack_file(tmp_path / "leasing.md")
    assert parsed["description"] == TRICKY_DESCRIPTION

    # 连续两轮 export→import 仍逐字节相等（每轮不得多一层反斜杠）
    for _ in range(2):
        with SessionLocal() as db:
            expert_packs.import_pack("leasing", db)
            row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "leasing").one()
            assert row.description == TRICKY_DESCRIPTION
            expert_packs.export_pack("leasing", db)
        assert expert_packs.parse_pack_file(tmp_path / "leasing.md")["description"] == TRICKY_DESCRIPTION


# ---------- B7：import_all 单文件异常中断整批 ----------

def _pack_text(name: str, label: str) -> str:
    return f"---\nname: {name}\nlabel: {label}\n---\n\n{label}的正文。\n"


def test_b7_import_all_continues_after_unexpected_exception(client, tmp_path, monkeypatch):
    (tmp_path / "a_good.md").write_text(_pack_text("a_good", "甲"), encoding="utf-8")
    (tmp_path / "bomb.md").write_text(_pack_text("bomb", "炸弹"), encoding="utf-8")
    (tmp_path / "z_good.md").write_text(_pack_text("z_good", "乙"), encoding="utf-8")

    real_import = expert_packs.import_pack

    def flaky_import(name, db):
        if name == "bomb":
            raise RuntimeError("模拟数据库异常")
        return real_import(name, db)

    monkeypatch.setattr(expert_packs, "import_pack", flaky_import)
    with SessionLocal() as db:
        result = expert_packs.import_all(db)

    # 炸弹包计入 errors，其余包照常导入
    assert result["imported"] == ["a_good", "z_good"]
    assert [e["file"] for e in result["errors"]] == ["bomb.md"]
    assert "模拟数据库异常" in result["errors"][0]["error"]
    with SessionLocal() as db:
        assert db.query(AgentSpecModel).filter(AgentSpecModel.name == "a_good").one_or_none() is not None
        assert db.query(AgentSpecModel).filter(AgentSpecModel.name == "z_good").one_or_none() is not None
        assert db.query(AgentSpecModel).filter(AgentSpecModel.name == "bomb").one_or_none() is None


# ---------- B8：_to_list 只按英文逗号切分 ----------

def test_b8_to_list_splits_chinese_and_english_commas():
    assert expert_packs._to_list("发票，报销") == ["发票", "报销"]
    assert expert_packs._to_list("发票,报销，税务") == ["发票", "报销", "税务"]
    assert expert_packs._to_list("[发票，报销]") == ["发票", "报销"]


# ---------- B9：_store.json 非原子写 + 启停无锁 ----------

def test_b9_concurrent_set_enabled_no_lost_update(skill_store_dir):
    """两个包并发停用：读-改-写必须整体互斥，终态两个包都在停用列表。"""
    (skill_store_dir / "pack-a").mkdir(parents=True)
    (skill_store_dir / "pack-b").mkdir(parents=True)
    store_json = skill_store_dir / "_store.json"

    for round_no in range(30):
        store_json.write_text('{"disabled": []}', encoding="utf-8")  # 每轮从「全部启用」开始
        barrier = threading.Barrier(3)

        def worker(pack_name):
            barrier.wait()
            skill_store.set_enabled(pack_name, False)

        threads = [
            threading.Thread(target=worker, args=("pack-a",)),
            threading.Thread(target=worker, args=("pack-b",)),
        ]
        for t in threads:
            t.start()
        barrier.wait()
        for t in threads:
            t.join()

        disabled = sorted(skill_store._load_disabled())
        assert disabled == ["pack-a", "pack-b"], f"第 {round_no} 轮并发停用丢状态：{disabled}"
        # 文件本身必须是合法 JSON（原子写，不留半截）
        assert isinstance(json.loads(store_json.read_text(encoding="utf-8")), dict)


# ---------- B10：export_pack 原子写 + ensure_seed_packs 半成品清理 ----------

def test_b10_export_pack_write_failure_keeps_existing_file(client, tmp_path, monkeypatch):
    """导出写入中断：已存在的同名文件保持原样，不留半截内容或临时文件。"""
    target = tmp_path / "leasing.md"
    target.write_text("旧版本内容", encoding="utf-8")
    real_write_text = Path.write_text

    def flaky_write_text(self, data, *args, **kwargs):
        real_write_text(self, "半截写入", encoding="utf-8")
        raise OSError("模拟写入中断")

    monkeypatch.setattr(Path, "write_text", flaky_write_text)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="导出"):
            expert_packs.export_pack("leasing", db)

    assert target.read_text(encoding="utf-8") == "旧版本内容"
    assert list(tmp_path.glob("*.tmp")) == []


def test_b10_seed_copy_failure_cleans_partial_dir_and_rerun_completes(skill_store_dir, monkeypatch):
    """copytree 中断：半成品目录清理并抛错；恢复后重跑能补全种子。"""
    real_copytree = shutil.copytree
    state = {"fail": True}

    def flaky_copytree(src, dst, *args, **kwargs):
        if state["fail"] and Path(str(src)).name == "month-end-close":
            dst = Path(dst)
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "SKILL.md").write_text("半截复制", encoding="utf-8")
            raise OSError("模拟复制中断")
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(skill_store.shutil, "copytree", flaky_copytree)
    with pytest.raises(OSError):
        skill_store.ensure_seed_packs()
    # 半成品目录已清理（否则后续运行因「已存在」永久跳过，种子永远残缺）
    assert not (skill_store_dir / "month-end-close").exists()

    state["fail"] = False
    copied = skill_store.ensure_seed_packs()
    assert "month-end-close" in copied
    text = (skill_store_dir / "month-end-close" / "SKILL.md").read_text(encoding="utf-8")
    assert "半截复制" not in text


# ---------- B11：supports_thinking == 0 防御（Task 6 fix round 1） ----------

def test_b11_supports_thinking_zero_never_sends_thinking_params():
    """明确不支持思考（supports_thinking=0）：任何档位/风格/协议都不发思考参数。
    目录或手动字段可能与探测结论不一致（如目录标了档位但实测不支持），此处兜底。"""
    cfg = _reasoning_cfg(supports_thinking=0, default_thinking_level="high", thinking_levels=["low", "high"])
    for level in ("", "default", "off", "none", "low", "medium", "high", "xhigh", "max", "turbo"):
        assert llm.reasoning_request(cfg, level) == ({}, {}), level
        assert llm._resolve_thinking_level(cfg, level) is None, level
    # 字符串 "0" 同样防御（JSON/表单可能传字符串）
    assert llm.reasoning_request(_reasoning_cfg(supports_thinking="0"), "high") == ({}, {})
    # 未显式传 effort（读 cfg.reasoning_effort）也被防御
    assert llm.reasoning_request(dict(cfg, reasoning_effort="high"), None) == ({}, {})
    # anthropic 协议同样不发
    acfg = _reasoning_cfg(protocol="anthropic", supports_thinking=0, default_thinking_level="high")
    assert llm.reasoning_request(acfg, "high") == ({}, {})
    assert llm.reasoning_request(acfg, "off") == ({}, {})
    # toggle/budget 风格同样被防御
    assert llm.reasoning_request(_reasoning_cfg(reasoning_style="toggle", supports_thinking=0), "high") == ({}, {})
    assert llm.reasoning_request(_reasoning_cfg(reasoning_style="budget", supports_thinking=0), "high") == ({}, {})

    # 未知（-1）与缺省不受阻：保持既有兼容行为
    assert llm.reasoning_request(_reasoning_cfg(supports_thinking=-1), "high") == ({"thinking_enable": True, "reasoning_effort": "high"}, {})
    assert llm.reasoning_request(_reasoning_cfg(), "high") == ({"thinking_enable": True, "reasoning_effort": "high"}, {})
    assert llm.reasoning_request(_reasoning_cfg(supports_thinking=None), "off") == ({"thinking_enable": False}, {})
