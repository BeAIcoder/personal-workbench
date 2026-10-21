"""技能包 store 测试：seed 幂等、扫描校验、启停持久化、新建/删除、防穿越、HTTP 端点，
以及停用过滤到 Agent 技能注入（disabled_packs 读取与 orchestrator loader 过滤）。

store_dir 统一 monkeypatch 到 tmp_path（保留「不存在则创建」的真实行为），
不写真实 data 目录；种子来源仍为仓库内的 backend/seed_skills（只读）。
本模块的 client 夹具在 conftest 版本之上先把 store_dir 指向 tmp_path，
保证 lifespan 里的 ensure_seed_packs 也只落临时目录。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.agents import skill_store
from app.main import app


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """把 store_dir 指到 tmp_path/skills。"""
    root = tmp_path / "skills"

    def _store_dir():
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(skill_store, "store_dir", _store_dir)
    return root


@pytest.fixture()
def client(store):
    """TestClient（lifespan 启动时的种子播种同样落在 tmp store）。"""
    with TestClient(app) as c:
        yield c


def _write_pack(root, dir_name, skill_md=None, extra=None):
    """在 store 里手工放一个技能包目录（skill_md 为 None 时不建 SKILL.md）。"""
    pack = root / dir_name
    pack.mkdir(parents=True)
    if skill_md is not None:
        (pack / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for rel, content in (extra or {}).items():
        target = pack / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return pack


# ---------- 种子播种 ----------

def test_ensure_seed_packs_copies_once_and_is_idempotent(store):
    copied = skill_store.ensure_seed_packs()
    assert sorted(copied) == ["invoice-review", "month-end-close"]
    assert (store / "month-end-close" / "SKILL.md").is_file()
    assert (store / "invoice-review" / "SKILL.md").is_file()
    # 二次执行不复制，也不覆盖用户在 store 里的改动
    (store / "month-end-close" / "SKILL.md").write_text("用户改动", encoding="utf-8")
    assert skill_store.ensure_seed_packs() == []
    assert (store / "month-end-close" / "SKILL.md").read_text(encoding="utf-8") == "用户改动"


# ---------- 扫描与校验 ----------

def test_scan_packs_valid_invalid_files_and_hidden(store):
    _write_pack(
        store,
        "good-pack",
        "---\nname: good-pack\ndescription: 好用\nversion: 2.1.0\n---\n\n# 正文\n",
        extra={"notes/a.md": "A", "ref.txt": "B"},
    )
    _write_pack(store, "bad-yaml", "---\nname: [unclosed\n---\nbody\n")
    _write_pack(store, "no-name", "---\ndescription: 缺 name\n---\nbody\n")
    _write_pack(store, "plain", "# 没有 frontmatter 的正文\n")
    _write_pack(store, "no-skill-md")
    (store / ".hidden").mkdir()
    (store / "_store.json").write_text('{"disabled": []}', encoding="utf-8")

    packs = {p["dir"]: p for p in skill_store.scan_packs()}
    assert set(packs) == {"good-pack", "bad-yaml", "no-name", "plain", "no-skill-md"}

    good = packs["good-pack"]
    assert good["valid"] is True and good["error"] == ""
    assert good["name"] == "good-pack" and good["version"] == "2.1.0"
    assert good["description"] == "好用" and good["enabled"] is True
    assert good["files"] == ["notes/a.md", "ref.txt"]

    # 坏 frontmatter / 缺 name / 无 frontmatter / 缺 SKILL.md：标记无效但不抛异常
    assert packs["bad-yaml"]["valid"] is False and "解析失败" in packs["bad-yaml"]["error"]
    assert packs["no-name"]["valid"] is False and "name" in packs["no-name"]["error"]
    assert packs["plain"]["valid"] is False and "frontmatter" in packs["plain"]["error"]
    assert packs["no-skill-md"]["valid"] is False and packs["no-skill-md"]["error"] == "缺少 SKILL.md"
    # 无效包 name 回退为目录名，仍可被管理
    assert packs["bad-yaml"]["name"] == "bad-yaml"


def test_store_json_corrupt_tolerated(store):
    _write_pack(store, "demo", "---\nname: demo\ndescription: d\n---\n")
    (store / "_store.json").write_text("坏 JSON", encoding="utf-8")
    assert skill_store.scan_packs()[0]["enabled"] is True  # 按无停用记录处理，不抛异常
    skill_store.set_enabled("demo", False)  # 可恢复写入
    assert json.loads((store / "_store.json").read_text(encoding="utf-8")) == {"disabled": ["demo"]}


# ---------- 启停持久化 ----------

def test_set_enabled_persists_across_rescan(store):
    _write_pack(store, "demo", "---\nname: demo\ndescription: d\n---\n")
    assert skill_store.set_enabled("demo", False) == {"name": "demo", "enabled": False}
    assert json.loads((store / "_store.json").read_text(encoding="utf-8")) == {"disabled": ["demo"]}
    assert skill_store.scan_packs()[0]["enabled"] is False
    # 重新扫描后停用状态仍在（读的是 _store.json）
    assert skill_store.scan_packs()[0]["enabled"] is False
    assert skill_store.set_enabled("demo", True)["enabled"] is True
    assert json.loads((store / "_store.json").read_text(encoding="utf-8")) == {"disabled": []}
    # 重复停用不产生重复项
    skill_store.set_enabled("demo", False)
    skill_store.set_enabled("demo", False)
    assert json.loads((store / "_store.json").read_text(encoding="utf-8")) == {"disabled": ["demo"]}
    with pytest.raises(LookupError):
        skill_store.set_enabled("ghost", False)


# ---------- 新建 / 删除 ----------

def test_create_and_delete_pack(store):
    pack = skill_store.create_pack("tax-check", "税负核对", "自定义正文")
    assert pack["valid"] is True and pack["name"] == "tax-check" and pack["version"] == "1.0.0"
    text = (store / "tax-check" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: tax-check" in text and 'description: "税负核对"' in text and "自定义正文" in text
    # 正文留空用内置骨架
    skill_store.create_pack("empty-body", "描述")
    assert "使用场景" in (store / "empty-body" / "SKILL.md").read_text(encoding="utf-8")
    # 重名拒绝
    with pytest.raises(ValueError):
        skill_store.create_pack("tax-check", "x")
    # 非法名称一律拒绝（大写/空格/中文/空/穿越/下划线开头/连字符开头）
    for bad in ("Bad Name", "中文名", "", "../evil", "_hidden", "-lead"):
        with pytest.raises(ValueError):
            skill_store.create_pack(bad, "x")
    # 空描述拒绝
    with pytest.raises(ValueError):
        skill_store.create_pack("no-desc", "  ")
    # 描述是自由文本：含「: 」/ # / 换行 / 引号 / 反斜杠时 frontmatter 仍必须合法，
    # 且扫描结果里的描述与输入逐字一致（YAML 双引号标量序列化回归）
    tricky = '报销审核: 需核对抬头 #1\n第二行 "引号" 与 C:\\temp'
    pack = skill_store.create_pack("tricky-desc", tricky, "正文")
    assert pack["valid"] is True, pack.get("error")
    assert pack["description"] == tricky
    # 删除：目录与停用记录一并清理
    skill_store.set_enabled("tax-check", False)
    skill_store.delete_pack("tax-check")
    assert not (store / "tax-check").exists()
    assert json.loads((store / "_store.json").read_text(encoding="utf-8"))["disabled"] == []
    with pytest.raises(LookupError):
        skill_store.delete_pack("tax-check")
    # 删除接口同样防目录穿越
    with pytest.raises(ValueError):
        skill_store.delete_pack("..")


# ---------- 文件读取与防穿越 ----------

def test_read_pack_file_and_traversal(store):
    _write_pack(
        store,
        "reader",
        "---\nname: reader\ndescription: d\n---\n正文",
        extra={"sub/deep.md": "深层文件"},
    )
    assert skill_store.read_pack_file("reader", "SKILL.md").startswith("---")
    assert skill_store.read_pack_file("reader", "sub/deep.md") == "深层文件"
    for bad in ("../_store.json", "../../etc/passwd", "/etc/passwd", "..\\..\\x", ""):
        with pytest.raises(ValueError):
            skill_store.read_pack_file("reader", bad)
    with pytest.raises(LookupError):
        skill_store.read_pack_file("reader", "missing.md")
    with pytest.raises(LookupError):
        skill_store.read_pack_file("ghost", "SKILL.md")
    with pytest.raises(ValueError):
        skill_store.read_pack_file("..", "SKILL.md")


# ---------- 停用过滤到 Agent 技能注入（P0-1） ----------

def test_disabled_packs_reads_store_json(store):
    """disabled_packs：默认读 store 根目录；指定目录有 _store.json 以它为准，否则回退。"""
    _write_pack(store, "demo", "---\nname: demo\ndescription: d\n---\n")
    assert skill_store.disabled_packs() == []
    skill_store.set_enabled("demo", False)
    assert skill_store.disabled_packs() == ["demo"]
    assert skill_store.disabled_packs(store) == ["demo"]
    # 自定义目录无 _store.json：回退 store 根目录的停用记录
    other = store.parent / "other-pool"
    other.mkdir()
    assert skill_store.disabled_packs(other) == ["demo"]
    # 自定义目录自带 _store.json：以它为准
    (other / "_store.json").write_text('{"disabled": ["custom-off"]}', encoding="utf-8")
    assert skill_store.disabled_packs(other) == ["custom-off"]
    # 损坏文件按空列表处理
    (other / "_store.json").write_text("坏 JSON", encoding="utf-8")
    assert skill_store.disabled_packs(other) == []


def test_disabled_pack_filtered_from_agent_skill_injection(store):
    """停用开关对 Agent 注入生效：loader 与 Toolkit 只返回启用中的技能包。"""
    import asyncio

    from agentscope.tool import Toolkit

    from app.agents import orchestrator
    from app.agents.team import TEAM

    _write_pack(store, "alpha", "---\nname: alpha\ndescription: A\n---\n正文A")
    _write_pack(store, "beta", "---\nname: beta\ndescription: B\n---\n正文B")
    assert skill_store.set_enabled("beta", False)["enabled"] is False

    cfg = {"enable_skills": True, "skills_dir": str(store)}
    loaders = asyncio.run(orchestrator._resolve_skill_loaders(cfg, TEAM["realestate"]))
    assert loaders  # 仍有启用中的包：返回 loader 列表
    assert {s.name for s in asyncio.run(loaders[0].list_skills())} == {"alpha"}
    # 经 Toolkit 汇总的技能同样不含停用包（Agent 实际注入路径）
    toolkit = Toolkit(skills_or_loaders=loaders)
    assert set(asyncio.run(toolkit._get_available_skills())) == {"alpha"}


def test_expert_skills_empty_list_disables_injection(store):
    """专家级 skills=[] 显式关闭技能池；None 跟随全局；全局关闭/目录缺失不注入。"""
    import asyncio

    from app.agents import orchestrator
    from app.agents.team import AgentSpec

    _write_pack(store, "alpha", "---\nname: alpha\ndescription: A\n---\n正文")
    cfg = {"enable_skills": True, "skills_dir": str(store)}
    spec_off = AgentSpec(name="x", label="X", emoji="", color="", description="", system_prompt="", skills=[])
    spec_follow = AgentSpec(name="y", label="Y", emoji="", color="", description="", system_prompt="", skills=None)
    assert asyncio.run(orchestrator._resolve_skill_loaders(cfg, spec_off)) is None
    assert asyncio.run(orchestrator._resolve_skill_loaders(cfg, spec_follow))
    assert asyncio.run(orchestrator._resolve_skill_loaders({"enable_skills": False, "skills_dir": str(store)}, spec_follow)) is None
    assert asyncio.run(orchestrator._resolve_skill_loaders({"enable_skills": True, "skills_dir": ""}, spec_follow)) is None
    assert asyncio.run(orchestrator._resolve_skill_loaders({"enable_skills": True, "skills_dir": str(store / "missing")}, spec_follow)) is None


# ---------- HTTP 端点 ----------

def test_http_packs_list_includes_seeds(client):
    body = client.get("/api/skills/packs").json()
    names = {p["name"] for p in body["items"]}
    assert {"month-end-close", "invoice-review"} <= names


def test_http_pack_lifecycle(client):
    resp = client.post("/api/skills/packs", json={"name": "http-demo", "description": "HTTP 测试包", "body": "正文"})
    assert resp.status_code == 201
    pack = resp.json()
    assert pack["name"] == "http-demo" and pack["valid"] is True
    # 重名与非法名
    assert client.post("/api/skills/packs", json={"name": "http-demo", "description": "x"}).status_code == 400
    assert client.post("/api/skills/packs", json={"name": "Bad Name", "description": "x"}).status_code == 400
    # 清单里能查到
    items = client.get("/api/skills/packs").json()["items"]
    assert any(p["dir"] == "http-demo" for p in items)
    # 启停
    resp = client.put("/api/skills/packs/http-demo/enabled", json={"enabled": False})
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    items = client.get("/api/skills/packs").json()["items"]
    assert next(p for p in items if p["dir"] == "http-demo")["enabled"] is False
    assert client.put("/api/skills/packs/ghost/enabled", json={"enabled": False}).status_code == 404
    # 读文件
    resp = client.get("/api/skills/packs/http-demo/files/SKILL.md")
    assert resp.status_code == 200 and "http-demo" in resp.json()["content"]
    assert client.get("/api/skills/packs/http-demo/files/missing.md").status_code == 404
    # 防穿越：不得返回 store 根的状态文件内容
    resp = client.get("/api/skills/packs/http-demo/files/..%2F..%2F_store.json")
    assert resp.status_code in (400, 404) and "_store.json" not in resp.text
    # 删除
    assert client.delete("/api/skills/packs/http-demo").status_code == 200
    assert client.delete("/api/skills/packs/http-demo").status_code == 404


def test_http_rescan_idempotent(client):
    resp = client.post("/api/skills/rescan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["seeded"] == []  # 启动时已播种，再次扫描不重复复制
    assert any(p["name"] == "month-end-close" for p in body["items"])
