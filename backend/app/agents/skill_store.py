"""技能包 store：data/skills 目录的扫描、启停、新建、删除与文件读取。

技能包沿用 zcode / WorkBuddy 的 SKILL.md 约定：每个包一个子目录，内含
YAML frontmatter（name/description/version）+ Markdown 正文。agentscope 的
LocalSkillLoader 直接以目录作为技能池注入 Agent（见 agents/orchestrator.py
的 skills_dir 配置），本模块是这套技能池的管理层。

store 目录结构：
    data/skills/
      <name>/SKILL.md      # 技能包主文件（name 与目录名保持一致）
      <name>/...           # 包内其它参考文件（可建子目录）
      _store.json          # 启停状态：{"disabled": ["name", ...]}

约定：
    - 包的身份统一用「目录名」标识（API 路径中的 {name} 即目录名）；
      新建与种子包的 frontmatter name 与目录名一致。
    - frontmatter 解析失败或缺 name 的包标记 valid=False + error，不抛异常。
"""
import json
import logging
import re
import shutil
from pathlib import Path

import yaml

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

# 启停状态文件（存放在 store 根目录）
STORE_FILE = "_store.json"
# 新建技能包的名称规则：小写字母/数字/连字符，字母或数字开头，最长 50
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")

# 新建技能包使用的 SKILL.md 模板
PACK_TEMPLATE = """---
name: {name}
description: {description}
version: 1.0.0
---

# {title}

{body}
"""

# 正文留空时写入的骨架
DEFAULT_BODY = """## 使用场景

（什么时候让 AI 启用这个技能）

## 执行要点

1. （第一步做什么）
2. （第二步做什么）

## 输出

（技能执行后应产出什么）
"""


def store_dir() -> Path:
    """技能包 store 根目录：backend/data/skills（不存在则创建）。"""
    path = DATA_DIR / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _seed_dir() -> Path:
    """种子技能包来源目录：backend/seed_skills。"""
    from ..config import BASE_DIR

    return BASE_DIR / "seed_skills"


def ensure_seed_packs() -> list[str]:
    """把 seed_skills/* 整目录复制进 store（store 已有同名目录则跳过，幂等）。

    返回本次实际复制的包名列表；供启动播种与 /rescan 汇报使用。
    """
    seed_dir = _seed_dir()
    root = store_dir()
    copied: list[str] = []
    if not seed_dir.is_dir():
        return copied
    for src in sorted(p for p in seed_dir.iterdir() if p.is_dir()):
        dst = root / src.name
        if dst.exists():
            continue
        try:
            shutil.copytree(src, dst)
            copied.append(src.name)
        except OSError:
            logger.warning("种子技能包复制失败：%s", src, exc_info=True)
    if copied:
        logger.info("已复制种子技能包：%s", "、".join(copied))
    return copied


def scan_packs() -> list[dict]:
    """扫描 store 下所有技能包子目录，返回清单（含启停与校验状态）。

    每一项：{name, version, description, dir, files, enabled, valid, error}。
    files 为 SKILL.md 之外的包内文件（相对路径，含子目录）。
    """
    root = store_dir()
    disabled = _load_disabled()
    packs: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        packs.append(_scan_one(child, disabled))
    return packs


def _scan_one(pack_dir: Path, disabled: list[str]) -> dict:
    """扫描单个技能包目录，产出清单项（任何解析错误都转换为 valid=False）。"""
    entry = {
        "name": pack_dir.name,
        "version": "",
        "description": "",
        "dir": pack_dir.name,
        "files": _list_files(pack_dir),
        "enabled": pack_dir.name not in disabled,
        "valid": False,
        "error": "",
    }
    skill_md = pack_dir / "SKILL.md"
    if not skill_md.is_file():
        entry["error"] = "缺少 SKILL.md"
        return entry
    try:
        meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8-sig", errors="replace"))
        name = str(meta.get("name") or "").strip()
        if not name:
            raise ValueError("frontmatter 缺少 name")
    except ValueError as exc:
        entry["error"] = f"SKILL.md 解析失败：{exc}"
        return entry
    entry["name"] = name
    entry["version"] = str(meta.get("version") or "")
    entry["description"] = str(meta.get("description") or "").strip()
    entry["valid"] = True
    return entry


def _parse_frontmatter(text: str) -> dict:
    """解析 SKILL.md 的 YAML frontmatter（首尾 --- 包裹）；失败抛 ValueError。

    调用方需以 utf-8-sig 读取文件，BOM 在此前已剥掉。
    """
    if not text.startswith("---"):
        raise ValueError("缺少 frontmatter（文件需以 --- 开头）")
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        raise ValueError("frontmatter 未闭合（缺少结束 ---）")
    try:
        data = yaml.safe_load(rest[:end])
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 格式错误：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter 不是键值结构")
    return data


def _list_files(pack_dir: Path) -> list[str]:
    """列出包内除 SKILL.md 外的文件（相对路径，排序，跳过隐藏与缓存目录）。"""
    files: list[str] = []
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pack_dir)
        if rel.as_posix() == "SKILL.md":
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        files.append(rel.as_posix())
    return sorted(files)


def _load_disabled() -> list[str]:
    """读取 _store.json 的停用列表；文件缺失或损坏时按空列表处理。"""
    path = store_dir() / STORE_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        disabled = data.get("disabled", [])
        return [str(n) for n in disabled] if isinstance(disabled, list) else []
    except (json.JSONDecodeError, OSError, AttributeError):
        logger.warning("%s 解析失败，按无停用记录处理", STORE_FILE)
        return []


def _save_disabled(disabled: list[str]) -> None:
    """把停用列表写回 _store.json。"""
    path = store_dir() / STORE_FILE
    path.write_text(
        json.dumps({"disabled": sorted(set(disabled))}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pack_dir(name: str) -> Path:
    """把包名（目录名）解析为 store 下的目录；绝对路径/.. 穿越一律拒绝。"""
    raw = name or ""
    candidate = Path(raw)
    if not raw or candidate.is_absolute() or ".." in candidate.parts or "/" in raw or "\\" in raw:
        raise ValueError(f"非法的技能包名称：{raw!r}")
    root = store_dir().resolve()
    pack_dir = (root / raw).resolve()
    if not pack_dir.is_relative_to(root):
        raise ValueError(f"非法的技能包名称：{raw!r}")
    return pack_dir


def set_enabled(name: str, enabled: bool) -> dict:
    """启用/停用技能包（写 _store.json）；包不存在抛 LookupError。"""
    pack_dir = _pack_dir(name)
    if not pack_dir.is_dir():
        raise LookupError(f"技能包不存在：{name}")
    disabled = _load_disabled()
    if enabled:
        disabled = [n for n in disabled if n != name]
    elif name not in disabled:
        disabled.append(name)
    _save_disabled(disabled)
    return {"name": name, "enabled": enabled}


def read_pack_file(name: str, filename: str) -> str:
    """读取技能包内文件内容；包/文件不存在抛 LookupError，路径穿越抛 ValueError。"""
    pack_dir = _pack_dir(name)
    if not pack_dir.is_dir():
        raise LookupError(f"技能包不存在：{name}")
    candidate = Path(filename or "")
    if candidate.is_absolute() or ".." in candidate.parts or not filename:
        raise ValueError("非法的文件路径（只允许包内相对路径）")
    target = (pack_dir / candidate).resolve()
    if not target.is_relative_to(pack_dir.resolve()):
        raise ValueError("非法的文件路径（只允许包内相对路径）")
    if not target.is_file():
        raise LookupError(f"文件不存在：{filename}")
    return target.read_text(encoding="utf-8", errors="replace")


def create_pack(name: str, description: str, body: str = "") -> dict:
    """用内置模板创建新技能包目录；名称非法或已存在抛 ValueError。

    名称规则：小写字母/数字/连字符（字母或数字开头，最长 50）。
    """
    name = (name or "").strip()
    if not NAME_PATTERN.match(name):
        raise ValueError("名称只能使用小写字母、数字和连字符，且以字母或数字开头（1-50 字符）")
    description = (description or "").strip()
    if not description:
        raise ValueError("描述不能为空（AI 助手靠描述决定何时启用技能）")
    pack_dir = store_dir() / name
    if pack_dir.exists():
        raise ValueError(f"技能包已存在：{name}")
    pack_dir.mkdir(parents=True)
    content = PACK_TEMPLATE.format(
        name=name,
        title=name,
        description=description,
        body=(body or "").strip() or DEFAULT_BODY,
    )
    (pack_dir / "SKILL.md").write_text(content, encoding="utf-8")
    logger.info("已新建技能包：%s", name)
    return _scan_one(pack_dir, _load_disabled())


def delete_pack(name: str) -> None:
    """删除技能包目录（连同启停记录）；包不存在抛 LookupError。"""
    pack_dir = _pack_dir(name)
    if not pack_dir.is_dir():
        raise LookupError(f"技能包不存在：{name}")
    shutil.rmtree(pack_dir)
    if name in _load_disabled():
        _save_disabled([n for n in _load_disabled() if n != name])
    logger.info("已删除技能包：%s", name)
