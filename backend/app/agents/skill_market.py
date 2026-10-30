# -*- coding: utf-8 -*-
"""技能市场（skill_market）：聚合本机多个本地技能源，浏览/搜索/一键安装进平台技能池。

设计（移植 Qoder/QwenPaw「技能市场」形态，保障兼容）：
- 源：平台种子（backend/seed_skills）+ QwenPaw skill_pool（默认
  D:/AIApps/QwenPaw/data/skill_pool，env QWENPAW_SKILL_POOL 覆盖）+ WorkBuddy
  buddy-skill-store（复用 workbuddy_sync 扫描）；
- 每项：{name, description, version, source, installed, title}；
- 安装 = 整目录复制进平台技能池（backend/data/skills），**新包默认停用**（与 WorkBuddy
  导入同规则，不静默改变 Agent 注入）；已存在则更新（upsert），启停状态保留；
- 只读源目录；单文件 4MB 上限；不执行任何技能代码（安装 ≠ 启用）。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import skill_store, workbuddy_sync

# ---------- 常量 ----------

QWENPAW_SKILL_POOL_DEFAULT = "D:/AIApps/QwenPaw/data/skill_pool"
_MARKET_MAX_FILE_BYTES = 4 * 1024 * 1024
# 安装进技能池的目录名归一：小写字母/数字/连字符（与 skill_store 建包命名口径一致）
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _qwenpaw_pool() -> Path:
    """QwenPaw 技能池目录：设置/env/默认三级解析。"""
    return Path(os.environ.get("QWENPAW_SKILL_POOL") or QWENPAW_SKILL_POOL_DEFAULT)


def _norm_name(raw: str) -> str:
    """目录名归一：非法字符转连字符，全下划线转连字符（技能池目录命名口径）。"""
    name = re.sub(r"[^a-z0-9-]+", "-", str(raw or "").strip().lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    return name[:64]


def _safe_dir_join(root: Path, child: str) -> Path | None:
    """root/child 且 child 不越界（防源目录穿越）。"""
    candidate = (root / child).resolve()
    root_r = root.resolve()
    if candidate == root_r or not str(candidate).startswith(str(root_r) + os.sep):
        return None
    return candidate


def _read_skill_md(pack_dir: Path) -> tuple[str, str, str]:
    """读 SKILL.md（或 SKILL.cn.md 兜底）解析 name/description/version；失败返回空串。"""
    md = None
    for cand in ("SKILL.md", "SKILL.cn.md"):
        p = pack_dir / cand
        if p.is_file() and p.stat().st_size <= _MARKET_MAX_FILE_BYTES:
            md = p
            break
    if md is None:
        return "", "", ""
    try:
        text = md.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return "", "", ""
    meta = skill_store._parse_frontmatter(text)
    return (str(meta.get("name") or "").strip(),
            str(meta.get("description") or "").strip(),
            str(meta.get("version") or "").strip())


def _is_valid_pack(pack_dir: Path) -> bool:
    return pack_dir.is_dir() and ((pack_dir / "SKILL.md").is_file()
                                  or (pack_dir / "SKILL.cn.md").is_file())


# ---------- 市场扫描 ----------

def scan_market(keyword: str = "") -> dict:
    """扫描全部本地技能源，返回市场清单（keyword 对 name/description 子串过滤）。"""
    installed = {p["name"] for p in skill_store.scan_packs()}
    items: dict[str, dict] = {}
    kw = str(keyword or "").strip().lower()

    # 源 1：QwenPaw skill_pool
    pool = _qwenpaw_pool()
    if pool.is_dir():
        for child in sorted(pool.iterdir()):
            if not _is_valid_pack(child):
                continue
            name, desc, ver = _read_skill_md(child)
            key = name or child.name
            if kw and kw not in key.lower() and kw not in desc.lower():
                continue
            items[key] = {
                "name": key, "title": child.name, "description": desc[:300],
                "version": ver, "source": "qwenpaw",
                "source_dir": str(child), "installed": key in installed,
            }

    # 源 2：WorkBuddy buddy-skill-store（复用既有扫描）
    wb_scan = workbuddy_sync.scan("")
    for s in wb_scan.get("skills", []):
        key = s["name"]
        if kw and kw not in key.lower() and kw not in (s.get("title") or "").lower():
            continue
        if key in items:
            continue
        items[key] = {
            "name": key, "title": s.get("title") or key,
            "description": "", "version": s.get("version") or "",
            "source": "workbuddy", "source_dir": s.get("source_dir") or "",
            "installed": bool(s.get("exists_in_store")),
        }

    # 源 3：平台种子（backend/seed_skills）——展示为「已安装」
    from ..config import BASE_DIR
    seed = Path(BASE_DIR) / "seed_skills"
    if seed.is_dir():
        for child in sorted(seed.iterdir()):
            if not _is_valid_pack(child):
                continue
            name, desc, ver = _read_skill_md(child)
            key = name or child.name
            if kw and kw not in key.lower() and kw not in desc.lower():
                continue
            items.setdefault(key, {
                "name": key, "title": child.name, "description": desc[:300],
                "version": ver, "source": "builtin",
                "source_dir": str(child), "installed": True,
            })

    return {
        "sources": {
            "qwenpaw": {"dir": str(pool), "available": pool.is_dir()},
            "workbuddy": {"dir": workbuddy_sync.DEFAULT_HOME, "available": Path(workbuddy_sync.DEFAULT_HOME).is_dir()},
        },
        "keyword": kw,
        "items": sorted(items.values(), key=lambda x: (x["installed"], x["name"])),
    }


# ---------- 一键安装 ----------

def install_from_market(name: str) -> dict:
    """从市场安装单个技能包进平台技能池（默认停用；已存在则更新）。"""
    plan = scan_market("")
    target = next((i for i in plan["items"] if i["name"] == name), None)
    if target is None:
        raise ValueError(f"市场里没有这个技能包：{name}")
    src = Path(target["source_dir"] or "")
    if not _is_valid_pack(src):
        raise ValueError(f"技能包源目录无效：{src}")
    dest = skill_store._pack_dir(_norm_name(name))
    existed = (dest / "SKILL.md").is_file()
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    # 归一目录名与 frontmatter name 不一致时，重写 SKILL.md 的 name 字段保持一致
    _rewrite_frontmatter_name(dest, dest.name)
    if not existed:
        skill_store.set_enabled(dest.name, False)
    return {"name": dest.name, "installed": True, "updated": existed,
            "enabled": False, "source": target["source"]}


def _rewrite_frontmatter_name(pack_dir: Path, name: str) -> None:
    """把 SKILL.md frontmatter 的 name 重写为与目录名一致（保持技能池命名口径）。"""
    for cand in ("SKILL.md", "SKILL.cn.md"):
        p = pack_dir / cand
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return
        text = re.sub(r"(?m)^name:\s*.*$", f"name: {name}", text, count=1)
        try:
            p.write_text(text, encoding="utf-8")
        except OSError:
            pass
        return
