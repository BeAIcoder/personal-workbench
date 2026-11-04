"""
纳税申报路由
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from ....database import get_db
from ....utils_money import money
from ..models import TaxReturn, Enterprise
from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime
from fastapi.responses import StreamingResponse
import io
import logging
import re

from urllib.parse import quote

from ..services.tax_form_generator import (
    EnterpriseInfo,
    TaxPeriod,
    A200000FormData,
    export_a200000_excel,
    export_a200000_pdf,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tax-returns", tags=["纳税申报"])


def _round_wan(amount_wan: float) -> float:
    """万元金额按「元」取整到分后再折回万元（复核 I-3）。

    直接 `money(万元)` 会把金额四舍五入到 0.01 万元（100 元粒度），页面与落库/导出
    就会差到元级；这里统一按分取整，保证计算页 = 落库 = 导出。
    """
    return money(float(amount_wan or 0) * 10000.0) / 10000.0


# Pydantic模型
class TaxReturnCreate(BaseModel):
    enterprise_id: int
    tax_period: str
    quarter: str | None = None
    report_type: str = "A类"
    total_revenue: float = 0.0
    other_income: float = 0.0
    total_costs: float = 0.0
    total_expenses: float = 0.0
    tax_adjustments: float = 0.0
    tax_preferential: float = 0.0
    # 高新与小微同时符合时须显式选择（台账 L-056）：high_tech / small_micro；不选则 400
    preferential_choice: str = ""

    @field_validator("tax_period")
    @classmethod
    def _check_tax_period(cls, v: str) -> str:
        """年度须为 4 位数字（非法输入 422，避免 int() 转换 500）。"""
        if not v or not re.fullmatch(r"\d{4}", v):
            raise ValueError("tax_period 须为 4 位年度数字（如 2026）")
        return v

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str | None) -> str | None:
        """季度须为 Q1-Q4（兼容裸数字与大小写；空串归一为 None=年度申报）。"""
        if v is None or v == "":
            return None
        if not re.fullmatch(r"[Qq]?[1-4]", v):
            raise ValueError("quarter 须为 Q1-Q4")
        return v

class TaxReturnResponse(BaseModel):
    id: int
    enterprise_id: int
    year: int
    quarter: int | None
    return_type: str
    status: str
    taxable_income: float
    tax_amount: float
    # A200000 第 1/2/3 行来源与适用优惠类型（页面=服务=数据库=导出一致）
    operating_revenue: float = 0.0
    other_income: float = 0.0
    operating_cost: float = 0.0
    total_expenses: float = 0.0
    total_profit: float = 0.0
    tax_adjustments: float = 0.0
    tax_preferential: float = 0.0
    tax_type: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

def calculate_tax(
    return_data: dict,
    enterprise: Enterprise | None = None,
    include_adjustments: bool = False,
) -> dict:
    """按企业所得税引擎口径计算税额（单一税率来源，禁止本地税率阶梯）。

    BUGfix TAX-01：原实现用 5%/10%/25% 自造阶梯落库——10% 档法定不存在，
    且与 /calculate 的引擎展示口径不一致，保存后税额静默变错。
    现在统一复用引擎判定：
    - 小型微利（≤300 万所得、≤300 人、≤5000 万资产、非限制行业）→ 5%
    - 高新技术（企业档案 is_high_tech）→ 15%
    - 两者同时符合 → 必须显式选择其一（不得叠加，台账 L-056），否则中文 400
    - 其余 → 25%；所得 ≤ 0 不缴税
    企业从业人数/资产总额缺失时不臆断小微，按标准税率并在 note 说明。

    季度预缴（A 类）不纳入纳税调整（复核 I-1）：A200000 没有调整行次，
    调整留到年度汇算。`include_adjustments` 只在年度记录（B 类）下为 True；
    预缴记录的调整额仍落库留痕，但不改变应纳税所得额与税额。

    单位契约（P0 修复）：入参来自前端表单与 TaxReturnCreate，单位为**万元**；
    落库与 A200000 导出单位为**元**（申报表口径）。旧实现把万元入参当元再除以
    10000，导致小微误判（1000 万被判成 0.1 万、税率从 25% 变 5%）与税额量级
    错误（导出少 1 万倍）。此处只在边界换算一次。
    """
    from ..services.corporate_income_tax_calculator import (
        CorporateIncomeTaxCalculator,
        EnterpriseTaxType,
    )

    taxable_income_wan = float(
        return_data.get("total_revenue", 0)
        + return_data.get("other_income", 0)
        - return_data.get("total_costs", 0)
        - return_data.get("total_expenses", 0)
        + (return_data.get("tax_adjustments", 0) if include_adjustments else 0)
        - return_data.get("tax_preferential", 0)
    )
    taxable_income = money(taxable_income_wan * 10000.0)
    if taxable_income_wan <= 0:
        return {
            "taxable_income": taxable_income,
            "tax_rate": 0.0,
            "tax_amount": 0.0,
            "tax_type": EnterpriseTaxType.STANDARD.value,
            "note": "应纳税所得额为零或负数，无需缴纳企业所得税",
        }

    note = ""
    tax_type = EnterpriseTaxType.STANDARD
    is_high_tech = bool(getattr(enterprise, "is_high_tech", False))
    if (
        enterprise is not None
        and enterprise.employee_count is not None
        and enterprise.total_assets is not None
    ):
        small_micro = CorporateIncomeTaxCalculator.check_small_micro_enterprise(
            employee_count=int(enterprise.employee_count),
            total_assets_wan=float(enterprise.total_assets),
            taxable_income_wan=taxable_income_wan,
            is_restricted_industry=bool(enterprise.is_restricted_industry),
            industry_name=enterprise.industry_type or "",
        )
        tax_type = CorporateIncomeTaxCalculator.determine_enterprise_tax_type(
            is_high_tech=is_high_tech,
            small_micro_result=small_micro,
            preferential_choice=str(return_data.get("preferential_choice") or ""),
        )
    elif is_high_tech:
        tax_type = EnterpriseTaxType.HIGH_TECH
    else:
        note = "企业从业人数/资产总额未填写，无法判定小型微利，已按标准税率计算"

    # 小微优惠走政策有效期校验（过期/未生效时抛中文 ValueError，由路由转 400）
    CorporateIncomeTaxCalculator.calculate_tax_amount(
        taxable_income_wan=taxable_income_wan,
        enterprise_tax_type=tax_type,
        tax_year=return_data.get("tax_period") or None,
    )
    rate = CorporateIncomeTaxCalculator.get_applicable_tax_rate(tax_type)
    return {
        "taxable_income": taxable_income,
        "tax_rate": rate,
        "tax_amount": money(taxable_income * rate),
        "tax_type": tax_type.value,
        "note": note,
    }

@router.get("/", response_model=List[TaxReturnResponse])
async def get_tax_returns(
    enterprise_id: int | None = None,
    skip: int = Query(0, ge=0, description="跳过记录数"),
    limit: int = Query(100, ge=1, le=200, description="返回记录数上限"),
    db: Session = Depends(get_db)
):
    """获取纳税申报列表（固定按 id 倒序，分页顺序稳定）"""
    query = db.query(TaxReturn)
    if enterprise_id:
        query = query.filter(TaxReturn.enterprise_id == enterprise_id)

    returns = query.order_by(TaxReturn.id.desc()).offset(skip).limit(limit).all()
    return returns

@router.get("/{return_id}", response_model=TaxReturnResponse)
async def get_tax_return(return_id: int, db: Session = Depends(get_db)):
    """获取单个纳税申报详情"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    return tax_return

def _quarter_of(tax_return: TaxReturnCreate) -> int | None:
    """quarter (str "Q1") -> 库字段 int 1-4；空/非法归 None（年度申报）。"""
    if not tax_return.quarter:
        return None
    try:
        return int(tax_return.quarter.upper().replace("Q", ""))
    except (ValueError, TypeError):
        return None


def _resolve_period(tax_return: TaxReturnCreate) -> tuple[str, int | None]:
    """解析申报类型与季度（复核 I-2）。

    A 类预缴必须有季度：缺季度会让记录永远导不出 A200000（不可导出成功状态），
    因此创建与更正都直接 400，不静默补默认季度（尤其不得默认 Q4）。
    年度记录按类型归一为无季度——年度申报是整年口径，季度字段无意义。
    """
    return_type = "预缴" if tax_return.report_type == "A类" else "年度"
    quarter = _quarter_of(tax_return)
    if return_type == "预缴" and quarter is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "A类预缴申报必须指定申报季度（Q1-Q4）：A200000 是季度预缴表，"
                "缺季度的记录无法导出，请选择季度后重新保存"
            ),
        )
    if return_type == "年度":
        quarter = None
    return return_type, quarter


def _apply_return_inputs(db_return: TaxReturn, tax_return: TaxReturnCreate, tax_calc: dict) -> None:
    """把表单输入与计算结果写入申报记录（创建与更正共用同一口径）。"""
    db_return.taxable_income = tax_calc["taxable_income"]
    db_return.tax_amount = tax_calc["tax_amount"]
    db_return.tax_type = tax_calc.get("tax_type", "")
    db_return.operating_revenue = money(float(tax_return.total_revenue or 0) * 10000.0)
    db_return.other_income = money(float(tax_return.other_income or 0) * 10000.0)
    db_return.operating_cost = money(float(tax_return.total_costs or 0) * 10000.0)
    db_return.total_expenses = money(float(tax_return.total_expenses or 0) * 10000.0)
    db_return.total_profit = money(
        (float(tax_return.total_revenue or 0) + float(tax_return.other_income or 0)
         - float(tax_return.total_costs or 0) - float(tax_return.total_expenses or 0)) * 10000.0
    )
    db_return.tax_adjustments = money(float(tax_return.tax_adjustments or 0) * 10000.0)
    db_return.tax_preferential = money(float(tax_return.tax_preferential or 0) * 10000.0)


@router.post("/", response_model=TaxReturnResponse)
async def create_tax_return(
    tax_return: TaxReturnCreate,
    db: Session = Depends(get_db)
):
    """创建纳税申报"""
    # 验证企业是否存在
    enterprise = db.query(Enterprise).filter(Enterprise.id == tax_return.enterprise_id).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")

    # tax_period (str "2024") -> year (int)；A 类必须有季度（复核 I-2）
    year = int(tax_return.tax_period) if tax_return.tax_period else 2024
    return_type, quarter = _resolve_period(tax_return)

    # 计算税额（复用引擎口径；政策过期/未生效时引擎抛中文 ValueError）
    # 季度预缴不纳入纳税调整（复核 I-1），调整只落库留痕、留到年度汇算
    return_data = tax_return.model_dump()
    try:
        tax_calc = calculate_tax(
            return_data, enterprise, include_adjustments=(return_type == "年度")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 同一企业同一年度/季度/类型只允许一条申报记录（BUGfix TAX-17）：
    # 旧实现每次 POST 都新建 draft，重复提交产生多条无法删除的脏数据。
    dup = (db.query(TaxReturn)
           .filter(TaxReturn.enterprise_id == tax_return.enterprise_id)
           .filter(TaxReturn.year == year)
           .filter(TaxReturn.quarter == quarter)
           .filter(TaxReturn.return_type == return_type).first())
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该企业 {year} 年{'Q' + str(quarter) if quarter else '年度'} "
                f"{return_type}申报已存在（id={dup.id}）：请在申报记录列表点「更正」修改该记录"
                f"（PUT /tax-returns/{dup.id}），或删除该草稿后重新保存"
                if dup.status != "submitted" else
                f"该企业 {year} 年{'Q' + str(quarter) if quarter else '年度'} "
                f"{return_type}申报已提交（id={dup.id}），已提交记录不可修改或删除；"
                f"如需更正请联系主管税务机关办理更正申报"
            ),
        )

    db_return = TaxReturn(
        enterprise_id=tax_return.enterprise_id,
        year=year,
        quarter=quarter,
        return_type=return_type,
        status="draft",
    )
    _apply_return_inputs(db_return, tax_return, tax_calc)

    db.add(db_return)
    db.commit()
    db.refresh(db_return)
    return db_return


@router.put("/{return_id}", response_model=TaxReturnResponse)
async def update_tax_return(
    return_id: int,
    tax_return: TaxReturnCreate,
    db: Session = Depends(get_db)
):
    """更正草稿申报记录（草稿可更正：重算税额并覆盖金额字段；已提交记录受保护）。"""
    db_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not db_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    if db_return.status == "submitted":
        raise HTTPException(
            status_code=409,
            detail="该申报已提交，不能更正或删除；如需变更请先联系主管税务机关办理更正申报",
        )
    if tax_return.enterprise_id != db_return.enterprise_id:
        raise HTTPException(
            status_code=400,
            detail="更正不允许更换企业：如需为其他企业申报，请新建一条申报记录",
        )

    enterprise = db.query(Enterprise).filter(Enterprise.id == tax_return.enterprise_id).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")

    year = int(tax_return.tax_period) if tax_return.tax_period else db_return.year
    # 更正同样要求 A 类带季度（复核 I-2）：历史缺季度记录靠这里补季度后即可导出
    return_type, quarter = _resolve_period(tax_return)

    try:
        tax_calc = calculate_tax(
            tax_return.model_dump(), enterprise, include_adjustments=(return_type == "年度")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 更正后不得与另一条记录撞「企业+年度+季度+类型」唯一口径
    dup = (db.query(TaxReturn)
           .filter(TaxReturn.id != return_id)
           .filter(TaxReturn.enterprise_id == tax_return.enterprise_id)
           .filter(TaxReturn.year == year)
           .filter(TaxReturn.quarter == quarter)
           .filter(TaxReturn.return_type == return_type).first())
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=f"该企业 {year} 年{'Q' + str(quarter) if quarter else '年度'} "
                   f"{return_type}申报已存在（id={dup.id}），不能把本记录改成同期间同类型",
        )

    db_return.enterprise_id = tax_return.enterprise_id
    db_return.year = year
    db_return.quarter = quarter
    db_return.return_type = return_type
    _apply_return_inputs(db_return, tax_return, tax_calc)
    db.commit()
    db.refresh(db_return)
    return db_return


@router.delete("/{return_id}")
async def delete_tax_return(return_id: int, db: Session = Depends(get_db)):
    """删除草稿申报记录（已提交记录受保护，避免抹掉留痕）。"""
    db_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not db_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    if db_return.status == "submitted":
        raise HTTPException(
            status_code=409,
            detail="该申报已提交，不能删除；如需变更请先联系主管税务机关办理更正申报",
        )
    db.delete(db_return)
    db.commit()
    return {"ok": True, "deleted_id": return_id}


@router.put("/{return_id}/submit")
async def submit_tax_return(return_id: int, db: Session = Depends(get_db)):
    """提交纳税申报"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    
    tax_return.status = "submitted"
    db.commit()
    return {"message": "申报已提交", "status": "submitted"}


# ============================================================
# 详细计算API
# ============================================================

class CalculationRequest(BaseModel):
    """计算请求体"""
    enterprise_id: int
    revenue: float = 0.0              # 营业收入（万元）
    other_income: float = 0.0         # 其他收入（万元）
    total_costs: float = 0.0          # 营业成本（万元）
    total_expenses: float = 0.0       # 期间费用（万元）
    total_salary: float = 0.0         # 工资薪金总额（万元）
    # 纳税调整项
    business_entertainment: float = 0.0   # 业务招待费（万元）
    advertising_expense: float = 0.0      # 广告费（万元）
    donation_expense: float = 0.0         # 公益性捐赠（万元）
    rnd_expense: float = 0.0              # 研发费用（万元）
    welfare_expense: float = 0.0          # 职工福利费（万元）
    education_expense: float = 0.0        # 职工教育经费（万元）
    union_expense: float = 0.0            # 工会经费（万元）
    penalties: float = 0.0                # 罚款滞纳金（万元）
    equipment_purchase: float = 0.0       # 设备购置（万元）
    equipment_depreciation: float = 0.0   # 设备当年已提折旧（万元）：一次性扣除调减＝购置额−折旧
    # 企业信息
    employee_count: int = 0
    total_assets: float = 0.0             # 资产总额（万元）
    industry: str = "一般行业"
    is_restricted_industry: bool = False
    # 兼容字段：高新资格以企业档案为准，本字段仅为旧前端报文兼容，服务端忽略（复核 M-1）
    is_high_tech: bool = False
    enterprise_type: str = "一般企业"
    advertising_industry: str = "一般行业"
    # 高新与小微同时符合时的显式选择（high_tech / small_micro），不选则 400
    preferential_choice: str = ""
    # 所属年度（可选）：传入时按 12号公告校验小微优惠有效期（Part B）
    tax_year: str | int = ""
    # 兼容字段：前端历史版本提交的是 year，二者取其一（P1 修复）
    year: str | int = ""
    # 以前年度亏损
    prior_year_losses: dict = {}          # {year: amount}


class CalculationResultResponse(BaseModel):
    """计算结果响应"""
    success: bool
    enterprise_name: str = ""
    # 企业类型判定
    is_small_micro: bool = False
    is_high_tech: bool = False
    enterprise_tax_type: str = ""
    applicable_tax_rate: float = 0.0
    small_micro_details: dict = {}
    # 核心财务数据
    revenue_wan: float = 0.0
    other_income_wan: float = 0.0
    total_costs_wan: float = 0.0
    total_expenses_wan: float = 0.0
    accounting_profit_wan: float = 0.0
    total_salary_wan: float = 0.0
    # 纳税调整
    adjustments_detail: dict = {}
    total_adjustment_increase_wan: float = 0.0
    total_adjustment_decrease_wan: float = 0.0
    net_adjustment_wan: float = 0.0
    # 弥补亏损
    loss_carry_forward_used_wan: float = 0.0
    loss_used_by_year: dict = {}
    # 税额计算
    taxable_income_before_loss_wan: float = 0.0
    taxable_income_after_loss_wan: float = 0.0
    tax_amount_wan: float = 0.0
    tax_reason: str = ""
    # 季度预缴口径（复核 N-2）：A200000 不含纳税调整，与保存/导出同口径；
    # 上面 taxable_income_*/tax_amount_wan 是「年度测算（含调整）」，两个口径都要可见。
    prepay_taxable_income_wan: float = 0.0
    prepay_tax_amount_wan: float = 0.0
    prepay_tax_type: str = ""
    prepay_note: str = ""
    # 优惠政策有效期状态（valid / 空=未校验）
    policy_status: str = ""
    # 计算步骤
    calculation_steps: list = []
    # 税收优惠
    preferential_policies: list = []


def build_calculation_result(calc_request: CalculationRequest, enterprise: Enterprise, calc_result) -> dict:
    """构建前端可用的计算结果响应"""
    from ..services.corporate_income_tax_calculator import EnterpriseTaxType

    # 构建计算步骤
    steps = []
    steps.append({
        "step": 1,
        "title": "计算利润总额",
        "formula": "利润总额 = 营业收入 + 其他收入 - 营业成本 - 期间费用",
        "values": {
            "营业收入": f"{calc_request.revenue:.2f}万元",
            "其他收入": f"{calc_request.other_income:.2f}万元",
            "营业成本": f"{calc_request.total_costs:.2f}万元",
            "期间费用": f"{calc_request.total_expenses:.2f}万元",
        },
        "result": f"利润总额 = {calc_result.accounting_profit:.2f}万元",
    })

    # 纳税调整步骤
    adj_items = []
    total_increase = 0.0
    total_decrease = 0.0
    for key, detail in calc_result.adjustments_detail.items():
        increase = detail.get("tax_adjustment_increase_wan", 0) or 0
        decrease = detail.get("tax_adjustment_decrease_wan", 0) or 0
        if increase > 0 or decrease > 0:
            category = detail.get("category", key)
            if increase > 0:
                adj_items.append(f"{category}: 调增 {increase:.2f}万元")
                total_increase += increase
            if decrease > 0:
                adj_items.append(f"{category}: 调减 {decrease:.2f}万元")
                total_decrease += decrease

    if adj_items:
        steps.append({
            "step": 2,
            "title": "纳税调整",
            "formula": "调整后应纳税所得额 = 利润总额 + 纳税调增 - 纳税调减",
            "adjustments": adj_items,
            "result": f"纳税调增合计 {total_increase:.2f}万元，纳税调减合计 {total_decrease:.2f}万元",
        })

    steps.append({
        "step": 3,
        "title": "计算弥补亏损前应纳税所得额",
        "formula": "应纳税所得额 = 利润总额 + 纳税调增 - 纳税调减",
        "values": {
            "利润总额": f"{calc_result.accounting_profit:.2f}万元",
            "纳税调增": f"{total_increase:.2f}万元",
            "纳税调减": f"{total_decrease:.2f}万元",
        },
        "result": f"弥补亏损前应纳税所得额 = {calc_result.taxable_income_before_loss:.2f}万元",
    })

    # 小型微利企业判定步骤（按引擎返回的 conditions 结构取真实字段，台账 L-054）
    if calc_result.small_micro_details:
        sm = calc_result.small_micro_details
        conditions = sm.get("conditions") or {}
        criteria_met = []
        criteria_not_met = []

        def _judge(key: str, met_text: str, not_met_text: str) -> None:
            item = conditions.get(key) or {}
            if item.get("passed"):
                criteria_met.append(met_text)
            else:
                criteria_not_met.append(not_met_text)

        emp = conditions.get("employee_count") or {}
        _judge(
            "employee_count",
            f"从业人数 {emp.get('value', 0)}人 ≤ {emp.get('threshold', 300)}人 ✓",
            f"从业人数 {emp.get('value', 0)}人 > {emp.get('threshold', 300)}人 ✗",
        )
        assets = conditions.get("total_assets") or {}
        _judge(
            "total_assets",
            f"资产总额 {float(assets.get('value') or 0) / 10000:.2f}万元 ≤ "
            f"{float(assets.get('threshold') or 0) / 10000:.2f}万元 ✓",
            f"资产总额 {float(assets.get('value') or 0) / 10000:.2f}万元 > "
            f"{float(assets.get('threshold') or 0) / 10000:.2f}万元 ✗",
        )
        income = conditions.get("taxable_income") or {}
        _judge(
            "taxable_income",
            f"应纳税所得额 {float(income.get('value') or 0) / 10000:.2f}万元 ≤ "
            f"{float(income.get('threshold') or 0) / 10000:.2f}万元 ✓",
            f"应纳税所得额 {float(income.get('value') or 0) / 10000:.2f}万元 > "
            f"{float(income.get('threshold') or 0) / 10000:.2f}万元 ✗",
        )
        industry = conditions.get("industry") or {}
        _judge(
            "industry",
            "行业类型：非限制和禁止行业 ✓",
            f"行业类型：{industry.get('value') or '限制和禁止行业'}（限制和禁止行业）✗",
        )

        steps.append({
            "step": 4,
            "title": "小型微利企业判定",
            "criteria_met": criteria_met,
            "criteria_not_met": criteria_not_met,
            "result": f"判定结果：{'符合' if calc_result.is_small_micro else '不符合'}小型微利企业条件",
        })

    # 弥补亏损步骤
    if calc_result.loss_carry_forward_used > 0:
        steps.append({
            "step": 5,
            "title": "弥补以前年度亏损",
            "formula": "弥补亏损后应纳税所得额 = 弥补亏损前应纳税所得额 - 本期弥补亏损",
            "values": {
                "弥补亏损前应纳税所得额": f"{calc_result.taxable_income_before_loss:.2f}万元",
                "本期弥补亏损": f"{calc_result.loss_carry_forward_used:.2f}万元",
            },
            "result": f"弥补亏损后应纳税所得额 = {calc_result.taxable_income_after_loss:.2f}万元",
        })

    # 税额计算步骤
    rate_display = f"{calc_result.applicable_tax_rate * 100:.0f}%"
    if calc_result.enterprise_tax_type == EnterpriseTaxType.SMALL_MICRO:
        rate_display = "5%（实际税负）"
    elif calc_result.enterprise_tax_type == EnterpriseTaxType.HIGH_TECH:
        rate_display = "15%"

    steps.append({
        "step": 6,
        "title": "计算应纳税额",
        "formula": f"应纳税额 = 应纳税所得额 × 适用税率",
        "values": {
            "企业类型": calc_result.enterprise_tax_type.value,
            "适用税率": rate_display,
            "应纳税所得额": f"{calc_result.taxable_income_after_loss:.2f}万元",
        },
        "result": f"应纳税额 = {calc_result.tax_amount:.2f}万元",
    })

    # 构建税收优惠列表（同时符合两类优惠时，只有实际适用的那一项计入）
    preferential_policies = []
    if calc_result.enterprise_tax_type == EnterpriseTaxType.SMALL_MICRO:
        preferential_policies.append({
            "name": "小型微利企业所得税优惠",
            "description": "对小型微利企业减按25%计入应纳税所得额，按20%税率缴纳企业所得税，实际税负5%",
            "policy_basis": "《财政部 税务总局关于小微企业和个体工商户所得税优惠政策的公告》（财政部 税务总局公告2023年第12号）",
            "amount_wan": _round_wan(calc_result.taxable_income_after_loss * 0.20) if calc_result.taxable_income_after_loss > 0 else 0,
        })
    if calc_result.enterprise_tax_type == EnterpriseTaxType.HIGH_TECH:
        preferential_policies.append({
            "name": "高新技术企业优惠税率",
            "description": "经认定的高新技术企业减按15%税率征收企业所得税",
            "policy_basis": "《中华人民共和国企业所得税法》第二十八条",
            "amount_wan": _round_wan(calc_result.taxable_income_after_loss * 0.10) if calc_result.taxable_income_after_loss > 0 else 0,
        })
    # 研发费用加计扣除
    rd_detail = calc_result.adjustments_detail.get("rd_expense", {})
    if rd_detail and rd_detail.get("super_deduction_amount_wan", 0) > 0:
        preferential_policies.append({
            "name": "研发费用加计扣除",
            "description": f"研发费用在实际发生额基础上，加计扣除{rd_detail.get('deduction_rate', 0) * 100:.0f}%",
            "policy_basis": "《财政部 税务总局 科技部关于提高研究开发费用税前加计扣除比例的通知》（财税〔2018〕99号）",
            "amount_wan": rd_detail.get("super_deduction_amount_wan", 0),
        })
    # 设备一次性扣除
    eq_detail = calc_result.adjustments_detail.get("equipment_one_time", {})
    if eq_detail and eq_detail.get("eligible", False):
        preferential_policies.append({
            "name": "设备器具一次性扣除",
            "description": "单位价值不超过500万元的设备器具，允许一次性计入当期成本费用",
            "policy_basis": "《财政部 税务总局关于设备 器具扣除有关企业所得税政策的通知》（财税〔2018〕54号）",
            "amount_wan": eq_detail.get("one_time_deduction_wan", 0),
        })

    # 税额计算说明（含政策有效期状态：valid 时空串处理）
    tax_reason = ""
    if calc_result.enterprise_tax_type == EnterpriseTaxType.SMALL_MICRO:
        tax_reason = (
            f"小型微利企业优惠：应纳税所得额{calc_result.taxable_income_after_loss:.2f}万元 "
            f"× 25%（计入应纳税所得额比例）× 20%（优惠税率）= 实际税负5%"
        )
    elif calc_result.enterprise_tax_type == EnterpriseTaxType.HIGH_TECH:
        tax_reason = "高新技术企业优惠税率15%"
    else:
        tax_reason = f"一般企业所得税标准税率25%"
    # 两项优惠同时符合时，明确写出「不得叠加」与本次采用项
    if calc_result.is_small_micro and calc_result.enterprise_tax_type == EnterpriseTaxType.HIGH_TECH:
        tax_reason += "（该企业同时符合小型微利企业条件，两项优惠不得叠加，本次按所选高新技术企业计税）"
    elif bool(getattr(enterprise, "is_high_tech", False)) and calc_result.enterprise_tax_type == EnterpriseTaxType.SMALL_MICRO:
        tax_reason += "（该企业同时符合高新技术企业条件，两项优惠不得叠加，本次按所选小型微利企业计税）"

    return {
        "success": True,
        "enterprise_name": enterprise.name,
        "is_small_micro": calc_result.is_small_micro,
        "is_high_tech": calc_result.enterprise_tax_type == EnterpriseTaxType.HIGH_TECH,
        "enterprise_tax_type": calc_result.enterprise_tax_type.value,
        "applicable_tax_rate": calc_result.applicable_tax_rate,
        "small_micro_details": calc_result.small_micro_details,
        "revenue_wan": calc_request.revenue,
        "other_income_wan": calc_request.other_income,
        "total_costs_wan": calc_request.total_costs,
        "total_expenses_wan": calc_request.total_expenses,
        "accounting_profit_wan": _round_wan(calc_result.accounting_profit),
        "total_salary_wan": calc_request.total_salary,
        "adjustments_detail": calc_result.adjustments_detail,
        "total_adjustment_increase_wan": _round_wan(calc_result.total_adjustment_increase),
        "total_adjustment_decrease_wan": _round_wan(calc_result.total_adjustment_decrease),
        "net_adjustment_wan": _round_wan(
            calc_result.total_adjustment_increase - calc_result.total_adjustment_decrease
        ),
        "loss_carry_forward_used_wan": _round_wan(calc_result.loss_carry_forward_used),
        "loss_used_by_year": calc_result.loss_used_by_year,
        "taxable_income_before_loss_wan": _round_wan(calc_result.taxable_income_before_loss),
        "taxable_income_after_loss_wan": _round_wan(calc_result.taxable_income_after_loss),
        "tax_amount_wan": _round_wan(calc_result.tax_amount),
        "tax_reason": tax_reason,
        "policy_status": getattr(calc_result, "policy_status", ""),
        "calculation_steps": steps,
        "preferential_policies": preferential_policies,
    }


@router.post("/calculate", response_model=CalculationResultResponse)
async def calculate_tax_detail(
    request: CalculationRequest,
    db: Session = Depends(get_db),
):
    """
    执行详细的企业所得税计算。
    
    使用Python计算引擎进行完整的税额计算，包括：
    1. 小型微利企业判定
    2. 纳税调整项计算
    3. 应纳税所得额计算
    4. 弥补亏损计算
    5. 税额计算
    """
    # 验证企业是否存在
    enterprise = db.query(Enterprise).filter(Enterprise.id == request.enterprise_id).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")

    # 所属年度：前端历史字段名为 year，引擎字段为 tax_year，两者都接受。
    # 缺省时退回当前年度——旧实现缺省即跳过政策有效期校验，2028 年仍按小微 5%（P1 修复）。
    # 显式传参必须整串是 4 位数字：旧实现 str(...)[:4] 会把 "2026abc" 截成 2026 静默套政策（台账 L-061）。
    raw_year = request.tax_year or request.year or ""
    tax_year = str(raw_year).strip()
    if not tax_year:
        tax_year = str(datetime.now().year)
    elif not (tax_year.isdigit() and len(tax_year) == 4):
        raise HTTPException(
            status_code=400,
            detail=f"所属年度 {raw_year!r} 非法：须为 4 位年度数字（如 2026）",
        )

    try:
        from ..services.corporate_income_tax_calculator import (
            CorporateIncomeTaxCalculator,
            TaxAdjustmentInput,
            EnterpriseInfo,
            LossCarryForward,
        )
        from ..config.tax_rules import IndustryType

        # 构建企业息
        enterprise_info = EnterpriseInfo(
            employee_count=request.employee_count,
            total_assets=request.total_assets,
            industry=request.industry,
            is_restricted_industry=request.is_restricted_industry,
            # 高新资格以企业档案为准（复核 M-1）：请求体的 is_high_tech 只是历史兼容字段，
            # 不得让调用方通过报文临时「变成」高新企业（保存链读档案，页面必须同源）。
            is_high_tech=bool(getattr(enterprise, "is_high_tech", False)),
            enterprise_type=request.enterprise_type,
            advertising_industry=IndustryType(request.advertising_industry) if request.advertising_industry in ["一般企业", "化妆品制造或销售", "医药制造", "饮料制造", "烟草", "限制行业"] else IndustryType.GENERAL,
            tax_year=tax_year,
            preferential_choice=request.preferential_choice,
        )

        # 构建纳税调整项输入
        adjustments = TaxAdjustmentInput(
            business_entertainment=request.business_entertainment,
            advertising_expense=request.advertising_expense,
            donation_expense=request.donation_expense,
            rnd_expense=request.rnd_expense,
            welfare_expense=request.welfare_expense,
            education_expense=request.education_expense,
            union_expense=request.union_expense,
            penalties=request.penalties,
            equipment_purchase=request.equipment_purchase,
            equipment_depreciation=request.equipment_depreciation,
        )

        # 构建以前年度亏损信息
        losses = None
        if request.prior_year_losses:
            # 传入申报年度，按 5 年（高企 10 年）结转期限排除过期亏损（P1 修复）
            losses = LossCarryForward(
                losses_by_year=request.prior_year_losses,
                tax_year=int(tax_year),
            )

        # 计算利润总额
        accounting_profit = request.revenue + request.other_income - request.total_costs - request.total_expenses

        # 执行完整计算
        calc_result = CorporateIncomeTaxCalculator.calculate(
            accounting_profit_wan=accounting_profit,
            revenue_wan=request.revenue,
            adjustments=adjustments,
            total_salary_wan=request.total_salary,
            enterprise_info=enterprise_info,
            losses=losses,
        )

        # 季度预缴口径（复核 N-2）：A200000 不纳入纳税调整，与保存/导出同一函数计算，
        # 保证页面展示的预缴税额 == 保存记录的 tax_amount（而不是含调整的年度测算数）。
        prepay = calculate_tax(
            {
                "total_revenue": request.revenue,
                "other_income": request.other_income,
                "total_costs": request.total_costs,
                "total_expenses": request.total_expenses,
                "tax_adjustments": 0,
                "tax_preferential": 0,
                "preferential_choice": request.preferential_choice,
                "tax_period": tax_year,
            },
            enterprise,
            include_adjustments=False,
        )

        result = build_calculation_result(request, enterprise, calc_result)
        result.update({
            "prepay_taxable_income_wan": _round_wan(prepay["taxable_income"] / 10000.0),
            "prepay_tax_amount_wan": _round_wan(prepay["tax_amount"] / 10000.0),
            "prepay_tax_type": prepay["tax_type"],
            "prepay_note": (
                "季度预缴按 A200000 第 8 行实际利润额计税，不含纳税调整（调整留待年度汇算），"
                "与保存的申报记录及导出表一致；上方所得额/税额为年度测算口径（含调整）"
            ),
        })
        return result

    except ValueError as e:
        # 政策有效期拒绝（小微/研发加计到期、双优惠未选择）等业务校验：400 中文提示
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        # 内部实现细节只进日志：对外固定中文，避免把路径/栈信息回传给页面（台账 L-060）
        logger.exception("企业所得税计算失败（企业 id=%s）", request.enterprise_id)
        raise HTTPException(status_code=500, detail="计算失败，请检查输入数据后重试；如持续失败请查看服务日志")


_ZERO_EPS = 0.005


def _build_a200000_form_data(tax_return: TaxReturn, enterprise: Enterprise) -> A200000FormData:
    """根据申报记录和企业信息构建A200000申报表数据。

    A200000 是季度预缴表，行次口径（复核 I-1 / I-2 / I-4）：
    - 第 1/2/3 行取落库的真实利润表数据，不得用应纳税所得额冒充利润总额；
    - 第 8 行**严格**等于 行3-行4-行5-行6（季度预缴不纳入纳税调整）；
    - 第 11 行由落库税额反推，第 14 行恒等于库 `tax_amount`（一分不差）；
    - 只允许「预缴」记录导出：年度记录按其类型拒绝（A100000 未接入）；
    - 预缴记录必须有季度；历史缺季度给出补季度更正路径，不静默按 Q4 处理。

    历史记录（本轮修复前落库）没有真实利润表或含已计列的纳税调整时，表内无法
    如实表达：利润字段全 0 且应纳税所得额非 0 → 提示补录；应纳税所得额高于
    利润总额减所得减免（正向调整）→ 提示按预缴口径更正。两者都不伪造行次。
    """
    return_type = (tax_return.return_type or "").strip()
    if return_type == "年度":
        raise ValueError(
            "年度申报记录不能用 A200000 季度预缴表导出：本系统尚未接入 A100000 年度申报表，"
            "请使用该企业对应季度的预缴记录导出"
        )
    if tax_return.quarter is None:
        raise ValueError(
            "该预缴记录缺少申报季度（历史数据）：A200000 是季度预缴表，"
            "请在申报记录列表点「更正」补填年度/季度后重新导出（本系统不会默认按 Q4 处理）"
        )

    revenue = money(tax_return.operating_revenue or 0.0)
    other_income = money(tax_return.other_income or 0.0)
    operating_cost = money(tax_return.operating_cost or 0.0)
    total_expenses = money(tax_return.total_expenses or 0.0)
    total_profit = money(tax_return.total_profit or 0.0)
    tax_preferential = money(tax_return.tax_preferential or 0.0)
    taxable_income = money(tax_return.taxable_income or 0.0)
    stored_tax = money(tax_return.tax_amount or 0.0)

    profit_fields = (revenue, other_income, operating_cost, total_expenses, total_profit)
    if (
        all(abs(v) < _ZERO_EPS for v in profit_fields)
        and abs(taxable_income) >= _ZERO_EPS
    ):
        raise ValueError(
            "该申报记录缺少利润表数据（历史记录只有应纳税所得额）：A200000 第 1/2/3 行无法填列，"
            "请在「更正」中补录营业收入、营业成本、期间费用后重新导出"
        )

    # 第 5 行只允许来自「所得减免」（tax_preferential）。历史记录的纳税调整无论正向
    # 还是负向，都不得伪装成第 5 行：A200000 没有调整行次，任何差额都必须让用户更正
    # （复核 N-1）。差额为零时表内严格自洽。
    expected_row8 = money(total_profit - tax_preferential)
    bridge = money(expected_row8 - taxable_income)
    if abs(bridge) > _ZERO_EPS:
        direction = "高于" if bridge < 0 else "低于"
        raise ValueError(
            f"该记录应纳税所得额 {taxable_income:,.2f} 元{direction}利润总额减所得减免后的 "
            f"{expected_row8:,.2f} 元（差额 {abs(bridge):,.2f} 元，原含纳税调整）："
            "A200000 季度预缴表无纳税调整行次，也不能把调整放进第 5 行所得减免；"
            "请点「更正」按季度预缴口径（实际利润额）重新计算后再导出，"
            "调整留待年度汇算清缴时申报"
        )

    # 历史行「应税所得为正、落库税额为 0」是旧 100 元舍入缺陷的产物（复核 I-3 前），
    # 直接出表会显示 100% 减免，属错误成功状态 → 要求更正重算。
    if taxable_income > _ZERO_EPS and stored_tax <= _ZERO_EPS:
        raise ValueError(
            f"该记录应纳税所得额 {taxable_income:,.2f} 元为正但落库税额为 0"
            "（历史舍入缺陷，导出会显示 100% 减免、与真实申报不符）："
            "请在「更正」中按当前口径重新计算后再导出"
        )

    # 优先使用落库的 tax_type（页面=服务=库=导出同一口径）；历史行按企业属性回退。
    # 税额不依赖该判定：第 11 行由落库税额反推（复核 I-4）。
    tax_type = (tax_return.tax_type or "").strip()
    if tax_type:
        is_small_micro = tax_type == "小型微利企业"
        is_high_tech = tax_type == "高新技术企业"
    else:
        is_small_micro = _is_small_micro(enterprise, taxable_income)
        is_high_tech = bool(getattr(enterprise, "is_high_tech", False)) and not is_small_micro

    enterprise_info = EnterpriseInfo(
        name=enterprise.name,
        tax_number=enterprise.tax_number,
        industry_type=enterprise.industry_type or "",
        total_assets=enterprise.total_assets or 0.0,
        employee_count=employee_count_or_zero(enterprise),
        is_small_micro=is_small_micro,
        is_high_tech=is_high_tech,
    )

    form_data = A200000FormData(
        enterprise=enterprise_info,
        tax_period=TaxPeriod(year=tax_return.year, quarter=tax_return.quarter),
        operating_revenue=revenue,
        operating_cost=operating_cost,
        total_profit=total_profit,
        # 第 5 行只取落库的所得减免；这里 bridge 已保证为 0（否则上面已 409）
        income_reduction_amount=tax_preferential,
        prior_year_loss=0.0,
        actual_paid_tax=0.0,
        specific_prepaid_tax=0.0,
        prior_year_overpaid=0.0,
        stored_tax_amount=stored_tax,
        # 纳税调整不计入季度预缴，只在「计算方式」列说明（调整留待年度汇算）
        prepay_excluded_adjustment=money(tax_return.tax_adjustments or 0.0),
    )

    # 行次 8/10/11/14/16 统一由 calculate() 计算；第 11 行由落库税额反推，
    # 因此第 14 行与库 `tax_amount` 同源同值。
    form_data.calculate()
    return form_data


def employee_count_or_zero(enterprise: Enterprise) -> int:
    return enterprise.employee_count or 0


def _is_small_micro(enterprise: Enterprise, taxable_income_yuan: float = 0.0) -> bool:
    """判断是否为小型微利企业（复用引擎 criteria，含 300 万所得与限制行业）。

    BUGfix TAX-02：原实现只看人数/资产，任意规模企业都会被判小微并按 5% 出表。
    字段缺失时按「无法判定」处理（不臆断小微）。
    """
    if not enterprise:
        return False
    if enterprise.employee_count is None or enterprise.total_assets is None:
        return False
    from ..services.corporate_income_tax_calculator import CorporateIncomeTaxCalculator

    result = CorporateIncomeTaxCalculator.check_small_micro_enterprise(
        employee_count=int(enterprise.employee_count),
        total_assets_wan=float(enterprise.total_assets),
        taxable_income_wan=float(taxable_income_yuan or 0.0) / 10000.0,
        is_restricted_industry=bool(enterprise.is_restricted_industry),
        industry_name=enterprise.industry_type or "",
    )
    return bool(result.get("qualified"))


def _export_filename(enterprise: Enterprise, tax_return: TaxReturn, suffix: str) -> str:
    """导出文件名：A200000 是季度预缴表，年度记录在导出入口已被 409 拒绝。"""
    return f"A200000_{enterprise.name}_{tax_return.year}年Q{tax_return.quarter}.{suffix}"


def _download_headers(filename: str) -> dict:
    """Content-Disposition：ASCII 回退文件名 + RFC5987 UTF-8 编码（中文文件名直接拼接会触发 latin-1 编码崩溃）。

    回退名只保留可打印 ASCII（32–126）并剔除引号/反斜杠：CR/LF 等控制字符本身是
    ASCII 不会被 encode("ascii", "ignore") 丢弃，直接进响应头会造成头注入。
    """
    ascii_fallback = "".join(
        ch for ch in filename if 32 <= ord(ch) <= 126 and ch not in '"\\'
    ).strip()
    if not ascii_fallback:
        suffix = filename.rsplit(".", 1)[-1]
        suffix = "".join(ch for ch in suffix if 32 <= ord(ch) <= 126 and ch.isalnum()) or "xlsx"
        ascii_fallback = f"A200000.{suffix}"
    return {
        "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
    }


@router.get("/{return_id}/export/excel")
async def export_tax_return_excel(return_id: int, db: Session = Depends(get_db)):
    """导出预缴申报表为 Excel（年度记录 409：A100000 未接入）"""
    form_data, enterprise, tax_return = _form_data_for_export(return_id, db)

    excel_bytes = export_a200000_excel(form_data)

    filename = _export_filename(enterprise, tax_return, "xlsx")

    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename)
    )


@router.get("/{return_id}/export/pdf")
async def export_tax_return_pdf(return_id: int, db: Session = Depends(get_db)):
    """导出预缴申报表为 PDF（年度记录 409：A100000 未接入）"""
    form_data, enterprise, tax_return = _form_data_for_export(return_id, db)

    pdf_bytes = export_a200000_pdf(form_data)

    filename = _export_filename(enterprise, tax_return, "pdf")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers=_download_headers(filename)
    )


def _form_data_for_export(return_id: int, db: Session):
    """导出前置校验：记录/企业 404，年度记录 409，返回 (表单数据, 企业, 记录)。"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")

    enterprise = db.query(Enterprise).filter(Enterprise.id == tax_return.enterprise_id).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")

    try:
        form_data = _build_a200000_form_data(tax_return, enterprise)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return form_data, enterprise, tax_return
