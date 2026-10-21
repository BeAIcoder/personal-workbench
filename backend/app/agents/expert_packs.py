"""专家团文件化（Expert Packs）：把专家定义存成 .md 文件，支持导入/导出/一致性比对。

文件格式与 backend/seed_experts/*.md 一致（即 zcode 插件 agents/<name>.md 的
YAML frontmatter + 正文系统提示词格式）：

    ---
    name: realestate                          # 英文标识（必填，小写字母/数字/下划线）
    label: 商业地产管家                        # 中文名称（缺省用 name）
    emoji: 🏢                                   # 缺省 🤖
    color: #409EFF                             # 缺省 #409EFF
    description: "一句话职责"                  # 缺省空
    keywords: "商业地产,日程"                  # 逗号分隔字符串或 [a, b] 列表；缺省空
    tools: "query_tasks,create_task"           # 逗号分隔；缺省/空 = 跟随默认技能池
    sort: 6                                    # 缺省 0
    ---

    正文即角色系统提示词（role_prompt，COMMON_RULES 由编排器统一拼接）。

目录约定：backend/data/expert-packs（已 gitignore）。目录首次创建时从
backend/seed_experts 复制内置种子（幂等）；之后文件归用户所有，删除不复活，
需要恢复出厂用 reset_from_seed()。

导入为 upsert（按 name）：不存在则新建（is_builtin=False），存在则更新
display_name/role_prompt/keywords/tools/emoji/color/description/sort，
is_builtin/enabled/model_id/skills 保持原值。导入/导出后调用
invalidate_team_cache()，让 team.load_team() 的 60 秒缓存立即失效。
"""
import logging
import os
import re
import shutil
from pathlib import Path

from ..config import BASE_DIR, DATA_DIR
from ..models import AgentSpecModel
from .team import invalidate_team_cache

logger = logging.getLogger(__name__)

# 专家包目录（backend/data/expert-packs，已 gitignore）与内置种子目录（backend/seed_experts）
PACKS_DIR_NAME = "expert-packs"
SEED_DIR = BASE_DIR / "seed_experts"

# 专家标识：小写字母开头，仅小写字母/数字/下划线（与 AgentSpecBase.name 一致）
_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")

# frontmatter 默认值（与 models.AgentSpecModel 列默认值保持一致）
_DEFAULT_EMOJI = "🤖"
_DEFAULT_COLOR = "#409EFF"


# ---------------- 目录与种子 ----------------

def packs_dir() -> Path:
    """专家包目录 backend/data/expert-packs：不存在则创建，并从 seed_experts 复制种子（幂等）。

    仅目录首次创建时复制种子；此后文件归用户所有（删除不复活，恢复出厂走 reset_from_seed）。
    """
    directory = DATA_DIR / PACKS_DIR_NAME
    if directory.is_dir():
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    copied = _copy_seed_files(directory, overwrite=False)
    if copied:
        logger.info("专家包目录已创建，并从内置种子复制 %d 个文件：%s", len(copied), directory)
    return directory


def reset_from_seed() -> dict:
    """从 seed_experts 重新复制全部内置种子文件（覆盖同名文件），用于「恢复出厂」。"""
    directory = packs_dir()
    copied = _copy_seed_files(directory, overwrite=True)
    logger.info("已从内置种子重置专家包目录：%d 个文件（%s）", len(copied), directory)
    return {"reset": copied, "dir": str(directory)}


def _copy_seed_files(directory: Path, overwrite: bool) -> list[str]:
    """把 seed_experts/*.md 复制进专家包目录，返回复制的文件名列表。"""
    copied: list[str] = []
    if not SEED_DIR.is_dir():
        logger.warning("内置专家种子目录不存在：%s", SEED_DIR)
        return copied
    for src in sorted(SEED_DIR.glob("*.md")):
        dst = directory / src.name
        if dst.is_file() and not overwrite:
            continue
        shutil.copyfile(src, dst)
        copied.append(src.name)
    return copied


# ---------------- 解析 ----------------

def parse_pack_file(path: Path | str) -> dict:
    """解析专家包 .md 文件，返回 {name,label,emoji,color,description,keywords,tools,sort,body}。

    缺 name 或正文（角色提示词）抛 ValueError（中文信息）；name 须为小写字母/数字/下划线。
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取专家包文件 {path.name}：{exc}") from exc
    # 换行归一化：Windows 下产出的文件可能是 CRLF，与数据库（LF）比对前必须统一，
    # 否则 export→import 往返恒为「有差异」
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"专家包 {path.name} 缺少 YAML frontmatter（文件须以 --- 开头）")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError(f"专家包 {path.name} 的 frontmatter 未闭合（缺少结尾 ---）")

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"专家包 {path.name} 的 frontmatter 行不是 key: value 格式：{stripped}")
        meta[key.strip()] = _unquote(value.strip())

    name = (meta.get("name") or "").strip()
    if not name:
        raise ValueError(f"专家包 {path.name} 缺少 name 字段（frontmatter 中必须包含 name）")
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"专家包 {path.name} 的 name「{name}」不合法：须以小写字母开头，仅含小写字母/数字/下划线"
        )

    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise ValueError(f"专家包 {path.name} 缺少正文（frontmatter 之后须有角色提示词内容）")

    sort_raw = meta.get("sort") or "0"
    try:
        sort = int(sort_raw)
    except ValueError:
        raise ValueError(f"专家包 {path.name} 的 sort「{sort_raw}」不是整数") from None

    return {
        "name": name,
        "label": meta.get("label") or name,
        "emoji": meta.get("emoji") or _DEFAULT_EMOJI,
        "color": meta.get("color") or _DEFAULT_COLOR,
        "description": meta.get("description") or "",
        "keywords": _to_list(meta.get("keywords") or ""),
        "tools": _to_list(meta.get("tools") or ""),
        "sort": sort,
        "body": body,
    }


def _unquote(value: str) -> str:
    """去掉值外层成对的单/双引号；双引号值按 _quote 的规则反转义。

    _quote 只产生两种转义：\\" 与 \\\\；反转义须先 \\<引号> 后 \\\\（先拆引号
    转义再拆反斜杠转义），否则 export→parse 往返会多出一层反斜杠。
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def _to_list(value: str) -> list[str]:
    """把 frontmatter 的列表值转成字符串列表：支持逗号分隔字符串与 [a, b] 行内列表。

    中英文逗号都视为分隔符（中文用户惯用全角逗号，不切分会粘成一个词）。
    """
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item for item in (_unquote(x.strip()) for x in text.replace("，", ",").split(",")) if item]


def _quote(value: str) -> str:
    """导出时给值包上双引号（转义反斜杠与引号），与种子文件风格一致。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_pack(row: AgentSpecModel) -> str:
    """把 agent_specs 行渲染为专家包 .md 文本（frontmatter + 正文）。"""
    tools = ",".join(row.tools or [])
    keywords = ",".join(row.keywords or [])
    lines = [
        "---",
        f"name: {row.name}",
        f"label: {row.display_name or row.name}",
        f"emoji: {row.emoji or _DEFAULT_EMOJI}",
        f"color: {row.color or _DEFAULT_COLOR}",
        f"description: {_quote(row.description or '')}",
        f"keywords: {_quote(keywords)}",
        f"tools: {_quote(tools)}",
        f"sort: {int(row.sort or 0)}",
        "---",
        "",
        (row.role_prompt or "").strip(),
        "",
    ]
    return "\n".join(lines)


# ---------------- 一致性比对 ----------------

def _diff_fields(row: AgentSpecModel, data: dict) -> list[tuple[str, str]]:
    """比对数据库行与文件内容，返回 [(字段名, 差异描述), ...]（空 = 完全一致）。

    字段名供界面列出「差在哪」（diff_fields），描述供 diff 简述。
    """
    diffs: list[tuple[str, str]] = []
    if (row.display_name or "") != data["label"]:
        diffs.append(("显示名", "显示名不同"))
    if (row.role_prompt or "").strip() != data["body"]:
        diffs.append(("角色提示词", "角色提示词不同"))
    if list(row.keywords or []) != data["keywords"]:
        diffs.append(("关键词", "关键词不同"))
    # 工具按「文件声明的工具是否都已在白名单」比对（导入为并集语义，不比对多出部分）
    db_tools = set(row.tools or [])
    missing_in_db = [t for t in data["tools"] if t not in db_tools]
    if missing_in_db:
        diffs.append(("工具", "工具不同（文件有 " + "、".join(missing_in_db[:3]) + " 未入库）"))
    if (row.emoji or "") != data["emoji"]:
        diffs.append(("图标", "图标不同"))
    if (row.color or "") != data["color"]:
        diffs.append(("颜色", "颜色不同"))
    if (row.description or "") != data["description"]:
        diffs.append(("描述", "描述不同"))
    if int(row.sort or 0) != data["sort"]:
        diffs.append(("排序", "排序不同"))
    return diffs


def list_packs(db) -> list[dict]:
    """扫描专家包目录，返回文件清单及与数据库的一致性状态。

    每项：{name,label,description,file,valid,error,imported,diff,diff_fields}。
    imported=是否已导入 agent_specs；diff=与数据库行的差异简述（None 表示未导入或完全一致）；
    diff_fields=差异字段名列表（如 ["显示名","角色提示词"]，供界面列出具体差异项）。
    """
    items: list[dict] = []
    for path in sorted(packs_dir().glob("*.md")):
        entry = {
            "name": path.stem,
            "label": "",
            "description": "",
            "file": path.name,
            "valid": True,
            "error": None,
            "imported": False,
            "diff": None,
            "diff_fields": [],
        }
        try:
            data = parse_pack_file(path)
        except ValueError as exc:
            entry["valid"] = False
            entry["error"] = str(exc)
            items.append(entry)
            continue
        entry["name"] = data["name"]
        entry["label"] = data["label"]
        entry["description"] = data["description"]
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == data["name"]).one_or_none()
        if row is not None:
            entry["imported"] = True
            diffs = _diff_fields(row, data)
            entry["diff"] = "；".join(desc for _, desc in diffs) if diffs else None
            entry["diff_fields"] = [name for name, _ in diffs]
        items.append(entry)
    return items


# ---------------- 导入 / 导出 ----------------

def import_pack(name: str, db) -> dict:
    """把指定专家包 upsert 进 agent_specs，并使团队缓存失效。

    返回 {name,action,is_builtin,file,diff}；action ∈ created/updated/unchanged。
    文件不存在抛 FileNotFoundError；文件无法解析抛 ValueError。
    """
    path = _find_pack_path(name)
    if path is None:
        raise FileNotFoundError(f"专家包 {name} 不存在（目录：{packs_dir()}）")
    data = parse_pack_file(path)

    row = db.query(AgentSpecModel).filter(AgentSpecModel.name == data["name"]).one_or_none()
    diffs = _diff_fields(row, data) if row is not None else []
    if row is not None and not diffs:
        # 已导入且与文件一致：不写库，仅返回现状
        return {
            "name": data["name"],
            "action": "unchanged",
            "is_builtin": bool(row.is_builtin),
            "file": path.name,
            "diff": [],
        }

    if row is None:
        row = AgentSpecModel(name=data["name"], is_builtin=False, enabled=True)
        action = "created"
    else:
        action = "updated"
    # is_builtin/enabled/model_id/skills 保持原值（新建行 is_builtin=False）
    row.display_name = data["label"]
    row.emoji = data["emoji"]
    row.color = data["color"]
    row.description = data["description"]
    row.role_prompt = data["body"]
    row.keywords = data["keywords"]
    # 工具白名单取并集：导入不缩小白名单——模块预置（preset_experts）与界面勾选
    # 授予的工具不会因导入旧文件而丢失；文件只负责「声明追加」。
    merged = list(row.tools or [])
    for tool in data["tools"]:
        if tool not in merged:
            merged.append(tool)
    row.tools = merged
    row.sort = data["sort"]
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_team_cache()
    logger.info("专家包导入：%s（%s，is_builtin=%s）", row.name, action, row.is_builtin)
    return {
        "name": row.name,
        "action": action,
        "is_builtin": bool(row.is_builtin),
        "file": path.name,
        "diff": [desc for _, desc in diffs],
    }


def import_all(db) -> dict:
    """导入目录内全部有效专家包，返回 {imported,skipped,errors}。

    imported=实际写入的专家名；skipped=已存在且与文件一致的专家名；
    errors=[{file,error}]（文件解析失败或写入失败，不中断其余导入）。
    """
    imported: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    for path in sorted(packs_dir().glob("*.md")):
        try:
            data = parse_pack_file(path)
        except ValueError as exc:
            errors.append({"file": path.name, "error": str(exc)})
            continue
        try:
            result = import_pack(data["name"], db)
        except Exception as exc:
            # 逐文件隔离：任何异常（含 DB 写入失败）都计入 errors，不中断整批
            logger.warning("专家包导入失败：%s", path.name, exc_info=True)
            errors.append({"file": path.name, "error": str(exc)})
            continue
        if result["action"] == "unchanged":
            skipped.append(data["name"])
        else:
            imported.append(data["name"])
    return {"imported": imported, "skipped": skipped, "errors": errors}


def export_pack(expert_name: str, db) -> dict:
    """把 agent_specs 中的专家导出为 .md 文件（覆盖同名文件），并使团队缓存失效。

    返回 {name,file,path}；数据库中无此专家抛 FileNotFoundError。
    """
    if not expert_name or not _NAME_RE.fullmatch(expert_name):
        raise ValueError(f"非法的专家名称：{expert_name!r}（仅小写字母/数字/下划线，且以字母开头）")
    row = db.query(AgentSpecModel).filter(AgentSpecModel.name == expert_name).one_or_none()
    if row is None:
        raise FileNotFoundError(f"数据库中不存在专家 {expert_name}，无法导出")
    path = packs_dir() / f"{row.name}.md"
    # 原子写：tmp + os.replace，写入中断时已有同名文件保持原样，不留半截内容
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(render_pack(row), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"导出专家 {expert_name} 失败：{exc}") from exc
    invalidate_team_cache()
    logger.info("专家包导出：%s -> %s", row.name, path)
    return {"name": row.name, "file": path.name, "path": str(path)}


# ---------------- 读取 / 删除 ----------------

def read_pack_file(name: str) -> dict:
    """读取专家包 .md 原文，返回 {name,file,content}。

    名称不合法（含路径分隔符、.. 等）抛 ValueError；文件不存在抛 FileNotFoundError。
    """
    path = _safe_pack_path(name)
    if not path.is_file():
        found = _find_pack_path(name)
        if found is None:
            raise FileNotFoundError(f"专家包 {name} 不存在")
        path = found
    return {"name": name, "file": path.name, "content": path.read_text(encoding="utf-8")}


def delete_pack(name: str) -> dict:
    """删除专家包 .md 文件（不影响数据库中的专家），返回 {deleted,file}。

    名称不合法抛 ValueError；文件不存在抛 FileNotFoundError。
    """
    path = _safe_pack_path(name)
    if not path.is_file():
        found = _find_pack_path(name)
        if found is None:
            raise FileNotFoundError(f"专家包 {name} 不存在")
        path = found
    path.unlink()
    logger.info("专家包已删除：%s", path)
    return {"deleted": name, "file": path.name}


def _safe_pack_path(name: str) -> Path:
    """把专家名解析为包目录内的 .md 路径；名称不合法（防路径穿越）抛 ValueError。"""
    if not name or not _NAME_RE.fullmatch(name):
        raise ValueError(f"非法的专家包名称：{name!r}（仅小写字母/数字/下划线，且以字母开头）")
    directory = packs_dir()
    path = (directory / f"{name}.md").resolve()
    if path.parent != directory.resolve():
        raise ValueError(f"非法的专家包路径：{name!r}")
    return path


def _find_pack_path(name: str) -> Path | None:
    """按 stem 或 frontmatter name 定位专家包文件；找不到返回 None。"""
    if not name or not _NAME_RE.fullmatch(name):
        return None
    directory = packs_dir()
    direct = directory / f"{name}.md"
    if direct.is_file():
        return direct
    for path in sorted(directory.glob("*.md")):
        try:
            if parse_pack_file(path)["name"] == name:
                return path
        except ValueError:
            continue
    return None
