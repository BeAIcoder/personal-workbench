"""专家团文件化（Expert Packs）测试：解析、导入/导出、一致性比对、HTTP 全链、防穿越。

packs_dir 在各用例中 monkeypatch 到 tmp_path，测试数据不写真实 backend/data 目录；
数据库用 conftest 的临时 SQLite（client fixture 负责建表并灌入 8 位内置专家）。
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from app.agents import expert_packs
from app.agents import team as team_mod
from app.database import SessionLocal
from app.models import AgentSpecModel

P = "/api/experts/packs"

# 合法专家包（新建一位自定义专家）
GOOD_PACK = """---
name: legal_advisor
label: 法务顾问
emoji: ⚖️
color: #795548
description: "合同条款与合规咨询"
keywords: "合同,法务,诉讼"
tools: "query_tasks,create_task"
sort: 7
---

你是「法务顾问」，专注合同审查与合规建议。
"""

# 与库中内置专家 realestate 有差异的包（工具不同）
DIFF_REALESTATE = """---
name: realestate
label: 商业地产管家
emoji: 🏢
color: #409EFF
description: "综合协调人与商业地产分析师"
keywords: "商业地产,日程"
tools: "query_tasks,vault_query"
sort: 6
---

改动过的角色提示词。
"""

# 坏文件：缺 name / 缺正文 / 无 frontmatter / name 不合法
BAD_NO_NAME = """---
label: 无名专家
sort: 1
---

正文内容。
"""
BAD_NO_BODY = """---
name: nobody
label: 无正文专家
---
"""
BAD_NO_FRONTMATTER = """name: no_meta
正文但没有 frontmatter。
"""
BAD_NAME_PATTERN = """---
name: Bad Name
---

正文内容。
"""


@pytest.fixture(autouse=True, scope="session")
def _isolate_packs_dir_for_startup():
    """会话级隔离：把专家包目录重定向到会话临时目录。

    client fixture 启动应用时 lifespan 会调用 expert_packs.packs_dir() 做种子复制；
    会话级 fixture 先于函数级 fixture 生效，保证这一步也不写真实 data 目录。
    """
    tmp = Path(tempfile.mkdtemp(prefix="expert_packs_session_"))
    original = expert_packs.packs_dir
    expert_packs.packs_dir = lambda: tmp
    yield tmp
    expert_packs.packs_dir = original
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _tmp_packs_dir(tmp_path, monkeypatch):
    """每个用例的专家包目录指向独立 tmp_path，并清空团队缓存。"""
    monkeypatch.setattr(expert_packs, "packs_dir", lambda: tmp_path)
    team_mod.invalidate_team_cache()
    return tmp_path


def _write(directory: Path, filename: str, text: str) -> Path:
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


# ---------- 解析 ----------

def test_parse_good_file(tmp_path):
    data = expert_packs.parse_pack_file(_write(tmp_path, "legal_advisor.md", GOOD_PACK))
    assert data["name"] == "legal_advisor"
    assert data["label"] == "法务顾问"
    assert data["emoji"] == "⚖️"
    assert data["color"] == "#795548"
    assert data["description"] == "合同条款与合规咨询"
    assert data["keywords"] == ["合同", "法务", "诉讼"]
    assert data["tools"] == ["query_tasks", "create_task"]
    assert data["sort"] == 7
    assert data["body"].startswith("你是「法务顾问」")


def test_parse_defaults_and_inline_list(tmp_path):
    text = """---
name: minimal
keywords: [财务, 税]
---

正文提示词。
"""
    data = expert_packs.parse_pack_file(_write(tmp_path, "minimal.md", text))
    assert data["label"] == "minimal"          # label 缺省用 name
    assert data["emoji"] == "🤖"               # emoji 缺省
    assert data["color"] == "#409EFF"          # color 缺省
    assert data["description"] == ""
    assert data["keywords"] == ["财务", "税"]   # 行内列表形式
    assert data["tools"] == []                 # tools 缺省为空
    assert data["sort"] == 0
    assert data["body"] == "正文提示词。"


def test_parse_missing_name_raises(tmp_path):
    with pytest.raises(ValueError, match="name"):
        expert_packs.parse_pack_file(_write(tmp_path, "bad1.md", BAD_NO_NAME))


def test_parse_missing_body_raises(tmp_path):
    with pytest.raises(ValueError, match="正文"):
        expert_packs.parse_pack_file(_write(tmp_path, "bad2.md", BAD_NO_BODY))


def test_parse_missing_frontmatter_raises(tmp_path):
    with pytest.raises(ValueError, match="frontmatter"):
        expert_packs.parse_pack_file(_write(tmp_path, "bad3.md", BAD_NO_FRONTMATTER))


def test_parse_bad_name_pattern_raises(tmp_path):
    with pytest.raises(ValueError, match="name"):
        expert_packs.parse_pack_file(_write(tmp_path, "bad4.md", BAD_NAME_PATTERN))


# ---------- 导入 ----------

def test_import_creates_expert(client, tmp_path):
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)
    with SessionLocal() as db:
        result = expert_packs.import_pack("legal_advisor", db)
    assert result["action"] == "created"
    assert result["is_builtin"] is False
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "legal_advisor").one()
        assert row.display_name == "法务顾问"
        assert row.keywords == ["合同", "法务", "诉讼"]
        assert row.tools == ["query_tasks", "create_task"]
        assert row.sort == 7
        assert row.is_builtin is False
        assert row.enabled is True
        assert row.role_prompt.startswith("你是「法务顾问」")
    # 导入后团队缓存已失效：无需 force 也能加载到新专家
    assert "legal_advisor" in team_mod.load_team()


def test_import_updates_builtin_and_keeps_is_builtin(client, tmp_path):
    from app.agents.team import TEAM as _TEAM

    expert_packs_original_tools = list(_TEAM["realestate"].tools)
    text = """---
name: realestate
label: 商业地产管家·改
description: "改动后的描述"
keywords: "商业地产,日程"
tools: "query_tasks"
sort: 9
---

改动后的角色提示词。
"""
    _write(tmp_path, "realestate.md", text)
    with SessionLocal() as db:
        result = expert_packs.import_pack("realestate", db)
    assert result["action"] == "updated"
    assert result["is_builtin"] is True
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "realestate").one()
        assert row.is_builtin is True          # 内置专家的 is_builtin 保持原值
        assert row.display_name == "商业地产管家·改"
        assert row.description == "改动后的描述"
        assert row.keywords == ["商业地产", "日程"]
        # 工具为并集语义：文件声明的 query_tasks 并入原有白名单，不缩减
        assert "query_tasks" in row.tools
        assert set(row.tools) >= set(expert_packs_original_tools)
        assert row.sort == 9
        assert row.role_prompt == "改动后的角色提示词。"


def test_import_update_preserves_enabled(client, tmp_path):
    """更新只改文件覆盖的字段；enabled 等运行态字段保持原值。"""
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "security").one()
        row.enabled = False
        db.commit()
    text = "---\nname: security\nlabel: 网络安全工程师\n---\n新提示词。\n"
    _write(tmp_path, "security.md", text)
    with SessionLocal() as db:
        expert_packs.import_pack("security", db)
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "security").one()
        assert row.enabled is False
        assert row.role_prompt == "新提示词。"


def test_import_unchanged_short_circuit(client, tmp_path):
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)
    with SessionLocal() as db:
        first = expert_packs.import_pack("legal_advisor", db)
        second = expert_packs.import_pack("legal_advisor", db)
    assert first["action"] == "created"
    assert second["action"] == "unchanged"
    assert second["diff"] == []


def test_import_missing_pack_raises(client, tmp_path):
    with SessionLocal() as db:
        with pytest.raises(FileNotFoundError, match="不存在"):
            expert_packs.import_pack("ghost", db)


def test_import_all(client, tmp_path):
    # 先导出一位内置专家，得到与数据库完全一致的文件（应判定 skipped）
    with SessionLocal() as db:
        expert_packs.export_pack("realestate", db)
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)
    _write(tmp_path, "broken.md", BAD_NO_NAME)
    with SessionLocal() as db:
        result = expert_packs.import_all(db)
    assert result["imported"] == ["legal_advisor"]
    assert result["skipped"] == ["realestate"]
    assert [e["file"] for e in result["errors"]] == ["broken.md"]
    assert "name" in result["errors"][0]["error"]


# ---------- 导出与往返 ----------

def test_export_roundtrip(client, tmp_path):
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "leasing").one()
        row.display_name = "招商专员·改"
        row.tools = ["query_tasks", "create_note"]
        db.commit()
        expected = {
            "display_name": row.display_name,
            "role_prompt": row.role_prompt,
            "keywords": list(row.keywords),
            "emoji": row.emoji,
            "color": row.color,
            "description": row.description,
            "sort": row.sort,
        }
        result = expert_packs.export_pack("leasing", db)
    assert result["file"] == "leasing.md"
    assert (tmp_path / "leasing.md").is_file()
    # 导出内容可被重新解析，字段一致
    parsed = expert_packs.parse_pack_file(tmp_path / "leasing.md")
    assert parsed["name"] == "leasing"
    assert parsed["label"] == expected["display_name"]
    assert parsed["body"] == expected["role_prompt"]
    assert parsed["keywords"] == expected["keywords"]
    assert parsed["tools"] == ["query_tasks", "create_note"]
    assert parsed["emoji"] == expected["emoji"]
    assert parsed["color"] == expected["color"]
    assert parsed["description"] == expected["description"]
    assert parsed["sort"] == expected["sort"]
    # 删掉库中专家，再从文件导入：字段一致地重建（按新建处理，is_builtin=False）
    with SessionLocal() as db:
        db.delete(db.query(AgentSpecModel).filter(AgentSpecModel.name == "leasing").one())
        db.commit()
    with SessionLocal() as db:
        imported = expert_packs.import_pack("leasing", db)
    assert imported["action"] == "created"
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "leasing").one()
        assert row.display_name == expected["display_name"]
        assert row.role_prompt == expected["role_prompt"]
        assert row.keywords == expected["keywords"]
        assert row.tools == ["query_tasks", "create_note"]
        assert row.emoji == expected["emoji"]
        assert row.color == expected["color"]
        assert row.description == expected["description"]
        assert row.sort == expected["sort"]
        assert row.is_builtin is False


def test_export_unknown_expert_raises(client, tmp_path):
    with SessionLocal() as db:
        with pytest.raises(FileNotFoundError, match="不存在"):
            expert_packs.export_pack("ghost", db)


# ---------- 清单 / 读取 / 删除 / 防穿越 ----------

def test_list_packs_states(client, tmp_path):
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)      # 未导入
    _write(tmp_path, "realestate.md", DIFF_REALESTATE)   # 已导入但有差异
    _write(tmp_path, "broken.md", BAD_NO_NAME)           # 文件错误
    with SessionLocal() as db:
        items = {i["name"]: i for i in expert_packs.list_packs(db)}
    assert set(items) == {"legal_advisor", "realestate", "broken"}

    new = items["legal_advisor"]
    assert new["valid"] is True and new["imported"] is False and new["diff"] is None
    assert new["label"] == "法务顾问" and new["file"] == "legal_advisor.md"

    diff = items["realestate"]
    assert diff["imported"] is True
    assert diff["diff"] and "工具" in diff["diff"]

    broken = items["broken"]
    assert broken["valid"] is False and broken["imported"] is False
    assert broken["error"] and "name" in broken["error"]


def test_read_and_delete_pack(client, tmp_path):
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)
    data = expert_packs.read_pack_file("legal_advisor")
    assert data["content"] == GOOD_PACK
    assert data["file"] == "legal_advisor.md"
    result = expert_packs.delete_pack("legal_advisor")
    assert result["deleted"] == "legal_advisor"
    assert not (tmp_path / "legal_advisor.md").exists()
    with pytest.raises(FileNotFoundError, match="不存在"):
        expert_packs.delete_pack("legal_advisor")
    with pytest.raises(FileNotFoundError, match="不存在"):
        expert_packs.read_pack_file("legal_advisor")


def test_traversal_rejected(client, tmp_path):
    # 目录外放一个诱饵文件，任何穿越名都不应读到它
    secret = tmp_path.parent / "expert_packs_secret.md"
    secret.write_text("---\nname: secret\n---\n机密正文。\n", encoding="utf-8")
    for bad in ("../expert_packs_secret", "..", "a/b", "sub/dir", "./x", "UPPER"):
        with pytest.raises(ValueError):
            expert_packs.read_pack_file(bad)
        with pytest.raises(ValueError):
            expert_packs.delete_pack(bad)
    assert secret.is_file()  # 诱饵未被触碰


def test_reset_from_seed(client, tmp_path):
    _write(tmp_path, "realestate.md", "---\nname: realestate\n---\n被改坏的正文。\n")
    result = expert_packs.reset_from_seed()
    assert len(result["reset"]) == 8
    restored = (tmp_path / "realestate.md").read_text(encoding="utf-8")
    assert "商业地产管家" in restored and "被改坏" not in restored


# ---------- HTTP 端点全链 ----------

def test_http_list_and_content(client, tmp_path):
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)
    body = client.get(P).json()
    assert [i["name"] for i in body["items"]] == ["legal_advisor"]
    item = body["items"][0]
    assert item["valid"] is True and item["imported"] is False and item["diff"] is None
    content = client.get(f"{P}/legal_advisor/content").json()
    assert content["content"] == GOOD_PACK
    resp = client.get(f"{P}/ghost/content")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


def test_http_import_one_and_cache(client, tmp_path):
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)
    team_mod.load_team()  # 预热团队缓存，验证导入后缓存立即失效
    resp = client.post(f"{P}/import/legal_advisor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "created" and body["is_builtin"] is False
    assert "legal_advisor" in team_mod.load_team()
    # 数据库专家列表可见（非内置）
    agents = client.get("/api/assistant/agents").json()["agents"]
    legal = next(a for a in agents if a["name"] == "legal_advisor")
    assert legal["is_builtin"] is False and legal["display_name"] == "法务顾问"
    # 再次导入 → unchanged；清单显示已导入且无差异
    assert client.post(f"{P}/import/legal_advisor").json()["action"] == "unchanged"
    items = {i["name"]: i for i in client.get(P).json()["items"]}
    assert items["legal_advisor"]["imported"] is True
    assert items["legal_advisor"]["diff"] is None


def test_http_import_all(client, tmp_path):
    with SessionLocal() as db:
        expert_packs.export_pack("realestate", db)
    _write(tmp_path, "legal_advisor.md", GOOD_PACK)
    _write(tmp_path, "broken.md", BAD_NO_NAME)
    resp = client.post(f"{P}/import-all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == ["legal_advisor"]
    assert body["skipped"] == ["realestate"]
    assert [e["file"] for e in body["errors"]] == ["broken.md"]


def test_http_export_and_delete(client, tmp_path):
    resp = client.post(f"{P}/export/security")
    assert resp.status_code == 200
    assert resp.json()["file"] == "security.md"
    assert (tmp_path / "security.md").is_file()
    # 内置专家 seed 与 TEAM 一致：导出后清单显示已导入且无差异
    items = {i["name"]: i for i in client.get(P).json()["items"]}
    assert items["security"]["imported"] is True
    assert items["security"]["diff"] is None
    # 删除文件（不影响数据库）
    assert client.delete(f"{P}/security").status_code == 200
    assert client.delete(f"{P}/security").status_code == 404
    agents = client.get("/api/assistant/agents").json()["agents"]
    assert any(a["name"] == "security" for a in agents)


def test_http_import_invalid_file_400(client, tmp_path):
    _write(tmp_path, "broken.md", BAD_NO_NAME)
    resp = client.post(f"{P}/import/broken")
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"]


def test_http_import_missing_404(client, tmp_path):
    resp = client.post(f"{P}/import/ghost")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


def test_http_export_unknown_expert_404(client, tmp_path):
    resp = client.post(f"{P}/export/ghost")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


def test_http_reset_from_seed(client, tmp_path):
    _write(tmp_path, "realestate.md", "---\nname: realestate\n---\n坏正文。\n")
    resp = client.post(f"{P}/reset")
    assert resp.status_code == 200
    assert len(resp.json()["reset"]) == 8
    assert "商业地产管家" in (tmp_path / "realestate.md").read_text(encoding="utf-8")


def test_http_traversal_blocked(client, tmp_path):
    # HTTP 层：编码后的穿越名不会 200 读到目录外文件（路由层或参数校验层拦截）
    resp = client.get(f"{P}/..%2Fsecret/content")
    assert resp.status_code != 200
    assert resp.status_code in (400, 404)
