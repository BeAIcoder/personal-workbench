# -*- coding: utf-8 -*-
"""finance-ledger 区域深度 E2E 探测（BIP 企业记账 + 个人记账）。

覆盖：
- 6 个路由逐页访问（控制台/页面错误/失败请求 + 截图）；
- BIP：导入向导全流程 / 重选同一文件 / 未知类型人工指定+手动映射 /
  凭证列表筛选后页码复位 / 清除筛选后页码复位 / 组合筛选 / 分录抽屉 /
  按期间入仓 / 余额汇总默认月份=本月 / 非法期间前端校验 / 去导入导航；
- 个人：快速记账默认日期=本地今天 / 「本月流水」标题 / 类型切换分类联动 /
  类型切换不残留旧分类 / 快速记账提交（支出+收入）/ 空金额校验 /
  账户管理（增删+引用保护）/ 分类管理（增删）/ 流水页月份下拉含当前月 /
  按月筛选 / 关键词筛选（含空态）/ 统计页渲染与占比口径 / 导出 CSV。

可重复运行：独立测试库 + 独立财务数据目录 + 自有端口 8213。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
E2E_DIR = ROOT / "dev" / "e2e"
PROBE_DIR = E2E_DIR / "probe_results" / "finance-ledger"
SHOTS = PROBE_DIR / "shots"
DB = PROBE_DIR / "finance-ledger.db"
FIN_DIR = PROBE_DIR / "finance-ledger-fin"
PORT = 8213
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

for d in (PROBE_DIR, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
if DB.exists():
    DB.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["PYTHONIOENCODING"] = "utf-8"

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=open(PROBE_DIR / "finance-ledger.server.log", "w"),
    stderr=subprocess.STDOUT,
)

TODAY = date.today()
TODAY_S = TODAY.strftime("%Y-%m-%d")
CUR_MONTH = TODAY.strftime("%Y-%m")
PREV_MONTH = (TODAY.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
# 导入后预期：凭证 26（本月13/上月13）+ 辅助明细账 2 + 人工映射 1 = 29；本月 16，上月 13
EXP_TOTAL, EXP_CUR, EXP_PREV = "29", "16", "13"

results = []   # (id, name, passed, detail)
pages_report = []
page_errors_all = []


def record(tid, name, fn):
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}", flush=True)
    except Exception as e:
        results.append((tid, name, False, str(e)[:300]))
        print(f"[FAIL] {tid} {name}: {str(e)[:300]}", flush=True)


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"))


# ---------------- fixtures：BIP 导出 Excel ----------------

def _xlsx(columns, rows) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(columns)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_fixtures() -> dict:
    """凭证列表 26 张（本月 13 + 上月 13，借贷平衡）+ 辅助明细账（本月，带科目编码）+ 无法识别类型的文件。"""
    cols = ["日期", "核算账簿", "凭证号", "摘要", "借方金额", "贷方金额"]
    rows = []
    for i in range(1, 27):
        day = f"{CUR_MONTH}-{(i % 28) + 1:02d}" if i <= 13 else f"{PREV_MONTH}-{(i % 28) + 1:02d}"
        rows.append([day, "记账凭证", f"V{i:04d}", f"采购发票{i}", 100.0, 0.0])
        rows.append([day, "记账凭证", f"V{i:04d}", f"采购发票{i}", 0.0, 100.0])
    vouchers = _xlsx(cols, rows)

    aux_cols = ["日期", "核算账簿", "凭证号", "摘要", "科目编码", "科目名称", "借方金额", "贷方金额"]
    aux_rows = [
        [f"{CUR_MONTH}-05", "明细账", "A001", "办公费", "6602", "管理费用", 75.0, 0.0],
        [f"{CUR_MONTH}-05", "明细账", "A001", "办公费", "1002", "银行存款", 0.0, 75.0],
        [f"{CUR_MONTH}-08", "明细账", "A002", "差旅费", "6602", "管理费用", 25.0, 0.0],
        [f"{CUR_MONTH}-08", "明细账", "A002", "差旅费", "1001", "库存现金", 0.0, 25.0],
    ]
    aux = _xlsx(aux_cols, aux_rows)

    # 列名为甲乙丙丁：无法自动识别类型，走人工指定 + 手动映射
    unknown_cols = ["甲", "乙", "丙", "丁"]
    unknown_rows = [
        [f"{CUR_MONTH}-09", "手工账簿", "U001", 88.0],
        [f"{CUR_MONTH}-09", "手工账簿", "U001", 0.0],
    ]
    unknown = _xlsx(unknown_cols, unknown_rows)

    fx = PROBE_DIR / "fixtures"
    fx.mkdir(exist_ok=True)
    (fx / "vouchers.xlsx").write_bytes(vouchers)
    (fx / "aux.xlsx").write_bytes(aux)
    (fx / "unknown.xlsx").write_bytes(unknown)
    return {"vouchers": fx / "vouchers.xlsx", "aux": fx / "aux.xlsx", "unknown": fx / "unknown.xlsx"}


ROUTES = [
    ("/finance/ledger-bip", "bip-import"),
    ("/finance/ledger-bip/vouchers", "bip-vouchers"),
    ("/finance/ledger-bip/summary", "bip-summary"),
    ("/finance/ledger-personal", "pl-quickadd"),
    ("/finance/ledger-personal/transactions", "pl-transactions"),
    ("/finance/ledger-personal/stats", "pl-stats"),
]


def main():
    if not wait_ready():
        print("FATAL: server not ready")
        server.terminate()
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    fx = build_fixtures()
    page = None
    console_errors = []

    def goto(route):
        page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(600)

    def reset_to(route):
        """goto + reload：SPA 同路由导航不重置组件状态，reload 保证每个用例从干净状态开始。"""
        page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
        page.reload(wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(800)

    def visible_toast_text():
        return " ".join(t.inner_text() for t in page.locator(".el-message:visible").all())

    def upload(file_path):
        page.locator("input[type=file]").set_input_files(str(file_path))
        page.wait_for_timeout(1800)

    def pager_active():
        return page.locator(".el-pager li.is-active").inner_text()

    def total_text():
        return page.locator(".el-pagination__total").inner_text()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)

        # ================= 阶段 1：逐页侦察（每页独立 page，错误不串页） =================
        for route, name in ROUTES:
            ce, pe, fr = [], [], []
            rp = ctx.new_page()
            rp.on("console", lambda m, e=ce: e.append(m.text) if m.type == "error" else None)
            rp.on("pageerror", lambda e, l=pe: l.append(str(e)))
            rp.on("requestfailed", lambda r, l=fr: l.append(f"{r.method} {r.url}"))
            resp = rp.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
            rp.wait_for_timeout(800)
            rp.screenshot(path=str(SHOTS / f"{name}.png"))
            pages_report.append({
                "route": route, "name": name,
                "status": resp.status if resp else "?",
                "console_errors": ce, "page_errors": pe,
                "failed_reqs": [f for f in fr if "favicon" not in f],
            })
            rp.close()

        # ================= 阶段 2：交互探测 =================
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors_all.append(str(e)))

        # ---------- BIP 导入向导 ----------

        def tc_bip_import_vouchers():
            reset_to("/finance/ledger-bip")
            upload(fx["vouchers"])
            assert page.locator("text=类型识别与映射").count() > 0, "未进入映射步骤"
            tag = page.locator(".el-descriptions .el-tag").first.inner_text()
            assert "凭证列表" in tag, f"类型识别错误: {tag}"
            # 总行数为数据行数：26 张凭证 × 借贷两行 = 52
            assert "52" in page.locator(".el-descriptions", has_text="总行数").inner_text(), "总行数不是 52"
            page.locator("button", has_text="确认入库").click()
            page.wait_for_timeout(1800)
            body = page.content()
            assert "导入完成" in body, f"导入结果未出现: {body[:200]}"
            assert "新增凭证 26 张" in body, "导入统计不正确"
            batch_rows = page.locator(".el-table__row")
            assert batch_rows.count() >= 1, "批次列表为空"
            assert "vouchers.xlsx" in batch_rows.first.inner_text(), "批次文件名不正确"

        record("TC-FL-BIP-001", "BIP 导入向导全流程（凭证列表 26 张）", tc_bip_import_vouchers)

        def tc_bip_reupload_same_file():
            reset_to("/finance/ledger-bip")
            # 直接在步骤 0 选同一文件两次（第二次需重新触发 change）
            upload(fx["vouchers"])
            assert page.locator("text=类型识别与映射").count() > 0, "首次上传未触发预览"
            # 上一步回到步骤 0（input 重建）后再选同一文件
            page.locator("button", has_text="上一步").click()
            page.wait_for_timeout(500)
            assert page.locator("input[type=file]").count() > 0, "未回到上传步骤"
            upload(fx["vouchers"])
            assert page.locator("text=类型识别与映射").count() > 0, "重选同一文件未触发预览"
            tag = page.locator(".el-descriptions .el-tag").first.inner_text()
            assert "凭证列表" in tag, f"重选后类型识别异常: {tag}"

        record("TC-FL-BIP-002", "导入向导重选同一文件", tc_bip_reupload_same_file)

        def tc_bip_import_aux():
            reset_to("/finance/ledger-bip")
            upload(fx["aux"])
            tag = page.locator(".el-descriptions .el-tag").first.inner_text()
            assert "辅助明细账" in tag, f"辅助明细账识别错误: {tag}"
            page.locator("button", has_text="确认入库").click()
            page.wait_for_timeout(1800)
            assert "导入完成" in page.content(), "辅助明细账导入失败"

        record("TC-FL-BIP-003", "辅助明细账导入（含科目编码）", tc_bip_import_aux)

        def tc_bip_import_unknown_manual():
            reset_to("/finance/ledger-bip")
            upload(fx["unknown"])
            tag = page.locator(".el-descriptions .el-tag").first.inner_text()
            assert "未识别" in tag, f"应识别为未识别类型: {tag}"
            # 人工指定类型后，应出现该类型的列映射下拉（含必填字段与金额列）
            page.locator(".el-radio-button", has_text="凭证列表").click()
            page.wait_for_timeout(500)
            selects = page.locator(".el-select")
            assert selects.count() >= 4, f"人工指定类型后未出现列映射下拉: {selects.count()}"
            # 依次映射：日期→甲、核算账簿→乙、凭证号→丙、借方金额→丁
            for idx, col in enumerate(["甲", "乙", "丙", "丁"]):
                selects.nth(idx).click()
                page.wait_for_timeout(300)
                page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text=col).first.click()
                page.wait_for_timeout(200)
            page.locator("button", has_text="确认入库").click()
            page.wait_for_timeout(1800)
            body = page.content()
            assert "导入完成" in body, f"人工映射导入失败: {body[:300]}"
            assert "新增凭证 1 张" in body, "人工映射导入统计不正确"

        record("TC-FL-BIP-004", "未知类型人工指定+手动映射导入", tc_bip_import_unknown_manual)

        # ---------- BIP 凭证查询 ----------

        def tc_bip_pagination_reset_on_filter():
            reset_to("/finance/ledger-bip/vouchers")
            page.wait_for_timeout(1500)
            assert EXP_TOTAL in total_text(), f"凭证总数应为 {EXP_TOTAL}，实际: {total_text()}"
            # 翻到第 2 页
            page.locator(".el-pager li").nth(1).click()
            page.wait_for_timeout(1200)
            assert pager_active() == "2", f"翻页失败，当前页 {pager_active()}"
            first_row_p2 = page.locator(".el-table__row").first.inner_text()
            # 筛选期间=本月 → 页码必须复位到 1
            page.locator("input[placeholder='期间 YYYY-MM']").fill(CUR_MONTH)
            page.locator("button", has_text="筛选").click()
            page.wait_for_timeout(1500)
            assert pager_active() == "1", f"筛选后页码未复位: {pager_active()}"
            assert EXP_CUR in total_text(), f"本月凭证应为 {EXP_CUR}，实际: {total_text()}"
            assert page.locator(".el-table__row").first.inner_text() != first_row_p2, "筛选后首行未变化"

        record("TC-FL-BIP-005", "凭证列表筛选后页码复位", tc_bip_pagination_reset_on_filter)

        def tc_bip_pagination_reset_on_clear():
            reset_to("/finance/ledger-bip/vouchers")
            page.wait_for_timeout(1500)
            # 关键词筛选后仍有 2 页 → 翻到第 2 页 → 清除筛选 → 必须回第 1 页
            page.locator("input[placeholder='摘要关键词']").fill("采购发票")
            page.locator("button", has_text="筛选").click()
            page.wait_for_timeout(1500)
            assert "26" in total_text(), f"关键词筛选总数应为 26，实际: {total_text()}"
            page.locator(".el-pager li").nth(1).click()
            page.wait_for_timeout(1200)
            assert pager_active() == "2", f"翻页失败，当前页 {pager_active()}"
            page.locator("input[placeholder='摘要关键词']").fill("")
            page.locator("button", has_text="筛选").click()
            page.wait_for_timeout(1500)
            assert pager_active() == "1", f"清除筛选后页码未复位: {pager_active()}"
            assert EXP_TOTAL in total_text(), f"清除筛选后应回到全量 {EXP_TOTAL}，实际: {total_text()}"

        record("TC-FL-BIP-006", "凭证列表清除筛选后页码复位", tc_bip_pagination_reset_on_clear)

        def tc_bip_voucher_filters_combination():
            reset_to("/finance/ledger-bip/vouchers")
            page.wait_for_timeout(1500)
            # 凭证号模糊筛选
            page.locator("input[placeholder='凭证号']").fill("V0003")
            page.locator("button", has_text="筛选").click()
            page.wait_for_timeout(1500)
            assert total_text().startswith("共 1 "), f"凭证号筛选应为 1，实际: {total_text()}"
            assert "平" in page.locator(".el-table__row .el-tag").first.inner_text(), "平衡标记异常"
            # 账簿筛选（模糊）
            page.locator("input[placeholder='凭证号']").fill("")
            page.locator("input[placeholder='核算账簿']").fill("明细账")
            page.locator("button", has_text="筛选").click()
            page.wait_for_timeout(1500)
            assert total_text().startswith("共 2 "), f"账簿筛选应为 2，实际: {total_text()}"

        record("TC-FL-BIP-007", "凭证组合筛选（凭证号/账簿）+ 平衡标记", tc_bip_voucher_filters_combination)

        def tc_bip_voucher_detail_drawer():
            reset_to("/finance/ledger-bip/vouchers")
            page.wait_for_timeout(1500)
            page.locator(".el-table__row button", has_text="分录").first.click()
            page.wait_for_timeout(1500)
            drawer = page.locator(".el-drawer:visible")
            assert drawer.count() > 0, "分录抽屉未打开"
            body = drawer.inner_text()
            assert "借方合计" in body and "贷方合计" in body, "抽屉缺少合计行"
            assert drawer.locator(".el-table__row").count() >= 1, "抽屉分录为空"
            drawer.locator(".el-drawer__close-btn").click()
            page.wait_for_timeout(400)

        record("TC-FL-BIP-008", "凭证分录抽屉", tc_bip_voucher_detail_drawer)

        def tc_bip_export_to_vault():
            reset_to("/finance/ledger-bip/vouchers")
            page.wait_for_timeout(1500)
            btn = page.locator("button", has_text="按期间入仓")
            assert not btn.is_enabled(), "未填期间时入仓按钮应禁用"
            page.locator("input[placeholder='期间 YYYY-MM']").fill(CUR_MONTH)
            page.wait_for_timeout(300)
            assert btn.is_enabled(), "填入期间后入仓按钮应可用"
            btn.click()
            page.wait_for_timeout(3000)
            toast = visible_toast_text()
            assert "已入仓" in toast, f"入仓无成功提示: {toast[:200]}"

        record("TC-FL-BIP-009", "按期间入仓（按钮禁用态+成功提示）", tc_bip_export_to_vault)

        def tc_bip_balance_summary_default_month():
            reset_to("/finance/ledger-bip/summary")
            page.wait_for_timeout(1800)
            period_input = page.locator("input[placeholder='期间 YYYY-MM']")
            val = period_input.input_value()
            assert val == CUR_MONTH, f"默认期间应为本月 {CUR_MONTH}，实际 {val}"
            body = page.content()
            assert "6602" in body, f"本月汇总未自动加载或无数据: {body[:300]}"
            assert "100.00" in body, "汇总金额不正确"

        record("TC-FL-BIP-010", "余额汇总默认月份=本月并自动加载", tc_bip_balance_summary_default_month)

        def tc_bip_balance_summary_invalid_period():
            reset_to("/finance/ledger-bip/summary")
            page.wait_for_timeout(1500)
            page.locator("input[placeholder='期间 YYYY-MM']").fill("2026-13")
            page.locator("button", has_text="汇总").click()
            page.wait_for_timeout(800)
            assert "期间格式应为 YYYY-MM" in visible_toast_text(), "非法期间缺少前端校验提示"

        record("TC-FL-BIP-011", "余额汇总非法期间前端校验", tc_bip_balance_summary_invalid_period)

        def tc_bip_goto_import():
            reset_to("/finance/ledger-bip/vouchers")
            page.wait_for_timeout(1500)
            page.locator("button", has_text="去导入").click()
            page.wait_for_timeout(1200)
            assert page.locator("text=BIP 导出导入向导").count() > 0, "去导入导航失败"

        record("TC-FL-BIP-012", "凭证页去导入导航", tc_bip_goto_import)

        # ---------- 个人记账 ----------

        def tc_pl_quickadd_defaults():
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            date_input = page.locator(".el-date-editor input").first
            val = date_input.input_value()
            assert val.startswith(TODAY_S), f"默认日期应为本地今天 {TODAY_S}，实际 {val}"
            title = page.locator(".el-card__header").last.inner_text()
            assert "本月流水" in title and CUR_MONTH in title, f"本月流水标题异常: {title}"

        record("TC-FL-PL-001", "快速记账默认日期=本地今天 + 本月流水标题", tc_pl_quickadd_defaults)

        def tc_pl_quickadd_submit():
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            # 支出 75 元（餐饮）
            page.locator(".el-input-number input").first.fill("75")
            page.locator(".el-select").first.click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first.click()
            page.locator(".el-select").nth(1).click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="餐饮").first.click()
            page.locator("input[maxlength='100']").fill("探测支出")
            page.locator("button", has_text="记一笔").first.click()
            page.wait_for_timeout(1800)
            assert "已记账" in visible_toast_text(), "记账成功提示缺失"
            assert "探测支出" in page.content(), "本月流水未出现新记录"
            # 收入 1000 元（工资）
            page.locator(".el-radio-button", has_text="收入").click()
            page.wait_for_timeout(400)
            page.locator(".el-input-number input").first.fill("1000")
            page.locator(".el-select").nth(1).click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="工资").first.click()
            page.locator("input[maxlength='100']").fill("探测收入")
            page.locator("button", has_text="记一笔").first.click()
            page.wait_for_timeout(1800)
            body = page.content()
            assert "探测收入" in body, "本月流水未出现收入记录"
            assert "收入" in body, "收入标签缺失"

        record("TC-FL-PL-002", "快速记账提交（支出+收入）", tc_pl_quickadd_submit)

        def tc_pl_kind_switch_category():
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            page.locator(".el-select").nth(1).click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="餐饮").first.click()
            page.wait_for_timeout(300)
            # 切到收入：分类下拉应只剩收入分类
            page.locator(".el-radio-button", has_text="收入").click()
            page.wait_for_timeout(500)
            page.locator(".el-select").nth(1).click()
            page.wait_for_timeout(400)
            opts = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").all_inner_texts()
            page.keyboard.press("Escape")
            assert any("工资" in o for o in opts), f"收入分类缺失: {opts[:5]}"
            assert not any("餐饮" in o for o in opts), f"收入类型下仍显示支出分类: {opts[:8]}"

        record("TC-FL-PL-003", "类型切换后分类联动（收入/支出）", tc_pl_kind_switch_category)

        def tc_pl_kind_switch_stale_category():
            """选支出分类后切收入再提交：不应因残留旧分类提交失败（400 分类与类型不一致）。"""
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            page.locator(".el-input-number input").first.fill("9.9")
            page.locator(".el-select").first.click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first.click()
            page.locator(".el-select").nth(1).click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="餐饮").first.click()
            page.wait_for_timeout(300)
            page.locator(".el-radio-button", has_text="收入").click()
            page.wait_for_timeout(500)
            page.locator("button", has_text="记一笔").first.click()
            page.wait_for_timeout(2000)
            toast = visible_toast_text()
            assert "不一致" not in toast, f"切换类型后残留旧分类导致提交失败: {toast[:200]}"

        record("TC-FL-PL-004", "类型切换后不残留旧分类（提交不报 400）", tc_pl_kind_switch_stale_category)

        def tc_pl_account_manage():
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            page.locator("button", has_text="账户 / 分类管理").click()
            page.wait_for_timeout(600)
            drawer = page.locator(".el-drawer:visible")
            drawer.locator("input[placeholder='新账户名']").fill("探测账户")
            drawer.locator("button", has_text="添加").first.click()
            page.wait_for_timeout(1800)
            assert "探测账户" in drawer.inner_text(), "新账户未出现在管理列表"
            row = drawer.locator(".manage-row", has_text="探测账户")
            row.locator("button", has_text="删除").click()
            page.wait_for_timeout(1800)
            assert "探测账户" not in drawer.inner_text(), "空账户删除失败"

        record("TC-FL-PL-005", "账户管理（新增/删除）", tc_pl_account_manage)

        def tc_pl_account_delete_guard():
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            page.locator("button", has_text="账户 / 分类管理").click()
            page.wait_for_timeout(600)
            drawer = page.locator(".el-drawer:visible")
            drawer.locator("input[placeholder='新账户名']").fill("探测账户2")
            drawer.locator("button", has_text="添加").first.click()
            page.wait_for_timeout(1800)
            drawer.locator(".el-drawer__close-btn").click()
            page.wait_for_timeout(500)
            # 在该账户记一笔
            page.locator(".el-input-number input").first.fill("5")
            page.locator(".el-select").first.click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="探测账户2").first.click()
            page.locator(".el-select").nth(1).click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first.click()
            page.locator("button", has_text="记一笔").first.click()
            page.wait_for_timeout(1800)
            # 尝试删除：应被拒绝且账户仍在
            page.locator("button", has_text="账户 / 分类管理").click()
            page.wait_for_timeout(600)
            drawer = page.locator(".el-drawer:visible")
            drawer.locator(".manage-row", has_text="探测账户2").locator("button", has_text="删除").click()
            page.wait_for_timeout(1800)
            assert "探测账户2" in drawer.inner_text(), "有交易引用的账户被误删"
            assert "不能删除" in visible_toast_text(), "缺少拒绝删除提示"
            drawer.locator(".el-drawer__close-btn").click()

        record("TC-FL-PL-006", "账户删除保护（有交易引用拒绝）", tc_pl_account_delete_guard)

        def tc_pl_category_manage():
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            page.locator("button", has_text="账户 / 分类管理").click()
            page.wait_for_timeout(600)
            drawer = page.locator(".el-drawer:visible")
            drawer.locator(".add-category .el-input input").fill("探测分类")
            drawer.locator(".add-category button", has_text="添加").click()
            page.wait_for_timeout(1800)
            assert "探测分类" in drawer.inner_text(), "新分类未出现在管理列表"
            row = drawer.locator(".manage-row", has_text="探测分类")
            row.locator("button", has_text="删除").click()
            page.wait_for_timeout(1800)
            assert "探测分类" not in drawer.inner_text(), "空分类删除失败"

        record("TC-FL-PL-007", "分类管理（新增/删除）", tc_pl_category_manage)

        def tc_pl_empty_amount_guard():
            reset_to("/finance/ledger-personal")
            page.wait_for_timeout(1500)
            page.locator("button", has_text="记一笔").first.click()
            page.wait_for_timeout(800)
            assert "请填写" in visible_toast_text(), "空金额缺少前端校验提示"

        record("TC-FL-PL-008", "快速记账空金额前端校验", tc_pl_empty_amount_guard)

        def tc_pl_transactions_month_dropdown():
            reset_to("/finance/ledger-personal/transactions")
            page.wait_for_timeout(1800)
            page.locator(".el-select").first.click()
            page.wait_for_timeout(500)
            opts = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").all_inner_texts()
            assert CUR_MONTH in opts, f"月份下拉缺少当前月 {CUR_MONTH}: {opts[:5]}"
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            page.locator(".el-select").first.click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text=CUR_MONTH).first.click()
            page.locator("button", has_text="筛选").click()
            page.wait_for_timeout(1800)
            assert "探测支出" in page.content(), "按月筛选后未见探测支出"
            # ag-grid 分页模块注册后分页面板应渲染（未注册时 pagination 静默失效）
            assert page.locator(".ag-paging-panel").count() > 0, "ag-grid 分页面板未渲染（PaginationModule 未注册）"

        record("TC-FL-PL-009", "流水页月份下拉含当前月 + 按月筛选", tc_pl_transactions_month_dropdown)

        def tc_pl_transactions_keyword():
            reset_to("/finance/ledger-personal/transactions")
            page.wait_for_timeout(1800)
            page.locator("input[placeholder='备注关键词']").fill("绝不存在的关键词xyz")
            page.locator("button", has_text="筛选").click()
            page.wait_for_timeout(1800)
            assert "暂无流水" in page.content(), "关键词无结果时空态缺失"
            page.locator("input[placeholder='备注关键词']").fill("探测支出")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1800)
            assert "探测支出" in page.content(), "关键词回车筛选未生效"

        record("TC-FL-PL-010", "流水页关键词筛选（含空态）", tc_pl_transactions_keyword)

        def tc_pl_stats_page():
            reset_to("/finance/ledger-personal/stats")
            page.wait_for_timeout(3000)
            assert page.locator("canvas").count() >= 2, f"统计图表未渲染: canvas={page.locator('canvas').count()}"
            body = page.content()
            assert "当月支出分类构成" in body, "分类构成卡片缺失"
            assert "收入" in body and "结余" in body, "汇总行缺失"
            # 占比口径回归：不得出现四位及以上整数位的百分数（早期前端把后端 0-100 的 percent 又 ×100，75% 变 7500.0%）
            bad = re.findall(r"\d{4,}(?:\.\d+)?%", body)
            assert not bad, f"占比显示异常（疑似双重×100）: {bad[:5]}"

        record("TC-FL-PL-011", "统计页渲染 + 占比口径", tc_pl_stats_page)

        def tc_pl_export_csv():
            reset_to("/finance/ledger-personal/transactions")
            page.wait_for_timeout(1800)
            with page.expect_download(timeout=10000) as dl_info:
                page.locator("button", has_text="导出 CSV").click()
            dl = dl_info.value
            assert dl.suggested_filename.endswith(".csv"), f"导出文件名异常: {dl.suggested_filename}"
            path = PROBE_DIR / "export_check.csv"
            dl.save_as(str(path))
            text = path.read_bytes().decode("utf-8-sig")
            assert text.splitlines()[0].startswith("日期,类型,金额"), f"CSV 表头异常: {text[:80]}"
            assert "探测支出" in text, "CSV 内容缺少测试记录"

        record("TC-FL-PL-012", "导出 CSV", tc_pl_export_csv)

        def tc_pl_stats_percent_exact():
            reset_to("/finance/ledger-personal/stats")
            page.wait_for_timeout(3000)
            body = page.content()
            pcts = re.findall(r"(\d+(?:\.\d+)?)%", body)
            over = [p for p in pcts if float(p) > 100.0]
            assert not over, f"存在 >100% 的占比: {over[:5]}"

        record("TC-FL-PL-013", "统计页占比均 ≤100%", tc_pl_stats_percent_exact)

        def tc_pl_stats_range_switch():
            reset_to("/finance/ledger-personal/stats")
            page.wait_for_timeout(3000)
            page.locator(".el-radio-button", has_text="近 12 月").click()
            page.wait_for_timeout(2500)
            assert page.locator("canvas").count() >= 2, "切换 12 月后图表缺失"

        record("TC-FL-PL-014", "统计页切换近 12 月", tc_pl_stats_range_switch)

        ctx.close()
        browser.close()

    print("=" * 70)
    passed = sum(1 for r in results if r[2])
    print(f"tests total={len(results)} passed={passed} failed={len(results) - passed}")
    bad_pages = [p for p in pages_report if p["console_errors"] or p["page_errors"] or p["failed_reqs"]]
    print(f"pages={len(pages_report)} bad={len(bad_pages)}")
    for p in bad_pages:
        for e in (p["console_errors"] + p["page_errors"] + p["failed_reqs"])[:5]:
            print(f"  !! [{p['name']}] {e[:200]}")
    for e in page_errors_all[:5]:
        print(f"  !! [global] {e[:200]}")
    print("=" * 70)

    out = {
        "area": "finance-ledger",
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages_report,
        "tests": [{"id": r[0], "name": r[1], "passed": r[2], "detail": r[3]} for r in results],
        "bugs": [],
    }
    (PROBE_DIR / "finance-ledger.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"results -> {PROBE_DIR / 'finance-ledger.json'}")


try:
    main()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
