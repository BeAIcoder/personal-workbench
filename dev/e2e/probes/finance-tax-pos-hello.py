# -*- coding: utf-8 -*-
"""E2E 深度探测：finance-tax / finance-pos / finance-hello（端口 8214）。

覆盖：
- API 层：企业 CRUD（legal_person 等四字段持久化）、申报记录校验（非法 tax_period/quarter 422）、
  导出 Excel/PDF（状态码 + 响应头 + 魔数）、政策克隆两次第二次 400、税费计算、
  POS 运行（非法文件 400 / zip-slip 400 / 无产物 409 / 垃圾输入执行后 failed）、hello 条目。
- 浏览器层：三页 + 税务子页的渲染、控制台错误、关键交互（建企业、算税、存申报、政策停启用、POS 对话框）。

可重复运行：每次运行重建独立库 dev/e2e/probe_results/finance-tax-pos-hello.db 与
finance-tax-pos-hello-fin/，服务器日志写 finance-tax-pos-hello.server.log。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS_DIR = ROOT / "dev" / "e2e" / "probe_results"
AREA = "finance-tax-pos-hello"
AREA_DIR = RESULTS_DIR / AREA
SHOTS = AREA_DIR / "shots"
DB = RESULTS_DIR / f"{AREA}.db"
FIN_DIR = RESULTS_DIR / f"{AREA}-fin"
SERVER_LOG = RESULTS_DIR / f"{AREA}.server.log"
PORT = 8214
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

AREA_DIR.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)

if DB.exists():
    DB.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["PYTHONIOENCODING"] = "utf-8"

_log_fh = open(SERVER_LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=_log_fh, stderr=subprocess.STDOUT,
)

tests = []  # {id, name, passed, detail}
pages = []  # {route, name, status, console_errors, page_errors, failed_reqs}


def record(tid, name, fn):
    try:
        fn()
        tests.append({"id": tid, "name": name, "passed": True, "detail": ""})
        print(f"[PASS] {tid} {name}")
    except Exception as e:
        tests.append({"id": tid, "name": name, "passed": False, "detail": str(e)[:400]})
        print(f"[FAIL] {tid} {name}: {str(e)[:300]}")


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


# ---------- HTTP 助手 ----------

def req(method, path, body=None, raw=False, files=None, headers=None):
    """极简 HTTP 客户端：返回 (status, headers, bytes|json)。

    headers 统一转小写：HTTP 头大小写不敏感，Starlette 实际发送小写键，
    旧实现 dict(resp.headers) 保留原样导致 h.get("Content-Type") 恒为空。
    """
    url = f"{BASE}{path}"
    data = None
    hdrs = dict(headers or {})
    if files is not None:
        boundary = uuid.uuid4().hex
        buf = io.BytesIO()
        for fname, content, ctype in files:
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(f'Content-Disposition: form-data; name="files"; filename="{fname}"\r\n'.encode())
            buf.write(f"Content-Type: {ctype}\r\n\r\n".encode())
            buf.write(content)
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        data = buf.getvalue()
        hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            payload = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, (payload if raw else _maybe_json(payload))
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, {k.lower(): v for k, v in e.headers.items()}, (payload if raw else _maybe_json(payload))


def _maybe_json(payload: bytes):
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return payload.decode("utf-8", "replace")


def make_xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["col1", "col2"])
    ws.append(["a", "b"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- API 探测用例 ----------

CTX = {}


def t_hello_ping():
    s, _, d = req("GET", "/api/finance/hello/ping")
    assert s == 200 and d.get("ok") is True and d.get("module") == "finance_hello", f"ping 异常: {s} {d}"


def t_hello_entries():
    s, _, d = req("POST", "/api/finance/hello/entries", {"name": "E2E探测条目"})
    assert s == 201 and d["name"] == "E2E探测条目", f"创建条目失败: {s} {d}"
    s, _, d = req("GET", "/api/finance/hello/entries")
    assert s == 200 and any(e["name"] == "E2E探测条目" for e in d), f"列表未含新条目: {s}"
    # 空名 → 422
    s, _, d = req("POST", "/api/finance/hello/entries", {"name": ""})
    assert s == 422, f"空名应 422，实际 {s}: {d}"
    # 超限名 → 422（max_length=100）
    s, _, d = req("POST", "/api/finance/hello/entries", {"name": "x" * 101})
    assert s == 422, f"超长名应 422，实际 {s}"


def t_enterprise_create_persist_4fields():
    """上轮修复点：legal_person/contact_phone/address/tax_type 持久化。"""
    payload = {
        "name": "E2E探测企业有限公司",
        "tax_number": "91120116E2E0001X",
        "legal_person": "张三",
        "contact_phone": "022-12345678",
        "address": "天津市滨海新区探测路1号",
        "industry": "软件",
        "tax_type": "核定征收",
    }
    s, _, d = req("POST", "/api/finance/tax/enterprises/", payload)
    assert s in (200, 201), f"创建企业失败: {s} {d}"
    CTX["eid"] = d["id"]
    for k in ("legal_person", "contact_phone", "address", "tax_type"):
        assert d.get(k) == payload[k], f"创建响应字段 {k} 未回显: {d.get(k)!r}"
    # 重新 GET 验证真持久化（非仅回显）
    s, _, g = req("GET", f"/api/finance/tax/enterprises/{d['id']}")
    assert s == 200, f"GET 企业失败: {s}"
    for k in ("legal_person", "contact_phone", "address", "tax_type"):
        assert g.get(k) == payload[k], f"持久化字段 {k} 丢失: {g.get(k)!r}"


def t_enterprise_update_4fields():
    eid = CTX["eid"]
    s, _, d = req("PUT", f"/api/finance/tax/enterprises/{eid}",
                  {"legal_person": "李四", "contact_phone": "13800000000"})
    assert s == 200, f"更新失败: {s} {d}"
    s, _, g = req("GET", f"/api/finance/tax/enterprises/{eid}")
    assert g.get("legal_person") == "李四" and g.get("contact_phone") == "13800000000", \
        f"更新未持久化: legal_person={g.get('legal_person')!r} phone={g.get('contact_phone')!r}"
    # 未触碰的 address/tax_type 不应被清空
    assert g.get("address") == "天津市滨海新区探测路1号" and g.get("tax_type") == "核定征收", \
        f"部分更新误清其他字段: address={g.get('address')!r} tax_type={g.get('tax_type')!r}"


def t_enterprise_dup_tax_number():
    s, _, d = req("POST", "/api/finance/tax/enterprises/",
                  {"name": "重复税号企业", "tax_number": "91120116E2E0001X"})
    assert s == 400, f"重复税号应 400，实际 {s}: {d}"


def t_return_create_normal():
    s, _, d = req("POST", "/api/finance/tax/tax-returns/", {
        "enterprise_id": CTX["eid"], "tax_period": "2025", "quarter": "Q2",
        "report_type": "A类", "total_revenue": 100.0, "total_costs": 40.0,
        "total_expenses": 10.0,
    })
    assert s in (200, 201), f"创建申报失败: {s} {d}"
    assert d["year"] == 2025 and d["quarter"] == 2, f"季度映射错误: year={d['year']} quarter={d['quarter']}"
    assert d["tax_amount"] > 0, f"税额未计算: {d}"
    CTX["rid"] = d["id"]


def t_return_bad_period_422():
    """上轮修复点：非法 tax_period → 422 而非 500。"""
    for bad in ("20ab", "20264", "", "202"):
        s, _, d = req("POST", "/api/finance/tax/tax-returns/", {
            "enterprise_id": CTX["eid"], "tax_period": bad, "report_type": "A类",
        })
        assert s == 422, f"tax_period={bad!r} 应 422，实际 {s}: {str(d)[:120]}"
        assert "detail" in d, f"422 响应缺 detail: {d}"


def t_return_bad_quarter_422():
    s, _, d = req("POST", "/api/finance/tax/tax-returns/", {
        "enterprise_id": CTX["eid"], "tax_period": "2025", "quarter": "Q9",
    })
    assert s == 422, f"quarter=Q9 应 422，实际 {s}: {str(d)[:120]}"
    # 小写 q1 兼容
    s, _, d = req("POST", "/api/finance/tax/tax-returns/", {
        "enterprise_id": CTX["eid"], "tax_period": "2025", "quarter": "q1",
    })
    assert s in (200, 201) and d["quarter"] == 1, f"小写 q1 未兼容: {s} {d}"
    CTX["rid_q1"] = d["id"]


def t_return_enterprise_not_found():
    s, _, d = req("POST", "/api/finance/tax/tax-returns/", {
        "enterprise_id": 99999, "tax_period": "2025",
    })
    assert s == 404, f"不存在企业应 404，实际 {s}"


def t_calculate():
    s, _, d = req("POST", "/api/finance/tax/tax-returns/calculate", {
        "enterprise_id": CTX["eid"], "revenue": 100.0, "total_costs": 40.0,
        "total_expenses": 10.0, "total_salary": 20.0,
        "business_entertainment": 1.0, "employee_count": 10, "total_assets": 500.0,
    })
    assert s == 200, f"计算接口失败: {s} {str(d)[:200]}"
    assert d.get("success") is True, f"success 不为 true: {str(d)[:200]}"
    assert d["tax_amount_wan"] >= 0 and d["calculation_steps"], f"结果缺字段: {list(d.keys())[:8]}"


def t_calculate_404():
    s, _, d = req("POST", "/api/finance/tax/tax-returns/calculate",
                  {"enterprise_id": 99999, "revenue": 1.0})
    assert s == 404, f"不存在企业计算应 404，实际 {s}"


def t_export_excel():
    """上轮修复点：导出 Excel 不 500，响应头带 filename*。"""
    s, h, d = req("GET", f"/api/finance/tax/tax-returns/{CTX['rid']}/export/excel", raw=True)
    assert s == 200, f"导出 Excel 状态 {s}: {d[:200]}"
    assert d[:2] == b"PK", f"非 xlsx 魔数: {d[:8]!r}"
    ct = h.get("content-type", "")
    assert "spreadsheetml" in ct, f"Content-Type 异常: {ct}"
    cd = h.get("content-disposition", "")
    assert "attachment" in cd and "filename*=" in cd, f"Content-Disposition 缺 filename*: {cd}"


def t_export_pdf():
    """上轮修复点：导出 PDF 不 500。"""
    s, h, d = req("GET", f"/api/finance/tax/tax-returns/{CTX['rid']}/export/pdf", raw=True)
    assert s == 200, f"导出 PDF 状态 {s}: {d[:200]}"
    assert d[:5] == b"%PDF-", f"非 PDF 魔数: {d[:8]!r}"
    ct = h.get("content-type", "")
    assert "application/pdf" in ct, f"Content-Type 异常: {ct}"
    cd = h.get("content-disposition", "")
    assert "attachment" in cd and "filename*=" in cd, f"Content-Disposition 缺 filename*: {cd}"


def t_export_404():
    s, _, d = req("GET", "/api/finance/tax/tax-returns/99999/export/excel", raw=True)
    assert s == 404, f"不存在申报导出应 404，实际 {s}"


def t_policy_clone():
    pnum = "财税〔2026〕E2E-1号"
    s, _, d = req("POST", "/api/finance/tax/policies/", {
        "policy_number": pnum, "title": "E2E探测政策", "policy_type": "其他",
        "effective_date": "2026-01-01T00:00:00",
    })
    assert s in (200, 201), f"创建政策失败: {s} {d}"
    pid = d["id"]
    # 第一次克隆 → 201
    s, _, d = req("POST", f"/api/finance/tax/policies/{pid}/clone?new_version=2.0")
    assert s in (200, 201), f"首次克隆失败: {s} {d}"
    assert d["policy_number"] == f"{pnum}-v2.0", f"克隆文号异常: {d['policy_number']}"
    # 第二次同版本克隆 → 400（上轮修复点）
    s, _, d = req("POST", f"/api/finance/tax/policies/{pid}/clone?new_version=2.0")
    assert s == 400, f"重复克隆应 400，实际 {s}: {str(d)[:120]}"
    assert "已存在" in str(d.get("detail", "")), f"400 提示不含'已存在': {d}"
    # 克隆不存在政策 → 404
    s, _, d = req("POST", "/api/finance/tax/policies/99999/clone?new_version=9.9")
    assert s == 404, f"克隆不存在政策应 404，实际 {s}"
    CTX["pid"] = pid


def t_policy_date_range():
    s, _, d = req("POST", "/api/finance/tax/policies/", {
        "policy_number": "财税〔2026〕E2E-2号", "title": "日期倒挂政策", "policy_type": "其他",
        "effective_date": "2026-06-01T00:00:00", "expiry_date": "2026-01-01T00:00:00",
    })
    assert s == 400, f"失效早于生效应 400，实际 {s}: {str(d)[:120]}"


def t_pos_bad_suffix():
    s, _, d = req("POST", "/api/finance/pos/runs", files=[("evil.txt", b"hello", "text/plain")])
    assert s == 400, f"非法文件类型应 400，实际 {s}: {str(d)[:120]}"


def t_pos_zip_slip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.xlsx", b" junk ")
    s, _, d = req("POST", "/api/finance/pos/runs",
                  files=[("pack.zip", buf.getvalue(), "application/zip")])
    assert s == 400, f"zip-slip 应 400，实际 {s}: {str(d)[:160]}"
    assert "非法路径" in str(d.get("detail", "")), f"提示不含非法路径: {d}"


def t_pos_create_and_execute_garbage():
    """垃圾 xlsx：建运行 201 → 执行后状态 failed 且有 error（引擎失败不应使记录卡死）。"""
    s, _, d = req("POST", "/api/finance/pos/runs",
                  files=[("junk.xlsx", make_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")])
    assert s == 201, f"建运行失败: {s} {str(d)[:200]}"
    rid = d["id"]
    CTX["pos_rid"] = rid
    s, _, d = req("POST", f"/api/finance/pos/runs/{rid}/execute")
    # 引擎对垃圾输入必然失败：HTTP 层 503（EngineUnavailableError），记录层 failed + error
    assert s == 503, f"垃圾输入执行应 503，实际 {s}: {str(d)[:160]}"
    s, _, g = req("GET", f"/api/finance/pos/runs/{rid}")
    assert s == 200 and g["status"] == "failed", f"运行状态应为 failed: {s} {g.get('status')}"
    assert g.get("error"), f"failed 运行应有 error 信息: {g}"


def t_pos_download_empty_409():
    s, _, d = req("GET", f"/api/finance/pos/runs/{CTX['pos_rid']}/download", raw=True)
    assert s == 409, f"无产物下载应 409，实际 {s}"


def t_pos_404():
    s, _, d = req("GET", "/api/finance/pos/runs/99999")
    assert s == 404, f"不存在运行应 404，实际 {s}"
    s, _, d = req("GET", f"/api/finance/pos/runs/{CTX['pos_rid']}/artifacts/nope.xlsx", raw=True)
    assert s == 404, f"不存在产物应 404，实际 {s}"


def t_pos_no_files():
    s, _, d = req("POST", "/api/finance/pos/runs", data := None) if False else req("POST", "/api/finance/pos/runs")
    assert s == 422, f"无文件应 422，实际 {s}"


# ---------- 浏览器探测 ----------

def run_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console_errors, page_errors, failed_reqs = [], [], []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_reqs.append(f"{r.method} {r.url}"))

        def snap(name):
            page.screenshot(path=str(SHOTS / f"{name}.png"))

        def goto(route):
            return page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)

        def page_recon(route, name):
            ce0, pe0, fr0 = len(console_errors), len(page_errors), len(failed_reqs)
            resp = goto(route)
            page.wait_for_timeout(800)
            pages.append({
                "route": route, "name": name,
                "status": resp.status if resp else "?",
                "console_errors": console_errors[ce0:],
                "page_errors": page_errors[pe0:],
                "failed_reqs": [f for f in failed_reqs[fr0:] if "favicon" not in f],
            })
            snap(name)

        def b_hello():
            page_recon("/finance/hello", "hello")
            body = page.content()
            assert "finance_hello" in body and "ok" in body, "hello 页未显示 pong 信息"
        record("B-HELLO-001", "hello 页自检显示", b_hello)

        def b_tax_list():
            page_recon("/finance/tax", "tax-list")
            assert page.locator("td:visible", has_text="E2E探测企业有限公司").count() > 0, "企业列表未显示 API 创建的企业"
        record("B-TAX-001", "企业列表渲染", b_tax_list)

        def b_tax_new_validation():
            goto("/finance/tax/enterprises/new")
            page.wait_for_timeout(500)
            # 空表单直接保存 → 前端校验拦截，不应跳转
            page.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(600)
            assert "enterprises/new" in page.url, "空表单保存后不应离开新建页"
            assert page.locator(".el-form-item__error").count() > 0, "未出现必填校验提示"
            snap("tax-new-validation")
        record("B-TAX-002", "新建企业空表单校验", b_tax_new_validation)

        def b_tax_create_via_ui():
            goto("/finance/tax/enterprises/new")
            page.wait_for_timeout(500)
            form = page.locator(".el-form")
            form.locator("input").nth(0).fill("E2E界面建企业")
            form.locator("input").nth(1).fill("91120116UI00002Y")
            page.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1200)
            assert page.url.endswith("#/finance/tax"), f"保存后未跳回列表: {page.url}"
            page.wait_for_timeout(800)
            assert page.locator("td:visible", has_text="E2E界面建企业").count() > 0, "列表未出现 UI 新建企业"
            snap("tax-created-ui")
        record("B-TAX-003", "UI 新建企业全流程", b_tax_create_via_ui)

        def b_tax_edit_via_ui():
            goto("/finance/tax")
            page.wait_for_timeout(600)
            row = page.locator("tr", has_text="E2E界面建企业").first
            row.locator("button", has_text="编辑").click()
            page.wait_for_timeout(800)
            # 税号在编辑态禁用
            disabled = page.locator(".el-form input").nth(1).is_disabled()
            assert disabled, "编辑态税号输入框应禁用"
            snap("tax-edit")
            page.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1000)
            assert page.url.endswith("#/finance/tax"), "编辑保存后未跳回列表"
        record("B-TAX-004", "UI 编辑企业（税号锁定）", b_tax_edit_via_ui)

        def b_tax_calculate_ui():
            goto("/finance/tax")
            page.wait_for_timeout(600)
            row = page.locator("tr", has_text="E2E探测企业有限公司").first
            row.locator("button", has_text="税额计算").click()
            page.wait_for_timeout(1000)
            assert "calculate" in page.url, f"未进入计算页: {page.url}"
            # 填营业收入 100、营业成本 40
            nums = page.locator(".calc-form .el-input-number input")
            nums.nth(0).fill("100")
            nums.nth(2).fill("40")
            page.locator("button", has_text="开始计算").click()
            page.wait_for_timeout(1500)
            body = page.content()
            assert "计算结果" in body and "应纳税额" in body, "计算结果未渲染"
            assert "利润总额" in body, "计算步骤未渲染"
            snap("tax-calculate")
            # 保存为申报记录 → 跳申报列表
            page.locator("button", has_text="保存为申报记录").click()
            page.wait_for_timeout(1500)
            assert "returns" in page.url, f"保存后未跳申报记录页: {page.url}"
            assert page.locator("td:visible", has_text="2026").count() > 0 or page.locator("td").count() > 0
            snap("tax-returns-after-save")
        record("B-TAX-005", "税额计算页全流程", b_tax_calculate_ui)

        def b_tax_returns_export_buttons():
            goto("/finance/tax/returns")
            page.wait_for_timeout(800)
            assert page.locator("button", has_text="Excel").count() > 0, "申报记录页无 Excel 按钮"
            assert page.locator("button", has_text="PDF").count() > 0, "申报记录页无 PDF 按钮"
            # 预览跳转
            page.locator("button", has_text="预览").first.click()
            page.wait_for_timeout(1000)
            assert "returns/" in page.url, f"未进入预览页: {page.url}"
            assert page.locator(".el-descriptions").count() > 0, "预览页描述列表未渲染"
            snap("tax-return-preview")
        record("B-TAX-006", "申报记录页导出按钮与预览", b_tax_returns_export_buttons)

        def b_tax_policies_ui():
            page_recon("/finance/tax/policies", "tax-policies")
            row = page.locator("tr", has_text="E2E探测政策").first
            btn = row.locator("button", has_text="停用")
            btn.click()
            page.wait_for_timeout(1000)
            row2 = page.locator("tr", has_text="E2E探测政策").first
            assert "停用" in row2.inner_text(), "停用后状态未变"
            # 再启用
            row2.locator("button", has_text="启用").click()
            page.wait_for_timeout(1000)
            row3 = page.locator("tr", has_text="E2E探测政策").first
            assert "启用" in row3.inner_text(), "启用后状态未变"
        record("B-TAX-007", "政策停启用切换", b_tax_policies_ui)

        def b_tax_subpages():
            for route, name in (("/finance/tax/statistics", "tax-statistics"),
                                ("/finance/tax/import", "tax-import"),
                                ("/finance/tax/textbook", "tax-textbook")):
                page_recon(route, name)
        record("B-TAX-008", "税务子页侦察（统计/导入/教材）", b_tax_subpages)

        def b_pos_ui():
            page_recon("/finance/pos", "pos")
            page.locator("button", has_text="新建运行").first.click()
            page.wait_for_timeout(500)
            dlg = page.locator(".el-dialog:visible")
            assert dlg.count() > 0, "新建运行对话框未打开"
            # 未选文件时创建按钮禁用
            create_btn = dlg.locator("button", has_text="创建")
            assert create_btn.is_disabled(), "未选文件时创建按钮应禁用"
            dlg.locator("button", has_text="取消").click()
            page.wait_for_timeout(400)
            # 列表显示 API 创建的 failed 运行
            assert page.locator("tr", has_text="junk.xlsx").count() > 0, "POS 列表未显示已建运行"
            # 日志按钮可开
            page.locator("button", has_text="日志").first.click()
            page.wait_for_timeout(500)
            assert page.locator(".el-dialog:visible .log").count() > 0, "日志对话框未打开"
            snap("pos-log")
        record("B-POS-001", "POS 页交互（对话框/禁用/日志）", b_pos_ui)

        browser.close()

        # 汇总浏览器阶段 console/page 错误（不含 favicon）
        bad_console = [e for e in set(console_errors) if "favicon" not in e]
        return bad_console, page_errors


try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    # ---- API 阶段 ----
    record("A-HELLO-001", "hello ping 自检", t_hello_ping)
    record("A-HELLO-002", "hello 条目增查与校验", t_hello_entries)
    record("A-TAX-001", "企业创建四字段持久化", t_enterprise_create_persist_4fields)
    record("A-TAX-002", "企业更新四字段（部分更新不误清）", t_enterprise_update_4fields)
    record("A-TAX-003", "重复税号 400", t_enterprise_dup_tax_number)
    record("A-TAX-004", "创建申报记录（季度映射+税额）", t_return_create_normal)
    record("A-TAX-005", "非法 tax_period 422", t_return_bad_period_422)
    record("A-TAX-006", "非法/兼容 quarter", t_return_bad_quarter_422)
    record("A-TAX-007", "申报企业不存在 404", t_return_enterprise_not_found)
    record("A-TAX-008", "税费计算", t_calculate)
    record("A-TAX-009", "计算企业不存在 404", t_calculate_404)
    record("A-TAX-010", "导出 Excel（状态+头+魔数）", t_export_excel)
    record("A-TAX-011", "导出 PDF（状态+头+魔数）", t_export_pdf)
    record("A-TAX-012", "导出不存在的申报 404", t_export_404)
    record("A-TAX-013", "政策克隆（二次 400 有提示）", t_policy_clone)
    record("A-TAX-014", "政策日期倒挂 400", t_policy_date_range)
    record("A-POS-001", "POS 非法文件类型 400", t_pos_bad_suffix)
    record("A-POS-002", "POS zip-slip 拦截 400", t_pos_zip_slip)
    record("A-POS-003", "POS 垃圾输入执行→failed", t_pos_create_and_execute_garbage)
    record("A-POS-004", "POS 无产物下载 409", t_pos_download_empty_409)
    record("A-POS-005", "POS 不存在资源 404", t_pos_404)
    record("A-POS-006", "POS 无文件 422", t_pos_no_files)

    # ---- 浏览器阶段 ----
    bad_console, page_errs = run_browser()

    print("=" * 70)
    passed = sum(1 for t in tests if t["passed"])
    print(f"total={len(tests)} passed={passed} failed={len(tests) - passed}")
    print(f"browser_console_errors={len(bad_console)} page_errors={len(page_errs)}")
    for e in bad_console[:10]:
        print(f"  !! console: {e[:200]}")
    for e in page_errs[:10]:
        print(f"  !! pageerror: {e[:200]}")

    result = {
        "area": AREA,
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages,
        "tests": tests,
        "browser_console_errors": bad_console,
        "browser_page_errors": page_errs,
        "bugs": [],  # 由主控归并前手工填充
    }
    out = RESULTS_DIR / f"{AREA}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"results -> {out}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    _log_fh.close()
