# -*- coding: utf-8 -*-
"""skills-experts 区域深度 E2E 探测（端口 8220，独立库 + 独立技能池/专家包目录）。

覆盖两个页面：
- /skill-packs  技能包管理（SKILL.md）：种子/空态/rescan 播种、新建（合法/非法/重名）、
  文件查看与切换、磁盘编辑→重新扫描往返、启停持久化、rescan 发现磁盘新增、
  无效 SKILL.md 告警、删除取消/确认、路径穿越、XSS 转义、特殊目录名 URL 编码、异常态；
- /expert-packs 专家团文件：清单一致性、内置/自定义/有差异徽标与 tooltip、查看原文、
  单条导入（有差异/内置确认文案）、全部导入、导出为文件（同名差异确认）、
  从种子重置（提示需再点全部导入）、删除、未知包 404、路径穿越、导入后刷新。

隔离说明（重要）：
    后端把技能池写死在 app.config.DATA_DIR/skills、专家包写死在 DATA_DIR/expert-packs，
    config 没有环境变量可覆盖（config.py 无 data_dir 配置）。为满足「绝不读写真实
    backend/data/skills」，本探针启动前写一个仅探针使用的 sitecustomize.py 放进
    PYTHONPATH，在 app 模块导入时把两个根目录改到：
        dev/e2e/probe_results/skills-experts-skills/
        dev/e2e/probe_results/skills-experts-packs/
    （等价于单测里的 monkeypatch；技能种子仍从只读的 backend/seed_skills 复制，
    专家包种子仍从 backend/seed_experts 复制。末条 ISO 用例核对真实目录零改动。）

可重复运行：清库、清目录、自起自停；端口 8220。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "skills-experts"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
SKILLS_DIR = RESULTS / f"{AREA}-skills"
PACKS_DIR = RESULTS / f"{AREA}-packs"
DEBUG_DIR = RESULTS / f"{AREA}-debug"
BOOT_DIR = RESULTS / f"{AREA}-boot"
SHOTS = RESULTS / AREA / "shots"
LOG = RESULTS / f"{AREA}.server.log"
RESULT_JSON = RESULTS / f"{AREA}.json"
PORT = 8220
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 真实数据目录（只读快照，用于最后的隔离校验；绝不写入）
REAL_SKILLS = BACKEND / "data" / "skills"
REAL_PACKS = BACKEND / "data" / "expert-packs"
SEED_SKILL_NAMES = ["invoice-review", "month-end-close"]
SEED_EXPERT_FILES = sorted(p.name for p in (BACKEND / "seed_experts").glob("*.md"))

# 仅探针使用：把技能池/专家包根目录改到隔离目录（等价单测 monkeypatch）
BOOTSTRAP = r'''"""Probe-only bootstrap: redirect skill/expert pack roots to isolated dirs."""
import importlib.abc
import importlib.machinery
import os
import sys
from pathlib import Path

_SKILLS = Path(os.environ["PROBE_SKILLS_DIR"])
_PACKS_PARENT = Path(os.environ["PROBE_PACKS_PARENT"])
_PACKS_NAME = os.environ["PROBE_PACKS_NAME"]


def _skills_root():
    _SKILLS.mkdir(parents=True, exist_ok=True)
    return _SKILLS


_ATTR_PATCHES = {
    "app.agents.skill_store": {"store_dir": _skills_root},
    "app.agents.expert_packs": {"DATA_DIR": _PACKS_PARENT, "PACKS_DIR_NAME": _PACKS_NAME},
}


class _Loader(importlib.abc.Loader):
    def __init__(self, inner, patches):
        self.inner = inner
        self.patches = patches

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        self.inner.exec_module(module)
        for attr, value in self.patches.items():
            setattr(module, attr, value)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        patches = _ATTR_PATCHES.get(fullname)
        if not patches:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _Loader(spec.loader, patches)
        return spec


sys.meta_path.insert(0, _Finder())
'''


def rm_path(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def snapshot(path: Path):
    """返回目录下所有文件的 (相对路径, 大小, mtime_ns) 快照。"""
    if not path.is_dir():
        return []
    rows = []
    for p in sorted(path.rglob("*")):
        if p.is_file():
            st = p.stat()
            rows.append((p.relative_to(path).as_posix(), st.st_size, st.st_mtime_ns))
    return rows


# ---------- 环境准备：干净库 + 干净隔离目录 ----------
for d in (SKILLS_DIR, PACKS_DIR, FIN_DIR, DEBUG_DIR, BOOT_DIR, SHOTS):
    rm_path(d)
RESULTS.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)
BOOT_DIR.mkdir(parents=True, exist_ok=True)
(BOOT_DIR / "sitecustomize.py").write_text(BOOTSTRAP, encoding="utf-8")
if DB.exists():
    DB.unlink()

REAL_SKILLS_BEFORE = snapshot(REAL_SKILLS)
REAL_PACKS_BEFORE = snapshot(REAL_PACKS)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["SEED_ON_STARTUP"] = "0"
env["PYTHONIOENCODING"] = "utf-8"
# 探针专用隔离：见文件头「隔离说明」
env["PYTHONPATH"] = str(BOOT_DIR)
env["PROBE_SKILLS_DIR"] = str(SKILLS_DIR)
env["PROBE_PACKS_PARENT"] = str(RESULTS)
env["PROBE_PACKS_NAME"] = PACKS_DIR.name

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)

results = []   # {"id","name","passed","detail"}
console_errors = []
page_errors = []
failed_reqs = []
page = None
bugs = [
    {
        "id": "BUG-001", "severity": "高", "fixed": True,
        "status": "已修复（主 Agent 收口）",
        "symptom": "新建技能包时描述含「: 」/换行/「 #」会生成非法或被截断的 YAML frontmatter，包立即 valid=false",
        "root_cause": "skill_store.PACK_TEMPLATE 把 description 原样拼进 frontmatter，未做 YAML 引用/转义",
        "files": ["backend/app/agents/skill_store.py", "backend/tests/test_skill_store.py"],
        "tests": ["SKP-API-009", "pytest test_skill_store.py::test_create_and_delete_pack"],
    },
    {
        "id": "BUG-002", "severity": "中", "fixed": True,
        "status": "已修复（dist 已重建，复跑通过）",
        "symptom": "目录名含 # ? % 时启停、读文件、删除请求被 URL 截断（405/404）",
        "root_cause": "SkillPackView 直接把 row.dir/fileDialog.current 拼进 URL，未逐段 encodeURIComponent",
        "files": ["frontend/src/views/SkillPackView.vue", "frontend/src/utils/skillPack.js",
                  "frontend/tests/skill-experts.test.js"],
        "tests": ["SKP-UI-013（重建 dist 后复跑通过）",
                  "frontend/tests/skill-experts.test.js（6/6 通过）"],
    },
    {
        "id": "BUG-003", "severity": "中低", "fixed": True,
        "status": "已修复（主 Agent 收口）",
        "symptom": "关键词本身含英文逗号时，导出→导入被判为有差异，关键词被拆成多个",
        "root_cause": "render_pack 用 ','.join 序列化 keywords，parse_pack_file 又按逗号拆分，无转义",
        "files": ["backend/app/agents/expert_packs.py", "backend/tests/test_expert_packs.py"],
        "tests": ["EXP-API-010", "pytest test_expert_packs.py::test_export_keywords_with_comma_roundtrip"],
    },
    {
        "id": "BUG-004", "severity": "中", "fixed": True,
        "status": "已修复（主 Agent 收口）",
        "symptom": "调用 /api/assistant/agents/reset 后 finance_specialist 丢失模块预置财务工具，专家包页显示「有差异」，全部导入变成导入 1 位",
        "root_cause": "reset_agents 按 TEAM 静态定义覆盖 tools，未重新合并已加载模块 preset_experts 的工具",
        "files": ["backend/app/routers/assistant.py", "backend/app/modules/registry.py",
                  "backend/tests/test_agent_specs.py"],
        "tests": ["pytest test_agent_specs.py::test_reset_restores_builtin（新增模块工具断言）"],
    },
]


def shot(tag):
    try:
        if page is not None:
            page.screenshot(path=str(SHOTS / f"{tag}.png"))
    except Exception:
        pass


def record(tid, name, fn):
    try:
        detail = fn() or ""
        results.append({"id": tid, "name": name, "passed": True, "detail": detail})
        print(f"[PASS] {tid} {name}" + (f" | {detail}" if detail else ""), flush=True)
    except Exception as e:
        results.append({"id": tid, "name": name, "passed": False, "detail": str(e)[:400]})
        print(f"[FAIL] {tid} {name}: {str(e)[:400]}", flush=True)
        shot(f"{tid}-FAIL")


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{BASE}/api/skills/packs", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def api(method, path, **kw):
    if "json" in kw:
        kw["data"] = json.dumps(kw.pop("json"), ensure_ascii=False)
        headers = kw.pop("headers", {})
        headers["Content-Type"] = "application/json"
        kw["headers"] = headers
    return page.request.fetch(f"{BASE}{path}", method=method, timeout=30000, **kw)


def jget(resp):
    return json.loads(resp.text() or "null")


def raw_http(method, path, payload=None):
    """原样发送 URL（不做点段归一化），用于路径穿越用例；返回 (status, bytes)。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---------- 通用等待 / 断言工具 ----------
def wait_for(fn, timeout=6.0, interval=0.12, msg="条件未满足"):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001 - 轮询期间的瞬时异常视为未满足
            last = e
        page.wait_for_timeout(int(interval * 1000))
    raise AssertionError(f"{msg}（{timeout}s 超时，最后状态：{last!r}）")


def wait_toast(text, timeout=5.0):
    def _hit():
        for t in page.locator(".el-message:visible").all_inner_texts():
            if text in t:
                return t
        return ""
    return wait_for(_hit, timeout, msg=f"未出现的提示：{text}")


def toast_texts(kind=""):
    sel = ".el-message:visible" + (f".el-message--{kind}" if kind else "")
    return page.locator(sel).all_inner_texts()


def dlg():
    return page.locator(".el-dialog:visible")


def msgbox():
    return page.locator(".el-message-box:visible")


def wait_dialog_closed(timeout=6.0):
    wait_for(lambda: dlg().count() == 0, timeout, msg="对话框未关闭")


# ---------- 子进程内的自造数据 ----------
def write_skill_raw(dir_name, text, extra=None):
    pack = SKILLS_DIR / dir_name
    pack.mkdir(parents=True, exist_ok=True)
    if text is not None:
        (pack / "SKILL.md").write_text(text, encoding="utf-8")
    for rel, content in (extra or {}).items():
        target = pack / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return pack


def write_skill(dir_name, name=None, description="探测技能描述", version="1.0.0",
                body="## 正文\n\n探测正文标记", extra=None):
    name = name or dir_name
    text = (f"---\nname: {name}\ndescription: {description}\nversion: {version}\n---\n\n{body}\n")
    return write_skill_raw(dir_name, text, extra)


def write_expert(name, label, body="你是测试专家，只回答测试问题。", description="测试专家描述",
                 keywords="测试,专家", tools="", sort=3, emoji="🧪", color="#409EFF"):
    text = (
        "---\n"
        f"name: {name}\n"
        f"label: {label}\n"
        f"emoji: {emoji}\n"
        f"color: {color}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        f"keywords: {json.dumps(keywords, ensure_ascii=False)}\n"
        f"tools: {json.dumps(tools, ensure_ascii=False)}\n"
        f"sort: {sort}\n"
        "---\n\n"
        f"{body}\n"
    )
    (PACKS_DIR / f"{name}.md").write_text(text, encoding="utf-8")
    return PACKS_DIR / f"{name}.md"


def get_agents():
    return jget(api("GET", "/api/assistant/agents"))["agents"]


def agent_by_name(name):
    for a in get_agents():
        if a["name"] == name:
            return a
    return None


def create_agent(**payload):
    payload.setdefault("display_name", payload.get("name", "探测专家"))
    r = api("POST", "/api/assistant/agents", json=payload)
    assert r.status == 201, f"创建专家失败 {r.status}: {r.text()[:200]}"
    return jget(r)["agent"]


def reset_agents_db():
    api("POST", "/api/assistant/agents/reset")
    for a in get_agents():
        if not a["is_builtin"]:
            api("DELETE", f"/api/assistant/agents/{a['id']}")
            continue
        # 探测基线：/agents/reset 只还原 TEAM 静态定义，会丢掉模块 manifest
        # 预置进 finance_specialist 的财务工具（启动时由 registry 合并）。
        # 这里按启动快照补回工具，保证专家包页面的初始态与「刚启动」一致；
        # reset 端点这一副作用不在本区域断言内，单独记录在报告里。
        base = BASE_AGENTS.get(a["name"])
        if base and a["tools"] != base["tools"]:
            api("PUT", f"/api/assistant/agents/{a['id']}", json={"tools": base["tools"]})


def reset_expert_files(seed=True):
    if PACKS_DIR.is_dir():
        for p in list(PACKS_DIR.glob("*.md")) + list(PACKS_DIR.glob("*.md.tmp")):
            p.unlink()
    if seed:
        r = api("POST", "/api/experts/packs/reset")
        assert r.status == 200, f"恢复专家种子失败 {r.status}"


def reset_skills(seed=True):
    if SKILLS_DIR.is_dir():
        for child in list(SKILLS_DIR.iterdir()):
            rm_path(child)
    else:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if seed:
        r = api("POST", "/api/skills/rescan")
        assert r.status == 200, f"重扫技能包失败 {r.status}"


def reset_all():
    reset_agents_db()
    reset_expert_files()
    reset_skills()


def open_skills():
    page.goto(f"{BASE}/#/skill-packs", wait_until="networkidle", timeout=30000)
    page.reload(wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(450)


def open_experts():
    page.goto(f"{BASE}/#/expert-packs", wait_until="networkidle", timeout=30000)
    page.reload(wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(500)


def skill_rows():
    return page.locator("tr.el-table__row:visible")


def skill_row(text):
    return page.locator("tr.el-table__row", has_text=text).first


def expert_left():
    return page.locator(".el-row > .el-col").nth(0)


def expert_right():
    return page.locator(".el-row > .el-col").nth(1)


def expert_row(name):
    return expert_left().locator("tr.el-table__row", has_text=name).first


def db_expert_row(name):
    return expert_right().locator("tr.el-table__row", has_text=name).first


def switch_checked(sw):
    return "is-checked" in (sw.get_attribute("class") or "")


def row_tag(row):
    tags = row.locator(".el-tag").all_inner_texts()
    return tags[0].strip() if tags else ""


def store_disabled():
    path = SKILLS_DIR / "_store.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("disabled", [])


def recon(route, name):
    ce0, pe0, fr0 = len(console_errors), len(page_errors), len(failed_reqs)
    resp = page.goto(f"{BASE}/#/{route}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(900)
    shot(f"recon-{name}")
    return {
        "route": f"/{route}", "name": name,
        "status": resp.status if resp else "?",
        "console_errors": console_errors[ce0:],
        "page_errors": page_errors[pe0:],
        "failed_reqs": [f for f in failed_reqs[fr0:] if "favicon" not in f],
    }


try:
    if not wait_ready():
        print("FATAL: server not ready")
        try:
            print(LOG.read_text(encoding="utf-8", errors="replace")[-3000:])
        except Exception:
            pass
        sys.exit(2)

    # 启动即校验隔离生效：技能池/专家包都必须落在隔离目录（此处 page 尚未创建，用 urllib）
    def _boot_get(path):
        with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as r:
            return json.loads(r.read())

    _packs = _boot_get("/api/skills/packs")["items"]
    assert {p["name"] for p in _packs} == set(SEED_SKILL_NAMES), f"隔离目录未按预期播种: {_packs}"
    _experts = _boot_get("/api/experts/packs")["items"]
    assert [e["file"] for e in _experts] == SEED_EXPERT_FILES, \
        f"专家包目录未按预期播种: {[e['file'] for e in _experts]}"
    # 启动基线（含模块 registry 合并进内置专家的财务工具），供 reset_agents_db 复原
    BASE_AGENTS = {a["name"]: a for a in _boot_get("/api/assistant/agents")["agents"]}
    assert "hello_ping" in (BASE_AGENTS["finance_specialist"]["tools"] or []), \
        "启动基线缺少模块预置工具，专家包种子将恒显差异"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(10000)
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_reqs.append(f"{r.method} {r.url}"))

        # ---------- 页面侦察（启动种子状态） ----------
        pages = [recon("skill-packs", "skill-packs"), recon("expert-packs", "expert-packs")]

        # ================= 技能包管理 =================
        def skp_page_001():
            reset_all()
            ce0, pe0 = len(console_errors), len(page_errors)
            open_skills()
            assert "技能包管理" in page.locator(".card-head").first.inner_text(), "缺少页面标题"
            assert page.locator("button", has_text="新建技能包").count() > 0, "缺少新建按钮"
            assert page.locator("button", has_text="重新扫描").count() > 0, "缺少重扫按钮"
            headers = page.locator(".el-table__header th").all_inner_texts()
            assert headers == ["名称", "版本", "描述", "状态", "文件数", "操作"], f"表头异常: {headers}"
            assert skill_rows().count() == 2, f"启动种子应 2 个技能包，实际 {skill_rows().count()}"
            for seeded in SEED_SKILL_NAMES:
                assert skill_row(seeded).count() > 0, f"缺少种子技能包 {seeded}"
            assert skill_row("month-end-close").locator("td").nth(1).inner_text().strip() == "1.0.0", \
                "种子版本未展示"
            assert not console_errors[ce0:], f"页面控制台报错: {console_errors[ce0:][:2]}"
            assert not page_errors[pe0:], f"页面未捕获异常: {page_errors[pe0:][:2]}"
            return "种子 2 包、表头/工具栏正常，无控制台与页面错误"
        record("SKP-PAGE-001", "页面加载与启动种子", skp_page_001)

        def skp_page_002():
            reset_skills(seed=False)
            open_skills()
            assert skill_rows().count() == 0, f"空目录仍有 {skill_rows().count()} 行"
            assert page.locator(".el-table__empty-text").count() > 0, "空库未显示空态"
            assert "暂无技能包" in page.locator(".el-table__empty-text").first.inner_text(), "空态文案异常"
            assert page.locator(".el-switch").count() == 0, "空态仍渲染开关"
            return "空目录展示空态文案，无残留行"
        record("SKP-PAGE-002", "技能包空态", skp_page_002)

        def skp_api_001():
            reset_skills(seed=False)
            assert jget(api("GET", "/api/skills/packs"))["items"] == [], "清空后清单应为空"
            r = api("POST", "/api/skills/rescan")
            body = jget(r)
            assert r.status == 200
            assert body["seeded"] == SEED_SKILL_NAMES, f"rescan 播种异常: {body['seeded']}"
            assert len(body["items"]) == 2, f"rescan 后清单数异常: {len(body['items'])}"
            # 删除一个种子目录后重扫：只补回缺失的那个（幂等）
            rm_path(SKILLS_DIR / "invoice-review")
            body = jget(api("POST", "/api/skills/rescan"))
            assert body["seeded"] == ["invoice-review"], f"增量播种异常: {body['seeded']}"
            assert {i["name"] for i in body["items"]} == set(SEED_SKILL_NAMES), "增量播种后清单不完整"
            return "空库重扫补 2 个种子；删单个后只补回该包"
        record("SKP-API-001", "种子扫描与 rescan 幂等播种", skp_api_001)

        def skp_api_002():
            reset_skills()
            r = api("POST", "/api/skills/packs", json={
                "name": "api-create", "description": "API 新建描述", "body": "## 自定义正文\n\n- 步骤一"})
            assert r.status == 201, f"新建应 201，实际 {r.status}: {r.text()[:200]}"
            item = jget(r)
            assert item["name"] == "api-create" and item["dir"] == "api-create", f"新建返回异常: {item}"
            assert item["valid"] is True and item["version"] == "1.0.0", f"新建包不可用: {item}"
            assert item["enabled"] is True and item["files"] == [], f"新建包默认值异常: {item}"
            content = jget(api("GET", "/api/skills/packs/api-create/files/SKILL.md"))["content"]
            # 描述以 YAML 双引号标量写入（自由文本安全序列化），内容需逐字可读
            assert 'name: api-create' in content and 'description: "API 新建描述"' in content, \
                f"SKILL.md frontmatter 异常: {content[:120]}"
            assert "## 自定义正文" in content and "- 步骤一" in content, "正文未写入"
            # 正文留空 → 内置模板骨架
            r = api("POST", "/api/skills/packs", json={"name": "api-default", "description": "空正文"})
            assert r.status == 201 and jget(r)["valid"] is True
            content = jget(api("GET", "/api/skills/packs/api-default/files/SKILL.md"))["content"]
            assert "## 使用场景" in content and "（第一步做什么）" in content, "空正文未使用内置模板"
            return "合法新建 201、frontmatter/正文/默认模板均正确"
        record("SKP-API-002", "新建技能包契约", skp_api_002)

        def skp_api_003():
            reset_skills()
            n0 = len(jget(api("GET", "/api/skills/packs"))["items"])

            def code(payload):
                return api("POST", "/api/skills/packs", json=payload).status

            assert code({"name": "", "description": "x"}) == 422, "空名称应 422"
            assert code({"name": "a" * 51, "description": "x"}) == 422, "51 字符名称应 422"
            assert code({"name": "api-ok", "description": ""}) == 422, "空描述应 422"
            assert code({"name": "api-ok"}) == 422, "缺描述应 422"
            r = api("POST", "/api/skills/packs", json={"name": "Bad_Name", "description": "x"})
            assert r.status == 400 and "名称只能使用" in r.text(), f"大写/下划线应 400: {r.status}"
            assert code({"name": "bad name", "description": "x"}) == 400, "空格名称应 400"
            assert code({"name": "-lead", "description": "x"}) == 400, "连字符开头应 400"
            r = api("POST", "/api/skills/packs", json={"name": "api-blank", "description": "   "})
            assert r.status == 400 and "描述不能为空" in r.text(), f"纯空格描述应 400: {r.status}"
            r = api("POST", "/api/skills/packs", json={"name": "invoice-review", "description": "x"})
            assert r.status == 400 and "已存在" in r.text(), f"重名应 400: {r.status}"
            assert len(jget(api("GET", "/api/skills/packs"))["items"]) == n0, "被拒请求创建了目录"
            return "422（长度/必填）与 400（字符/空格/重名）边界正确，无脏目录"
        record("SKP-API-003", "名称与描述校验边界", skp_api_003)

        def skp_api_004():
            reset_skills()
            r = api("PUT", "/api/skills/packs/invoice-review/enabled", json={"enabled": False})
            assert r.status == 200 and jget(r)["enabled"] is False, "停用接口异常"
            assert store_disabled() == ["invoice-review"], f"_store.json 未落盘: {store_disabled()}"
            items = {i["name"]: i for i in jget(api("GET", "/api/skills/packs"))["items"]}
            assert items["invoice-review"]["enabled"] is False, "清单未反映停用"
            api("PUT", "/api/skills/packs/invoice-review/enabled", json={"enabled": False})
            assert store_disabled() == ["invoice-review"], "重复停用产生重复记录"
            api("PUT", "/api/skills/packs/invoice-review/enabled", json={"enabled": True})
            assert store_disabled() == [], "启用未清理停用记录"
            assert api("PUT", "/api/skills/packs/nosuch/enabled", json={"enabled": False}).status == 404
            assert api("PUT", "/api/skills/packs/invoice-review/enabled",
                       json={"enabled": "not-a-bool"}).status == 422
            return "停用/启用持久化到 _store.json，幂等、404/422 正确"
        record("SKP-API-004", "启停持久化与边界", skp_api_004)

        def skp_api_005():
            reset_skills()
            write_skill_raw("month-end-close", (SKILLS_DIR / "month-end-close" / "SKILL.md").read_text(
                encoding="utf-8"), extra={"notes/checklist.md": "嵌套文件内容ZQ",
                                          "reference.txt": "参考内容"})
            items = {i["name"]: i for i in jget(api("GET", "/api/skills/packs"))["items"]}
            assert items["month-end-close"]["files"] == ["notes/checklist.md", "reference.txt"], \
                f"包内文件清单异常: {items['month-end-close']['files']}"
            assert "name: month-end-close" in jget(
                api("GET", "/api/skills/packs/month-end-close/files/SKILL.md"))["content"]
            assert jget(api("GET", "/api/skills/packs/month-end-close/files/notes/checklist.md"))[
                "content"] == "嵌套文件内容ZQ", "嵌套文件读取异常"
            assert api("GET", "/api/skills/packs/month-end-close/files/nope.md").status == 404
            assert api("GET", "/api/skills/packs/nosuch/files/SKILL.md").status == 404
            sc, _ = raw_http("GET", "/api/skills/packs/month-end-close/files/%2Fetc%2Fpasswd")
            assert sc == 400, f"绝对路径应 400，实际 {sc}"
            sc, _ = raw_http("GET", "/api/skills/packs/month-end-close/files/%2E%2E%2F%2E%2E%2Fworkbench.db")
            assert sc == 400, f"相对穿越应 400，实际 {sc}"
            return "SKILL.md/嵌套文件可读，缺失 404，绝对与相对穿越均 400"
        record("SKP-API-005", "包内文件读取与穿越防护", skp_api_005)

        def skp_api_006():
            reset_skills()
            api("POST", "/api/skills/packs", json={"name": "del-target", "description": "删除目标"})
            api("PUT", "/api/skills/packs/del-target/enabled", json={"enabled": False})
            assert store_disabled() == ["del-target"]
            r = api("DELETE", "/api/skills/packs/del-target")
            assert r.status == 200 and jget(r)["deleted"] == "del-target", f"删除返回异常: {r.text()}"
            assert not (SKILLS_DIR / "del-target").exists(), "目录未物理删除"
            assert store_disabled() == [], "删除后停用记录未清理"
            assert api("GET", "/api/skills/packs/del-target/files/SKILL.md").status == 404
            assert api("DELETE", "/api/skills/packs/del-target").status == 404, "重复删除应 404"
            return "整目录删除、停用记录清理、二次删除 404"
        record("SKP-API-006", "删除与清理", skp_api_006)

        def skp_api_007():
            reset_skills()
            sc, _ = raw_http("DELETE", "/api/skills/packs/%2E%2E")
            assert sc == 400, f"DELETE .. 应 400，实际 {sc}"
            sc, _ = raw_http("DELETE", "/api/skills/packs/..")
            assert sc == 400, f"DELETE ..（原样）应 400，实际 {sc}"
            sc, _ = raw_http("PUT", "/api/skills/packs/%2E%2E/enabled", {"enabled": False})
            assert sc == 400, f"PUT .. 应 400，实际 {sc}"
            for path in ("/api/skills/packs/..%2F..%2Fbackend",
                         "/api/skills/packs/%2E%2E%2F%2E%2E%2Fbackend"):
                sc, _ = raw_http("DELETE", path)
                assert sc in (400, 404, 405), f"{path} 未拦截: {sc}"
            assert (BACKEND / "app").is_dir(), "穿越请求影响了 backend 目录"
            assert {i["name"] for i in jget(api("GET", "/api/skills/packs"))["items"]} == \
                set(SEED_SKILL_NAMES), "穿越请求改变了技能包清单"
            return ".. 直传 400；编码斜杠变体 400/404/405，均未逃逸"
        record("SKP-API-007", "技能包路径穿越", skp_api_007)

        def skp_api_008():
            reset_skills()
            write_skill_raw("no-skillmd", None)
            write_skill_raw("bad-yaml", "---\nname: bad-yaml\ndescription: [未闭合\n---\n正文")
            write_skill_raw("no-name", "---\ndescription: 缺名称\nversion: 1.0.0\n---\n正文")
            write_skill_raw(".hidden-pack", "---\nname: hidden\n---\n正文")
            (SKILLS_DIR / "loose.md").write_text("不是包", encoding="utf-8")
            items = {i["name"]: i for i in jget(api("GET", "/api/skills/packs"))["items"]}
            assert "no-skillmd" in items and items["no-skillmd"]["valid"] is False
            assert "缺少 SKILL.md" in items["no-skillmd"]["error"], items["no-skillmd"]["error"]
            assert items["bad-yaml"]["valid"] is False and "解析失败" in items["bad-yaml"]["error"]
            assert items["no-name"]["valid"] is False and "name" in items["no-name"]["error"]
            assert "hidden" not in items and "loose" not in items, f"隐藏目录/根文件未跳过: {items.keys()}"
            assert len(items) == 5, f"清单数异常（2 种子 + 3 坏包）: {sorted(items)}"
            return "缺 SKILL.md / YAML 坏 / 缺 name 均降级 valid=false；隐藏目录与根文件被跳过"
        record("SKP-API-008", "无效技能包扫描降级", skp_api_008)

        def skp_api_009():
            """BUG-001：description 直接拼进 YAML frontmatter，特殊字符让新建包立即无效/被截断。"""
            reset_skills()
            r = api("POST", "/api/skills/packs", json={
                "name": "yaml-colon", "description": "报销审核: 需核对抬头"})
            assert r.status == 201
            item = jget(r)
            assert item["valid"] is True, (
                "新建后技能包立即无效（description 含 ': ' 被当 YAML 映射）："
                f"{item['error']}")
            assert item["description"] == "报销审核: 需核对抬头", f"描述被改写: {item['description']!r}"

            r = api("POST", "/api/skills/packs", json={
                "name": "yaml-newline", "description": "第一行\n第二行"})
            assert r.status == 201
            item = jget(r)
            assert item["valid"] is True, f"多行描述导致新建包无效: {item['error']}"

            r = api("POST", "/api/skills/packs", json={
                "name": "yaml-hash", "description": "说明 #1 重点"})
            assert r.status == 201
            item = jget(r)
            assert item["description"] == "说明 #1 重点", f"描述被 '#' 截断: {item['description']!r}"
            return "特殊字符描述往返正常"
        record("SKP-API-009", "描述含 YAML 特殊字符（: / 换行 / #）", skp_api_009)

        def skp_ui_001():
            reset_skills()
            open_skills()
            page.locator("button", has_text="新建技能包").first.click()
            page.wait_for_timeout(350)
            d = dlg()
            d.locator("input").first.fill("ui-create")
            d.locator("textarea").nth(0).fill("UI 新建描述")
            d.locator("textarea").nth(1).fill("## 正文\n\nUI 正文标记ZQ")
            d.locator("button", has_text="创建").first.click()
            wait_dialog_closed()
            wait_toast("已创建")
            row = skill_row("ui-create")
            assert row.count() > 0, "新技能包未出现在列表"
            text = row.inner_text()
            assert "1.0.0" in text and "UI 新建描述" in text, f"列表展示异常: {text[:100]}"
            assert row.locator("td").nth(4).inner_text().strip() == "1", "文件数应为 1"
            content = jget(api("GET", "/api/skills/packs/ui-create/files/SKILL.md"))["content"]
            assert "UI 正文标记ZQ" in content, "正文未落库"
            return "表单三项写入成功，列表与 SKILL.md 一致"
        record("SKP-UI-001", "UI 新建技能包", skp_ui_001)

        def skp_ui_002():
            reset_skills()
            open_skills()
            n0 = skill_rows().count()
            page.locator("button", has_text="新建技能包").first.click()
            page.wait_for_timeout(350)
            d = dlg()
            d.locator("input").first.fill("Bad_Name")
            d.locator("textarea").nth(0).fill("非法名称探测")
            d.locator("button", has_text="创建").first.click()
            t = wait_toast("名称只能使用")
            assert dlg().count() > 0, "400 后对话框被关闭"
            assert skill_rows().count() == n0, "400 后列表出现脏数据"
            assert "Bad_Name" not in (SKILLS_DIR.as_posix()), "非法目录被创建"
            # 改正后可直接成功（saving 状态已复位）
            d.locator("input").first.fill("ui-fixed")
            d.locator("button", has_text="创建").first.click()
            wait_dialog_closed()
            wait_toast("已创建")
            assert skill_row("ui-fixed").count() > 0, "改正后未能创建"
            return f"400 提示「{t}」，对话框保留且可改正重试"
        record("SKP-UI-002", "非法名称 400 反馈与重试", skp_ui_002)

        def skp_ui_003():
            reset_skills()
            open_skills()
            page.locator("button", has_text="新建技能包").first.click()
            page.wait_for_timeout(350)
            d = dlg()
            d.locator("button", has_text="创建").first.click()
            wait_toast("请填写名称")
            assert dlg().count() > 0, "空名称对话框被关闭"
            d.locator("input").first.fill("ui-blank-desc")
            d.locator("button", has_text="创建").first.click()
            wait_toast("请填写描述")
            assert dlg().count() > 0, "空描述对话框被关闭"
            assert not (SKILLS_DIR / "ui-blank-desc").exists(), "前端校验未拦截，目录已创建"
            return "空名称/空描述均在前端拦截，不产生目录"
        record("SKP-UI-003", "空名称/空描述前端校验", skp_ui_003)

        def skp_ui_004():
            reset_skills()
            api("POST", "/api/skills/packs", json={"name": "dup-ui", "description": "已存在"})
            open_skills()
            page.locator("button", has_text="新建技能包").first.click()
            page.wait_for_timeout(350)
            d = dlg()
            d.locator("input").first.fill("dup-ui")
            d.locator("textarea").nth(0).fill("重名探测")
            d.locator("button", has_text="创建").first.click()
            wait_toast("技能包已存在")
            assert dlg().count() > 0, "重名 400 后对话框被关闭"
            assert page.locator("tr.el-table__row", has_text="dup-ui").count() == 1, "出现重复行"
            return "重名 400 有提示、对话框保留、列表不重复"
        record("SKP-UI-004", "重名 400 反馈", skp_ui_004)

        def skp_ui_005():
            reset_skills()
            write_skill_raw("month-end-close",
                            (SKILLS_DIR / "month-end-close" / "SKILL.md").read_text(encoding="utf-8"),
                            extra={"notes/check.md": "额外文件内容Z"})
            open_skills()
            row = skill_row("month-end-close")
            assert row.locator("td").nth(4).inner_text().strip() == "2", "文件数应为 2（SKILL.md+1）"
            row.locator("button", has_text="查看文件").click()
            page.wait_for_timeout(500)
            d = dlg()
            assert "month-end-close" in d.locator(".el-dialog__title").inner_text(), "对话框标题缺包名"
            assert "name: month-end-close" in d.locator("pre").inner_text(), "默认未展示 SKILL.md"
            d.locator(".el-select").click()
            page.wait_for_timeout(250)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item",
                         has_text="notes/check.md").first.click()
            wait_for(lambda: "额外文件内容Z" in d.locator("pre").inner_text(), msg="切换文件后内容未更新")
            return "默认 SKILL.md、切换嵌套文件均正确"
        record("SKP-UI-005", "查看包内文件与内容切换", skp_ui_005)

        def skp_ui_006():
            """页面无「编辑/保存」入口；以磁盘编辑 + 重新扫描 + 查看文件验证往返。"""
            reset_skills()
            open_skills()
            row = skill_row("month-end-close")
            assert "磁盘改后描述ZQ" not in row.inner_text(), "前置条件不成立"
            md = SKILLS_DIR / "month-end-close" / "SKILL.md"
            text = md.read_text(encoding="utf-8")
            text = re.sub(r"^description:.*$", "description: 磁盘改后描述ZQ", text, count=1, flags=re.M)
            md.write_text(text + "\n磁盘新增标记ZQ\n", encoding="utf-8")
            page.locator("button", has_text="重新扫描").first.click()
            wait_toast("扫描完成")
            row = skill_row("month-end-close")
            wait_for(lambda: "磁盘改后描述ZQ" in row.inner_text(), msg="重扫后描述未更新")
            row.locator("button", has_text="查看文件").click()
            page.wait_for_timeout(500)
            assert "磁盘新增标记ZQ" in dlg().locator("pre").inner_text(), "重扫后文件内容未更新"
            page.locator(".el-dialog__headerbtn:visible").click()
            wait_dialog_closed()
            assert page.locator(".el-dialog:visible").count() == 0, "文件对话框未关闭"
            return "磁盘编辑→重新扫描→列表/正文同步（页面本身无编辑入口，属设计观察）"
        record("SKP-UI-006", "文件编辑保存往返（磁盘+重扫）", skp_ui_006)

        def skp_ui_007():
            reset_skills()
            open_skills()
            sw = skill_row("invoice-review").locator(".el-switch")
            assert switch_checked(sw), "初始应启用"
            sw.click()
            wait_toast("已停用")
            wait_for(lambda: not switch_checked(skill_row("invoice-review").locator(".el-switch")),
                     msg="停用后开关未复位")
            assert store_disabled() == ["invoice-review"], "_store.json 未持久化停用"
            assert {i["name"]: i["enabled"] for i in jget(
                api("GET", "/api/skills/packs"))["items"]}["invoice-review"] is False
            open_skills()
            assert not switch_checked(skill_row("invoice-review").locator(".el-switch")), \
                "重进页面开关未回填停用态"
            skill_row("invoice-review").locator(".el-switch").click()
            wait_toast("已启用")
            assert store_disabled() == [], "启用后未清理停用记录"
            open_skills()
            assert switch_checked(skill_row("invoice-review").locator(".el-switch")), "启用态未持久化"
            return "停用/启用跨页面与 API 一致"
        record("SKP-UI-007", "启停持久化（重进页面一致）", skp_ui_007)

        def skp_ui_008():
            reset_skills()
            open_skills()
            assert skill_rows().count() == 2
            write_skill("disk-new", name="disk-new", description="磁盘新增描述")
            page.locator("button", has_text="重新扫描").first.click()
            wait_toast("扫描完成，共 3 个技能包")
            wait_for(lambda: skill_rows().count() == 3, msg="重扫未发现磁盘新增目录")
            assert skill_row("disk-new").count() > 0, "新增包未出现在列表"
            return "重扫发现磁盘新增目录并提示总数"
        record("SKP-UI-008", "rescan 发现磁盘新增目录", skp_ui_008)

        def skp_ui_009():
            reset_skills()
            write_skill_raw("no-skillmd", None)
            write_skill_raw("bad-yaml", "---\nname: bad-yaml\ndescription: [未闭合\n---\n正文")
            open_skills()
            alerts = page.locator(".el-alert:visible").all_inner_texts()
            joined = "\n".join(alerts)
            assert "no-skillmd：缺少 SKILL.md" in joined, f"缺 SKILL.md 未告警: {joined[:200]}"
            assert "bad-yaml：SKILL.md 解析失败" in joined, f"YAML 错误未告警: {joined[:200]}"
            assert skill_rows().count() == 4, f"坏包行应保留展示，实际 {skill_rows().count()}"
            return "无效包逐条红条告警且仍在表中展示"
        record("SKP-UI-009", "无效 SKILL.md 告警行", skp_ui_009)

        def skp_ui_010():
            reset_skills()
            api("POST", "/api/skills/packs", json={"name": "del-cancel", "description": "取消删除"})
            open_skills()
            skill_row("del-cancel").locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            box = msgbox()
            assert "删除技能包「del-cancel」" in box.inner_text(), "确认框文案异常"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert skill_row("del-cancel").count() > 0, "取消后行消失"
            assert (SKILLS_DIR / "del-cancel").is_dir(), "取消后目录被删除"
            return "取消删除保留行与目录"
        record("SKP-UI-010", "删除确认-取消", skp_ui_010)

        def skp_ui_011():
            reset_skills()
            api("POST", "/api/skills/packs", json={"name": "del-confirm", "description": "确认删除"})
            open_skills()
            skill_row("del-confirm").locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="确定").click()
            wait_toast("已删除")
            wait_for(lambda: skill_row("del-confirm").count() == 0, msg="确认后行仍在")
            assert not (SKILLS_DIR / "del-confirm").exists(), "目录未删除"
            assert api("GET", "/api/skills/packs/del-confirm/files/SKILL.md").status == 404
            return "确认删除后行、目录、API 三层一致"
        record("SKP-UI-011", "删除确认-确定", skp_ui_011)

        def skp_ui_012():
            reset_skills()
            evil = '<img src=x onerror="window.__xss=1">'
            write_skill("evil", name=evil, description="<b>加粗</b>", body="正文")
            open_skills()
            page.wait_for_timeout(500)
            assert page.evaluate("window.__xss === undefined") is True, "XSS 被执行"
            row = page.locator("tr.el-table__row", has_text="<img src=x").first
            assert row.count() > 0, "特殊字符名称未按字面展示"
            assert row.locator("td").first.inner_text() == evil, "名称未按字面显示"
            assert "&lt;img" in row.locator("td").first.inner_html(), "名称未转义（innerHTML 出现原始标签）"
            assert "<b>加粗</b>" in row.inner_text(), "描述未按字面展示"
            return "frontmatter 名称/描述中的 HTML 均转义，无脚本执行"
        record("SKP-UI-012", "XSS 名称/描述转义", skp_ui_012)

        def skp_ui_013():
            """BUG-002：目录名未 encodeURIComponent，# 之后被当 URL fragment 截断。"""
            reset_skills()
            write_skill("tax#1", name="tax#1", description="含井号目录")
            open_skills()
            row = skill_row("tax#1")
            assert row.count() > 0, "含 # 的包未展示"
            bad = [t for t in toast_texts("error") if "Method Not Allowed" in t]
            assert not bad, f"前置：已有错误提示 {bad}"
            row.locator(".el-switch").click()
            page.wait_for_timeout(1200)
            items = {i["name"]: i for i in jget(api("GET", "/api/skills/packs"))["items"]}
            assert items["tax#1"]["enabled"] is False, (
                "切换含 # 目录名的技能包失败：请求被截断为 /api/skills/packs/tax（目录名未 URL 编码），"
                f"提示={toast_texts('error')}")
            row = skill_row("tax#1")
            assert not switch_checked(row.locator(".el-switch")), "开关未落为停用"
            # 删除同样走未编码路径
            row.locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="确定").click()
            wait_for(lambda: not (SKILLS_DIR / "tax#1").exists(), 6,
                     msg="含 # 目录名的技能包无法删除（DELETE 同样被截断）")
            return "含 # 目录名可启停并删除"
        record("SKP-UI-013", "特殊目录名 URL 编码（#）", skp_ui_013)

        def skp_ui_014():
            reset_skills()
            open_skills()
            assert skill_rows().count() == 2
            pe0 = len(page_errors)
            handler = lambda route: route.abort()
            page.route(re.compile(r"/api/skills/rescan$"), handler)
            page.locator("button", has_text="重新扫描").first.click()
            wait_for(lambda: toast_texts("error"), timeout=6, msg="重扫失败无错误提示")
            page.unroute(re.compile(r"/api/skills/rescan$"), handler)
            assert skill_rows().count() == 2, "重扫失败后列表被清空"
            # loading 遮罩关闭带过渡动画，等它真正隐藏（Element Plus 会保留 DOM）
            wait_for(lambda: page.locator(".el-loading-mask").count() == 0
                     or not page.locator(".el-loading-mask").first.is_visible(),
                     timeout=4, msg="失败后 loading 未复位")
            assert not page_errors[pe0:], f"重扫失败出现未捕获异常: {page_errors[pe0:][:2]}"
            return "重扫失败有错误提示、列表保留、loading 复位"
        record("SKP-UI-014", "异常态-重新扫描失败", skp_ui_014)

        # ================= 专家团文件 =================
        def exp_page_001():
            reset_all()
            ce0, pe0 = len(console_errors), len(page_errors)
            open_experts()
            assert expert_left().count() == 1 and expert_right().count() == 1, "双栏布局缺失"
            assert "文件专家包" in page.locator(".card-head").first.inner_text()
            assert expert_left().locator("tr.el-table__row:visible").count() == 8, "左侧种子应 8 行"
            assert expert_right().locator("tr.el-table__row:visible").count() == 8, "右侧内置应 8 行"
            tags = page.locator(".el-tag:visible").all_inner_texts()
            assert tags.count("已导入") == 8, f"未导入徽标异常: {tags}"
            assert tags.count("内置") == 8, f"内置徽标异常: {tags}"
            assert page.locator("button", has_text="全部导入").count() > 0
            assert page.locator("button", has_text="从种子重置").count() > 0
            assert not console_errors[ce0:], f"控制台报错: {console_errors[ce0:][:2]}"
            assert not page_errors[pe0:], f"页面异常: {page_errors[pe0:][:2]}"
            return "双栏 8+8、已导入/内置徽标、操作按钮正常，无错误"
        record("EXP-PAGE-001", "页面加载与种子一致", exp_page_001)

        def exp_page_002():
            reset_all()
            reset_expert_files(seed=False)
            open_experts()
            left = expert_left()
            assert left.locator("tr.el-table__row:visible").count() == 0, "空目录仍有文件行"
            assert "目录中暂无 .md 专家包" in left.inner_text(), "空态文案缺失"
            assert expert_right().locator("tr.el-table__row:visible").count() == 8, "数据库专家不应被清空"
            return "文件目录空态与数据库专家解耦"
        record("EXP-PAGE-002", "专家包空态", exp_page_002)

        def exp_api_001():
            reset_all()
            items = jget(api("GET", "/api/experts/packs"))["items"]
            assert [i["file"] for i in items] == SEED_EXPERT_FILES, "文件清单与种子不一致"
            for i in items:
                assert i["valid"] is True and i["imported"] is True, f"{i['name']} 状态异常: {i}"
                assert i["diff"] is None and i["diff_fields"] == [], f"{i['name']} 不应有差异: {i}"
                assert i["label"], f"{i['name']} 缺 label"
            assert {i["name"] for i in items} == {Path(f).stem for f in SEED_EXPERT_FILES}
            return "8 个种子包均 valid/imported/无差异，label 完整"
        record("EXP-API-001", "清单与数据库一致性", exp_api_001)

        def exp_api_002():
            reset_all()
            data = jget(api("GET", "/api/experts/packs/realestate/content"))
            assert data["file"] == "realestate.md" and "name: realestate" in data["content"], \
                f"原文接口异常: {data}"
            assert api("GET", "/api/experts/packs/nosuch/content").status == 404
            sc, _ = raw_http("GET", "/api/experts/packs/%2E%2E/content")
            assert sc == 400, f".. 应 400，实际 {sc}"
            sc, _ = raw_http("GET", "/api/experts/packs/..%2Fworkbench/content")
            assert sc in (400, 404), f"编码斜杠应拦截，实际 {sc}"
            return "原文接口 200；未知 404；.. 400；编码斜杠 400/404"
        record("EXP-API-002", "原文读取与穿越防护", exp_api_002)

        def exp_api_003():
            reset_all()
            write_expert("custom_api", "接口专家甲", description="接口导入描述",
                         keywords="接口,导入", tools="query_tasks", sort=7)
            r = api("POST", "/api/experts/packs/import/custom_api")
            assert r.status == 200, f"导入失败 {r.status}: {r.text()[:200]}"
            body = jget(r)
            assert body["action"] == "created" and body["is_builtin"] is False, f"动作异常: {body}"
            a = agent_by_name("custom_api")
            assert a and a["display_name"] == "接口专家甲", f"显示名未入库: {a}"
            assert a["keywords"] == ["接口", "导入"] and a["tools"] == ["query_tasks"], f"字段异常: {a}"
            assert a["description"] == "接口导入描述" and a["sort"] == 7, f"字段异常: {a}"
            assert a["role_prompt"].strip() == "你是测试专家，只回答测试问题。", \
                f"正文未入库: {a['role_prompt']!r}"
            entry = [i for i in jget(api("GET", "/api/experts/packs"))["items"] if i["name"] == "custom_api"][0]
            assert entry["imported"] is True and entry["diff"] is None, f"清单状态异常: {entry}"
            nxt = jget(api("POST", "/api/experts/packs/import/custom_api"))
            assert nxt["action"] == "unchanged" and nxt["diff"] == [], f"重复导入应无变化: {nxt}"
            return "created 全字段一致；重复导入 unchanged"
        record("EXP-API-003", "单包导入（创建）", exp_api_003)

        def exp_api_004():
            reset_all()
            write_expert("custom_upd", "更新前", keywords="旧词", tools="query_tasks")
            api("POST", "/api/experts/packs/import/custom_upd")
            a = agent_by_name("custom_upd")
            api("PUT", f"/api/assistant/agents/{a['id']}",
                json={"enabled": False, "skills": ["month-end-close"],
                      "tools": ["query_tasks", "vault_query"]})
            write_expert("custom_upd", "更新后", description="新描述", keywords="新词,导入",
                         tools="query_tasks", sort=9, body="更新后的角色提示词。")
            body = jget(api("POST", "/api/experts/packs/import/custom_upd"))
            assert body["action"] == "updated", f"应更新: {body}"
            a = agent_by_name("custom_upd")
            assert a["display_name"] == "更新后" and a["keywords"] == ["新词", "导入"], f"字段未更新: {a}"
            assert a["role_prompt"].strip() == "更新后的角色提示词。", "角色提示词未更新"
            assert a["enabled"] is False, "enabled 被导入覆盖"
            assert a["skills"] == ["month-end-close"], "技能白名单被导入覆盖"
            assert "vault_query" in a["tools"] and "query_tasks" in a["tools"], \
                f"工具并集语义失败: {a['tools']}"
            return "更新字段正确；enabled/skills 保留；工具并集不缩水"
        record("EXP-API-004", "单包导入（更新与保留字段）", exp_api_004)

        def exp_api_005():
            reset_all()
            write_expert("diff_api", "文件中文名", keywords="", tools="")
            api("POST", "/api/experts/packs/import/diff_api")
            a = agent_by_name("diff_api")
            api("PUT", f"/api/assistant/agents/{a['id']}", json={"display_name": "库中中文名"})
            entry = [i for i in jget(api("GET", "/api/experts/packs"))["items"] if i["name"] == "diff_api"][0]
            assert entry["imported"] is True and entry["diff"], f"应报告差异: {entry}"
            assert entry["diff_fields"] == ["显示名"], f"差异字段异常: {entry['diff_fields']}"
            assert "显示名不同" in entry["diff"], f"差异简述异常: {entry['diff']}"
            return "差异字段精确到「显示名」，简述含原因"
        record("EXP-API-005", "差异字段比对", exp_api_005)

        def exp_api_006():
            reset_all()
            create_agent(name="exp_api", display_name="导出甲", description="导出描述",
                         keywords=["导出"], tools=["query_tasks"], sort=4,
                         role_prompt="导出用角色提示词。")
            r = api("POST", "/api/experts/packs/export/exp_api")
            assert r.status == 200 and jget(r)["file"] == "exp_api.md", f"导出异常: {r.text()[:200]}"
            path = PACKS_DIR / "exp_api.md"
            assert path.is_file(), "导出文件不存在"
            content = path.read_text(encoding="utf-8")
            assert "name: exp_api" in content and 'label: "导出甲"' not in content, content[:120]
            assert "label: 导出甲" in content, f"label 未写入: {content[:160]}"
            assert "导出用角色提示词。" in content, "角色提示词未写入"
            nxt = jget(api("POST", "/api/experts/packs/import/exp_api"))
            assert nxt["action"] == "unchanged", f"导出→导入应无变化: {nxt}"
            assert api("POST", "/api/experts/packs/export/nosuch").status == 404
            assert api("POST", "/api/experts/packs/export/Bad-Name").status == 400
            # 覆盖已修改的同名文件
            path.write_text(content.replace("label: 导出甲", "label: 文件旧值"), encoding="utf-8")
            api("PUT", f"/api/assistant/agents/{agent_by_name('exp_api')['id']}",
                json={"display_name": "导出乙"})
            assert api("POST", "/api/experts/packs/export/exp_api").status == 200
            assert "label: 导出乙" in path.read_text(encoding="utf-8"), "导出未覆盖同名文件"
            return "导出文件字段完整、往返 unchanged、未知 404、非法名 400、覆盖生效"
        record("EXP-API-006", "导出为文件", exp_api_006)

        def exp_api_007():
            reset_all()
            write_expert("custom_all", "全量导入专家", keywords="全量", tools="query_tasks")
            (PACKS_DIR / "broken.md").write_text("# 没有 frontmatter\n正文", encoding="utf-8")
            seed_file = PACKS_DIR / "realestate.md"
            seed_file.write_text(seed_file.read_text(encoding="utf-8").replace(
                "label: 商业地产管家", "label: 文件改动名"), encoding="utf-8")
            body = jget(api("POST", "/api/experts/packs/import-all"))
            assert "custom_all" in body["imported"], f"新包未导入: {body}"
            assert "realestate" in body["imported"], f"差异包未更新: {body}"
            assert len(body["skipped"]) == 7, f"跳过期数异常: {body['skipped']}"
            assert [e["file"] for e in body["errors"]] == ["broken.md"], f"错误清单异常: {body['errors']}"
            assert agent_by_name("custom_all") is not None
            assert agent_by_name("realestate")["display_name"] == "文件改动名", "差异包未落库"
            return "imported=2、skipped=7、errors=broken.md，字段落库"
        record("EXP-API-007", "全部导入（成功/跳过/失败隔离）", exp_api_007)

        def exp_api_008():
            reset_all()
            target = PACKS_DIR / "realestate.md"
            target.write_text(target.read_text(encoding="utf-8").replace(
                "label: 商业地产管家", "label: 被改坏的名字"), encoding="utf-8")
            body = jget(api("POST", "/api/experts/packs/reset"))
            assert sorted(body["reset"]) == SEED_EXPERT_FILES, f"重置清单异常: {body['reset']}"
            assert target.read_text(encoding="utf-8") == (
                BACKEND / "seed_experts" / "realestate.md").read_text(encoding="utf-8"), "文件未回到种子"
            entry = [i for i in jget(api("GET", "/api/experts/packs"))["items"] if i["name"] == "realestate"][0]
            assert entry["diff"] is None, f"重置后仍报差异: {entry['diff']}"
            return "8 个种子文件字节级复原，清单无差异"
        record("EXP-API-008", "从种子重置文件", exp_api_008)

        def exp_api_009():
            reset_all()
            write_expert("custom_del", "删除专家", tools="query_tasks")
            api("POST", "/api/experts/packs/import/custom_del")
            r = api("DELETE", "/api/experts/packs/custom_del")
            assert r.status == 200 and jget(r)["file"] == "custom_del.md", f"删除异常: {r.text()[:200]}"
            assert not (PACKS_DIR / "custom_del.md").exists(), "文件未删除"
            assert api("GET", "/api/experts/packs/custom_del/content").status == 404
            assert api("DELETE", "/api/experts/packs/custom_del").status == 404
            assert agent_by_name("custom_del") is not None, "删除文件不应删除数据库专家"
            assert api("DELETE", "/api/experts/packs/nosuch").status == 404
            sc, _ = raw_http("DELETE", "/api/experts/packs/%2E%2E")
            assert sc == 400, f"DELETE .. 应 400，实际 {sc}"
            return "文件删除、二次 404、数据库专家保留、穿越 400"
        record("EXP-API-009", "删除专家包文件", exp_api_009)

        def exp_api_010():
            """BUG-003：keywords 导出用逗号连接，含逗号的关键词会 split 成多个，往返丢数据。"""
            reset_all()
            create_agent(name="comma_api", display_name="逗号专家", description="逗号往返",
                         keywords=["甲,乙", "丙"], tools=["query_tasks"], sort=2,
                         role_prompt="逗号关键词往返测试。")
            assert api("POST", "/api/experts/packs/export/comma_api").status == 200
            body = jget(api("POST", "/api/experts/packs/import/comma_api"))
            a = agent_by_name("comma_api")
            assert body["action"] == "unchanged", (
                "导出→导入被判为有变化（keywords 含逗号被拆分）："
                f"diff={body['diff']}，导入前=[甲,乙, 丙]，导入后={a['keywords']}")
            assert a["keywords"] == ["甲,乙", "丙"], f"关键词被改写: {a['keywords']}"
            return "含逗号关键词往返无损"
        record("EXP-API-010", "关键词含逗号导出导入往返", exp_api_010)

        def exp_ui_001():
            reset_all()
            create_agent(name="ui_custom", display_name="界面自定义", description="自定义专家",
                         keywords=["界面"], tools=["query_tasks"], sort=2,
                         role_prompt="界面自定义专家提示词。")
            api("POST", "/api/experts/packs/export/ui_custom")
            open_experts()
            left_rows = expert_left().locator("tr.el-table__row:visible").count()
            assert left_rows == 9, f"左侧应 9 行（8 种子+1 导出的），实际 {left_rows}"
            right = db_expert_row("ui_custom")
            assert right.count() > 0, "右侧缺少自定义专家"
            assert row_tag(right) == "自定义", f"自定义徽标异常: {row_tag(right)}"
            assert row_tag(expert_row("ui_custom")) == "已导入", "左侧应显示已导入"
            builtin = db_expert_row("realestate")
            assert row_tag(builtin) == "内置", f"内置徽标异常: {row_tag(builtin)}"
            return "自定义/内置徽标与文件导入态正确"
        record("EXP-UI-001", "内置/自定义徽标", exp_ui_001)

        def exp_ui_002():
            reset_all()
            a = agent_by_name("realestate")
            api("PUT", f"/api/assistant/agents/{a['id']}", json={"display_name": "商业地产管家-库改"})
            open_experts()
            row = expert_row("realestate")
            assert row_tag(row) == "有差异", f"应显示有差异，实际 {row_tag(row)}"
            row.locator(".el-tag", has_text="有差异").hover()
            tip = wait_for(lambda: page.locator(".el-popper:visible", has_text="差异字段").count(),
                           msg="有差异 tooltip 未出现")
            text = page.locator(".el-popper:visible", has_text="差异字段").first.inner_text()
            assert "显示名" in text and "显示名不同" in text, f"tooltip 内容异常: {text}"
            page.mouse.move(10, 10)
            return f"有差异徽标 + tooltip「{text.strip()}」"
        record("EXP-UI-002", "有差异徽标与 tooltip", exp_ui_002)

        def exp_ui_003():
            reset_all()
            a = agent_by_name("realestate")
            api("PUT", f"/api/assistant/agents/{a['id']}", json={"display_name": "商业地产管家-库改"})
            open_experts()
            row = expert_row("realestate")
            row.locator("button", has_text="导入").click()
            page.wait_for_timeout(400)
            box = msgbox()
            text = box.inner_text()
            assert "将用文件「realestate.md」的内容覆盖数据库专家「realestate」" in text, \
                f"导入确认文案异常: {text[:160]}"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(400)
            assert agent_by_name("realestate")["display_name"] == "商业地产管家-库改", "取消后被导入"
            expert_row("realestate").locator("button", has_text="导入").click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="覆盖导入").click()
            wait_toast("已导入「realestate」（更新）")
            wait_for(lambda: agent_by_name("realestate")["display_name"] == "商业地产管家", msg="导入未落库")
            wait_for(lambda: row_tag(expert_row("realestate")) == "已导入", msg="导入后徽标未刷新")
            return "差异确认文案正确；取消不改库；覆盖导入字段回填文件值"
        record("EXP-UI-003", "单条导入-有差异确认", exp_ui_003)

        def exp_ui_004():
            reset_all()
            open_experts()
            row = expert_row("finance_specialist")
            assert row_tag(row) == "已导入", "内置专家无差异时应为已导入"
            row.locator("button", has_text="导入").click()
            page.wait_for_timeout(400)
            text = msgbox().inner_text()
            assert "覆盖数据库专家「finance_specialist」" in text, f"内置专家未触发确认: {text[:120]}"
            msgbox().locator("button", has_text="取消").click()
            page.wait_for_timeout(300)
            assert row_tag(expert_row("finance_specialist")) == "已导入"
            return "内置专家即使无差异也先确认，取消无副作用"
        record("EXP-UI-004", "单条导入-内置确认", exp_ui_004)

        def exp_ui_005():
            reset_all()
            open_experts()
            page.locator("button", has_text="全部导入").first.click()
            page.wait_for_timeout(400)
            box = msgbox()
            assert "存在与数据库有差异或为内置的专家" in box.inner_text(), "全部导入确认文案缺失"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(300)
            page.locator("button", has_text="全部导入").first.click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="覆盖导入").click()
            t = wait_toast("导入 0 位，跳过 8 位，失败 0 位")
            assert expert_left().locator("tr.el-table__row:visible").count() == 8
            return f"确认文案与统计正确（{t}）"
        record("EXP-UI-005", "全部导入确认与统计", exp_ui_005)

        def exp_ui_006():
            reset_all()
            (PACKS_DIR / "broken.md").write_text("# 没有 frontmatter\n正文", encoding="utf-8")
            open_experts()
            row = expert_row("broken")
            assert row_tag(row) == "文件错误", f"坏文件徽标异常: {row_tag(row)}"
            row.locator(".el-tag", has_text="文件错误").hover()
            wait_for(lambda: page.locator(".el-popper:visible", has_text="缺少 YAML frontmatter").count(),
                     msg="坏文件 tooltip 未出现")
            btn = row.locator("button", has_text="导入").first
            assert btn.is_disabled() or "is-disabled" in (btn.get_attribute("class") or ""), \
                "坏文件仍可点导入"
            page.mouse.move(10, 10)
            return "文件错误徽标 + tooltip 展示原因，导入按钮禁用"
        record("EXP-UI-006", "文件错误徽标与禁用", exp_ui_006)

        def exp_ui_007():
            reset_all()
            open_experts()
            expert_row("realestate").locator("button", has_text="查看").click()
            page.wait_for_timeout(500)
            d = dlg()
            assert d.locator(".el-dialog__title").inner_text().strip() == "realestate.md", "原文标题异常"
            content = d.locator("pre").inner_text()
            assert "name: realestate" in content and "商业地产管家" in content, "原文内容异常"
            d.locator(".el-dialog__headerbtn").click()
            wait_dialog_closed()
            assert dlg().count() == 0, "原文对话框未关闭"
            return "原文对话框标题/内容正确，可关闭"
        record("EXP-UI-007", "查看专家包原文", exp_ui_007)

        def exp_ui_008():
            reset_all()
            create_agent(name="ui_export", display_name="导出甲", description="导出确认",
                         keywords=["导出"], tools=["query_tasks"], sort=3,
                         role_prompt="导出确认测试提示词。")
            api("POST", "/api/experts/packs/export/ui_export")
            a = agent_by_name("ui_export")
            api("PUT", f"/api/assistant/agents/{a['id']}", json={"display_name": "导出乙"})
            open_experts()
            assert row_tag(expert_row("ui_export")) == "有差异", "前置：文件与数据库应有差异"
            row = db_expert_row("ui_export")
            row.locator("button", has_text="导出为文件").click()
            page.wait_for_timeout(400)
            box = msgbox()
            assert "将覆盖已修改的文件 ui_export.md" in box.inner_text(), f"导出确认文案异常: {box.inner_text()[:160]}"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(300)
            assert "label: 导出甲" in (PACKS_DIR / "ui_export.md").read_text(encoding="utf-8"), \
                "取消后文件被覆盖"
            db_expert_row("ui_export").locator("button", has_text="导出为文件").click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="覆盖导出").click()
            wait_toast("已导出为 ui_export.md")
            assert "label: 导出乙" in (PACKS_DIR / "ui_export.md").read_text(encoding="utf-8"), \
                "确认后文件未更新"
            wait_for(lambda: row_tag(expert_row("ui_export")) == "已导入", msg="导出后差异徽标未清除")
            return "同名差异先确认；取消不改文件；确认后文件与徽标同步"
        record("EXP-UI-008", "导出为文件-同名差异确认", exp_ui_008)

        def exp_ui_009():
            reset_all()
            a = agent_by_name("realestate")
            api("PUT", f"/api/assistant/agents/{a['id']}", json={"display_name": "数据库改"})
            target = PACKS_DIR / "realestate.md"
            target.write_text(target.read_text(encoding="utf-8").replace(
                "label: 商业地产管家", "label: 文件改"), encoding="utf-8")
            open_experts()
            assert row_tag(expert_row("realestate")) == "有差异"
            page.locator("button", has_text="从种子重置").first.click()
            page.wait_for_timeout(400)
            box = msgbox()
            text = box.inner_text()
            assert "覆盖专家包目录中的同名文件" in text and "需再点「全部导入」才会生效到数据库" in text, \
                f"重置确认文案异常: {text[:200]}"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(300)
            assert "label: 文件改" in target.read_text(encoding="utf-8"), "取消后文件被重置"
            page.locator("button", has_text="从种子重置").first.click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="确定").click()
            t = wait_toast("已重置 8 个种子文件；文件已重置，需再点「全部导入」才会生效到数据库")
            assert target.read_text(encoding="utf-8") == (
                BACKEND / "seed_experts" / "realestate.md").read_text(encoding="utf-8"), "文件未回到种子"
            assert agent_by_name("realestate")["display_name"] == "数据库改", "重置不应直接改数据库"
            wait_for(lambda: row_tag(expert_row("realestate")) == "有差异", msg="重置后差异徽标应保留")
            return f"确认文案含「需再点全部导入」；取消/确认路径正确（{t}）"
        record("EXP-UI-009", "从种子重置（不直接生效）", exp_ui_009)

        def exp_ui_010():
            reset_all()
            a = agent_by_name("realestate")
            api("PUT", f"/api/assistant/agents/{a['id']}", json={"display_name": "数据库改"})
            target = PACKS_DIR / "realestate.md"
            target.write_text(target.read_text(encoding="utf-8").replace(
                "label: 商业地产管家", "label: 文件改"), encoding="utf-8")
            open_experts()
            page.locator("button", has_text="从种子重置").first.click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="确定").click()
            wait_toast("需再点「全部导入」才会生效到数据库")
            page.locator("button", has_text="全部导入").first.click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="覆盖导入").click()
            t = wait_toast("导入 1 位，跳过 7 位，失败 0 位")
            wait_for(lambda: agent_by_name("realestate")["display_name"] == "商业地产管家",
                     msg="全部导入后数据库未回到文件值")
            wait_for(lambda: row_tag(expert_row("realestate")) == "已导入", msg="导入后差异未清除")
            return f"重置后按提示全部导入即生效（{t}）"
        record("EXP-UI-010", "重置后全部导入生效", exp_ui_010)

        def exp_ui_011():
            reset_all()
            write_expert("custom_ui_del", "界面删除专家", tools="query_tasks")
            api("POST", "/api/experts/packs/import/custom_ui_del")
            open_experts()
            row = expert_row("custom_ui_del")
            row.locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            box = msgbox()
            assert "不影响数据库中的专家" in box.inner_text(), "删除确认未说明数据库不受影响"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(300)
            assert expert_row("custom_ui_del").count() > 0, "取消后行消失"
            expert_row("custom_ui_del").locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            msgbox().locator("button", has_text="确定").click()
            wait_toast("已删除 custom_ui_del")
            wait_for(lambda: expert_row("custom_ui_del").count() == 0, msg="确认后行仍在")
            assert not (PACKS_DIR / "custom_ui_del.md").exists(), "文件未删除"
            assert db_expert_row("custom_ui_del").count() > 0, "数据库专家被误删"
            return "取消/确认路径正确；文件删除而数据库专家保留"
        record("EXP-UI-011", "删除包-取消/确认", exp_ui_011)

        def exp_ui_012():
            reset_all()
            write_expert("custom_ui_import", "界面导入专家", keywords="界面", tools="query_tasks")
            open_experts()
            row = expert_row("custom_ui_import")
            assert row_tag(row) == "未导入", f"初始徽标异常: {row_tag(row)}"
            row.locator("button", has_text="导入").click()
            wait_toast("已导入「custom_ui_import」（新建）")
            wait_for(lambda: row_tag(expert_row("custom_ui_import")) == "已导入", msg="导入后徽标未刷新")
            right = db_expert_row("custom_ui_import")
            assert right.count() > 0, "右侧未即时出现新专家"
            assert row_tag(right) == "自定义", f"导入后应为自定义: {row_tag(right)}"
            return "无确认直接导入（自定义无差异），左右两表即时刷新"
        record("EXP-UI-012", "导入后即时刷新", exp_ui_012)

        def exp_ui_013():
            reset_all()
            open_experts()
            pe0 = len(page_errors)
            (PACKS_DIR / "realestate.md").unlink()
            expert_row("realestate").locator("button", has_text="查看").click()
            t = wait_toast("专家包 realestate 不存在", timeout=6)
            assert dlg().count() == 0, "404 后仍打开了空对话框"
            assert not page_errors[pe0:], f"404 出现未捕获异常: {page_errors[pe0:][:2]}"
            reset_all()  # 复原现场，供末条隔离快照校验
            return f"文件被外部删除后查看有错误提示（{t}），无未捕获异常"
        record("EXP-UI-013", "异常态-查看已删文件", exp_ui_013)

        # ---------- 隔离校验：真实 backend/data 未被触碰 ----------
        def iso_001():
            after_skills = snapshot(REAL_SKILLS)
            after_packs = snapshot(REAL_PACKS)
            assert after_skills == REAL_SKILLS_BEFORE, \
                "真实 backend/data/skills 在探针运行期间被修改"
            assert after_packs == REAL_PACKS_BEFORE, \
                "真实 backend/data/expert-packs 在探针运行期间被修改"
            assert (SKILLS_DIR / "month-end-close" / "SKILL.md").is_file(), "隔离技能目录未生效"
            assert (PACKS_DIR / "realestate.md").is_file(), "隔离专家包目录未生效"
            return f"真实目录快照一致（skills {len(after_skills)} 文件 / packs {len(after_packs)} 文件）"
        record("SKP-ISO-001", "真实 data 目录零改动（隔离校验）", iso_001)

        browser.close()

    print("=" * 70)
    passed = sum(1 for r in results if r["passed"])
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    print(f"console_errors={len(console_errors)} page_errors={len(page_errors)}")
    for e in page_errors[:10]:
        print(f"  !! pageerror: {e[:200]}")

    raw = {
        "area": AREA, "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages,
        "tests": results,
        "bugs": bugs,
    }
    RESULT_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"result -> {RESULT_JSON}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
