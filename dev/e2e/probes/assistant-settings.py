# -*- coding: utf-8 -*-
"""assistant-settings 区域深度 E2E 探测（可重复运行的正式资产）。

区域：assistant-settings（端口 8206）。入口路由 /assistant → 左上「设置」打开设置抽屉：
- 供应商与模型 tab：增删改供应商/模型、启停开关、「已停用」徽标、当前生效模型来源行、
  激活切换、连通/能力探测按钮在无密钥隔离库下的优雅降级；
- 专家团队 tab：路由测试、新建/编辑/启停/删除专家、恢复出厂确认；
- 工作参数 tab：技能池目录检测、启用技能开关、保存工作参数。

隔离：DATABASE_URL=sqlite:///dev/e2e/probe_results/assistant-settings.db，
FINANCE_DATA_DIR=dev/e2e/probe_results/assistant-settings-fin（每次运行重置）。
厂商地址一律用 http://127.0.0.1:9/v1（拒接快速失败），不发起真实外网请求。

结果写 dev/e2e/probe_results/assistant-settings.json；失败截图放
dev/e2e/probe_results/assistant-settings/shots/。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# 控制台中文/符号（如 ✓）在 GBK 代码页下打印会崩：统一替换式输出，不影响判断
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
E2E_DIR = ROOT / "dev" / "e2e"
RESULTS = E2E_DIR / "probe_results"
SHOTS = RESULTS / "assistant-settings" / "shots"
DB = RESULTS / "assistant-settings.db"
FIN_DIR = RESULTS / "assistant-settings-fin"
SKILLS_POOL = RESULTS / "assistant-settings-skills"
PORT = 8206
AREA = "assistant-settings"
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
UNREACHABLE = "http://127.0.0.1:9/v1"

for p in (DB,):
    if p.exists():
        p.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)
if SKILLS_POOL.exists():
    shutil.rmtree(SKILLS_POOL)
SHOTS.mkdir(parents=True, exist_ok=True)

# 技能池夹具：一个合法 SKILL.md（LocalSkillLoader 扫描用）
(SKILLS_POOL / "probe-skill").mkdir(parents=True)
(SKILLS_POOL / "probe-skill" / "SKILL.md").write_text(
    "---\nname: probe-skill\ndescription: 探测用技能\nversion: 1.0.0\n---\n\n# 探测技能\n\n用于 E2E 探测。\n",
    encoding="utf-8",
)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["PYTHONIOENCODING"] = "utf-8"

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=open(RESULTS / f"{AREA}.server.log", "w", encoding="utf-8"),
    stderr=subprocess.STDOUT,
)

results = []  # (id, name, passed, detail)
page = None
console_errors = []
page_errors = []
failed_reqs = []


def cleanup_after_failure():
    """失败兜底：关闭遗留的确认框/下拉/对话框，避免遮罩让后续用例级联超时。

    只在用例失败后执行（成功用例按自身流程收尾），先截图保留现场再清理；
    清理本身的异常不得覆盖原始断言失败。注意不要按 ESC：Element Plus 的
    select/dialog/drawer 都监听 ESC，会连带把设置抽屉关掉，反而污染后续用例。
    """
    if page is None:
        return
    try:
        # 1) 确认框（删除/探测确认）：点「取消」或右上角 X
        box = page.locator(".el-message-box:visible").first
        if box.count():
            btn = box.locator("button", has_text="取消")
            if not btn.count():
                btn = box.locator(".el-message-box__headerbtn")
            if btn.count():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(250)
        # 2) 下拉浮层：点对话框/抽屉标题（clickoutside），避免 ESC 连抽屉一起关
        if page.locator(".el-select-dropdown:visible").count():
            neutral = page.locator(".el-dialog:visible .el-dialog__title").last
            if not neutral.count():
                neutral = page.locator(".el-drawer:visible .el-drawer__title").first
            if neutral.count():
                neutral.click(timeout=1500)
                page.wait_for_timeout(250)
        # 3) 表单对话框（供应商/模型/专家）：优先「取消」，否则右上角 X
        for _ in range(3):
            dlgs = page.locator(".el-dialog:visible")
            if not dlgs.count():
                break
            dlg = dlgs.last
            btn = dlg.locator(".el-dialog__footer button", has_text="取消")
            if not btn.count():
                btn = dlg.locator(".el-dialog__headerbtn")
            if not btn.count():
                break
            btn.first.click(timeout=1500)
            page.wait_for_timeout(300)
    except Exception:
        pass


def record(tid, name, fn):
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}")
    except Exception as e:
        detail = str(e)[:300]
        results.append((tid, name, False, detail))
        print(f"[FAIL] {tid} {name}: {detail}")
        try:
            if page is not None:
                page.screenshot(path=str(SHOTS / f"{tid}.png"))
        except Exception:
            pass
        cleanup_after_failure()


def api(method, path, payload=None, timeout=60):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, method=method, data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def goto(route):
    page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(600)


def drawer():
    return page.locator(".el-drawer:visible")


def dialog():
    return page.locator(".el-dialog:visible")


def confirm_box(btn_text="确定"):
    box = page.locator(".el-message-box:visible")
    box.wait_for(state="visible", timeout=5000)
    box.locator("button", has_text=btn_text).first.click()
    page.wait_for_timeout(600)


def wait_toast(text, timeout=6000):
    page.locator(".el-message", has_text=text).first.wait_for(state="visible", timeout=timeout)


def wait_class(locator, name, timeout=6000):
    """轮询等待元素 class 出现目标类（父组件异步刷新回填场景），timeout 单位毫秒。"""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if name in (locator.get_attribute("class") or "").split():
            return True
        page.wait_for_timeout(150)
    return False


def open_drawer():
    if drawer().count():
        return
    page.locator("button", has_text="设置").first.click()
    drawer().wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(400)


def switch_tab(name):
    page.locator(".el-tabs__item", has_text=name).first.click()
    page.wait_for_timeout(500)


def prov_card(name):
    return page.locator(".el-drawer .prov-card", has_text=name).first


def model_row(card, model):
    return card.locator(".model-row", has_text=model).first


def form_item(dlg, label):
    return dlg.locator(".el-form-item", has_text=label).first


def fill_input(dlg, label, value):
    form_item(dlg, label).locator("input").first.fill(value)


def fill_model_name(dlg, name):
    """模型名输入框：无预设候选时是 el-input，有预设候选时是 allow-create 的 el-select。"""
    item = form_item(dlg, "模型名")
    if item.locator(".el-select").count():
        item.locator(".el-select").click()
        page.wait_for_timeout(300)
        page.keyboard.type(name)
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
    else:
        item.locator("input").first.fill(name)


def save_dialog(dlg):
    dlg.locator("button", has_text="保存").first.click()
    page.wait_for_timeout(900)


try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_reqs.append(f"{r.method} {r.url}"))

        # ---------- 页面与控制台 ----------
        def tc_page_load():
            goto("/assistant")
            assert page.locator(".agent-item", has_text="智能路由").count() > 0, "Agent 快速列表未渲染"
            assert page.locator("button", has_text="设置").count() > 0, "设置按钮不存在"
            bad = [e for e in console_errors if "favicon" not in e]
            assert not bad, f"控制台错误: {bad[:3]}"
        record("AS-PAGE-001", "/assistant 页面加载无控制台错误", tc_page_load)

        def tc_drawer_open():
            open_drawer()
            assert drawer().locator(".el-tabs__item").count() == 3, "应有三个 tab"
            assert drawer().locator(".el-tabs__item.is-active", has_text="供应商与模型").count() == 1, "默认 tab 应为供应商与模型"
            alert = drawer().locator(".el-alert").first
            txt = alert.inner_text()
            assert "当前生效模型" in txt, f"生效模型行缺失: {txt[:60]}"
            assert ".env 兜底" in txt, f"隔离库无供应商时应显示 .env 兜底来源: {txt[:80]}"
        record("AS-PAGE-002", "设置抽屉打开：三 tab + 生效模型来源行", tc_drawer_open)

        # ---------- 供应商与模型 ----------
        def tc_provider_create():
            drawer().locator("button", has_text="新增供应商").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_input(dlg, "名称", "探测供应商A")
            fill_input(dlg, "Base URL", UNREACHABLE)
            save_dialog(dlg)
            wait_toast("供应商已保存")
            card = prov_card("探测供应商A")
            card.wait_for(state="visible", timeout=5000)
            assert UNREACHABLE in card.inner_text(), "base_url 未展示"
        record("AS-PROV-001", "新增供应商：卡片与 base_url 展示", tc_provider_create)

        def tc_provider_toggle():
            card = prov_card("探测供应商A")
            switch = card.locator(".prov-head .el-switch")
            switch.click()
            page.wait_for_timeout(700)
            assert "off" in (card.get_attribute("class") or ""), "停用后卡片无 off 样式"
            assert "已停用" in card.inner_text(), "停用徽标未显示「已停用」"
            switch.click()
            page.wait_for_timeout(700)
            assert "off" not in (card.get_attribute("class") or ""), "启用后卡片仍 off"
            assert "已停用" not in card.inner_text(), "启用后仍显示已停用"
        record("AS-PROV-002", "供应商启停开关 + 「已停用」徽标", tc_provider_toggle)

        def tc_provider_edit():
            card = prov_card("探测供应商A")
            card.locator(".prov-head button", has_text="编辑").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_input(dlg, "名称", "探测供应商A2")
            save_dialog(dlg)
            wait_toast("供应商已保存")
            prov_card("探测供应商A2").wait_for(state="visible", timeout=5000)
            st, body = api("GET", "/api/assistant/providers")
            assert any(p["name"] == "探测供应商A2" for p in body["providers"]), "后端未持久化改名"
        record("AS-PROV-003", "编辑供应商改名并持久化", tc_provider_edit)

        def tc_model_create():
            card = prov_card("探测供应商A2")
            card.locator(".prov-models button", has_text="模型").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_model_name(dlg, "probe-model-1")
            form_item(dlg, "输出上限").locator("input").first.fill("4096")
            save_dialog(dlg)
            wait_toast("模型已保存")
            row = model_row(card, "probe-model-1")
            row.wait_for(state="visible", timeout=5000)
        record("AS-PROV-004", "新增模型：行出现", tc_model_create)

        def tc_model_edit():
            card = prov_card("探测供应商A2")
            row = model_row(card, "probe-model-1")
            row.locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_input(dlg, "备注", "探测备注")
            form_item(dlg, "输出上限").locator("input").first.fill("8192")
            save_dialog(dlg)
            wait_toast("模型已保存")
            st, body = api("GET", "/api/assistant/providers")
            prov = next(p for p in body["providers"] if p["name"] == "探测供应商A2")
            m = next(m for m in prov["models"] if m["model"] == "probe-model-1")
            assert m["max_tokens"] == 8192, f"输出上限未持久化: {m['max_tokens']}"
            assert m["note"] == "探测备注", f"备注未持久化: {m['note']!r}"
        record("AS-PROV-005", "编辑模型：输出上限/备注持久化", tc_model_edit)

        def tc_model_disable():
            card = prov_card("探测供应商A2")
            row = model_row(card, "probe-model-1")
            row.locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            form_item(dlg, "启用").locator(".el-switch").click()
            page.wait_for_timeout(300)
            save_dialog(dlg)
            wait_toast("模型已保存")
            page.wait_for_timeout(300)
            assert "off" in (model_row(card, "probe-model-1").get_attribute("class") or ""), "停用模型行无 off 样式"
            st, body = api("GET", "/api/assistant/models")
            assert not any("probe-model-1" in m["label"] for m in body["models"]), "停用模型仍出现在扁平列表"
        record("AS-PROV-006", "模型停用：行 off 样式且退出快速切换列表", tc_model_disable)

        def tc_model_activate():
            card = prov_card("探测供应商A2")
            row = model_row(card, "probe-model-1")
            # 先经对话框重新启用（上一步停用了它），停用模型激活会 404（属预期）
            row.locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            form_item(dlg, "启用").locator(".el-switch").click()
            page.wait_for_timeout(300)
            save_dialog(dlg)
            wait_toast("模型已保存")
            page.wait_for_timeout(400)
            row.locator(".model-name").click()
            page.wait_for_timeout(900)
            wait_toast("已切换到")
            assert "active" in (model_row(card, "probe-model-1").get_attribute("class") or ""), "激活行无 active 样式"
            assert "✓" in model_row(card, "probe-model-1").inner_text(), "激活模型缺 ✓ 前缀"
            alert = drawer().locator(".el-alert").first.inner_text()
            assert "probe-model-1" in alert and "探测供应商A2" in alert, f"生效模型来源行未更新: {alert[:100]}"
            st, body = api("GET", "/api/assistant/config")
            assert body["config"]["model"] == "probe-model-1", "后端激活未生效"
        record("AS-PROV-007", "激活模型：✓ 前缀 + 生效来源行切换", tc_model_activate)

        def tc_provider_test_no_key():
            card = prov_card("探测供应商A2")
            card.locator(".prov-ops button", has_text="连通").click()
            box = card.locator(".prov-test")
            box.wait_for(state="visible", timeout=30000)
            txt = box.inner_text()
            assert "❌" in txt, f"无密钥连通未优雅报错: {txt[:100]}"
            assert "请先填写" in txt, f"错误文案不符合预期: {txt[:120]}"
            assert not page_errors, f"出现页面异常: {page_errors[:2]}"
        record("AS-PROV-008", "连通测试（无密钥）：优雅 ❌ 不崩", tc_provider_test_no_key)

        def tc_probe_all_models():
            card = prov_card("探测供应商A2")
            btn = card.locator(".prov-ops button", has_text="探测全部模型")
            assert btn.count() == 1, "「探测全部模型」按钮不存在"
            btn.click()
            confirm_box("开始探测")
            # 无密钥 + 拒连地址：探测应以 error 字段收尾，弹「批量探测完成」且无页面异常
            wait_toast("批量探测完成", timeout=120000)
            assert not page_errors, f"探测过程出现页面异常: {page_errors[:2]}"
            st, body = api("GET", "/api/assistant/providers")
            prov = next(p for p in body["providers"] if p["name"] == "探测供应商A2")
            m = next(m for m in prov["models"] if m["model"] == "probe-model-1")
            assert m["probed_at"], "探测后 probed_at 未刷新（探测未真正执行）"
        record("AS-PROV-009", "探测全部模型（无密钥）：优雅降级不崩", tc_probe_all_models)

        def tc_probe_all_no_models():
            card = prov_card("探测供应商A2")
            row = model_row(card, "probe-model-1")
            row.locator(".pill-x").click()
            page.wait_for_timeout(800)
            assert model_row(card, "probe-model-1").count() == 0, "删除模型后行仍在"
            card.locator(".prov-ops button", has_text="探测全部模型").click()
            wait_toast("没有启用中的模型")
        record("AS-PROV-010", "删除模型（X 即删）；无启用模型时批量探测警告", tc_probe_all_no_models)

        def tc_preset_fill():
            drawer().locator("button", has_text="新增供应商").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            form_item(dlg, "快速预设").locator(".el-select").click()
            page.wait_for_timeout(300)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="DeepSeek").first.click()
            page.wait_for_timeout(300)
            assert form_item(dlg, "Base URL").locator("input").first.input_value() == "https://api.deepseek.com/v1", "预设未填充 base_url"
            assert form_item(dlg, "候选模型").count() == 1, "候选模型项未出现"
            fill_input(dlg, "名称", "预设探测供应商")
            save_dialog(dlg)
            wait_toast("供应商已保存")
            card = prov_card("预设探测供应商")
            card.wait_for(state="visible", timeout=5000)
            page.wait_for_timeout(600)
            assert card.locator(".model-row").count() == 2, f"预设候选模型未自动添加: {card.locator('.model-row').count()}"
            # 复位供应商表单状态（presetFill 残留会影响后续模型表单形态）
            drawer().locator("button", has_text="新增供应商").click()
            page.wait_for_timeout(300)
            dialog().locator("button", has_text="取消").first.click()
            page.wait_for_timeout(300)
        record("AS-PROV-011", "预设填充：base_url + 候选模型自动添加", tc_preset_fill)

        def tc_provider_delete():
            for name in ("预设探测供应商", "探测供应商A2"):
                card = prov_card(name)
                if not card.count():
                    continue
                card.locator(".prov-head button", has_text="删除").click()
                confirm_box("确定")
                page.wait_for_timeout(800)
                assert prov_card(name).count() == 0, f"删除后 {name} 仍在"
            st, body = api("GET", "/api/assistant/providers")
            assert body["providers"] == [], "后端供应商未删空"
            alert = drawer().locator(".el-alert").first.inner_text()
            assert ".env 兜底" in alert, f"删除后生效行未回退 env 兜底: {alert[:100]}"
        record("AS-PROV-012", "删除供应商：卡片消失 + 激活态回退 env 兜底", tc_provider_delete)

        # ---------- 专家团队 ----------
        def tc_agents_tab():
            switch_tab("专家团队")
            cards = page.locator(".agent-card")
            cards.first.wait_for(state="visible", timeout=5000)
            assert cards.count() > 0, "内置专家未渲染"
            assert page.locator(".agent-card .el-tag", has_text="内置").count() > 0, "内置徽标缺失"
        record("AS-AGENT-001", "专家团队 tab：内置专家卡片渲染", tc_agents_tab)

        def tc_route_test():
            inp = page.locator(".route-test input")
            inp.fill("帮我算一下增值税")
            page.locator(".route-test button", has_text="测试路由").click()
            res = page.locator(".route-result")
            res.wait_for(state="visible", timeout=10000)
            txt = res.inner_text()
            assert "命中" in txt or "无命中" in txt, f"路由测试无结果: {txt[:80]}"
        record("AS-AGENT-002", "路由测试：输入一句话返回命中专家", tc_route_test)

        def tc_agent_create():
            page.locator("button", has_text="新建专家").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_input(dlg, "名称", "probe_agent")
            fill_input(dlg, "显示名", "探测专家")
            kw = form_item(dlg, "路由关键词").locator(".el-select").first
            kw.click()
            page.wait_for_timeout(200)
            page.keyboard.type("探测词")
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            save_dialog(dlg)
            wait_toast("专家已保存")
            page.wait_for_timeout(500)
            assert page.locator(".agent-card", has_text="探测专家").count() > 0, "新专家卡片未出现"
        record("AS-AGENT-003", "新建专家：卡片出现", tc_agent_create)

        def tc_agent_invalid_name():
            before = api("GET", "/api/assistant/agents")[1]["agents"]
            page.locator("button", has_text="新建专家").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_input(dlg, "名称", "Probe_Agent")
            fill_input(dlg, "显示名", "非法名专家")
            dlg.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(600)
            assert dlg.locator(".el-form-item__error", has_text="小写字母开头").count() > 0, "非法名称未前端拦截"
            assert dialog().count() > 0, "校验失败对话框不应关闭"
            after = api("GET", "/api/assistant/agents")[1]["agents"]
            assert len(after) == len(before), "非法名称仍发起了创建请求"
            dlg.locator("button", has_text="取消").first.click()
            page.wait_for_timeout(300)
        record("AS-AGENT-004", "非法专家名：前端校验拦截不发起请求", tc_agent_invalid_name)

        def tc_agent_dup_name():
            page.locator("button", has_text="新建专家").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_input(dlg, "名称", "probe_agent")
            fill_input(dlg, "显示名", "重名专家")
            dlg.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(900)
            wait_toast("已存在")
            assert dialog().count() > 0, "重名 400 后对话框应保持打开"
            dlg.locator("button", has_text="取消").first.click()
            page.wait_for_timeout(300)
        record("AS-AGENT-005", "重名专家：400 优雅提示且对话框不关", tc_agent_dup_name)

        def tc_agent_edit():
            card = page.locator(".agent-card", has_text="探测专家")
            card.locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            name_input = form_item(dlg, "名称").locator("input").first
            assert name_input.is_disabled(), "编辑时名称输入框应禁用"
            fill_input(dlg, "显示名", "探测专家改名")
            save_dialog(dlg)
            wait_toast("专家已保存")
            page.wait_for_timeout(500)
            assert page.locator(".agent-card", has_text="探测专家改名").count() > 0, "改名未生效"
        record("AS-AGENT-006", "编辑专家：显示名持久化、标识锁定", tc_agent_edit)

        def tc_agent_toggle():
            card = page.locator(".agent-card", has_text="探测专家改名")
            switch = card.locator(".el-switch")
            switch.click()
            page.wait_for_timeout(800)
            assert "off" in (card.get_attribute("class") or ""), "停用专家卡片无 off 样式"
            st, body = api("GET", "/api/assistant/agents")
            a = next(a for a in body["agents"] if a["name"] == "probe_agent")
            assert a["enabled"] is False, "后端 enabled 未置 false"
            switch.click()
            page.wait_for_timeout(800)
            assert "off" not in (card.get_attribute("class") or ""), "启用后仍 off"
        record("AS-AGENT-007", "专家启停：卡片样式 + 后端持久化", tc_agent_toggle)

        def tc_agent_reset():
            # 先改一个内置专家，再恢复出厂验证覆盖
            builtin = page.locator(".agent-card", has_text="网络安全工程师")
            builtin.locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            fill_input(dlg, "显示名", "改名内置专家")
            save_dialog(dlg)
            wait_toast("专家已保存")
            page.wait_for_timeout(400)
            assert page.locator(".agent-card", has_text="改名内置专家").count() > 0, "内置专家改名未生效"
            page.locator("button", has_text="恢复出厂").click()
            confirm_box("确定")
            wait_toast("恢复出厂")
            page.wait_for_timeout(600)
            assert page.locator(".agent-card", has_text="网络安全工程师").count() > 0, "恢复出厂未还原内置专家"
            assert page.locator(".agent-card", has_text="探测专家改名").count() > 0, "恢复出厂误伤自定义专家"
        record("AS-AGENT-008", "恢复出厂：覆盖内置修改、保留自定义专家", tc_agent_reset)

        def tc_agent_model_bind():
            # 造一个启用中的模型供绑定下拉验证
            st, _ = api("POST", "/api/assistant/providers", {"name": "绑定探测", "base_url": UNREACHABLE})
            pid = _["provider"]["id"]
            st, _ = api("POST", f"/api/assistant/providers/{pid}/models", {"model": "bind-model"})
            mid = _["model"]["id"]
            page.locator("button", has_text="新建专家").click()
            page.wait_for_timeout(400)
            dlg = dialog()
            dlg.wait_for(state="visible", timeout=5000)
            sel = form_item(dlg, "绑定模型").locator(".el-select").first
            sel.click()
            page.wait_for_timeout(300)
            opts = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
            texts = opts.all_inner_texts()
            assert any("bind-model" in t for t in texts), f"绑定下拉缺少启用模型: {texts[:5]}"
            opts.filter(has_text="bind-model").first.click()
            page.wait_for_timeout(200)
            fill_input(dlg, "名称", "bind_agent")
            fill_input(dlg, "显示名", "绑定模型专家")
            save_dialog(dlg)
            wait_toast("专家已保存")
            page.wait_for_timeout(400)
            card = page.locator(".agent-card", has_text="绑定模型专家")
            assert "bind-model" in card.inner_text(), "卡片未展示绑定模型名"
            # 清理：删专家 → 删供应商（级联模型）
            card.locator("button", has_text="删除").click()
            confirm_box("删除")
            page.wait_for_timeout(600)
            api("DELETE", f"/api/assistant/providers/{pid}")
            page.wait_for_timeout(300)
        record("AS-AGENT-009", "专家绑定模型：下拉含启用模型 + 卡片展示", tc_agent_model_bind)

        def tc_agent_delete():
            card = page.locator(".agent-card", has_text="探测专家改名")
            card.locator("button", has_text="删除").click()
            confirm_box("删除")
            page.wait_for_timeout(700)
            assert page.locator(".agent-card", has_text="探测专家改名").count() == 0, "删除后卡片仍在"
            st, body = api("GET", "/api/assistant/agents")
            assert not any(a["name"] == "probe_agent" for a in body["agents"]), "后端专家未删除"
        record("AS-AGENT-010", "删除自定义专家：确认后消失", tc_agent_delete)

        # ---------- 工作参数 ----------
        def tc_globals_tab():
            switch_tab("工作参数")
            page.locator("button", has_text="保存工作参数").wait_for(state="visible", timeout=5000)
            assert form_item(drawer(), "最大工具轮次").count() == 1, "工作参数表单未渲染"
        record("AS-GLOB-001", "工作参数 tab：表单渲染", tc_globals_tab)

        def tc_skills_detect_missing():
            fill_input(drawer(), "技能池目录", str(RESULTS / "no_such_dir_xyz"))
            drawer().locator("button", has_text="检测").first.click()
            hint = form_item(drawer(), "技能池目录").locator(".cfg-hint").first
            hint.wait_for(state="visible", timeout=10000)
            txt = hint.inner_text()
            assert "未发现技能" in txt or "目录不存在" in txt, f"检测无结论: {txt[:80]}"
        record("AS-GLOB-002", "技能池检测（目录不存在）：有结论不崩", tc_skills_detect_missing)

        def tc_skills_detect_hit():
            fill_input(drawer(), "技能池目录", str(SKILLS_POOL))
            drawer().locator("button", has_text="检测").first.click()
            hint = form_item(drawer(), "技能池目录").locator(".cfg-hint").first
            hint.filter(has_text="probe-skill").wait_for(state="visible", timeout=10000)
            assert "probe-skill" in hint.inner_text(), f"未发现夹具技能: {hint.inner_text()[:80]}"
        record("AS-GLOB-003", "技能池检测（含 SKILL.md）：发现技能清单", tc_skills_detect_hit)

        def tc_globals_save():
            form_item(drawer(), "最大工具轮次").locator("input").first.fill("8")
            form_item(drawer(), "启用技能").locator(".el-switch").click()
            page.wait_for_timeout(200)
            drawer().locator("button", has_text="保存工作参数").click()
            wait_toast("工作参数已保存")
            page.wait_for_timeout(500)
            assert drawer().count() == 0, "保存后抽屉应自动关闭"
            st, body = api("GET", "/api/assistant/config")
            cfg = body["config"]
            assert cfg["max_iters"] == 8, f"max_iters 未持久化: {cfg['max_iters']}"
            assert cfg["enable_skills"] is True, "enable_skills 未持久化"
            assert "assistant-settings-skills" in cfg["skills_dir"], f"skills_dir 未持久化: {cfg['skills_dir']!r}"
        record("AS-GLOB-004", "保存工作参数：max_iters/技能开关/目录持久化", tc_globals_save)

        def tc_globals_reload_values():
            open_drawer()
            switch_tab("工作参数")
            v = form_item(drawer(), "最大工具轮次").locator("input").first.input_value()
            assert v == "8", f"重开抽屉未回填已保存值: {v}"
            skills_switch = form_item(drawer(), "启用技能").locator(".el-switch")
            assert wait_class(skills_switch, "is-checked"), "启用技能开关未回填"
        record("AS-GLOB-005", "重开抽屉：工作参数回填已保存值", tc_globals_reload_values)

        def tc_plugins_detect_missing():
            fill_input(drawer(), "插件目录", str(RESULTS / "no_such_plugins"))
            drawer().locator("button", has_text="检测").nth(1).click()
            hint = drawer().locator(".cfg-hint", has_text="插件").last
            hint.wait_for(state="visible", timeout=10000)
            assert hint.count() > 0, "插件检测无结论"
        record("AS-GLOB-006", "插件目录检测（不存在）：有结论不崩", tc_plugins_detect_missing)

        # ---------- API 边界（契约级） ----------
        def tc_api_activate_missing():
            st, _ = api("POST", "/api/assistant/models/999999/activate")
            assert st == 404, f"激活不存在模型应 404，实际 {st}"
        record("AS-API-001", "激活不存在模型 → 404", tc_api_activate_missing)

        def tc_api_model_empty_name():
            st, _ = api("POST", "/api/assistant/providers", {"name": "边界供应商", "base_url": UNREACHABLE})
            pid = _["provider"]["id"]
            st, body = api("POST", f"/api/assistant/providers/{pid}/models", {"model": ""})
            # 空模型名是请求体 schema 校验（min_length=1），按 API.md 错误约定为 422；
            # 业务校验（供应商不存在 / 名称去空白后为空）才是 400。
            assert st == 422, f"空模型名属参数校验应 422，实际 {st}"
            assert body.get("detail"), "422 响应应包含 detail"
            st, body = api("POST", f"/api/assistant/providers/99999/models", {"model": "x"})
            assert st == 400, f"不存在供应商应 400，实际 {st}"
            api("DELETE", f"/api/assistant/providers/{pid}")
        record("AS-API-002", "空模型名 → 422（schema）/ 不存在供应商 → 400", tc_api_model_empty_name)

        def tc_api_reasoning_effort_normalize():
            st, _ = api("PUT", "/api/assistant/config", {"reasoning_effort": "bogus-level"})
            assert st == 200
            cfg = api("GET", "/api/assistant/config")[1]["config"]
            assert cfg["reasoning_effort"] == "", f"非法 reasoning_effort 未归一化: {cfg['reasoning_effort']!r}"
        record("AS-API-003", "非法 reasoning_effort → 归一化空串", tc_api_reasoning_effort_normalize)

        def tc_api_test_no_enabled_model():
            st, _ = api("POST", "/api/assistant/providers", {"name": "空模型供应商", "base_url": UNREACHABLE})
            pid = _["provider"]["id"]
            st, body = api("POST", "/api/assistant/config/test", {"provider_id": pid})
            assert st == 400, f"无启用模型的供应商测试应 400，实际 {st}"
            assert "没有启用中的模型" in body.get("detail", ""), f"detail 文案: {body.get('detail')!r}"
            api("DELETE", f"/api/assistant/providers/{pid}")
        record("AS-API-004", "测试无启用模型的供应商 → 400 明确文案", tc_api_test_no_enabled_model)

        def tc_api_skills_discover_contract():
            st, body = api("GET", f"/api/assistant/skills/discover?dir={urllib.request.quote(str(RESULTS / 'no_such_dir_xyz'))}")
            assert st == 200 and body.get("count") == 0 and body.get("error"), f"目录不存在契约: {st} {body}"
            st, body = api("GET", f"/api/assistant/skills/discover?dir={urllib.request.quote(str(SKILLS_POOL))}")
            assert st == 200 and body.get("count") == 1, f"技能池扫描契约: {st} {body}"
        record("AS-API-005", "skills/discover 契约：不存在报错 / 存在计数", tc_api_skills_discover_contract)

        def tc_api_delete_provider_cascade():
            st, _ = api("POST", "/api/assistant/providers", {"name": "级联供应商", "base_url": UNREACHABLE})
            pid = _["provider"]["id"]
            api("POST", f"/api/assistant/providers/{pid}/models", {"model": "cascade-model"})
            api("DELETE", f"/api/assistant/providers/{pid}")
            st, body = api("GET", "/api/assistant/providers")
            assert all(p["id"] != pid for p in body["providers"]), "供应商未删除"
            flat = api("GET", "/api/assistant/models")[1]["models"]
            assert not any("cascade-model" in m["label"] for m in flat), "级联删除后模型仍残留"
        record("AS-API-006", "删除供应商级联清理模型", tc_api_delete_provider_cascade)

        def tc_api_reset_agents_contract():
            # 删掉一个内置专家再恢复出厂：应补插回来
            st, body = api("GET", "/api/assistant/agents")
            builtin = next(a for a in body["agents"] if a["is_builtin"])
            api("DELETE", f"/api/assistant/agents/{builtin['id']}")
            st, body = api("POST", "/api/assistant/agents/reset")
            assert st == 200 and builtin["name"] in body["restored"], f"恢复出厂未补插: {body}"
            names = {a["name"] for a in api("GET", "/api/assistant/agents")[1]["agents"]}
            assert builtin["name"] in names, "恢复后内置专家缺失"
        record("AS-API-007", "恢复出厂契约：删除的内置专家补插还原", tc_api_reset_agents_contract)

        def tc_agent_tab_shot():
            open_drawer()
            switch_tab("专家团队")
            page.wait_for_timeout(400)
            page.screenshot(path=str(SHOTS / "tab-agents.png"))
            switch_tab("工作参数")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / "tab-globals.png"))
            switch_tab("供应商与模型")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / "tab-providers.png"))
        record("AS-SHOT-001", "三 tab 截图存档", tc_agent_tab_shot)

        browser.close()

    # ---------- 汇总 ----------
    pages = [{
        "route": "/assistant",
        "name": "assistant-settings-drawer",
        "status": 200,
        "console_errors": sorted(set(e for e in console_errors if "favicon" not in e)),
        "page_errors": sorted(set(page_errors)),
        "failed_reqs": sorted(set(f for f in failed_reqs if "favicon" not in f)),
    }]

    passed = sum(1 for r in results if r[2])
    print("=" * 70)
    print(f"total={len(results)} passed={passed} failed={len(results)-passed}")
    for e in pages[0]["console_errors"][:10]:
        print(f"  !! console: {e[:200]}")
    for e in pages[0]["page_errors"][:10]:
        print(f"  !! pageerror: {e[:200]}")
    print("=" * 70)

    report = {
        "area": AREA,
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages,
        "tests": [{"id": r[0], "name": r[1], "passed": r[2], "detail": r[3]} for r in results],
        "bugs": [],
    }
    (RESULTS / f"{AREA}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report -> {RESULTS / f'{AREA}.json'}")

    sys.exit(0 if passed == len(results) else 1)
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
