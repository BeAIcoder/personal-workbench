# -*- coding: utf-8 -*-
"""finance-invoice-vault 区域深度 E2E 探测（可重复运行的正式资产）。

覆盖（端口 8212，独立 SQLite 库 + 独立财务数据目录，脚本自管服务器生命周期）：
- 发票批次上传（单/多 PDF、ZIP 拍平、路径穿越、空 ZIP、混传、空文件、超 200 文件）；
- 解析状态流转：uploaded→parsed→exported、failed 批次与 failed 明细展示、跨批次去重、冲红配对；
- 明细列表：关键字过滤、人工修正（corrected）、导出与 ZIP 下载、重复导出；
- 档案库：xlsx 入仓（replace/append）、表清单、预览弹窗、只读 SQL 白名单
  （SELECT 放行 / DROP·DELETE·多语句·文件路径·系统命令拒绝 / 字符串误报防护）、
  强制限流截断、CSV 导出、URL 表名参数校验、损坏文件与 append 列不匹配的 400 降级；
- file input 重选同一文件可再次触发（上轮 takeFile/takeFiles 修复点回归）。

用法：系统 python（playwright 装在系统 python）运行：
    python dev/e2e/probes/finance-invoice-vault.py
结果：dev/e2e/probe_results/finance-invoice-vault.json（+ shots/ 失败截图）。
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "finance-invoice-vault"
PORT = 8212
BASE = f"http://127.0.0.1:{PORT}"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
DEBUG_DIR = RESULTS / f"{AREA}-debug"
AREA_DIR = RESULTS / AREA
FIXTURES = AREA_DIR / "fixtures"
SHOTS = AREA_DIR / "shots"
LOG = RESULTS / f"{AREA}.server.log"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- fixtures ----------------

OK_TEXT = (
    "电子发票（增值税专用发票） 发票号码：26937000000123 校验码：12345678901234567890 "
    "开票日期：2026年10月01日 购 名称：探测购方甲公司 销 名称：探测销方乙公司 "
    "价税合计 ￥113.00 税率：13% 备注：办公用品采购"
)
OK2_TEXT = (
    "电子发票（增值税普通发票） 发票号码：26937000000125 校验码：222222222222 "
    "开票日期：2026年10月02日 购 名称：探测购方甲公司 销 名称：探测销方乙公司 "
    "价税合计 ￥226.00 税率：1% 备注：差旅费报销"
)
OK3_TEXT = (
    "电子发票（增值税专用发票） 发票号码：26937000000126 校验码：444444444444 "
    "开票日期：2026年10月03日 购 名称：探测购方甲公司 销 名称：探测销方乙公司 "
    "价税合计 ￥58.00 税率：13% 备注：咨询服务费"
)
OK4_TEXT = (
    "电子发票（增值税专用发票） 发票号码：26937000000130 校验码：555555555555 "
    "开票日期：2026年10月08日 购 名称：探测购方甲公司 销 名称：探测销方乙公司 "
    "价税合计 ￥113.00 税率：13% 备注：办公用品采购"
)
RED_TEXT = (
    "电子发票（增值税普通发票） 发票号码：26937000000124 校验码：333333333333 "
    "开票日期：2026年10月05日 购 名称：探测购方甲公司 销 名称：探测销方乙公司 "
    "价税合计 ￥-113.00 冲红 备注：退货冲红"
)
OK5_TEXT = (
    "电子发票（增值税专用发票） 发票号码：26937000000140 校验码：666666666666 "
    "开票日期：2026年10月09日 购 名称：探测购方丙公司 销 名称：探测销方丁公司 "
    "价税合计 ￥86.00 税率：13% 备注：设备维修费"
)
BROKEN_PDF = b"%PDF-1.4 this is not a real pdf at all"


def _pdf_bytes(text: str) -> bytes:
    """reportlab 生成中文文本 PDF（STSong-Light CID 字体，pdfplumber 可提取）。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:
        pass
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    c.setFont("STSong-Light", 11)
    y = 800
    for i in range(0, len(text), 36):
        c.drawString(50, y, text[i : i + 36])
        y -= 18
    c.save()
    return buf.getvalue()


def build_fixtures() -> dict:
    """生成全部探测用文件（PDF/ZIP/xlsx），返回 name -> Path。"""
    import pandas as pd

    FIXTURES.mkdir(parents=True, exist_ok=True)
    fx: dict[str, Path] = {}

    def put(name: str, data: bytes) -> Path:
        p = FIXTURES / name
        p.write_bytes(data)
        fx[name] = p
        return p

    put("ok.pdf", _pdf_bytes(OK_TEXT))
    put("ok2.pdf", _pdf_bytes(OK2_TEXT))
    put("ok3.pdf", _pdf_bytes(OK3_TEXT))
    put("ok4.pdf", _pdf_bytes(OK4_TEXT))
    put("ok5.pdf", _pdf_bytes(OK5_TEXT))
    put("red.pdf", _pdf_bytes(RED_TEXT))
    put("broken.pdf", BROKEN_PDF)
    put("empty.pdf", b"")
    put("notes.txt", b"hello, not a pdf")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("发票A.pdf", _pdf_bytes(OK5_TEXT))
        zf.writestr("子目录/broken2.pdf", BROKEN_PDF)
        zf.writestr("说明.txt", b"not a pdf")
    put("pack.zip", buf.getvalue())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.pdf", BROKEN_PDF)
    put("evil.zip", buf.getvalue())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("说明.txt", b"only text")
    put("empty.zip", buf.getvalue())

    pd.DataFrame(
        {"月份": ["2026-09", "2026-09", "2026-10"], "分类": ["办公费", "办公费", "差旅费"], "金额": [100.0, 200.0, 150.0]}
    ).to_excel(FIXTURES / "data.xlsx", index=False)
    fx["data.xlsx"] = FIXTURES / "data.xlsx"

    pd.DataFrame({"id": range(1200), "val": [f"v{i}" for i in range(1200)]}).to_excel(
        FIXTURES / "big.xlsx", index=False
    )
    fx["big.xlsx"] = FIXTURES / "big.xlsx"

    pd.DataFrame({"a.b": ["点值1", "点值2"], "普通列": ["x", "y"], "金额": [1, 2]}).to_excel(
        FIXTURES / "dotted.xlsx", index=False
    )
    fx["dotted.xlsx"] = FIXTURES / "dotted.xlsx"

    put("bad_zipmagic.xlsx", b"PK\x03\x04broken zip magic but not a real xlsx")
    put("bad_plain.xlsx", b"not an excel file at all")

    (FIXTURES / "gbk.csv").write_bytes(
        pd.DataFrame({"月份": ["2026-09", "2026-10"], "分类": ["办公费", "差旅费"], "金额": [10.0, 20.0]})
        .to_csv(index=False, encoding="gbk")
        .encode("gbk")
    )
    fx["gbk.csv"] = FIXTURES / "gbk.csv"
    return fx


# ---------------- 服务器生命周期 ----------------

def _kill_port(port: int) -> None:
    """清理占用端口的陈旧进程（Windows netstat/taskkill）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            pid = line.strip().split()[-1]
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)


def start_server():
    RESULTS.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    if FIN_DIR.exists():
        import shutil

        shutil.rmtree(FIN_DIR)
    if DEBUG_DIR.exists():
        import shutil

        shutil.rmtree(DEBUG_DIR)
    _kill_port(PORT)
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
    env["FINANCE_DATA_DIR"] = str(FIN_DIR)
    env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    log = open(LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(BACKEND), env=env, stdout=log, stderr=log,
    )
    t0 = time.time()
    while time.time() - t0 < 60:
        if proc.poll() is not None:
            raise RuntimeError(f"探测服务器启动即退出（见 {LOG}）")
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return proc
        except Exception:
            time.sleep(1)
    proc.terminate()
    raise RuntimeError("探测服务器 60s 内未就绪")


# ---------------- 记录器 ----------------

results: list[tuple[str, str, bool, str]] = []
current = {"tid": "", "name": ""}


def record(tid, name, fn):
    current["tid"], current["name"] = tid, name
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}", flush=True)
    except Exception as e:
        detail = str(e)[:400]
        results.append((tid, name, False, detail))
        print(f"[FAIL] {tid} {name}: {detail}", flush=True)
        try:
            page.screenshot(path=str(SHOTS / f"{tid}.png"))
        except Exception:
            pass


def wait_toast(page, text, kind="error", timeout=10):
    """轮询等待 ElMessage（3s 自动消失，必须主动轮询）。"""
    loc = page.locator(f".el-message--{kind}", has_text=text)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def wait_row(page, text, timeout=20):
    loc = page.locator("tr", has_text=text)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if loc.count() > 0:
            return loc.first
        time.sleep(0.2)
    raise AssertionError(f"列表未出现包含 {text!r} 的行")


def wait_visible(page, selector, has_text=None, timeout=15):
    loc = page.locator(selector, has_text=has_text) if has_text else page.locator(selector)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if loc.count() > 0 and loc.first.is_visible():
            return loc.first
        time.sleep(0.15)
    raise AssertionError(f"元素未出现：{selector} {has_text or ''}")


def desc_value(page, label):
    """el-descriptions 中指定标签的取值。"""
    lab = page.locator(".el-descriptions__label", has_text=label).first
    if lab.count() == 0:
        raise AssertionError(f"descriptions 缺少标签：{label}")
    return lab.locator("xpath=following-sibling::*[1]").inner_text().strip()


def goto(page, route, fresh=True):
    url = f"{BASE}#{route}"
    page.goto(url, wait_until="networkidle", timeout=30000)
    if fresh:
        page.reload(wait_until="networkidle")
    page.wait_for_timeout(400)


FX: dict = {}
page = None
page_errors: list[str] = []
console_errors: list[str] = []
downloads: list = []


def _install_handlers(p):
    p.on("pageerror", lambda e: page_errors.append(str(e)))
    p.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)


# ---------------- 探测主体 ----------------

def run_probes():
    import requests

    global page

    # ---------- 发票：上传与状态流转 ----------

    def tc_001_upload_single_pdf():
        goto(page, "/finance/invoice")
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-单票")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["ok.pdf"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        row = wait_row(page, "探测-单票")
        assert "已解析" in row.inner_text(), f"批次状态异常：{row.inner_text()[:80]}"
        cells = row.locator("td")
        assert cells.nth(3).inner_text().strip() == "1", "文件数应为 1"
        assert cells.nth(4).inner_text().strip() == "1", "已解析应为 1"
        assert cells.nth(5).inner_text().strip() == "0", "失败应为 0"
    record("TC-FIV-001", "上传单张真实 PDF 建批次（uploaded→parsed）", tc_001_upload_single_pdf)

    def tc_002_detail_fields_and_patch():
        goto(page, "/finance/invoice")
        wait_row(page, "探测-单票").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        assert desc_value(page, "状态") == "已解析"
        row = page.locator(".el-table__row", has_text="26937000000123").first
        assert row.count() > 0, "明细缺少发票号 26937000000123"
        assert "113.00" in row.inner_text(), "金额列未渲染 113.00"
        proj = row.locator("input").first
        proj.fill("探测项目甲")
        proj.press("Tab")
        page.wait_for_timeout(1200)
        goto(page, f"/finance/invoice/batches/{_batch_id(page)}")
        row2 = page.locator(".el-table__row", has_text="26937000000123").first
        assert row2.locator("input").first.input_value() == "探测项目甲", "项目归类未持久化"
        assert "已修正" in row2.inner_text(), "corrected 标签未显示"
    record("TC-FIV-002", "明细字段渲染与人工修正（corrected）", tc_002_detail_fields_and_patch)

    def tc_003_export_and_download():
        goto(page, "/finance/invoice")
        wait_row(page, "探测-单票").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        page.locator("button", has_text="导出（重命名").click()
        assert wait_toast(page, "导出完成", kind="success"), "导出成功提示未出现"
        assert desc_value(page, "状态") == "已导出", "导出后状态未变为已导出"
        dl_btn = page.locator("button", has_text="下载 ZIP")
        assert dl_btn.is_visible(), "导出后下载按钮未出现"
        with page.expect_download(timeout=15000) as dl_info:
            dl_btn.click()
        d = dl_info.value
        path = d.path()
        content = Path(path).read_bytes()
        assert content[:2] == b"PK", "下载内容不是 ZIP"
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
        assert any(n.endswith("发票清单.xlsx") for n in names), f"ZIP 缺少发票清单：{names[:5]}"
        assert any(n.endswith(".pdf") for n in names), "ZIP 缺少重命名 PDF"
    record("TC-FIV-003", "批次导出与 ZIP 下载", tc_003_export_and_download)

    def tc_004_re_export():
        goto(page, "/finance/invoice")
        wait_row(page, "探测-单票").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        page.locator("button", has_text="导出（重命名").click()
        assert wait_toast(page, "导出完成", kind="success"), "重复导出未成功"
        assert desc_value(page, "状态") == "已导出"
    record("TC-FIV-004", "重复导出（覆盖旧包）", tc_004_re_export)

    def tc_005_keyword_filter():
        goto(page, "/finance/invoice")
        wait_row(page, "探测-单票").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        kw = page.locator("input[placeholder*='发票号']")
        kw.fill("26937000000123")
        page.wait_for_timeout(600)
        assert page.locator(".el-table__row").count() == 1, "命中过滤未生效"
        kw.fill("绝不存在的关键词zzz")
        page.wait_for_timeout(600)
        assert page.locator(".el-table__row").count() == 0, "未命中过滤未生效"
    record("TC-FIV-005", "明细关键字过滤（命中/未命中）", tc_005_keyword_filter)

    def tc_006_broken_pdf_failed_item():
        goto(page, "/finance/invoice")
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-损坏PDF")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["broken.pdf"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        row = wait_row(page, "探测-损坏PDF")
        assert "已解析" in row.inner_text(), "含失败明细的批次应仍为已解析"
        assert row.locator("td").nth(5).inner_text().strip() == "1", "失败计数应为 1"
        row.locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        assert desc_value(page, "失败") == "1", "详情失败计数应为 1"
        assert desc_value(page, "已解析") == "0", "详情已解析应为 0"
        rows = page.locator(".el-table__row")
        assert rows.count() == 1, "应只有 1 条明细"
        assert "失败" in rows.first.inner_text(), "failed 明细未展示失败标签"
    record("TC-FIV-006", "损坏 PDF → failed 明细展示", tc_006_broken_pdf_failed_item)

    def tc_007_zip_upload():
        goto(page, "/finance/invoice")
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-ZIP批次")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["pack.zip"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        wait_row(page, "探测-ZIP批次")
        goto(page, "/finance/invoice")
        wait_row(page, "探测-ZIP批次").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        head = page.locator(".el-card__header").first.inner_text()
        assert "2 个文件" in head, f"ZIP 应拍平出 2 个 PDF：{head}"
        assert desc_value(page, "已解析") == "1", "ZIP 内有效 PDF 应解析 1"
        assert desc_value(page, "失败") == "1", "ZIP 内损坏 PDF 应失败 1"
    record("TC-FIV-007", "ZIP 上传（有效+损坏+非PDF成员拍平）", tc_007_zip_upload)

    def tc_008_zip_traversal():
        goto(page, "/finance/invoice")
        before = page.locator(".el-table__body tr").count()
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-路径穿越")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["evil.zip"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        assert wait_toast(page, "非法路径"), "路径穿越 ZIP 未给出 400 提示"
        page.locator(".el-dialog:visible button", has_text="取消").click()
        goto(page, "/finance/invoice")  # 失败上传不触发列表重载，需刷新后才能看到 failed 批次
        row = wait_row(page, "探测-路径穿越")
        assert "失败" in row.inner_text(), "路径穿越批次应置为 failed 态"
        row.locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        assert "非法路径" in desc_value(page, "错误"), "failed 批次错误未留痕"
    record("TC-FIV-008", "路径穿越 ZIP → 400 + failed 批次展示", tc_008_zip_traversal)

    def tc_009_zip_no_pdf():
        goto(page, "/finance/invoice")
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-空ZIP")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["empty.zip"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        assert wait_toast(page, "未找到 PDF"), "空 ZIP 未给出 400 提示"
        page.locator(".el-dialog:visible button", has_text="取消").click()
        goto(page, "/finance/invoice")
        assert "失败" in wait_row(page, "探测-空ZIP").inner_text(), "空 ZIP 批次应置 failed"
    record("TC-FIV-009", "空 ZIP（无 PDF）→ 400 + failed 批次", tc_009_zip_no_pdf)

    def tc_010_txt_rejected():
        goto(page, "/finance/invoice")
        before = page.locator(".el-table__body tr").count()
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["notes.txt"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        assert wait_toast(page, "不支持的文件类型"), "txt 上传未给出 400 提示"
        page.locator(".el-dialog:visible button", has_text="取消").click()
        page.wait_for_timeout(600)
        assert page.locator(".el-table__body tr").count() == before, "txt 上传不应建批次"
    record("TC-FIV-010", ".txt 上传 → 400 且不建批次", tc_010_txt_rejected)

    def tc_011_mixed_zip_rejected():
        goto(page, "/finance/invoice")
        before = page.locator(".el-table__body tr").count()
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(
            [str(FX["ok2.pdf"]), str(FX["pack.zip"])]
        )
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        assert wait_toast(page, "ZIP 上传时仅允许单个 ZIP"), "PDF+ZIP 混传未给出 400 提示"
        page.locator(".el-dialog:visible button", has_text="取消").click()
        page.wait_for_timeout(600)
        assert page.locator(".el-table__body tr").count() == before, "混传不应建批次"
    record("TC-FIV-011", "PDF+ZIP 混传 → 400", tc_011_mixed_zip_rejected)

    def tc_012_empty_file_rejected():
        goto(page, "/finance/invoice")
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["empty.pdf"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        assert wait_toast(page, "空文件"), "空文件上传未给出 400 提示"
        page.locator(".el-dialog:visible button", has_text="取消").click()
    record("TC-FIV-012", "空文件上传 → 400", tc_012_empty_file_rejected)

    def tc_013_multi_pdf():
        goto(page, "/finance/invoice")
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-多选批次")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(
            [str(FX["ok2.pdf"]), str(FX["ok3.pdf"])]
        )
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        wait_row(page, "探测-多选批次")
        goto(page, "/finance/invoice")
        wait_row(page, "探测-多选批次").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        assert "2 个文件" in page.locator(".el-card__header").first.inner_text()
        assert desc_value(page, "已解析") == "2", "两张新 PDF 均应解析成功"
    record("TC-FIV-013", "多选两张 PDF → 一批两明细", tc_013_multi_pdf)

    def tc_014_reselect_same_file():
        goto(page, "/finance/invoice")
        # 第一次：选 ok.pdf 并上传成功（对话框关闭，form.files 被重置）
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-复选A")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["ok.pdf"]))
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        wait_row(page, "探测-复选A")
        # 第二次：重开对话框，再次选择同一文件——input.value 已被 takeFiles 清空，
        # change 必须再次触发，否则 form.files 为空、上传按钮保持禁用
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-复选B")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["ok.pdf"]))
        page.wait_for_timeout(400)
        btn = page.locator(".el-dialog:visible button", has_text="上传并解析")
        assert btn.is_enabled(), "重选同一文件后上传按钮应可用（takeFiles 回归：change 未再次触发）"
        btn.click()
        wait_row(page, "探测-复选B")
    record("TC-FIV-014", "file input 重选同一文件可再次触发（takeFiles 回归）", tc_014_reselect_same_file)

    def tc_015_cross_batch_duplicate():
        goto(page, "/finance/invoice")
        wait_row(page, "探测-复选B").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        assert desc_value(page, "重复") == "1", "跨批次重复上传应标记 1 条 duplicate"
        assert desc_value(page, "已解析") == "0", "重复明细不应计入已解析"
    record("TC-FIV-015", "跨批次重复上传 → duplicate 计数", tc_015_cross_batch_duplicate)

    def tc_016_red_pairing_and_tag():
        goto(page, "/finance/invoice")
        page.locator("button", has_text="上传发票").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("探测-冲红配对")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(
            [str(FX["ok4.pdf"]), str(FX["red.pdf"])]
        )
        page.locator(".el-dialog:visible button", has_text="上传并解析").click()
        wait_row(page, "探测-冲红配对")
        goto(page, "/finance/invoice")
        wait_row(page, "探测-冲红配对").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        assert desc_value(page, "红冲已配对") == "1", "同购方等额正负票应配对成功"
        assert desc_value(page, "红冲未配对") == "0", "配对应无剩余"
        red_row = page.locator(".el-table__row", has_text="26937000000124").first
        assert red_row.count() > 0, "缺少冲红明细行"
        assert "113.00" in red_row.inner_text(), "冲红金额列未渲染"
        assert red_row.locator(".el-tag", has_text="红").count() > 0, "冲红明细缺少「红」标签"
    record("TC-FIV-016", "冲红配对 + 「红」标签（上轮修复点）", tc_016_red_pairing_and_tag)

    def tc_017_detail_404():
        errors_before = len(page_errors)
        goto(page, "/finance/invoice/batches/99999")
        assert wait_toast(page, "批次不存在"), "不存在批次未给出 404 提示"
        assert len(page_errors) == errors_before, f"出现未捕获异常：{page_errors[-3:]}"
        assert page.locator(".el-result", has_text="批次不存在或加载失败").count() > 0, \
            "404 未切到错误态（页面结构断言已随失败态实现更新）"
    record("TC-FIV-017", "不存在批次详情 → 404 优雅提示", tc_017_detail_404)

    def tc_018_export_all_failed():
        goto(page, "/finance/invoice")
        wait_row(page, "探测-损坏PDF").locator("button", has_text="详情").click()
        wait_visible(page, ".el-descriptions")
        page.locator("button", has_text="导出（重命名").click()
        assert wait_toast(page, "无可导出"), "全失败批次导出未给出 400 提示"
        assert desc_value(page, "状态") == "已解析", "导出失败不应改变批次状态"
    record("TC-FIV-018", "全失败批次导出 → 400 优雅提示", tc_018_export_all_failed)

    def tc_019_too_many_files():
        files = [("files", (f"f{i}.pdf", b"%PDF-x", "application/pdf")) for i in range(201)]
        resp = requests.post(f"{BASE}/api/finance/invoice/batches", files=files, timeout=60)
        assert resp.status_code == 400, f"201 文件应 400，实际 {resp.status_code}"
        assert "200" in resp.json()["detail"], resp.json()["detail"]
    record("TC-FIV-019", "单批次 201 文件 → 400（API 直连）", tc_019_too_many_files)

    # ---------- 档案库 ----------

    def tc_101_ingest_xlsx():
        goto(page, "/finance/vault")
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_probe")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["data.xlsx"]))
        page.locator(".el-dialog:visible button", has_text="入仓").click()
        assert wait_toast(page, "入仓成功", kind="success"), "入仓成功提示未出现"
        row = wait_row(page, "t_probe")
        cells = row.locator("td")
        assert cells.nth(2).inner_text().strip() == "3", "行数应为 3"
        assert cells.nth(3).inner_text().strip() == "3", "列数应为 3"
    record("TC-FIV-101", "上传 xlsx 入仓（表清单/行数/列数）", tc_101_ingest_xlsx)

    def tc_102_reselect_same_file_append():
        goto(page, "/finance/vault")
        # tc_101 的入仓已完成且对话框关闭：重开对话框重选同一文件，
        # input.value 已被 takeFile 清空，change 必须再次触发（回归点）
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_probe")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["data.xlsx"]))
        page.wait_for_timeout(400)
        btn = page.locator(".el-dialog:visible button", has_text="入仓")
        assert btn.is_enabled(), "重选同一文件后入仓按钮应可用（takeFile 回归：change 未再次触发）"
        page.locator(".el-dialog:visible .el-radio", has_text="追加").click()
        btn.click()
        assert wait_toast(page, "入仓成功", kind="success"), "追加入仓提示未出现"
        row = wait_row(page, "t_probe")
        assert row.locator("td").nth(2).inner_text().strip() == "6", "追加后行数应为 6"
    record("TC-FIV-102", "重选同一文件 + append 行数翻倍（takeFile 回归）", tc_102_reselect_same_file_append)

    def tc_103_preview_dialog():
        goto(page, "/finance/vault")
        wait_row(page, "t_probe").locator("button", has_text="预览").click()
        dlg = wait_visible(page, ".el-dialog:visible")
        page.wait_for_timeout(500)
        assert dlg.locator("td", has_text="办公费").count() > 0, "预览弹窗未渲染分类单元格（数组行+列名 prop 缺陷）"
        assert dlg.locator("td", has_text="2026-09").count() > 0, "预览弹窗未渲染月份单元格"
        assert dlg.locator("td", has_text="150.0").count() > 0 or dlg.locator("td", has_text="150").count() > 0, "预览弹窗未渲染金额单元格"
    record("TC-FIV-103", "预览弹窗数据渲染（数组行映射修复点）", tc_103_preview_dialog)

    def tc_104_use_in_query():
        goto(page, "/finance/vault")
        wait_row(page, "t_probe").locator("button", has_text="查询").click()
        wait_visible(page, ".el-textarea__inner")
        page.wait_for_timeout(500)
        assert "table=t_probe" in page.url, f"URL 未带表名参数：{page.url}"
        val = page.locator(".el-textarea__inner").input_value()
        assert val == "SELECT * FROM t_probe LIMIT 100", f"SQL 模板预填异常：{val!r}"
    record("TC-FIV-104", "「查询」按钮跳转预填 SQL 模板", tc_104_use_in_query)

    def _run_sql(sql, expect_error=None, expect_ok=False):
        card = page.locator(".el-card", has_text="结果：")
        before = card.first.inner_text() if card.count() else ""
        page.locator(".el-textarea__inner").fill(sql)
        page.locator("button", has_text="执行").click()
        if expect_error:
            assert wait_toast(page, expect_error), f"未出现预期错误提示：{expect_error}"
        if expect_ok:
            deadline = time.time() + 15
            while time.time() < deadline:
                if card.count() and card.first.inner_text() != before:
                    return
                time.sleep(0.15)
            raise AssertionError(f"结果卡未刷新（before={before[:40]!r}）")

    def tc_105_valid_select():
        goto(page, "/finance/vault/query")
        _run_sql("SELECT * FROM t_probe", expect_ok=True)
        card = page.locator(".el-card", has_text="结果：").last
        assert "6 行" in card.inner_text(), f"结果行数异常：{card.inner_text()[:60]}"
        assert page.locator("td", has_text="差旅费").count() > 0, "结果表未渲染数据单元格"
    record("TC-FIV-105", "合法 SELECT 执行并渲染结果", tc_105_valid_select)

    def tc_106_drop_rejected():
        _run_sql("DROP TABLE t_probe", expect_error="只允许 SELECT / WITH")
    record("TC-FIV-106", "DROP 语句拒绝（首关键词校验）", tc_106_drop_rejected)

    def tc_107_delete_rejected():
        _run_sql("DELETE FROM t_probe", expect_error="只允许 SELECT")
    record("TC-FIV-107", "DELETE 语句拒绝", tc_107_delete_rejected)

    def tc_108_unregistered_rejected():
        _run_sql("SELECT * FROM not_registered_table", expect_error="未注册的数据集")
    record("TC-FIV-108", "未注册数据集引用拒绝", tc_108_unregistered_rejected)

    def tc_109_multi_statement_rejected():
        _run_sql("SELECT * FROM t_probe; DROP TABLE t_probe", expect_error="单条查询语句")
    record("TC-FIV-109", "分号多语句拒绝", tc_109_multi_statement_rejected)

    def tc_110_file_path_rejected():
        _run_sql("SELECT * FROM '/etc/passwd'", expect_error="文件路径")
        _run_sql("SELECT * FROM read_csv('/etc/passwd')", expect_error="未注册的数据集")
    record("TC-FIV-110", "文件路径表拒绝（直接路径 + read_csv 函数）", tc_110_file_path_rejected)

    def tc_111_forbidden_keyword_rejected():
        _run_sql("WITH c AS (SELECT * FROM t_probe) DELETE FROM c", expect_error="DELETE")
    record("TC-FIV-111", "WITH+DELETE 禁词拒绝", tc_111_forbidden_keyword_rejected)

    def tc_112_with_comment_cte_ok():
        _run_sql("-- 注释\nWITH c AS (SELECT * FROM t_probe) SELECT count(*) AS n FROM c LIMIT 999999", expect_ok=True)
        card = page.locator(".el-card", has_text="结果：").last
        assert "1 行" in card.inner_text(), "CTE 聚合应返回 1 行"
        assert page.locator("td", has_text="6").count() > 0, "CTE 计数结果应为 6（3 行 replace + 3 行 append）"
    record("TC-FIV-112", "WITH+注释+CTE+超限 LIMIT 改写放行", tc_112_with_comment_cte_ok)

    def tc_113_truncate_no_limit():
        goto(page, "/finance/vault")
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_big")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["big.xlsx"]))
        page.locator(".el-dialog:visible button", has_text="入仓").click()
        assert wait_toast(page, "入仓成功", kind="success")
        wait_row(page, "t_big").locator("button", has_text="查询").click()
        wait_visible(page, ".el-textarea__inner")
        page.wait_for_timeout(400)
        _run_sql("SELECT * FROM t_big", expect_ok=True)
        card = page.locator(".el-card", has_text="结果：").last
        assert "已截断" in card.inner_text(), "超过 1000 行应标记已截断"
        assert "1000 行" in card.inner_text(), f"截断行数异常：{card.inner_text()[:60]}"
    record("TC-FIV-113", "无 LIMIT 查询强制截断至 1000 行", tc_113_truncate_no_limit)

    def tc_114_limit_rewrite():
        # 上一条用例的卡片已是「1000 行（已截断）」，同表同结果会让 _run_sql 的
        # 「卡片文本必须变化」刷新判定失效（假失败）；先重回查询页清空卡片再执行。
        goto(page, "/finance/vault/query")
        _run_sql("SELECT * FROM t_big LIMIT 999999", expect_ok=True)
        card = page.locator(".el-card", has_text="结果：").last
        assert "已截断" in card.inner_text(), "LIMIT 999999 应被改写并截断"
        assert "1000 行" in card.inner_text(), f"截断行数异常：{card.inner_text()[:60]}"
    record("TC-FIV-114", "LIMIT 超上限改写截断", tc_114_limit_rewrite)

    def tc_115_empty_sql_noop():
        goto(page, "/finance/vault/query")
        page.locator(".el-textarea__inner").fill("")
        page.locator("button", has_text="执行").click()
        page.wait_for_timeout(1200)
        assert page.locator(".el-card", has_text="结果：").count() == 0, "空 SQL 不应产生结果卡"
        assert page.locator(".el-message").count() == 0, "空 SQL 不应弹提示"
    record("TC-FIV-115", "空 SQL 点击执行无动作", tc_115_empty_sql_noop)

    def tc_116_export_csv():
        goto(page, "/finance/vault/query")
        _run_sql("SELECT * FROM t_probe", expect_ok=True)
        with page.expect_download(timeout=15000) as dl_info:
            page.locator("button", has_text="导出 CSV").click()
        text = Path(dl_info.value.path()).read_text(encoding="utf-8")
        assert text.startswith("\ufeff"), "CSV 缺少 BOM（Excel 中文兼容）"
        assert "月份" in text.splitlines()[0], f"CSV 表头异常：{text.splitlines()[0][:60]}"
        assert "办公费" in text, "CSV 内容缺少数据行"
    record("TC-FIV-116", "导出 CSV 下载（BOM+表头+数据）", tc_116_export_csv)

    def tc_117_url_bad_table_param():
        goto(page, "/finance/vault/query?table=bad-name!")
        assert wait_toast(page, "不合法", kind="warning"), "非法表名参数未给出警告"
        val = page.locator(".el-textarea__inner").input_value()
        assert val == "SELECT * FROM ", f"应回退默认模板：{val!r}"
    record("TC-FIV-117", "URL 非法表名参数 → 警告 + 默认模板", tc_117_url_bad_table_param)

    def tc_118_corrupt_xlsx_400():
        goto(page, "/finance/vault")
        inbox = FIN_DIR / "finance_vault" / "inbox"
        # inbox 对成功上传留档是设计行为，只能断言「失败上传不新增留档」（前后快照对比）
        before = {p.name for p in inbox.iterdir()} if inbox.exists() else set()
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_bad_zipmagic")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["bad_zipmagic.xlsx"]))
        page.locator(".el-dialog:visible button", has_text="入仓").click()
        assert wait_toast(page, "文件解析失败"), "损坏 xlsx 未给出 400 级提示（疑 500）"
        page.wait_for_timeout(600)
        assert page.locator("tr", has_text="t_bad_zipmagic").count() == 0, "损坏文件不应入仓"
        after = {p.name for p in inbox.iterdir()} if inbox.exists() else set()
        new_files = sorted(after - before)
        assert not new_files, f"失败上传不应在 inbox 新增留档：{new_files}"
    record("TC-FIV-118", "损坏 xlsx（ZIP 魔数残缺）→ 400 而非 500", tc_118_corrupt_xlsx_400)

    def tc_119_append_column_mismatch_400():
        goto(page, "/finance/vault")
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_app2")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["data.xlsx"]))
        page.locator(".el-dialog:visible button", has_text="入仓").click()
        assert wait_toast(page, "入仓成功", kind="success")
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_app2")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["dotted.xlsx"]))
        page.locator(".el-dialog:visible .el-radio", has_text="追加").click()
        page.locator(".el-dialog:visible button", has_text="入仓").click()
        assert wait_toast(page, "入仓失败"), "append 列不匹配未给出 400 级提示（疑 500）"
        page.wait_for_timeout(600)
        row = page.locator("tr", has_text="t_app2").first
        assert row.locator("td").nth(2).inner_text().strip() == "3", "失败的 append 不应改变行数"
    record("TC-FIV-119", "append 列不匹配 → 400 而非 500", tc_119_append_column_mismatch_400)

    def tc_120_dotted_columns_preview():
        goto(page, "/finance/vault")
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_dot")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["dotted.xlsx"]))
        page.locator(".el-dialog:visible button", has_text="入仓").click()
        assert wait_toast(page, "入仓成功", kind="success")
        wait_row(page, "t_dot").locator("button", has_text="预览").click()
        dlg = wait_visible(page, ".el-dialog:visible")
        page.wait_for_timeout(500)
        assert dlg.locator("td", has_text="点值1").count() > 0, "点号列名数据未渲染（列名被当作深层路径）"
        assert dlg.locator("td", has_text="点值2").count() > 0, "点号列名第二行未渲染"
    record("TC-FIV-120", "点号列名数据渲染", tc_120_dotted_columns_preview)

    def tc_121_uppercase_table_query():
        goto(page, "/finance/vault/query")
        _run_sql("SELECT * FROM T_PROBE", expect_ok=True)
        card = page.locator(".el-card", has_text="结果：").last
        assert "6 行" in card.inner_text(), "DuckDB 标识符大小写不敏感，应放行并返回 6 行"
    record("TC-FIV-121", "大写表名查询（大小写不敏感放行）", tc_121_uppercase_table_query)

    def tc_122_string_false_positive():
        _run_sql("SELECT * FROM t_probe WHERE 分类 LIKE '%drop%'", expect_ok=True)
        card = page.locator(".el-card", has_text="结果：").last
        assert "0 行" in card.inner_text(), "字符串内的禁词不应误报，应返回 0 行"
    record("TC-FIV-122", "字符串内容含禁词不误报（SELECT 放行）", tc_122_string_false_positive)

    def tc_123_gbk_csv_ingest():
        goto(page, "/finance/vault")
        page.locator("button", has_text="上传入仓").first.click()
        wait_visible(page, ".el-dialog:visible")
        page.locator(".el-dialog:visible .el-input input").first.fill("t_gbk")
        page.locator(".el-dialog:visible input[type=file]").set_input_files(str(FX["gbk.csv"]))
        page.locator(".el-dialog:visible button", has_text="入仓").click()
        assert wait_toast(page, "入仓成功", kind="success"), "GBK CSV 入仓提示未出现"
        row = wait_row(page, "t_gbk")
        assert row.locator("td").nth(2).inner_text().strip() == "2", "GBK CSV 应解析出 2 行"
    record("TC-FIV-123", "GBK 编码 CSV 入仓（编码回退）", tc_123_gbk_csv_ingest)


def _batch_id(p):
    """从详情页 URL 提取批次 id。"""
    return p.url.rstrip("/").split("/")[-1]


def snapshot_pages(context):
    """逐页快照（控制台/页面错误/失败请求/截图）。数据已就绪。"""
    import requests

    routes = [("/finance/invoice", "fin-invoice-list"), ("/finance/vault", "fin-vault-tables"),
              ("/finance/vault/query", "fin-vault-query")]
    batches = requests.get(f"{BASE}/api/finance/invoice/batches", timeout=10).json()
    if batches:
        routes.append((f"/finance/invoice/batches/{batches[0]['id']}", "fin-invoice-detail"))
    pages = []
    for route, name in routes:
        p = context.new_page()
        errs, perrs, freqs = [], [], []
        p.on("console", lambda m, e=errs: e.append(m.text) if m.type == "error" else None)
        p.on("pageerror", lambda e, l=perrs: l.append(str(e)))
        p.on("requestfailed", lambda r, l=freqs: l.append(f"{r.method} {r.url}"))
        resp = p.goto(f"{BASE}#{route}", wait_until="networkidle", timeout=30000)
        p.wait_for_timeout(900)
        p.screenshot(path=str(SHOTS / f"{name}.png"))
        pages.append({
            "route": route, "name": name, "status": resp.status if resp else "?",
            "console_errors": errs, "page_errors": perrs,
            "failed_reqs": [f for f in freqs if "favicon" not in f],
        })
        p.close()
    return pages


def main():
    global FX, page
    FX = build_fixtures()
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = start_server()
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900},
                                          accept_downloads=True)
            context.on("download", lambda d: downloads.append(d))
            page = context.new_page()
            _install_handlers(page)
            run_probes()
            for extra in context.pages[1:]:
                try:
                    extra.close()
                except Exception:
                    pass
            pages = snapshot_pages(context)
            browser.close()

        passed = sum(1 for r in results if r[2])
        out = {
            "area": AREA, "base": BASE,
            "started_at": STARTED_AT,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pages": pages,
            "tests": [{"id": t[0], "name": t[1], "passed": t[2], "detail": t[3]} for t in results],
            "bugs": [],
        }
        (RESULTS / f"{AREA}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("=" * 70)
        for r in results:
            if not r[2]:
                print(f"  FAIL {r[0]} {r[1]}: {r[3][:120]}")
        print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
        print(f"console_errors={len(console_errors)} page_errors={len(page_errors)}")
        for e in sorted(set(console_errors))[:8]:
            print(f"  !! console: {e[:160]}")
        for e in sorted(set(page_errors))[:8]:
            print(f"  !! pageerror: {e[:160]}")
        print(f"json -> {RESULTS / f'{AREA}.json'}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()


if __name__ == "__main__":
    main()
