# -*- coding: utf-8 -*-
"""finance_lease 纯计算引擎：规则矩阵与合成黄金用例（Phase 0 冻结稿）。

本文件是规则矩阵的唯一测试源：``calculation.py`` / ``checks.py`` 已按它实现，全部用例
真实执行；任何断言都不得删除、跳过或放宽容差。修改规则必须同时改这里的矩阵与用例，
并由主模型复核。

引擎契约（由后端 Agent 在 ``app/modules/finance_lease/schemas.py`` 冻结，本文件按它断言）
----------------------------------------------------------------------------------------
``calculation.py`` 只需导出 ``RULE_VERSION: str`` 与
``compute(payload: CalculationInput) -> CalculationResult``；入参出参全部是 pydantic 模型，
引擎不读数据库、不写文件。字段名见第十节。

规则来源与优先级
----------------
1. 两份原始租赁底稿的真实公式（只读核对，不入仓库）——兼容性证据。
2. 计划文件冻结的业务边界。
3. 使用手册仅作需求说明；与源表冲突处以源表为准，逐条登记在第九节。

全部用例只用合成数据：合成合同号 ``SYN-LEASE-*``、合成金额与日期，不含真实合同、
真实项目名、真实路径。

====================================================================
一、结构规则（A）
====================================================================
A1 一行 = 一个付款期，``sequence`` 从 1 连续到 N，不跳号、不重号。
   （``schemas.validate_payment_sequences`` 已在入参层拒绝；引擎对非法入参仍应拒绝而非重排）
A2 ``period_end >= period_start``；不满足即拒绝。
A3 相邻期连续：``start(i+1) == end(i) + 1 天``；不连续即拒绝，不猜测、不补齐。
A4 ``amount`` 为不含税金额，2 位小数，``>= 0``；免租期金额 0 但必须占行。
A5 ``annual_rate >= 0``：显式 0 允许（零利率，利息恒 0），缺失/负值拒绝；
   **不得把缺失利率默认成 0**。
A6 空付款计划 → 拒绝（不返回 0 值结果）。
A7 全部付款期 ``months == 0`` → 拒绝（无法生成期间表）。
A8 ``months == 0`` 且 ``amount == 0`` → 拒绝（空记录无会计含义）。
A9 ``start_flag`` / ``prepay_flag`` / ``is_free_rent`` 仅作留痕；MVP 不实现期末支付模型，
   时间轴恒为“每期期初支付、首行不折现”。
A10 ``contract_start`` / ``contract_end`` 必须等于首期起日 / 末期止日；不一致说明可能漏录
   免租期或区间外期间，出 ``contract_range`` 检查 **unknown**，不静默按付款计划推算。

====================================================================
二、月期数（B，Excel DATEDIF 兼容）
====================================================================
B1 ``months_i = DATEDIF(start_i - 1 天, end_i + 1 天, "M")``，由引擎自行计算。
B2 等价整数公式，``s = start-1d``、``e = end+1d``：
   ``(e.year - s.year) * 12 + (e.month - s.month) - (1 if e.day < s.day else 0)``。
B3 已核对边界：

   | 计租起 | 计租止 | 月数 | 说明 |
   |---|---|---:|---|
   | 2021-01-01 | 2021-06-30 | 6 | 半年期 |
   | 2021-01-01 | 2021-01-31 | 1 | 整月 |
   | 2027-01-01 | 2027-03-31 | 3 | 季度 |
   | 2028-07-01 | 2028-07-01 | 0 | 末期 0 月期 |
   | 2021-01-15 | 2021-02-12 | 0 | 非整月，不足一个月界 |
   | 2021-01-15 | 2021-02-13 | 1 | 同日锚点即计一月 |
   | 2021-01-31 | 2021-02-28 | 1 | 月末 |

B4 不得用“月份差”“天数/30”之类近似；B2 是唯一口径。
B5 ``total_months = Σ months_i`` = 期间表月行数。
B6 入参若带 ``months``（源表填写值），引擎以自己算出的值为准，并在两者不一致时出
   ``month_count_consistency`` 检查 **failed**；绝不静默采用入参值。

====================================================================
三、期间利率与折现因子（C）
====================================================================
C1 ``period_rate_i = ROUND_HALF_UP(annual_rate * months_i / 12, 4)``。
   每行用**自己的**月数，同一合同 Q 可逐行不同（6 个月 0.0175、3 个月 0.0088）。
C2 折现因子 ``d_1 = 1``；``d_i = ROUND_HALF_UP(d_{i-1} * (1 + period_rate_{i-1}), 9)``。
   即付款 i 的因子覆盖此前各期月数；逐期四舍五入到 9 位，不是最后一次性取整。
C3 0 月期 ``period_rate = 0``，因子不再增长，其付款仍按累计因子折现。

====================================================================
四、评估行（D）
====================================================================
D1 ``present_value_1 = amount_1``（**首行不折现**；金额为 0 的免租首期同样成立）。
D2 ``present_value_i = ROUND_HALF_UP(amount_i / d_i, 2)``（i >= 2）。
D3 ``unamortized_finance_i = amount_i - present_value_i``（源表 S 列“利息（非实际当期
   利息）”，仅留痕，不进入期间表月利息）。
D4 ``present_value`` 合计 = H2 = 使用权资产原值 = 租赁负债现值。

====================================================================
五、负债滚动与当期利息（E）
====================================================================
E1 ``liability_balance_1 = H2 - amount_1``（首期付款后余额；源表 SUM(R)-M2 等价）。
E2 ``interest_u_1 = ROUND_HALF_UP(liability_balance_1 * period_rate_1, 2)``。
E3 ``liability_balance_i = liability_balance_{i-1} + interest_u_{i-1} - amount_i``（i >= 2）。
E4 ``interest_u_i = ROUND_HALF_UP(liability_balance_i * period_rate_i, 2)``。
E5 余额由 2 位小数金额与 2 位小数利息推出，天然 2 位，不再额外取整。
E6 0 月期 ``period_rate = 0`` → 利息 0，余额只减付款额。
E7 ``|liability_balance_N| <= TOL_TOTAL``：尾差来自 Q 4 位与分位取整，属预期；
   禁止把尾差倒挤进末期或做“调平”后再断言 0。

====================================================================
六、期间表（F，只有月份行）
====================================================================
F1 期间行 = ``total_months`` 个月份行，**不含**源表那种“期初汇总行”；
   ``summary.month_count == len(periods)``。月份从首个 ``months > 0`` 付款期的计租起始月起
   连续展开，按期间顺序归属 ``sequence``。
F2 ``summary.total_payment = K2 = Σ amount_i``（租赁付款额，未折现）；
   ``summary.total_interest = M2 = Σ interest_u_i``；
   ``summary.present_value = H2``；``summary.total_depreciation = Σ depreciation``。
F3 ``payable``（应付 L）= 付款前一月：
   - 付款期 i（i >= 2）的 amount_i 计入“其计租起始月的前一个月”那一行；
   - 首期付款计入首个月份行（期初支付）；
   - 首期 ``months == 0`` 时，amount_1 与 amount_2 同时计入第一个实际月份行；
   - 多个 0 月期落在同一月份时，该月 ``payable`` 为这些付款额合计（保住 ΣL = K2）。
F4 ``interest``（月利息 N）= ``ROUND_HALF_UP(interest_u / months_i, 2)``；
   0 月期无月份行。
F5 ``depreciation``（月折旧 J）= ``ROUND_HALF_UP(H2 / total_months, 2)``。
   **不吸收尾差**（D1 裁决）：ΣJ 与 H2 的差异允许 <= TOL_TOTAL；超过即判 ``failed``，
   不得把尾差倒挤进末期，也不得放宽容差。
   > 200 月的合同取整漂移可能超过 1.00，此时如实判差异（边界用例见文件末尾）。
F6 ``lease_payment`` = 该月份行所属付款期的 ``amount``（期内每月重复展示；
   按付款期去重后求和 = K2，逐月求和会重复计数）。仅作展示，不对账实付。
F7 ``unamortized_finance``（月末未确认融资费用余额）= M2 - 累计已确认利息（含本月）。
   与 ``AssessmentRow.unamortized_finance``（= M - R 折现差额）**同名不同义，禁止混用**。
F8 ``liability_balance``（月末租赁负债净额）= (K2 - 累计已确认应付 L) - 未确认融资费用余额。
   F6–F8 均为**本模块计划口径**，不得宣称等同 BIP 账面或外部实付数。
F9 不复制源表“公式拖过尾行”的 #DIV/0! 行。

====================================================================
七、三组恒等式、检查与容差（G）
====================================================================
G1 引擎返回的 ``checks`` 固定包含：``identity_depreciation``、``identity_payable``、
   ``identity_interest``、``liability_rollforward``、``remaining_planned_payment``、
   ``monthly_depreciation``、``monthly_interest``、``entry_recognition``、``entry_monthly``、
   ``contract_range``；带 ``months`` 入参时追加 ``month_count_consistency``。key 唯一。
G2 三组恒等式各自 ``|差| <= TOL_TOTAL = 1.00``；``TOL_UNIT = 0.01`` 用于单项/月摊对照。
G3 恒等式允许小尾差的理由：Q 取 4 位、R/U/J/N 取 2 位。D1 裁决：**不吸收尾差、
   不放宽容差**，超过 1.00 即判 ``failed``；> 200 月的合同会触碰容差上限并如实判差异。
G4 反向：把任一月份行折旧人为 +2.00 后，第一组恒等式必须超差（容差不得放大）。
G5 检查状态只有 ``ok / failed / unknown``；缺数据一律 ``unknown``，不得按 0 判 ``ok``。

====================================================================
八、错误与 unknown（H）
====================================================================
H1 计算前置校验失败抛 ``LeaseRuleError(code, message)``，``message`` 为中文；
   code 取值：``empty_payments`` / ``invalid_rate`` / ``invalid_period`` /
   ``discontinuous_period`` / ``zero_month_zero_amount`` / ``no_month_rows``。
   不得返回 0 值结果冒充成功。
H2 ``contract_range`` 不一致 → 检查 ``unknown``（合同区间与付款计划不对应，可能漏录免租期）。
H3 ``month_count_consistency`` 不一致 → 检查 ``failed``（源表月期数与 DATEDIF 冲突）。
H4 期数重复/跳号、金额为负、日期格式等由 ``schemas`` 在入参层拒绝，引擎不重复实现。

====================================================================
九、与手册 / 源表的冲突登记（I）
====================================================================
I1 手册 2.3、2.4 与附录 A 写“月利息 N = 评估表 S 列 ÷ 月数”；源表实际是
   ``N = 期间表 S ÷ 月数``，而期间表 ``S = SUMIFS(评估表 U, 对象, 支付期数)``。
   实现取源表口径；评估表 S（= M - R）落到 ``AssessmentRow.unamortized_finance`` 留痕。
I2 手册说月折旧“算两位”，源表 ``H2 / 月期总数`` 不取整；``schemas.PeriodRow`` 限定
   2 位，故实现按 2 位取整（F5），并与源表逐月对照用 0.01 容差。
I3 源表 ``T2 = SUM(R2:R23) - M2`` 是写死区间的等价写法；实现用 ``H2 - amount_1``（E1）。
I4 源表付款金额常为未取整公式结果（含税 ÷ 1.05 后再除期数），而 ``schemas`` 限定金额 2 位；
   与原始底稿逐项对照只承诺 0.01 / 1.00 容差，不承诺分毫不差。
I5 源表期间表末尾多一行 ``#DIV/0!``（公式拖过尾行）；本模块不复刻（F9）。
I6 源表 L 列公式依赖“下一行”定位付款期，中段 0 月期会漏计一期付款额；
   本模块按 F3 的月份归属实现，``ΣL = K2`` 恒成立。
I7 期初支付标识 P 列在源表未参与计算；MVP 同样不改变时间轴（A9）。
I8 源表首行 ``R = M`` 未取整；入参金额被限定 2 位后该差异消失，故真实底稿比对
   以 0.01 / 1.00 容差为准。

====================================================================
十、DTO 契约（唯一事实源：app/modules/finance_lease/schemas.py）
====================================================================
``PaymentInput(sequence, period_start, period_end, months|None, amount, start_flag,
 prepay_flag, is_free_rent)``
``CalculationInput(contract_start, contract_end, annual_rate, payments)``
``AssessmentRow(sequence, period_start, period_end, months, amount, period_rate,
 discount_factor, present_value, unamortized_finance, interest_u, liability_balance)``
``PeriodRow(period_month, sequence, depreciation, payable, interest, lease_payment,
 unamortized_finance, liability_balance)``
``CalculationSummary(total_payment, present_value, total_interest, total_depreciation,
 payment_count, month_count, first_period, last_period)``
``CalculationCheck(key, title, status: ok|failed|unknown, expected, actual, difference, message)``
``CalculationResult(rule_version, assessment, periods, summary, checks)``

精度：``period_rate`` 4 位、``discount_factor`` 9 位、其余金额 2 位（均 HALF_UP）。
辅助函数（引擎须导出）：``month_count(start, end)``、``period_rate(annual_rate, months)``、
``round_money(v)``、``round_factor9(v)``、``TOL_TOTAL``、``TOL_UNIT``、``RULE_VERSION``。
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date
from decimal import Decimal

import pytest

from app.modules.finance_lease import calculation as calc
from app.modules.finance_lease.schemas import CalculationInput, PaymentInput

TOL_TOTAL = Decimal("1.00")
TOL_UNIT = Decimal("0.01")
_CJK = re.compile(r"[\u4e00-\u9fff]")

MANUAL = [  # 手册 2.5 最小算例：2 期 × 6 月，年利率 6%，每期 103.00，期初支付
    ("2021-01-01", "2021-06-30", "103.00"),
    ("2021-07-01", "2021-12-31", "103.00"),
]
FREE_FIRST = [  # 首期免租占行：金额 0，但 6 个月照常累进折现因子
    ("2022-01-01", "2022-06-30", "0.00"),
    ("2022-07-01", "2022-12-31", "1000.00"),
    ("2023-01-01", "2023-06-30", "1000.00"),
]
MIXED = [  # 混合期数：6 个月 + 3 个月 + 3 个月，Q 逐行不同
    ("2024-01-01", "2024-06-30", "600.00"),
    ("2024-07-01", "2024-09-30", "320.00"),
    ("2024-10-01", "2024-12-31", "320.00"),
]
ZERO_TAIL = [  # 末期 0 月期：付款在租赁期末时点，不额外折现、不占月份行
    ("2025-01-01", "2025-12-31", "1200.00"),
    ("2026-01-01", "2026-01-01", "600.00"),
]
ZERO_FIRST = [  # 首期 0 月期：前两期付款并入第一个实际月份（相邻期仍连续）
    ("2026-01-01", "2026-01-01", "200.00"),
    ("2026-01-02", "2026-07-01", "500.00"),
    ("2026-07-02", "2027-01-01", "500.00"),
]
LONG = [  # 长期合同：10 期 × 6 个月 = 60 个月
    (f"{2020 + k // 2}-{'01' if k % 2 == 0 else '07'}-01",
     f"{2020 + k // 2}-{'06' if k % 2 == 0 else '12'}-{'30' if k % 2 == 0 else '31'}",
     "1000.00")
    for k in range(10)
]


def _pay(seq, spec, **kw):
    start, end, amount = spec
    return PaymentInput(
        sequence=seq, period_start=start, period_end=end, amount=Decimal(amount), **kw)


def _payload(rate, specs, **kw):
    pays = [_pay(i + 1, s) for i, s in enumerate(specs)]
    return CalculationInput(
        contract_start=pays[0].period_start,
        contract_end=pays[-1].period_end,
        annual_rate=Decimal(rate),
        payments=pays,
        **kw,
    )


def _run(rate, specs, **kw):
    return calc.compute(_payload(rate, specs, **kw))


def _month_labels(start, count):
    year, month = (int(x) for x in start.split("-"))
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _checks_by_key(res):
    return {check.key: check for check in res.checks}


def _gaps(res):
    t = res.summary
    return (
        t.total_depreciation - t.present_value,
        sum((row.payable for row in res.periods), Decimal("0")) - t.total_payment,
        t.total_interest - sum((row.interest for row in res.periods), Decimal("0")),
    )


def _expect_error(code, payload):
    with pytest.raises(calc.LeaseRuleError) as excinfo:
        calc.compute(payload)
    assert excinfo.value.code == code
    assert _CJK.search(str(excinfo.value)), "错误信息必须是中文"


# ---------------------------------------------------------------- 最小算例（手册 2.5）
def test_manual_minimal_example_matches_hand_computation():
    res = _run("0.06", MANUAL)
    rows = res.assessment

    assert [r.sequence for r in rows] == [1, 2]
    assert [r.months for r in rows] == [6, 6]
    assert [r.period_rate for r in rows] == [Decimal("0.03"), Decimal("0.03")]
    assert [r.discount_factor for r in rows] == [Decimal("1"), Decimal("1.03")]
    assert [r.present_value for r in rows] == [Decimal("103.00"), Decimal("100.00")]
    assert [r.unamortized_finance for r in rows] == [Decimal("0.00"), Decimal("3.00")]
    assert [r.liability_balance for r in rows] == [Decimal("100.00"), Decimal("0.00")]
    assert [r.interest_u for r in rows] == [Decimal("3.00"), Decimal("0.00")]

    summary = res.summary
    assert summary.present_value == Decimal("203.00")
    assert summary.total_payment == Decimal("206.00")
    assert summary.total_interest == Decimal("3.00")
    assert summary.payment_count == 2
    assert summary.month_count == 12
    assert summary.first_period == "2021-01"
    assert summary.last_period == "2021-12"


def test_manual_minimal_example_period_rows():
    res = _run("0.06", MANUAL)

    assert len(res.periods) == 12, "期间行只有月份行，不含期初汇总行"
    assert [r.period_month for r in res.periods] == _month_labels("2021-01", 12)
    assert [r.depreciation for r in res.periods] == [Decimal("16.92")] * 12
    assert [r.interest for r in res.periods[:6]] == [Decimal("0.50")] * 6
    assert res.summary.total_depreciation == Decimal("203.04")


def test_quoted_manual_ratios():
    res = _run("0.06", MANUAL)
    assert res.periods[0].depreciation == Decimal("16.92"), "203/12 = 16.92"
    assert res.periods[0].interest == Decimal("0.50"), "3/6 = 0.50"


# ---------------------------------------------------------------- 首行不折现 / 免租
def test_first_row_is_not_discounted():
    res = _run("0.06", MANUAL)
    assert res.assessment[0].present_value == res.assessment[0].amount

    free = _run("0.045", FREE_FIRST)
    assert free.assessment[0].amount == Decimal("0.00")
    assert free.assessment[0].present_value == Decimal("0.00")
    assert free.assessment[0].discount_factor == Decimal("1")


def test_free_rent_period_keeps_row_and_discount_chain():
    res = _run("0.045", FREE_FIRST)

    assert len(res.assessment) == 3
    assert res.assessment[0].months == 6, "免租期必须占行并计入月数"
    assert res.assessment[1].discount_factor == Decimal("1.0225")
    assert res.assessment[2].discount_factor == Decimal("1.045506250")
    assert res.summary.month_count == 18
    assert res.summary.total_payment == Decimal("2000.00")


def test_free_rent_period_still_produces_twelve_month_rows():
    res = _run("0.045", FREE_FIRST)

    assert len(res.periods) == 18
    first = res.periods[0]
    assert first.period_month == "2022-01"
    assert first.payable == Decimal("0.00"), "首期免租，不需确认应付"


# ---------------------------------------------------------------- 期初支付 / 付款前一月
def test_inception_payment_due_in_first_month():
    res = _run("0.06", MANUAL)

    assert res.periods[0].period_month == "2021-01"
    assert res.periods[0].payable == Decimal("103.00")
    assert sum(row.payable for row in res.periods) == Decimal("206.00")


def test_payment_due_lands_one_month_before_period_start():
    res = _run("0.035", MIXED)
    due = {row.period_month: row.payable for row in res.periods}

    assert due["2024-01"] == Decimal("600.00"), "首期在首月确认"
    assert due["2024-06"] == Decimal("320.00"), "第二期 2024-07 起租，前移一月"
    assert due["2024-09"] == Decimal("320.00"), "第三期 2024-10 起租，前移一月"
    others = [m for m in due if m not in {"2024-01", "2024-06", "2024-09"}]
    assert all(due[m] == Decimal("0.00") for m in others)


def test_payable_total_equals_payment_total():
    for rate, specs in (("0.06", MANUAL), ("0.045", FREE_FIRST), ("0.035", MIXED)):
        res = _run(rate, specs)
        assert sum(row.payable for row in res.periods) == res.summary.total_payment


def test_inception_flags_do_not_change_timeline():
    base = _run("0.06", MANUAL)
    flagged = calc.compute(CalculationInput(
        contract_start=date(2021, 1, 1),
        contract_end=date(2021, 12, 31),
        annual_rate=Decimal("0.06"),
        payments=[
            _pay(1, MANUAL[0], start_flag=True, prepay_flag=True),
            _pay(2, MANUAL[1]),
        ],
    ))
    assert [r.present_value for r in flagged.assessment] == \
        [r.present_value for r in base.assessment]
    assert [r.payable for r in flagged.periods] == [r.payable for r in base.periods]


# ---------------------------------------------------------------- DATEDIF 月期
@pytest.mark.parametrize("start, end, expected", [
    ("2021-01-01", "2021-06-30", 6),
    ("2021-01-01", "2021-01-31", 1),
    ("2027-01-01", "2027-03-31", 3),
    ("2028-07-01", "2028-07-01", 0),
    ("2021-01-15", "2021-02-12", 0),
    ("2021-01-15", "2021-02-13", 1),
    ("2021-01-31", "2021-02-28", 1),
    ("2020-12-31", "2021-01-30", 1),
])
def test_month_count_matches_datedif(start, end, expected):
    assert calc.month_count(date.fromisoformat(start), date.fromisoformat(end)) == expected


def test_month_count_is_not_calendar_month_difference():
    # 起止都取 15 日这类非锚点日期时，“月份差”不是答案。
    assert calc.month_count(date(2021, 1, 15), date(2021, 2, 12)) == 0
    assert calc.month_count(date(2021, 1, 15), date(2021, 2, 13)) == 1


def test_provided_months_mismatch_is_reported_not_used():
    res = calc.compute(CalculationInput(
        contract_start=date(2021, 1, 1),
        contract_end=date(2021, 12, 31),
        annual_rate=Decimal("0.06"),
        payments=[_pay(1, MANUAL[0], months=5), _pay(2, MANUAL[1])],
    ))
    check = _checks_by_key(res)["month_count_consistency"]

    assert check.status == "failed"
    assert res.assessment[0].months == 6, "以 DATEDIF 结果为准，不采用入参月数"


def test_provided_months_matching_keeps_checks_ok():
    res = calc.compute(CalculationInput(
        contract_start=date(2021, 1, 1),
        contract_end=date(2021, 12, 31),
        annual_rate=Decimal("0.06"),
        payments=[_pay(1, MANUAL[0], months=6), _pay(2, MANUAL[1], months=6)],
    ))
    assert _checks_by_key(res)["month_count_consistency"].status == "ok"


# ---------------------------------------------------------------- Q / AB / R / U 精度
def test_period_rate_uses_own_month_count_and_four_decimals():
    res = _run("0.035", MIXED)
    rates = [r.period_rate for r in res.assessment]

    assert rates == [Decimal("0.0175"), Decimal("0.0088"), Decimal("0.0088")]
    assert rates[1].as_tuple().exponent == -4
    assert calc.period_rate(Decimal("0.035"), 3) == Decimal("0.0088"), "0.00875 HALF_UP"
    assert calc.period_rate(Decimal("0.045"), 1) == Decimal("0.0038"), "0.00375 HALF_UP"
    assert calc.period_rate(Decimal("0.06"), 6) == Decimal("0.03")


def test_discount_factor_compounds_with_nine_decimals():
    res = _run("0.035", MIXED)
    factors = [r.discount_factor for r in res.assessment]

    assert factors[1] == Decimal("1.0175")
    assert factors[2] == Decimal("1.026454000")
    assert factors[2].as_tuple().exponent == -9
    assert calc.round_factor9(Decimal("1.03")) == Decimal("1.030000000")


def test_present_value_and_interest_round_half_up_to_cents():
    res = _run("0.035", MIXED)
    rows = res.assessment

    assert [r.present_value for r in rows] == [
        Decimal("600.00"), Decimal("314.50"), Decimal("311.75")]
    assert [r.interest_u for r in rows] == [Decimal("10.96"), Decimal("2.79"), Decimal("0.00")]
    for row in rows:
        assert row.present_value.as_tuple().exponent == -2
        assert row.interest_u.as_tuple().exponent == -2


def test_round_money_is_half_up_not_python_round():
    assert calc.round_money(Decimal("0.125")) == Decimal("0.13")
    assert calc.round_money(Decimal("2.675")) == Decimal("2.68")
    assert calc.round_money(Decimal("-0.125")) == Decimal("-0.13")


def test_liability_balance_is_exact_for_two_decimal_inputs():
    res = _run("0.035", MIXED)
    rows = res.assessment

    assert rows[0].liability_balance == Decimal("626.25")  # 1226.25 - 600.00
    assert rows[1].liability_balance == Decimal("317.21")  # 626.25 + 10.96 - 320.00
    assert abs(rows[-1].liability_balance) <= TOL_TOTAL
    assert rows[0].liability_balance.as_tuple().exponent >= -2


def test_interest_uses_post_payment_balance():
    res = _run("0.06", MANUAL)
    assert res.assessment[0].liability_balance == Decimal("100.00")
    assert res.assessment[0].interest_u == Decimal("3.00"), "100.00 × 3%"


def test_zero_rate_lease_has_no_interest():
    res = _run("0", MANUAL)

    assert res.summary.total_interest == Decimal("0.00")
    assert [r.discount_factor for r in res.assessment] == [Decimal("1"), Decimal("1")]
    assert [r.present_value for r in res.assessment] == [Decimal("103.00")] * 2
    assert {c.status for c in res.checks} == {"ok"}


# ---------------------------------------------------------------- 末期 0 月期
def test_zero_month_tail_is_undiscounted_and_keeps_no_month_row():
    res = _run("0.03", ZERO_TAIL)
    last = res.assessment[-1]

    assert last.months == 0
    assert last.period_rate == Decimal("0")
    assert last.interest_u == Decimal("0.00")
    assert last.present_value == Decimal("582.52"), "按累计因子折现，不额外折现"
    assert len(res.assessment) == 2
    assert len(res.periods) == 12, "0 月期不产生月份行"
    assert res.summary.month_count == 12
    assert abs(res.assessment[-1].liability_balance) <= TOL_TOTAL


def test_zero_month_tail_payable_in_last_month():
    res = _run("0.03", ZERO_TAIL)
    due = {row.period_month: row.payable for row in res.periods}

    assert due["2025-01"] == Decimal("1200.00")
    assert due["2025-12"] == Decimal("600.00")
    assert sum(due.values()) == Decimal("1800.00")


def test_zero_month_first_period_merges_into_first_real_month():
    res = _run("0.048", ZERO_FIRST)
    due = {row.period_month: row.payable for row in res.periods}

    assert res.periods[0].period_month == "2026-01", "月份从首个有月数的付款期起算"
    assert res.periods[0].sequence == 2
    assert res.periods[0].payable == Decimal("700.00"), "首期 200 + 第二期 500"
    assert due["2026-06"] == Decimal("500.00"), "第三期 2026-07 起租，前移一月"
    assert sum(due.values()) == Decimal("1200.00")
    assert res.summary.month_count == 12


# ---------------------------------------------------------------- 期间表列语义
def test_lease_payment_repeats_within_period():
    res = _run("0.035", MIXED)
    by_sequence = {}
    for row in res.periods:
        by_sequence.setdefault(row.sequence, set()).add(row.lease_payment)

    assert by_sequence[1] == {Decimal("600.00")}
    assert by_sequence[2] == {Decimal("320.00")}
    assert sum(next(iter(v)) for v in by_sequence.values()) == res.summary.total_payment


def test_monthly_interest_is_rounded_period_interest_over_months():
    res = _run("0.045", FREE_FIRST)
    first = [row for row in res.periods if row.sequence == 1]

    assert len(first) == 6
    assert {row.interest for row in first} == {Decimal("7.26")}, "43.53/6 = 7.255 → 7.26"


def test_monthly_depreciation_is_h2_over_total_months():
    res = _run("0.055", LONG)

    assert {row.depreciation for row in res.periods} == {Decimal("147.96")}, "8877.68/60"
    assert len(res.periods) == 60
    assert [row.period_month for row in res.periods] == _month_labels("2020-01", 60)
    assert [row.sequence for row in res.periods] == [
        seq for seq in range(1, 11) for _ in range(6)]


def test_period_balances_roll_down_to_zero():
    res = _run("0.06", MANUAL)
    first, last = res.periods[0], res.periods[-1]

    assert first.unamortized_finance == Decimal("2.50"), "3.00 - 已确认利息 0.50"
    assert first.liability_balance == Decimal("100.50"), "(206 - 103) - 2.50"
    assert abs(last.unamortized_finance) <= TOL_UNIT
    assert abs(last.liability_balance) <= TOL_UNIT
    unamortized = [row.unamortized_finance for row in res.periods]
    assert unamortized == sorted(unamortized, reverse=True), "未确认融资费用单调递减"


# ---------------------------------------------------------------- 恒等式与容差
@pytest.mark.parametrize("rate, specs", [
    ("0.06", MANUAL),
    ("0.045", FREE_FIRST),
    ("0.035", MIXED),
    ("0.03", ZERO_TAIL),
    ("0.048", ZERO_FIRST),
    ("0.055", LONG),
])
def test_three_identities_hold_within_tolerance(rate, specs):
    res = _run(rate, specs)
    dep_gap, due_gap, int_gap = _gaps(res)

    assert abs(dep_gap) <= TOL_TOTAL
    assert abs(due_gap) <= TOL_TOTAL
    assert abs(int_gap) <= TOL_TOTAL
    assert res.summary.total_depreciation == sum(
        (row.depreciation for row in res.periods), Decimal("0"))


def test_identity_checks_are_reported_as_ok():
    res = _run("0.06", MANUAL)
    checks = _checks_by_key(res)

    assert {"identity_depreciation", "identity_payable", "identity_interest",
            "liability_rollforward", "remaining_planned_payment",
            "monthly_depreciation", "monthly_interest", "entry_recognition",
            "entry_monthly", "contract_range"} == set(checks)
    assert all(check.status == "ok" for check in checks.values())
    assert len(checks) == len(res.checks), "检查 key 必须唯一"


def test_remaining_planned_payment_uses_plan_data_only():
    res = _run("0.06", MANUAL)
    check = _checks_by_key(res)["remaining_planned_payment"]

    assert check.status == "ok"
    assert check.actual == Decimal("0.00")
    assert "计划" in check.message, "必须写明只用计划数据，不对账实付"


def test_entry_checks_are_summary_level_and_balanced():
    res = _run("0.045", FREE_FIRST)  # H2 1934.47 + M2 65.54 = 2000.01 对 K2 2000.00
    checks = _checks_by_key(res)

    assert checks["entry_recognition"].status == "ok"
    assert checks["entry_recognition"].difference == Decimal("0.01")
    assert checks["entry_monthly"].status == "ok"
    for key in ("entry_recognition", "entry_monthly"):
        message = checks[key].message
        assert "本项仅做汇总级检查" in message
        assert "entries.py" in message, "必须指明逐笔底稿分录由 entries.py 生成并逐凭证校验"


def test_depreciation_identity_gap_is_visible_not_hidden():
    res = _run("0.06", MANUAL)
    dep_gap, _, _ = _gaps(res)

    # 12 × 16.92 = 203.04：取整漂移 0.04 必须如实反映，不得倒挤进末期
    assert dep_gap == Decimal("0.04")
    assert {row.depreciation for row in res.periods} == {Decimal("16.92")}


def test_reverse_identity_detects_two_yuan_error():
    res = _run("0.06", MANUAL)
    tampered = res.periods[0].model_copy(
        update={"depreciation": res.periods[0].depreciation + Decimal("2.00")})
    total = sum((row.depreciation for row in (tampered,) + tuple(res.periods[1:])),
                Decimal("0"))
    gap = total - res.summary.present_value

    assert abs(gap - Decimal("2.04")) <= TOL_UNIT
    assert abs(gap) > TOL_TOTAL, "容差不得吸收 2 元误差"


def test_tolerance_constants_are_frozen():
    assert calc.TOL_TOTAL == TOL_TOTAL
    assert calc.TOL_UNIT == TOL_UNIT


# ---------------------------------------------------------------- unknown
def test_contract_range_mismatch_is_unknown():
    res = calc.compute(CalculationInput(
        contract_start=date(2020, 12, 1),
        contract_end=date(2021, 12, 31),
        annual_rate=Decimal("0.06"),
        payments=[_pay(1, MANUAL[0]), _pay(2, MANUAL[1])],
    ))
    check = _checks_by_key(res)["contract_range"]

    assert check.status == "unknown", "合同区间与付款计划不一致，不得判通过"
    assert _CJK.search(check.message)


def test_contract_range_ok_for_consistent_input():
    res = _run("0.06", MANUAL)
    assert _checks_by_key(res)["contract_range"].status == "ok"


# ---------------------------------------------------------------- 输入校验
def test_discontinuous_periods_rejected():
    _expect_error("discontinuous_period", CalculationInput(
        contract_start=date(2021, 1, 1),
        contract_end=date(2021, 12, 31),
        annual_rate=Decimal("0.05"),
        payments=[_pay(1, ("2021-01-01", "2021-06-30", "100.00")),
                  _pay(2, ("2021-07-02", "2021-12-31", "100.00"))],
    ))


def test_zero_month_zero_amount_rejected():
    _expect_error("zero_month_zero_amount", CalculationInput(
        contract_start=date(2021, 1, 1),
        contract_end=date(2021, 7, 1),
        annual_rate=Decimal("0.05"),
        payments=[_pay(1, ("2021-01-01", "2021-06-30", "100.00")),
                  _pay(2, ("2021-07-01", "2021-07-01", "0.00"))],
    ))


def test_all_zero_month_rejected():
    _expect_error("no_month_rows", CalculationInput(
        contract_start=date(2021, 1, 1),
        contract_end=date(2021, 1, 1),
        annual_rate=Decimal("0.05"),
        payments=[_pay(1, ("2021-01-01", "2021-01-01", "100.00"))],
    ))


def test_rule_version_is_declared():
    assert calc.RULE_VERSION == "lease-mvp-1"
    assert _run("0.06", MANUAL).rule_version == calc.RULE_VERSION


# ---------------------------------------------------------------- 取整漂移边界（>200 月）
def _span(year, month, months):
    end_year, end_month = year, month + months - 1
    while end_month > 12:
        end_year, end_month = end_year + 1, end_month - 12
    return (f"{year:04d}-{month:02d}-01",
            f"{end_year:04d}-{end_month:02d}-{monthrange(end_year, end_month)[1]:02d}")


def _equal_periods(year, month, months_each, count, amount):
    specs = []
    for _ in range(count):
        specs.append((*_span(year, month, months_each), amount))
        month += months_each
        while month > 12:
            year, month = year + 1, month - 12
    return specs


def test_depreciation_drift_at_tolerance_boundary_still_passes():
    # 200 月、零利率：H2 = 10001.00，J = ROUND(50.005) = 50.01，ΣJ = 10002.00，漂移恰为 1.00
    res = _run("0", _equal_periods(2020, 1, 10, 20, "500.05"))
    checks = _checks_by_key(res)
    dep_gap, _, _ = _gaps(res)

    assert res.summary.month_count == 200
    assert res.summary.present_value == Decimal("10001.00")
    assert res.periods[0].depreciation == Decimal("50.01")
    assert dep_gap == Decimal("1.00")
    assert checks["identity_depreciation"].status == "ok", "恰好等于容差仍算通过"


def test_depreciation_drift_beyond_tolerance_fails():
    # 240 月、零利率：H2 = 24001.20，J = ROUND(100.005) = 100.01，ΣJ = 24002.40，漂移 1.20
    res = _run("0", _equal_periods(2020, 1, 6, 40, "600.03"))
    checks = _checks_by_key(res)
    dep_gap, due_gap, int_gap = _gaps(res)

    assert res.summary.month_count == 240
    assert res.periods[0].depreciation == Decimal("100.01")
    assert dep_gap == Decimal("1.20")
    assert due_gap == Decimal("0.00")
    assert int_gap == Decimal("0.00")

    check = checks["identity_depreciation"]
    assert check.status == "failed", "D1：超 1.00 即判 failed，不倒挤、不放宽"
    assert check.difference == Decimal("1.20")
    others = [item for key, item in checks.items() if key != "identity_depreciation"]
    assert all(item.status == "ok" for item in others), "差异只落在折旧恒等式，不连带其它检查"
