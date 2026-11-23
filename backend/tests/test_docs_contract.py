"""文档契约测试：用户手册/数据说明/API 文档必须与当前实现保持一致。

只读仓库内文档、前端 manifest、后端配置与 OpenAPI 路由表；不访问真实服务、
不读写 ``backend/data``、不依赖任何本机秘密。文档改动导致菜单/路由/安全事实
失配时，这里会先失败。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
USAGE = REPO / "使用说明.md"
DATA_DOC = REPO / "数据收传使用说明.md"
API_DOC = REPO / "API.md"
README = REPO / "README.md"
SPEC = REPO / "spec.md"
ALGO = REPO / "算法实现逻辑.md"
TOPOLOGY = REPO / "功能模块拓扑图.md"
NOTES = REPO / "注意事项.md"
TEMPLATE_README = REPO / "数据模板" / "README.md"
CONFIG = REPO / "backend" / "app" / "config.py"
FRONTEND_MODULES = REPO / "frontend" / "src" / "modules"
TEMPLATES = REPO / "数据模板"

DOCS = [USAGE, DATA_DOC, API_DOC, README, SPEC, ALGO, TOPOLOGY, NOTES, TEMPLATE_README]

# 固定八段式字段：每节按「用途/入口/前置/步骤/产物/限制/常见错误/下一步」编写
REQUIRED_FIELDS = ("用途", "入口", "前置", "步骤", "产物", "限制", "常见错误", "下一步")

# 不进侧栏、仅路由可达的模块（开发样板）：文档需明确说明，但不作为菜单章节
HIDDEN_MODULES = {"finance-hello"}

# 非 manifest 菜单的固定章节（基础 5 + AI 助手 + AI 配置 4 + 定时任务）
FIXED_SECTIONS = (
    "工作台概览",
    "任务管理",
    "日程管理",
    "笔记管理",
    "全局搜索",
    "AI 助手",
    "知识与记忆",
    "MCP 工具",
    "技能包管理",
    "专家团文件",
    "定时任务",
)

# 后端静态路由前缀（非模块）：文档必须逐一覆盖；WorkBuddy 实际挂在 /api/assistant 下
STATIC_PREFIXES = (
    "/api/health",
    "/api/tasks",
    "/api/schedules",
    "/api/notes",
    "/api/search",
    "/api/dashboard",
    "/api/assistant",
    "/api/assistant/workbuddy",
    "/api/jobs",
    "/api/knowledge",
    "/api/mcp",
    "/api/skills",
    "/api/experts",
    "/api/debug",
    "/api/templates",
)

PLACEHOLDER_PATTERNS = (
    r"待补充",
    r"待完善",
    r"占位内容",
    r"占位文本",
    r"位置占位",
    r"占位行",
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bFIXME\b",
    r"\{\{.*?\}\}",
    r"lorem ipsum",
)

def _read(path: Path) -> str:
    assert path.exists(), f"缺少文档：{path.name}"
    return path.read_text(encoding="utf-8")


def _config_version() -> str:
    text = _read(CONFIG)
    match = re.search(r'^\s*version:\s*str\s*=\s*"([^"]+)"', text, flags=re.M)
    assert match, "config.py 中找不到版本号定义"
    return match.group(1)


def _menu_block(text: str, start: int) -> str | None:
    """取 menu 字面量：支持单行对象、多行对象与 null。"""
    if text[start - 4:start] == "null":
        return "null"
    begin = start - 1
    depth = 0
    for idx in range(begin, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[begin:idx + 1]
    return None


def _frontend_menus() -> list[dict[str, str]]:
    """解析前端 manifest.js 的 menu 对象（单行/多行/null），返回全部可见菜单。"""
    menus: list[dict[str, str]] = []
    for path in sorted(FRONTEND_MODULES.glob("*/manifest.js")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"menu:\s*(null|\{)", text)
        assert match, f"{path.name} 缺少 menu 定义"
        raw = _menu_block(text, match.end())
        assert raw is not None, f"{path.name} 的 menu 对象括号不完整"
        if raw == "null":
            continue
        menu: dict[str, str] = {"module": path.parent.name}
        for key in ("group", "subgroup", "title", "path"):
            found = re.search(rf"{key}:\s*'([^']+)'", raw)
            if found:
                menu[key] = found.group(1)
        assert menu.get("title") and menu.get("path"), f"{path.name} 菜单缺少 title/path"
        menus.append(menu)
    assert len(menus) >= 25, f"只解析到 {len(menus)} 个菜单，manifest 解析可能失效"
    return menus


def _module_route_counts() -> dict[str, int]:
    """每个可见菜单对应模块的路由条数（用于确认子页在手册中有入口）。"""
    counts: dict[str, int] = {}
    for path in sorted(FRONTEND_MODULES.glob("*/manifest.js")):
        text = path.read_text(encoding="utf-8")
        name = re.search(r"name:\s*'([^']+)'", text)
        if not name:
            continue
        counts[name.group(1)] = len(re.findall(r"path:\s*'/", text))
    return counts


def _headings(text: str, level: int) -> list[tuple[int, str]]:
    """返回指定级别标题的 (行号, 标题文本)。"""
    prefix = "#" * level + " "
    out = []
    for idx, line in enumerate(text.splitlines()):
        if line.startswith(prefix):
            out.append((idx, line[len(prefix):].strip()))
    return out


def _section(text: str, title: str, level: int = 3) -> str | None:
    """按标题关键词取章节正文（到下一个同级或更高级标题为止）。"""
    lines = text.splitlines()
    for idx, heading in _headings(text, level):
        if title in heading:
            end = len(lines)
            for jdx in range(idx + 1, len(lines)):
                if re.match(r"^#{1,%d} " % level, lines[jdx]):
                    end = jdx
                    break
            return "\n".join(lines[idx:end])
    return None


def _assert_fields(section: str, label: str) -> None:
    missing = [field for field in REQUIRED_FIELDS if f"**{field}**" not in section]
    assert not missing, f"《使用说明》章节「{label}」缺少固定字段：{'、'.join(missing)}"


# ---------------- 版本与章节结构 ----------------


def test_versions_stay_on_config_single_source():
    """所有权威文档不得声明超出 config 的“未来版本”；spec 封面必须与 config 一致。"""
    version = _config_version()
    usage = _read(USAGE)
    assert f"v{version}" in "\n".join(usage.splitlines()[:8]), (
        f"《使用说明》封面未使用 config.py 的版本 v{version}"
    )
    assert f"v{version}" in _read(README), f"README 未使用 config.py 的版本 v{version}"
    spec_head = "\n".join(_read(SPEC).splitlines()[:10])
    assert f"v{version}" in spec_head, f"spec 封面未使用 config.py 的版本 v{version}"
    for path in (USAGE, DATA_DOC, API_DOC, README, SPEC, ALGO, TOPOLOGY, NOTES, TEMPLATE_README):
        text = _read(path)
        major, minor = (int(part) for part in version.split(".")[:2])
        for future in (f"v{major}.{minor + 1}.0", f"v{major}.{minor + 2}.0"):
            assert future not in text, f"{path.name} 声明了 config 之外的版本 {future}"


def test_usage_has_zero_to_start_and_required_chapters():
    text = _read(USAGE)
    for keyword in ("从零上手", "练习", "备份与恢复", "换机", "安全边界", "FAQ"):
        assert keyword in text, f"《使用说明》缺少章节：{keyword}"
    # 至少六条练习：标题必须是「练习 N」编号
    exercises = re.findall(r"^#{3,4}\s*练习\s*(\d+)", text, flags=re.M)
    assert len(set(exercises)) >= 6, f"端到端练习不足 6 条：{sorted(set(exercises))}"
    assert "六条端到端练习" not in text, "目录仍写六条，实际已扩为八条"


def test_usage_covers_every_visible_menu_section():
    text = _read(USAGE)
    for menu in _frontend_menus():
        title = menu["title"]
        assert title in text, f"菜单「{title}」（{menu['path']}）未出现在《使用说明》"
        assert menu["path"] in text, f"菜单路径 {menu['path']} 未出现在《使用说明》"
        section = _section(text, title)
        assert section is not None, f"缺少「{title}」章节标题（### 级）"
        _assert_fields(section, title)


def test_usage_fixed_sections_use_required_fields():
    text = _read(USAGE)
    for title in FIXED_SECTIONS:
        section = _section(text, title)
        assert section is not None, f"缺少固定章节「{title}」"
        _assert_fields(section, title)


def test_hidden_module_is_documented_as_route_only():
    text = _read(USAGE)
    assert "/finance/hello" in text, "未说明模块示例的直达路由"
    assert "不进侧栏" in text, "未说明模块示例不进侧栏"


# ---------------- 菜单地图与入口真实性 ----------------


def test_menu_map_lists_group_subgroup_and_order():
    text = _read(USAGE)
    menus = _frontend_menus()
    for group in sorted({m["group"] for m in menus}):
        assert group in text, f"菜单地图缺少分组：{group}"
    for subgroup in sorted({m.get("subgroup", "") for m in menus if m.get("subgroup")}):
        assert subgroup in text, f"菜单地图缺少二级子分组：{subgroup}"
    for menu in menus:
        assert menu["group"] in text and menu["title"] in text


def test_usage_documents_new_entries_and_api_only_capabilities():
    text = _read(USAGE)
    for keyword in (
        "银行流水模板",              # finance_bankflow
        "数据质量",                  # business_dq
        "技能市场",                  # skills market
        "记忆类型",                  # 记忆四类
        "导出 MD",
        "导入 MD",
        "页面问 AI",
        "看板产物校验",              # board verify（API-only 必须标注）
        "仅提供接口",
    ):
        assert keyword in text, f"《使用说明》缺少新入口/能力说明：{keyword}"
    # 问 AI 按钮的真实覆盖：不得把未接线的页面写成有按钮
    assert "本页没有问 AI 按钮" in text or "未接入问 AI" in text


def test_usage_fixes_known_old_claims():
    text = _read(USAGE)
    checks = {
        "迁移整包": "workbench_data_",
        "恢复快照": "before_restore",
        "本地配置快照": "restore-local",
        "BIP 判重含期间": "账簿+期间+凭证号",
        "非回环 503": "503",
        "ALLOW_NON_LOOPBACK": "ALLOW_NON_LOOPBACK",
        "CORS 来源": "CORS_ORIGINS",
        "上传总限额": "128MB",
        "发票批次分页": "50 / 100 / 200",
        "高新以档案为准": "以企业档案为准",
        "A200000 不导年度": "A100000",
        "预缴/含调整双口径": "季度预缴口径",
        "草稿更正": "更正",
        "HR 社保基数留空": "留空",
        "HR 显式 0": "显式 0",
        "DQ issue_count": "issue_count",
        "备份敏感配置": "SENSITIVE",
    }
    missing = [name for name, needle in checks.items() if needle not in text]
    assert not missing, f"《使用说明》仍缺关键口径：{', '.join(missing)}"


# ---------------- 数据收传文档 ----------------


def test_data_doc_names_every_template_file():
    text = _read(DATA_DOC)
    files = sorted(p.name for p in TEMPLATES.iterdir() if p.is_file() and p.name != "README.md")
    assert len(files) >= 10, f"数据模板不足 10 份：{files}"
    missing = [name for name in files if name not in text]
    assert not missing, f"《数据收传使用说明》未列出模板：{missing}"


def test_data_doc_covers_import_export_sections():
    text = _read(DATA_DOC)
    for keyword in (
        "数据池清洗",
        "费用分类",
        "POS 对账",
        "现金流填报",
        "发票处理",
        "企业记账",
        "个人记账",
        "银行流水",
        "企业所得税",
        "印花税",
        "增值税",
        "个人所得税",
        "残保金",
        "招商",
        "营运",
        "企划",
        "法务",
        "行政人事",
        "工程物业",
        "总经理经营聚合",
        "报表看板",
    ):
        assert keyword in text, f"《数据收传使用说明》缺少章节：{keyword}"


def test_data_doc_field_enums_limits_and_idempotency():
    text = _read(DATA_DOC)
    for needle in (
        "200,000 行",
        "20MB",
        "10MB",
        "5MB",
        "50MB",
        "200 张",
        "128MB",
        "skipped_duplicates",
        "账簿+期间+凭证号",
        "30 人",
        "1%",
        "90%",
        "50%",
        "2 倍",
        "1%/3%/5%",
        "累计预扣",
        "示例-请替换",
        "不要重命名",
        "template_skeleton.xlsx",
    ):
        assert needle in text, f"《数据收传使用说明》缺少字段/限额口径：{needle}"


def test_data_doc_has_error_quick_reference():
    text = _read(DATA_DOC)
    assert "报错速查" in text
    assert text.count("|") > 200, "报错速查表疑似为空"


# ---------------- API 文档 ----------------


def _openapi_paths() -> dict[str, list[str]]:
    from app.main import app

    schema = app.openapi()
    out: dict[str, list[str]] = {}
    for path, ops in schema.get("paths", {}).items():
        methods = sorted(m.upper() for m in ops)
        if path.startswith("/api"):
            out[path] = methods
    return out


def test_api_doc_covers_every_openapi_route():
    paths = _openapi_paths()
    assert len(paths) >= 250, f"OpenAPI 只解析到 {len(paths)} 个 /api 路径，路由注册可能失效"
    lines = _read(API_DOC).splitlines()
    missing: list[str] = []
    for path, methods in paths.items():
        matched = [line for line in lines if path in line]
        if not matched:
            missing.append(path)
            continue
        joined = "\n".join(matched)
        for method in methods:
            if f"`{method}`" not in joined:
                missing.append(f"{method} {path}")
    assert not missing, "API.md 未覆盖以下路由（前 20 条）：" + "、".join(missing[:20])


def test_api_doc_covers_static_prefixes_and_security_conventions():
    text = _read(API_DOC)
    for prefix in STATIC_PREFIXES:
        assert prefix in text, f"API.md 缺少静态路由前缀：{prefix}"
    for needle in ("ALLOW_NON_LOOPBACK", "CORS_ORIGINS", "503", "403", "Origin", "DPAPI"):
        assert needle in text, f"API.md 缺少安全约定：{needle}"


# ---------------- 开发文档追平 ----------------


def test_dev_docs_track_recent_capabilities():
    algo = _read(ALGO)
    topology = _read(TOPOLOGY)
    spec = _read(SPEC)
    for doc_name, text in (("算法实现逻辑", algo), ("功能模块拓扑图", topology), ("spec", spec)):
        for needle in ("银行流水", "数据质量", "看板", "发票"):
            assert needle in text, f"{doc_name} 未追平能力：{needle}"
    for needle in ("滚动摘要", "路由缓存", "trace", "计划任务"):
        assert needle in algo or needle in spec, f"算法/spec 未追平：{needle}"
    assert "finance_bankflow" in topology or "银行流水" in topology
    assert "business_dq" in topology or "数据质量" in topology


def test_readme_links_manual_and_is_quick_entry():
    text = _read(README)
    for needle in ("使用说明.md", "数据收传使用说明.md", "API.md", "启动工作台.bat"):
        assert needle in text, f"README 缺少快速入口：{needle}"
    assert "docs/" not in text or "docs/screenshots" not in text, "README 引用了分发包已剔除的 docs 图片"


def test_notes_doc_has_current_safety_facts():
    text = _read(NOTES)
    for needle in ("ALLOW_NON_LOOPBACK", "503", "CORS_ORIGINS", "workbench_data_", "before_restore", "DPAPI"):
        assert needle in text, f"《注意事项》缺少安全事实：{needle}"


def test_template_readme_lists_all_templates_and_no_hand_edit():
    text = _read(TEMPLATE_README)
    files = sorted(p.name for p in TEMPLATES.iterdir() if p.is_file() and p.name != "README.md")
    missing = [name for name in files if name not in text]
    assert not missing, f"数据模板 README 未列出：{missing}"
    assert "不要重命名" in text
    assert "不要手改" in text or "不要手工修改" in text


# ---------------- 占位噪声与悬空引用 ----------------


@pytest.mark.parametrize("path", DOCS, ids=[p.name for p in DOCS])
def test_no_placeholder_noise(path: Path):
    text = _read(path)
    for pattern in PLACEHOLDER_PATTERNS:
        # 大小写敏感：`todo` 是任务状态枚举，不是占位噪声
        match = re.search(pattern, text)
        assert not match, f"{path.name} 存在占位噪声：{match.group(0)!r}"


@pytest.mark.parametrize("path", [USAGE, DATA_DOC, README, SPEC, ALGO, TOPOLOGY, NOTES], ids=lambda p: p.name)
def test_markdown_relative_links_resolve(path: Path):
    text = _read(path)
    dangling = []
    for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        clean = target.split("#", 1)[0].strip()
        if not clean:
            continue
        if not (path.parent / clean).exists():
            dangling.append(f"{label} -> {target}")
    assert not dangling, f"{path.name} 存在悬空引用：{dangling}"


def test_usage_section_references_stay_in_range():
    text = _read(USAGE)
    refs = {int(n) for n in re.findall(r"§\s*(\d+)", text)}
    assert refs, "《使用说明》没有章节交叉引用"
    assert max(refs) <= 14, f"交叉引用指向不存在的章节：§{max(refs)}"


# ---------------- 生成脚本与索引新鲜度 ----------------


def _load_index_generator():
    import importlib.util

    path = REPO / "dev" / "gen_api_index.py"
    assert path.exists(), "缺少路由索引生成脚本 dev/gen_api_index.py"
    spec = importlib.util.spec_from_file_location("gen_api_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_index_generator_is_deterministic_and_sorted():
    module = _load_index_generator()
    schema = {
        "paths": {
            "/api/z": {"post": {"summary": "写|操作"}},
            "/api/a": {"get": {"summary": "读"}, "delete": {"summary": "删"}},
            "/other/x": {"get": {"summary": "非 API"}},
        }
    }
    table = module.render_index(schema)
    lines = table.splitlines()
    assert lines[0] == "| 方法 | 路径 | 说明 |"
    assert "`/other/x`" not in table, "只应输出 /api 前缀"
    assert lines[2] == "| `GET` | `/api/a` | 读 |", "方法顺序/路径排序应稳定"
    assert "| `DELETE` | `/api/a` | 删 |" in lines
    assert "| `POST` | `/api/z` | 写\\|操作 |" in table, "表格管道符必须转义"
    assert module.render_index(schema) == table, "同一 schema 渲染必须确定"


def test_api_doc_full_index_is_current():
    """API.md 的全量索引必须与当前 OpenAPI 完全一致（防止新增路由后文档漂移）。"""
    module = _load_index_generator()
    from app.main import app

    rendered = module.render_index(app.openapi())
    assert rendered.splitlines()[0] == "| 方法 | 路径 | 说明 |"
    assert rendered in _read(API_DOC), (
        "API.md 的全量路由索引已过期：请重新运行 "
        "`cd backend && PYTHONPATH=. venv/Scripts/python.exe ../dev/gen_api_index.py` 并替换索引表"
    )



# ---------------- 语义契约：复核修复项 ----------------


def test_exercises_are_executable_without_impossible_cleanup():
    usage = _read(USAGE)
    ex6 = _section(usage, "练习 6", 3)
    assert ex6, "缺少练习 6"
    assert "不登记收款" in ex6 and "未收款" in ex6, "练习 6 应避免产生不可删除的已收款账单"
    assert "登记收款 4000" not in ex6, "练习 6 仍要求收款，但已收款账单/合同按设计不可删除"
    assert "删除合同" in ex6 and "阶段改为「已流失」" in ex6, "练习 6 缺少可执行的清理顺序"
    assert "删除合同（无收款，会自动连同未收款账单）" in ex6, "删除合同会级联未收款账单，练习需写清"

    ex7 = _section(usage, "练习 7", 3)
    assert ex7, "缺少练习 7"
    assert "第一次恢复" in ex7 and "第二次恢复" in ex7, "练习 7 需先建旧目标再恢复才能产生快照"
    assert "before_restore" in ex7 and "--force" in ex7
    assert "不存在（存在就先删）" in ex7, "练习 7 需先确保临时目标不存在"

    ex8 = _section(usage, "练习 8", 3)
    assert ex8, "缺少练习 8"
    assert "若新机不装 Node" in ex8 and "保留 `frontend/dist`" in ex8, (
        "练习 8 排除 dist 与无 Node 依赖矛盾"
    )


def test_agent_write_boundary_matches_actual_tools():
    for path in (USAGE, NOTES, README, SPEC):
        text = _read(path)
        assert "pl_add_transaction" in text, f"{path.name} 未写明财税专员可新增个人记账"
    section = _section(_read(USAGE), "工具审计", 3)
    assert section and "新增一笔个人记账" in section
    assert "不能物理删除" in section


def test_builtin_expert_delete_uses_tombstone():
    usage = _read(USAGE)
    for heading in ("专家团队", "专家团文件"):
        section = _section(usage, heading, 3)
        assert section and "墓碑" in section, f"「{heading}」未说明删除墓碑"
    assert "重启不会自动补回" in usage
    assert "墓碑" in _read(NOTES)
    assert "墓碑" in _read(SPEC)


def test_dq_overall_score_is_average_of_available_domains():
    usage = _section(_read(USAGE), "数据质量", 3)
    assert usage and "可用域" in usage and "平均" in usage, "DQ 总分口径应为可用域平均"
    assert "总分（100 − Σ扣分" not in usage, "DQ 总分不是全局 Σ扣分"
    data_sec = _section(_read(DATA_DOC), "11.8", 3)
    assert data_sec and "可用域" in data_sec, "数据收传的 DQ 字段说明仍是旧口径"


def test_workbuddy_routes_use_real_prefix():
    api = _read(API_DOC)
    assert "/api/assistant/workbuddy" in api
    assert "/api/workbuddy" not in api, "API.md 仍写错误的 /api/workbuddy 前缀"


def _norm_api_path(path: str) -> str:
    """路径参数名不参与比较：{id}/{model_id} 统一为 {}；去尾部斜杠。"""
    return re.sub(r"\{[^}]+\}", "{}", path).rstrip("/")


def test_api_doc_paths_are_real_and_scan_backticks_and_bare():
    """API.md 里所有 /api 路径（反引号与裸文本）必须对应真实 OpenAPI 路径或其前缀。"""
    paths = {_norm_api_path(p) for p in _openapi_paths()}
    text = _read(API_DOC)
    tokens = set(re.findall(r"`(/api[^`\s]*)`", text))
    tokens |= set(re.findall(r"(?<![\w`])(/api/[A-Za-z0-9_/{}\.\-]+)", text))
    bad = []
    for raw in tokens:
        token = raw.split("?", 1)[0].rstrip(".,;:、")
        if not token or "*" in token or token == "/api":
            continue
        token = _norm_api_path(token)
        if token in paths:
            continue
        if any(path.startswith(token + "/") for path in paths):
            continue
        bad.append(token)
    assert not bad, f"API.md 存在不存在的路径：{sorted(set(bad))}"


def test_routing_docs_describe_five_levels_and_continuation():
    spec = _read(SPEC)
    topo = _read(TOPOLOGY)
    for name, text in (("spec", spec), ("拓扑", topo)):
        assert "五级路由" in text, f"{name} 未写五级路由"
        assert "续聊" in text, f"{name} 未写续聊保持"
        assert "四级路由" not in text, f"{name} 仍是旧的四级路由口径"


def test_topology_security_and_finance_counts():
    topo = _read(TOPOLOGY)
    assert "503" in topo, "拓扑未写非回环 503"
    assert "启动告警" not in topo, "拓扑仍写非回环仅告警"
    assert "财务 19" in topo, "拓扑未把财务模块数更新为 19"


def test_board_retry_wording_is_attempt_count():
    for path in (USAGE, SPEC, ALGO, TOPOLOGY):
        text = _read(path)
        assert "重试 2 次" not in text, f"{path.name} 仍把 2 次尝试写成重试 2 次"
        assert "尝试 2 次" in text, f"{path.name} 未写清最多尝试 2 次（重试 1 次）"


def test_minor_semantics_from_review():
    usage = _read(USAGE)
    data_doc = _read(DATA_DOC)
    notes = _read(NOTES)

    # 视觉模型清单只有 21 家预设预填，不是 33 家全有
    assert "21 家" in usage and "21 家" in notes
    assert "33 家预设已内置视觉模型清单" not in notes

    # POS ZIP 解压上限 200MB
    pos = _section(data_doc, "4. POS", 2)
    assert pos and "ZIP 解压 ≤200MB" in pos

    # 银行流水坏日期导出 400
    bank = _section(data_doc, "7. 银行流水", 2)
    assert bank and re.search(r"坏日期[^。]{0,100}400", bank)

    # 发票重新归类规则不可用 400
    invoice = _section(usage, "发票处理", 3)
    assert invoice and re.search(r"重新归类[^。]{0,100}400", invoice)

    # DQ 工具预置目标
    experts = _section(usage, "专家团队", 3)
    assert experts and "dq_summary" in experts and "realestate" in experts

    # spec 看板旧口径已替换
    spec = _read(SPEC)
    assert "m01–m04 管线子进程触发（单步 600s 超时）" not in spec
    assert "计划任务通道" in spec


def test_usage_cross_references_resolve_to_existing_targets():
    usage = _read(USAGE)
    chapter_map = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
        "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
    }
    chapters = set()
    for line in usage.splitlines():
        match = re.match(r"^## ([一二三四五六七八九十]+)、", line)
        if match:
            chapters.add(chapter_map[match.group(1)])
    sections = set(re.findall(r"^### (\d+(?:\.\d+)?)", usage, flags=re.M))
    for ref in re.findall(r"§\s*(\d+(?:\.\d+)?)", usage):
        if "." in ref:
            assert ref in sections, f"交叉引用 §{ref} 指向不存在的子章节"
        else:
            assert int(ref) in chapters, f"交叉引用 §{ref} 指向不存在的章"

    line = next(l for l in usage.splitlines() if "计划任务通道" in l and "§" in l)
    assert "§6.13" in line, f"计划任务通道引用错章：{line}"
    line = next(l for l in usage.splitlines() if "示例数据" in l and "§11" in l)
    assert "Q31" in line, f"示例数据应指向 Q31：{line}"


# ---------------- 复审修复：语义契约 ----------------


def test_exercise6_cleanup_matches_real_ui():
    usage = _read(USAGE)
    ex6 = _section(usage, "练习 6", 3)
    assert ex6, "缺少练习 6"
    assert "点「取消签约」" not in ex6, "UI 没有「取消签约」按钮，练习不得宣称该操作"
    assert "没有「取消签约」按钮" in ex6, "应明确说明 UI 无取消签约按钮，避免用户找不到"
    assert "编辑线索" in ex6 and "阶段改为「已流失」" in ex6, "清理链必须走真实路径：编辑线索阶段=已流失"
    assert "删除合同" in ex6 and "删除线索" in ex6 and "删除铺位" in ex6
    assert "开始/结束日期都改到上月" in ex6, "只改结束日期会造成开始晚于结束 409"
    assert "先改开始日期" in ex6, "改日期顺序需可执行"


def test_exercise6_cleanup_chain_works_at_service_layer(client):
    """按练习 6 的真实清理链跑一遍：删合同→阶段改已流失→删线索→删铺位。"""
    unit = client.post("/api/business/leasing/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "DOC-A101",
        "area": 100, "rent_price": 80, "property_fee": 12, "status": "vacant",
    })
    assert unit.status_code == 201, unit.text
    unit_id = unit.json()["id"]

    lead = client.post("/api/business/leasing/leads", json={
        "company": "文档练习客户", "stage": "signed", "unit_id": unit_id,
        "rent_form": "fixed", "free_rent_months": 0, "increase_rate": 0,
    })
    assert lead.status_code == 201, lead.text
    lead_id = lead.json()["id"]

    contract = client.post("/api/business/operations/contracts", json={
        "contract_no": "HT-DOC-001", "tenant": "文档练习客户", "unit_id": unit_id,
        "area": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        "rent_monthly": 10000, "property_fee_monthly": 1200,
    })
    assert contract.status_code == 201, contract.text
    contract_id = contract.json()["id"]

    bill = client.post(f"/api/business/operations/contracts/{contract_id}/bills",
                       json={"period": "2026-10"})
    assert bill.status_code == 201, bill.text
    assert bill.json()["status"] == "unpaid"

    # 1) 删除合同（未收款；级联未收款账单）
    assert client.delete(f"/api/business/operations/contracts/{contract_id}").status_code == 200
    # 2) 编辑线索阶段=已流失（释放占用；不是“取消签约”）
    lost = client.put(f"/api/business/leasing/leads/{lead_id}", json={"stage": "lost"})
    assert lost.status_code == 200, lost.text
    assert lost.json()["stage"] == "lost"
    # 3) 删除线索、4) 删除铺位
    assert client.delete(f"/api/business/leasing/leads/{lead_id}").status_code == 200
    assert client.delete(f"/api/business/leasing/units/{unit_id}").status_code == 200


def test_visual_switch_reference_points_to_model_config():
    usage = _read(USAGE)
    line = next(l for l in usage.splitlines() if "视觉开关" in l and "§" in l)
    assert "§3.2" in line or "Q11" in line, f"视觉开关引用错目标：{line}"


def test_bankflow_bad_date_is_400_in_spec_and_data_doc():
    spec = _read(SPEC)
    assert "日期问题计入 skipped" not in spec, "spec 仍把银行流水坏日期写成 skipped"
    for name, text in (("spec", spec), ("数据收传", _read(DATA_DOC))):
        assert re.search(r"坏日期[^。]{0,120}400", text), f"{name} 未写坏日期导出 400"


def test_invoice_rule_unavailable_stage_semantics():
    spec = _read(SPEC)
    algo = _read(ALGO)
    usage = _read(USAGE)
    data_doc = _read(DATA_DOC)
    # 创建阶段：规则为空 → unchanged（不清空已有归类）
    assert "入库/创建阶段" in spec and "unchanged" in spec
    assert "入库/创建阶段返回 unchanged" in algo
    # 重新归类阶段：规则不可用 → 400
    assert "该接口在规则不可用时返回 400" in spec, "spec 未写 reclassify 400"
    assert re.search(r"重新归类[^。]{0,100}400", usage)
    assert re.search(r"重新归类返回 400", data_doc)


def test_q31_mentions_expert_tombstone():
    usage = _read(USAGE)
    line = next(l for l in usage.splitlines() if l.startswith("**Q31"))
    assert "墓碑" in line, "Q31 未说明删除内置专家是墓碑、重启不复活"


def test_board_failure_messages_use_attempt_wording():
    source = (REPO / "backend" / "app" / "modules" / "finance_board" / "service.py").read_text(encoding="utf-8")
    assert "自动重试 {_PIPELINE_ATTEMPTS} 次" not in source
    assert "重试 {attempt} 次后仍失败" not in source
    assert "最多尝试" in source and "重试 {attempt - 1} 次" in source


def test_data_doc_summary_pos_row_has_zip_limit():
    text = _read(DATA_DOC)
    line = next(l for l in text.splitlines() if l.startswith("| POS 对账"))
    assert "ZIP 解压 200MB" in line, f"数据收传总览 POS 行缺 ZIP 200MB：{line}"


# ---------------- 台账自检（忽略 \| 转义） ----------------

LEDGER = REPO / "2026-11-03_检查结果.md"


def _split_ledger_row(line: str) -> list[str]:
    """按未转义的 | 切分 Markdown 表格行；`\\|` 保留为单元格内文本。"""
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in line.strip().strip("|"):
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    cells.append("".join(current).strip())
    return cells


def test_ledger_self_check_ignores_escaped_pipes_and_counts_are_current():
    synthetic = _split_ledger_row(
        "| L-999 | P2 | 描述含 \\| 转义管道 | 已修待验 | 静态 | 证据 | Task |"
    )
    assert synthetic[0] == "L-999"
    assert synthetic[3] == "已修待验", "转义管道不得让状态列右移"

    text = _read(LEDGER)
    rows = [line for line in text.splitlines() if line.startswith("| L-")]
    assert len(rows) == 138, f"台账条目应为 138，实际 {len(rows)}"
    counts: dict[str, int] = {}
    for line in rows:
        status = _split_ledger_row(line)[3]
        counts[status] = counts.get(status, 0) + 1
    # Task 11 终验：128 条 Task 1~10 修复项 + L-176/L-177 已关闭；
    # 3 条已知限制、1 条外部验证、1 条首跑外阻、3 条外部阻断
    assert counts.get("已关闭") == 132, counts
    assert counts.get("已确认", 0) == 0, counts
    assert counts.get("待复核", 0) == 0, counts
    assert counts.get("已修待验", 0) == 0, counts
    assert counts.get("已知限制（已确认未修）") == 1, counts
    assert counts.get("外部验证") == 1, counts
    assert counts.get("已修待验（首跑外阻）") == 1, counts
    assert counts.get("外部阻断") == 3, counts
    assert sum(counts.values()) == 138, counts


def test_opencode_go_manual_matches_factory_preset():
    """手册必须与出厂预设一致：openai + /go/openai/v1；anthropic 仅是需自验的替代。"""
    import json

    presets = json.loads((REPO / "backend" / "app" / "agents" / "provider_presets.json").read_text(encoding="utf-8"))
    items = presets if isinstance(presets, list) else presets.get("presets") or presets.get("items") or []
    go = next(x for x in items if isinstance(x, dict) and x.get("preset_key") == "opencode_go")
    assert go.get("protocol") == "openai"
    assert go.get("base_url", "").endswith("/go/openai/v1")

    usage = _read(USAGE)
    assert "出厂预设" in usage and "go/openai/v1" in usage
    assert "需自行验证" in usage
    assert "anthropic 端点应配" not in usage, "手册仍把 anthropic 写成必须配置"
