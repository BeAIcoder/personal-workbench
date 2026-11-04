# -*- coding: utf-8 -*-
"""WorkBuddy 同步导入测试（Part D）：扫描/导入/幂等/设置持久化，全部临时目录。"""
from __future__ import annotations

import pytest

from app.agents import expert_packs, skill_store
from app.agents import workbuddy_sync as wb


@pytest.fixture(autouse=True)
def _ensure_tables():
    """直连 SessionLocal 的用例也要有表（与 conftest client 夹具同库，幂等建表）。"""
    from app.database import Base, engine
    import app.models  # noqa: F401  注册宿主模型
    import app.modules  # noqa: F401  注册模块表
    Base.metadata.create_all(bind=engine)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时 WorkBuddy 家 + 临时技能池 + 临时专家包目录 + 临时设置文件。"""
    wb_home = tmp_path / ".workbuddy"
    store = tmp_path / "skills"
    packs = tmp_path / "packs"
    settings = tmp_path / "workbuddy_import.json"

    store.mkdir(parents=True)
    packs.mkdir(parents=True)
    skill_dir = wb_home / "buddy-skill-store" / "storeA" / "builtin--skill_1" / "skill_1"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: wb-demo-skill\ndescription: 演示技能\nversion: 1.0.0\n---\n\n# 演示\n",
        encoding="utf-8")
    (wb_home / "MEMORY.md").write_text(
        "<!-- agent-hub:workbuddy:memory:start -->\n托管块内容不导入\n"
        "<!-- agent-hub:workbuddy:memory:end -->\n\n"
        "## 本机环境事实\n\n- WPS 已装\n- Chrome 已装\n\n## 工作偏好\n\n- 证据优先\n",
        encoding="utf-8")
    (wb_home / "SOUL.md").write_text("# 人设正文\n", encoding="utf-8")
    (wb_home / "USER.md").write_text("# 用户档案正文\n", encoding="utf-8")

    def _store_dir():
        store.mkdir(parents=True, exist_ok=True)
        return store

    def _packs_dir():
        packs.mkdir(parents=True, exist_ok=True)
        return packs

    def _settings_path():
        return settings

    monkeypatch.setattr(skill_store, "store_dir", _store_dir)
    monkeypatch.setattr(expert_packs, "packs_dir", _packs_dir)
    monkeypatch.setattr(wb, "_settings_path", _settings_path)
    return {"home": wb_home, "store": store, "packs": packs, "settings": settings}


def test_scan_finds_all_sources(env):
    res = wb.scan(str(env["home"]))
    assert res["available"] is True
    assert [s["name"] for s in res["skills"]] == ["wb-demo-skill"]
    assert [m["title"] for m in res["memories"]] == ["本机环境事实", "工作偏好"]
    assert {"file": "SOUL.md", "chars": 7} in res["identity_files"]
    assert {"file": "USER.md", "chars": 9} in res["identity_files"]


def test_import_all_and_idempotent(env):
    from app.database import SessionLocal
    with SessionLocal() as db:
        res = wb.import_all(db, home=str(env["home"]))
        assert res["skills"]["added"] == ["wb-demo-skill"]
        assert res["identity"]["added"] == ["workbuddy_soul", "workbuddy_user"]
        assert res["memories"]["added"] == 2

        # 幂等：技能 updated、记忆近重复跳过、身份 updated
        res2 = wb.import_all(db, home=str(env["home"]))
        assert res2["skills"]["updated"] == ["wb-demo-skill"]
        assert res2["skills"]["added"] == []
        assert res2["memories"]["added"] == 0
        assert res2["memories"]["skipped"] == 2
        assert res2["identity"]["updated"] == ["workbuddy_soul", "workbuddy_user"]

    # 技能包新包默认停用
    assert "wb-demo-skill" in skill_store.disabled_packs(env["store"])


def test_import_dry_run_writes_nothing(env):
    from app.database import SessionLocal
    with SessionLocal() as db:
        res = wb.import_all(db, home=str(env["home"]), dry_run=True)
        assert res["skills"]["added"] == ["wb-demo-skill"]
        assert res["memories"]["added"] == 2
    assert not (env["store"] / "wb-demo-skill").exists()
    assert not (env["packs"] / "workbuddy_soul.md").exists()
    assert not env["settings"].exists()


def test_import_identity_draft_format(env):
    from app.database import SessionLocal
    from app.agents import expert_packs
    with SessionLocal() as db:
        wb.import_all(db, home=str(env["home"]), skills=False, memories=False)
    # SOUL.md 夹具含自身 frontmatter：草稿正文须剥离嵌套 frontmatter
    (env["home"] / "SOUL.md").write_text("---\nsummary: x\n---\n\n# 人设正文\n", encoding="utf-8")
    with SessionLocal() as db:
        wb.import_all(db, home=str(env["home"]), skills=False, memories=False)
    text = (env["packs"] / "workbuddy_soul.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: workbuddy_soul" in text
    assert "# 人设正文" in text
    # 生成草稿必须能通过 expert_packs 解析（name 规则/字段格式合法）且不残留嵌套 frontmatter
    parsed = expert_packs.parse_pack_file(env["packs"] / "workbuddy_soul.md")
    assert parsed["name"] == "workbuddy_soul"
    assert parsed["label"] == "WorkBuddy 人设"
    assert "---" not in parsed["body"][:5]


def test_settings_roundtrip(env):
    wb.save_settings({"home": str(env["home"])})
    assert wb.load_settings()["home"] == str(env["home"])


def test_skill_copy_failure_preserves_old_pack_and_cleans_temp(env, monkeypatch):
    from app.database import SessionLocal

    with SessionLocal() as db:
        wb.import_all(db, home=str(env["home"]), memories=False, identity=False)
    dest = env["store"] / "wb-demo-skill"
    original = (dest / "SKILL.md").read_bytes()

    def fail_copy(src, dst, **kwargs):
        from pathlib import Path

        partial = Path(dst)
        partial.mkdir(parents=True)
        (partial / "SKILL.md").write_text("半成品", encoding="utf-8")
        raise OSError("synthetic workbuddy copy failure")

    monkeypatch.setattr(skill_store.shutil, "copytree", fail_copy)
    with SessionLocal() as db:
        result = wb.import_all(db, home=str(env["home"]), memories=False, identity=False)
    assert result["skills"]["failed"][0]["name"] == "wb-demo-skill"
    assert (dest / "SKILL.md").read_bytes() == original
    assert not list(env["store"].glob(".*.installing"))
    assert not list(env["store"].glob(".*.bak"))


def test_new_skill_disable_failure_rolls_back_and_cleans_temp(env, monkeypatch):
    from app.database import SessionLocal

    def fail_disable(*args, **kwargs):
        raise RuntimeError("synthetic disable failure")

    monkeypatch.setattr(skill_store, "set_enabled", fail_disable)
    with SessionLocal() as db:
        result = wb.import_all(db, home=str(env["home"]), memories=False, identity=False)
    assert result["skills"]["failed"][0]["name"] == "wb-demo-skill"
    assert not (env["store"] / "wb-demo-skill").exists()
    assert not list(env["store"].glob(".*.installing"))
    assert not list(env["store"].glob(".*.bak"))


# ---------- API ----------

def test_api_scan_and_import(client, env):
    r = client.get(f"/api/assistant/workbuddy/scan?home={env['home']}")
    assert r.status_code == 200 and r.json()["available"] is True
    r2 = client.post("/api/assistant/workbuddy/import", json={
        "home": str(env["home"]), "skills": True, "memories": False, "identity": False})
    assert r2.status_code == 200
    assert r2.json()["skills"]["added"] == ["wb-demo-skill"]


def test_api_missing_home_400(client):
    r = client.post("/api/assistant/workbuddy/import", json={"home": str("D:/不存在路径_xyz")})
    assert r.status_code == 400
    assert "未找到" in r.json()["detail"]