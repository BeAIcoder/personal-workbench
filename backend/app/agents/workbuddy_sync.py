# -*- coding: utf-8 -*-
"""WorkBuddy 同步导入服务（Part D）：把本机腾讯 WorkBuddy 的技能包/记忆/身份文件导入平台。

数据边界：源目录只读；只写平台数据目录（技能池 backend/data/skills、专家包目录、memories 表）。
来源结构（本机实测 2026-10-30）：
- 技能包：~/.workbuddy/buddy-skill-store/<store>/<skill容器>/<skill目录>/SKILL.md（YAML frontmatter）；
- 记忆：~/.workbuddy/MEMORY.md（Markdown 分节；agent-hub 托管块跳过不导入）；
- 身份：SOUL/IDENTITY/USER/BOOTSTRAP.md → 专家团文件草稿（frontmatter name/label + 正文）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from . import expert_packs, skill_store

# ---------- 常量 ----------

DEFAULT_HOME = "C:/Users/Administrator/.workbuddy"
IDENTITY_FILES = ("SOUL.md", "IDENTITY.md", "USER.md", "BOOTSTRAP.md")
MEMORY_MD = "MEMORY.md"
SKILL_STORE_SUBDIR = "buddy-skill-store"
# 专家包草稿正文上限（与 agent_specs.role_prompt 60000 上限同口径）
_MAX_BODY = 60000
# 单文件读取上限（防异常大文件撑爆内存）
_MAX_FILE_BYTES = 4 * 1024 * 1024
# 设置/运行记录文件（不进 git；真实数据目录）
_SETTINGS_NAME = "workbuddy_import.json"


# ---------- 设置持久化（JSON 文件，不进 git） ----------

def _settings_path() -> Path:
    from ..config import DATA_DIR
    return DATA_DIR / _SETTINGS_NAME


def load_settings() -> dict:
    p = _settings_path()
    if not p.is_file():
        return {"home": "", "last_import_at": "", "last_result": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"home": "", "last_import_at": "", "last_result": None}
    if not isinstance(data, dict):
        return {"home": "", "last_import_at": "", "last_result": None}
    return data


def save_settings(data: dict) -> dict:
    data = dict(data or {})
    data.setdefault("home", "")
    data["last_import_at"] = data.get("last_import_at") or ""
    data["last_result"] = data.get("last_result")
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def _resolve_home(home: str = "") -> Path:
    home = (home or load_settings().get("home") or os.environ.get("WORKBUDDY_HOME")
            or DEFAULT_HOME)
    return Path(home)


# ---------- 扫描（只读） ----------

def scan(home: str = "") -> dict:
    root = _resolve_home(home)
    result = {"home": str(root), "available": root.is_dir(),
              "skills": [], "memories": [], "identity_files": []}
    if not result["available"]:
        result["message"] = f"未找到 WorkBuddy 数据目录：{root}"
        return result
    result["skills"] = _scan_skills(root)
    result["memories"] = _scan_memories(root)
    result["identity_files"] = _scan_identity(root)
    return result


def _scan_skills(root: Path) -> list[dict]:
    out = []
    store = root / SKILL_STORE_SUBDIR
    if store.is_dir():
        for skill_md in sorted(store.rglob("SKILL.md")):
            rel = skill_md.relative_to(store)
            if len(rel.parts) > 4:
                continue
            text = _read_text(skill_md)
            if text is None:
                continue
            meta = skill_store._parse_frontmatter(text)
            name = str(meta.get("name") or "").strip()
            if not name:
                continue
            try:
                exists = (skill_store._pack_dir(name) / "SKILL.md").is_file()
            except ValueError:
                exists = False
            out.append({
                "name": name, "version": str(meta.get("version") or ""),
                "title": str(meta.get("display_name") or name),
                "source_dir": str(skill_md.parent), "exists_in_store": exists,
            })
    return out


def _scan_memories(root: Path) -> list[dict]:
    text = _read_text(root / MEMORY_MD)
    if text is None:
        return []
    return [{"title": t or "(未命名)", "chars": len(b.strip())}
            for t, b in _split_memory_sections(text)]


def _scan_identity(root: Path) -> list[dict]:
    out = []
    for fname in IDENTITY_FILES:
        text = _read_text(root / fname)
        if text is not None:
            out.append({"file": fname, "chars": len(text)})
    return out


def _read_text(p: Path) -> str | None:
    try:
        if not p.is_file() or p.stat().st_size > _MAX_FILE_BYTES:
            return None
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


# ---------- MEMORY.md 分节（跳过 agent-hub 托管块） ----------

_HUB_BLOCK = re.compile(
    r"<!--\s*agent-hub:[^>]*:memory:start\s*-->.*?<!--\s*agent-hub:[^>]*:memory:end\s*-->",
    re.DOTALL)


def _split_memory_sections(text: str) -> list[tuple[str, str]]:
    """按 '## ' 分节；agent-hub 托管块整体剔除。"""
    cleaned = _HUB_BLOCK.sub("", text)
    sections: list[tuple[str, str]] = []
    title, buf = "", []
    for line in cleaned.splitlines():
        if line.startswith("## "):
            if title or buf:
                sections.append((title, "\n".join(buf)))
            title, buf = line[3:].strip(), []
        elif title or buf:
            buf.append(line)
    if title or buf:
        sections.append((title, "\n".join(buf)))
    return [(t, b) for t, b in sections if b.strip()]


# ---------- 导入（幂等 upsert） ----------

def import_all(db, home: str = "", skills: bool = True, memories: bool = True,
               identity: bool = True, dry_run: bool = False) -> dict:
    """执行导入，返回 {skills, memories, identity} 三组 {added, updated, skipped, failed}。

    - 技能包：整目录复制到平台技能池；新包默认停用（不静默改变 Agent 注入），
      已存在包更新 SKILL.md 并补缺失参考文件，启停状态保持不变；
    - 记忆：MEMORY.md 分节 → add_memory(source='import', tags=['workbuddy'])，
      近重复由记忆层去重（跳过计数）；
    - 身份：SOUL/IDENTITY/USER/BOOTSTRAP.md → 专家团文件草稿（upsert 覆盖写）。
    """
    from . import memory as memory_mod

    root = _resolve_home(home)
    if not root.is_dir():
        raise ValueError(f"未找到 WorkBuddy 数据目录：{root}")
    result = {
        "home": str(root), "dry_run": bool(dry_run),
        "skills": {"added": [], "updated": [], "skipped": [], "failed": []},
        "memories": {"added": 0, "skipped": 0, "failed": []},
        "identity": {"added": [], "updated": [], "failed": []},
    }
    plan = scan(home)
    if skills:
        _import_skills(plan["skills"], result, dry_run)
    if memories:
        _import_memories(db, root, result, dry_run)
    if identity:
        _import_identity(root, result, dry_run)
    if not dry_run:
        settings = load_settings()
        settings["last_import_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        settings["last_result"] = result
        save_settings(settings)
    return result


def _import_skills(items, result, dry_run: bool) -> None:
    bucket = result["skills"]
    for it in items:
        name = it["name"]
        src = Path(it["source_dir"])
        try:
            dest = skill_store._pack_dir(name)
        except ValueError as exc:
            bucket["failed"].append({"name": name, "error": str(exc)})
            continue
        exists = it["exists_in_store"]
        if dry_run:
            (bucket["updated"] if exists else bucket["added"]).append(name)
            continue
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            if not exists:
                skill_store.set_enabled(name, False)
            (bucket["updated"] if exists else bucket["added"]).append(name)
        except OSError as exc:
            bucket["failed"].append({"name": name, "error": str(exc)})


def _import_memories(db, root: Path, result, dry_run: bool) -> None:
    from . import memory as memory_mod

    bucket = result["memories"]
    text = _read_text(root / MEMORY_MD)
    if text is None:
        bucket["failed"].append({"name": MEMORY_MD, "error": "读取失败或文件过大"})
        return
    for title, body in _split_memory_sections(text):
        if dry_run:
            bucket["added"] += 1
            continue
        content = f"【{title}】\n{body.strip()}" if title else body.strip()
        try:
            memory_mod.add_memory(db, content, source="import",
                                  tags=["workbuddy", (title or "记忆")[:20]])
            bucket["added"] += 1
        except ValueError:
            bucket["skipped"] += 1  # 记忆层近重复判定
        except Exception as exc:  # noqa: BLE001
            bucket["failed"].append({"name": title or "(未命名)", "error": str(exc)})


def _import_identity(root: Path, result, dry_run: bool) -> None:
    bucket = result["identity"]
    for fname in IDENTITY_FILES:
        text = _read_text(root / fname)
        if text is None:
            continue
        stem = fname[:-3].lower()
        pack_name = f"workbuddy-{stem}"
        label = {"soul": "WorkBuddy 人设", "identity": "WorkBuddy 身份",
                 "user": "WorkBuddy 用户档案", "bootstrap": "WorkBuddy 引导"}.get(stem, stem)
        body = text.strip()[:_MAX_BODY]
        if dry_run:
            (bucket["updated"] if _expert_pack_exists(pack_name) else bucket["added"]).append(pack_name)
            continue
        try:
            existed = _expert_pack_exists(pack_name)
            content = (
                "---\n"
                f"name: {pack_name}\n"
                f"label: {label}\n"
                f"emoji: 🤖\n"
                f"color: \"#409EFF\"\n"
                f"description: 从腾讯 WorkBuddy 导入的 {label}（{fname}）草稿\n"
                "keywords: []\n"
                "---\n\n"
                f"{body}\n"
            )
            dest = expert_packs.packs_dir() / f"{pack_name}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            (bucket["updated"] if existed else bucket["added"]).append(pack_name)
        except OSError as exc:
            bucket["failed"].append({"name": pack_name, "error": str(exc)})


def _expert_pack_exists(name: str) -> bool:
    return (expert_packs.packs_dir() / f"{name}.md").is_file()
