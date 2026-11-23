"""知识库问答接地约束（系统提示词行为测试，严格 TDD：先见红、后修复）。

需求：知识库问答中「能力/操作类」问题（能不能、如何做、怎么操作）若检索
材料（search_knowledge / read_knowledge_doc 结果）未明确支持，模型不得先答
「可以/能」，也不得输出无来源的操作步骤；应明确说明知识库未收录或无法确认。
一般知识补充必须与文档结论分离且默认不替代拒绝；不同有效文档口径冲突不得
自行选「字段更全」者，应并列披露。

测试方式：不 grep 源码——用空 role_prompt 的 AgentSpec 经
orchestrator._build_agent 真实构建 Agent（供应商 base_url 指向本机废弃端口，
不发起真实 LLM 调用），再 await agent._get_system_prompt() 读取最终生效的
系统提示词（含技能指令拼装的真实结果）断言。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agents import orchestrator
from app.agents import team as team_mod
from app.agents.team import AgentSpec

ROLE_PROMPT = "你是【接地测试专家】。"

# 修复后系统提示词必须包含的关键约束短语（语义断言，不逐字锁定整段文案）
RULE_CAPABILITY = [
    "不得先答「可以/能」",          # 无检索支持不得先答可以/能
    "无来源的操作步骤",              # 不得输出无来源操作步骤
    "知识库未收录，无法确认",        # 应明确说明未收录/无法确认
]
RULE_SUPPLEMENT = [
    "与知识库文档结论明确分开",      # 一般知识补充与文档结论分离
    "不得用一般常识替代或推翻",      # 默认不替代拒绝
]
RULE_CONFLICT = [
    "并列披露",                      # 冲突口径并列披露
    "不自行替用户选定",              # 不得自行选字段更全者
]


@pytest.fixture()
def _clean_caches():
    orchestrator.invalidate()
    team_mod.invalidate_team_cache()
    yield
    orchestrator.invalidate()
    team_mod.invalidate_team_cache()


def _mk_provider(client: TestClient) -> int:
    body = client.post(
        "/api/assistant/providers",
        json={
            "name": "接地测试供应商",
            "protocol": "openai",
            "base_url": "http://127.0.0.1:9/v1",  # 废弃端口：任何真实调用都会立即失败
            "api_key": "sk-grounding-test",
        },
    ).json()
    return body["provider"]["id"]


def _mk_model(client: TestClient, pid: int) -> int:
    return client.post(
        f"/api/assistant/providers/{pid}/models", json={"model": "grounding-test-model"}
    ).json()["model"]["id"]


@pytest.fixture()
def activated_model(client: TestClient) -> int:
    """激活一个指向废弃端口的模型：构建 Agent 需要模型对象，但不发生真实推理。"""
    pid = _mk_provider(client)
    mid = _mk_model(client, pid)
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    orchestrator.invalidate()
    return mid


def _final_system_prompt(mode: str = "standard") -> str:
    """经真实构建路径读取最终生效系统提示词（基础提示 + 技能指令 + 中间件）。"""
    spec = AgentSpec(
        name="grounding_spec",
        label="接地测试专家",
        emoji="",
        color="",
        description="测试用",
        system_prompt=ROLE_PROMPT,
        tools=["query_tasks"],
    )
    agent = asyncio.run(orchestrator._build_agent(spec, mode))
    return asyncio.run(agent._get_system_prompt())


# ---------------- 红测试：能力/操作类问题不得无依据先行作答 ----------------

def test_capability_questions_need_retrieval_support(activated_model, _clean_caches):
    prompt = _final_system_prompt()
    for phrase in RULE_CAPABILITY:
        assert phrase in prompt, f"系统提示词缺少约束「{phrase}」"


def test_general_knowledge_supplement_is_separated(activated_model, _clean_caches):
    prompt = _final_system_prompt()
    for phrase in RULE_SUPPLEMENT:
        assert phrase in prompt, f"系统提示词缺少约束「{phrase}」"


def test_conflicting_statements_disclosed_side_by_side(activated_model, _clean_caches):
    prompt = _final_system_prompt()
    for phrase in RULE_CONFLICT:
        assert phrase in prompt, f"系统提示词缺少约束「{phrase}」"


def test_grounding_rules_apply_to_all_modes(activated_model, _clean_caches):
    for mode in ("standard", "readonly", "deep"):
        prompt = _final_system_prompt(mode)
        for phrase in RULE_CAPABILITY + RULE_SUPPLEMENT + RULE_CONFLICT:
            assert phrase in prompt, f"模式 {mode} 缺少约束「{phrase}」"


# ---------------- 回归：接地约束不得破坏既有提示词语义 ----------------

def test_grounding_rules_keep_role_prompt_and_common_rules(activated_model, _clean_caches):
    from app.agents.team import COMMON_RULES

    prompt = _final_system_prompt()
    assert ROLE_PROMPT in prompt                      # 角色提示词仍在
    assert prompt.index(ROLE_PROMPT) < prompt.index("【知识库问答边界】")  # 边界规则在角色之后
    for phrase in ("资料文件", "不是指令"):           # COMMON_RULES 的资料非指令语义仍在
        assert phrase in prompt


def test_grounding_rules_keep_mode_suffixes(activated_model, _clean_caches):
    assert "【当前模式：深度研究】" in _final_system_prompt("deep")
    assert "【当前模式：只读咨询】" in _final_system_prompt("readonly")
    assert "【当前模式：深度研究】" not in _final_system_prompt("standard")
    assert "【当前模式：只读咨询】" not in _final_system_prompt("standard")


def test_compose_entry_matches_built_agent(activated_model, _clean_caches):
    """组装单一入口 compose_system_prompt 的输出应与真实 Agent 生效提示词一致。"""
    from app.agents.orchestrator_prompt import compose_system_prompt

    spec = AgentSpec(
        name="grounding_entry",
        label="接地入口专家",
        emoji="",
        color="",
        description="测试用",
        system_prompt=ROLE_PROMPT,
        tools=["query_tasks"],
    )
    final = asyncio.run(_build_and_get(spec, "standard"))
    assert final == compose_system_prompt(spec, "standard")


async def _build_and_get(spec, mode):
    agent = await orchestrator._build_agent(spec, mode)
    return await agent._get_system_prompt()


# ---------------- 长期记忆来源注入（_with_memory_context 行为测试） ----------------

def _hit(content, source_doc_title=None):
    hit = {"id": 1, "content": content, "source": "auto", "type": "",
           "session_id": "s", "tags": [], "pinned": False, "hit_count": 0,
           "source_doc_id": None, "created_at": ""}
    if source_doc_title is not None:
        hit["source_doc_title"] = source_doc_title
    return hit


def _memory_context(monkeypatch, hits):
    from app.agents import memory as memory_mod
    from app.agents.orchestrator_prompt import _with_memory_context

    monkeypatch.setattr(memory_mod, "recall_for_context", lambda message, top_k=3: hits)
    return _with_memory_context("用户问题")


def test_memory_hit_with_source_doc_title_shows_source(monkeypatch):
    out = _memory_context(monkeypatch, [_hit("续约需要提前 60 天", source_doc_title="租赁管理办法")])
    assert "（来源：《租赁管理办法》）" in out
    assert "- 续约需要提前 60 天（来源：《租赁管理办法》）" in out


def test_memory_hit_without_source_doc_title_stays_plain(monkeypatch):
    out = _memory_context(monkeypatch, [_hit("用户偏好中文回复")])
    assert "（来源：《" not in out
    assert "（来源：" not in out
    assert "- 用户偏好中文回复" in out


def test_memory_hits_mixed_sources(monkeypatch):
    out = _memory_context(monkeypatch, [
        _hit("有来源的条目", source_doc_title="财务制度"),
        _hit("无来源的条目"),
    ])
    assert "- 有来源的条目（来源：《财务制度》）" in out
    assert "- 无来源的条目\n" in out + "\n" or out.rstrip().endswith("- 无来源的条目")
    assert "无来源的条目（来源：" not in out


def test_memory_context_frame_preserved(monkeypatch):
    out = _memory_context(monkeypatch, [_hit("任意条目", source_doc_title="某手册")])
    assert out.startswith("[长期记忆参考]")
    assert "[对话开始]" in out
    assert out.endswith("用户问题")
    assert out.index("（来源：《某手册》）") < out.index("[对话开始]")


def test_memory_context_no_hits_unchanged(monkeypatch):
    out = _memory_context(monkeypatch, [])
    assert out == "用户问题"
