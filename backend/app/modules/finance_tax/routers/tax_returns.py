"""
纳税申报路由
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from ....database import get_db
from ..models import TaxReturn, TaxAdjustment, Enterprise
from pydantic import BaseModel
from datetime import datetime
from fastapi.responses import StreamingResponse
import io

from ..services.tax_form_generator import (
    EnterpriseInfo,
    TaxPeriod,
    A200000FormData,
    export_a200000_excel,
    export_a200000_pdf,
)

router = APIRouter(prefix="/tax-returns", tags=["纳税申报"])

# Pydantic模型
class TaxAdjustmentCreate(BaseModel):
    item_name: str
    adjustment_type: str
    amount: float = 0.0
    reason: str | None = None

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
    adjustments: List[TaxAdjustmentCreate] = []

class TaxReturnResponse(BaseModel):
    id: int
    enterprise_id: int
    year: int
    quarter: int | None
    return_type: str
    status: str
    taxable_income: float
    tax_amount: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

def calculate_tax(return_data: dict) -> dict:
    """计算税额"""
    # 应纳税所得额 = 营业收入 + 其他收入 - 营业成本 - 期间费用 + 纳税调整 - 税收优惠
    taxable_income = (
        return_data.get("total_revenue", 0) +
        return_data.get("other_income", 0) -
        return_data.get("total_costs", 0) -
        return_data.get("total_expenses", 0) +
        return_data.get("tax_adjustments", 0) -
        return_data.get("tax_preferential", 0)
    )
    
    # 确定税率（小微企业优惠）
    if taxable_income <= 3000000:  # 300万以下
        tax_rate = 0.05  # 实际税率5%
    elif taxable_income <= 10000000:  # 1000万以下
        tax_rate = 0.10  # 实际税率10%
    else:
        tax_rate = 0.25  # 标准税率25%
    
    tax_amount = taxable_income * tax_rate
    
    return {
        "taxable_income": max(0, taxable_income),
        "tax_rate": tax_rate,
        "tax_amount": max(0, tax_amount)
    }

@router.get("/", response_model=List[TaxReturnResponse])
async def get_tax_returns(
    enterprise_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取纳税申报列表"""
    query = db.query(TaxReturn)
    if enterprise_id:
        query = query.filter(TaxReturn.enterprise_id == enterprise_id)
    
    returns = query.offset(skip).limit(limit).all()
    return returns

@router.get("/{return_id}", response_model=TaxReturnResponse)
async def get_tax_return(return_id: int, db: Session = Depends(get_db)):
    """获取单个纳税申报详情"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    return tax_return

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

    # 计算税额
    return_data = tax_return.model_dump()
    tax_calc = calculate_tax(return_data)

    # 将Pydantic字段映射到SQLAlchemy模型字段
    # tax_period (str "2024") -> year (int)
    year = int(tax_return.tax_period) if tax_return.tax_period else 2024
    # quarter (str "Q1") -> quarter (int 1-4)
    quarter = None
    if tax_return.quarter:
        q_str = tax_return.quarter.upper().replace("Q", "")
        try:
            quarter = int(q_str)
        except (ValueError, TypeError):
            quarter = None
    # report_type (str "A类") -> return_type (str "预缴"/"年度")
    return_type = "预缴" if tax_return.report_type == "A类" else "年度"

    # 创建申报记录（只使用SQLAlchemy模型中存在的字段）
    # tax_calc返回taxable_income、tax_rate、tax_amount，但TaxReturn模型只有
    # taxable_income和tax_amount，需要过滤tax_rate
    db_return = TaxReturn(
        enterprise_id=tax_return.enterprise_id,
        year=year,
        quarter=quarter,
        return_type=return_type,
        status="draft",
        taxable_income=tax_calc["taxable_income"],
        tax_amount=tax_calc["tax_amount"],
    )

    db.add(db_return)
    db.commit()
    db.refresh(db_return)
    return db_return

@router.put("/{return_id}/submit")
async def submit_tax_return(return_id: int, db: Session = Depends(get_db)):
    """提交纳税申报"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    
    tax_return.status = "submitted"
    db.commit()
    return {"message": "申报已提交", "status": "submitted"}

@router.get("/{return_id}/adjustments")
async def get_adjustments(return_id: int, db: Session = Depends(get_db)):
    """获取纳税调整明细"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")

    # TaxAdjustment关联的是FinancialData而非TaxReturn
    # 返回空列表（如需完整关联关系需调整数据模型）
    return []


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
    # 企业信息
    employee_count: int = 0
    total_assets: float = 0.0             # 资产总额（万元）
    industry: str = "一般行业"
    is_restricted_industry: bool = False
    is_high_tech: bool = False
    enterprise_type: str = "一般企业"
    advertising_industry: str = "一般行业"
    # 以前年度亏损
    prior_year_losses: dict = {}          # {year: amount}


class AdjustmentDetailItem(BaseModel):
    """调整项明细"""
    category: str
    expense_amount_wan: Optional[float] = None
    revenue_wan: Optional[float] = None
    limit_by_60_percent_wan: Optional[float] = None
    limit_by_revenue_5_per_mille_wan: Optional[float] = None
    allowed_deduction_wan: Optional[float] = None
    tax_adjustment_increase_wan: Optional[float] = None
    limit_12_percent_wan: Optional[float] = None
    donation_amount_wan: Optional[float] = None
    accounting_profit_wan: Optional[float] = None
    limit_14_percent_wan: Optional[float] = None
    total_salary_wan: Optional[float] = None
    limit_8_percent_wan: Optional[float] = None
    carry_forward_wan: Optional[float] = None
    limit_2_percent_wan: Optional[float] = None
    actual_rd_expense_wan: Optional[float] = None
    enterprise_type: Optional[str] = None
    deduction_rate: Optional[float] = None
    super_deduction_amount_wan: Optional[float] = None
    total_deduction_before_tax_wan: Optional[float] = None
    tax_adjustment_decrease_wan: Optional[float] = None
    industry: Optional[str] = None
    equipment_purchase_wan: Optional[float] = None
    threshold_wan: Optional[float] = None
    eligible: Optional[bool] = None
    one_time_deduction_wan: Optional[float] = None
    amount_wan: Optional[float] = None
    reason: Optional[str] = None


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

    # 小型微利企业判定步骤
    if calc_result.small_micro_details:
        sm = calc_result.small_micro_details
        criteria_met = []
        criteria_not_met = []
        if sm.get("employee_count_qualified"):
            criteria_met.append(f"从业人数 {sm.get('employee_count', 0)}人 ≤ {sm.get('employee_limit', 300)}人 ✓")
        else:
            criteria_not_met.append(f"从业人数 {sm.get('employee_count', 0)}人 > {sm.get('employee_limit', 300)}人 ✗")
        if sm.get("assets_qualified"):
            criteria_met.append(f"资产总额 {sm.get('total_assets_display', '')} ≤ 5000万元 ✓")
        else:
            criteria_not_met.append(f"资产总额 {sm.get('total_assets_display', '')} > 5000万元 ✗")
        if sm.get("taxable_income_qualified"):
            criteria_met.append(f"应纳税所得额 {sm.get('taxable_income_display', '')} ≤ 300万元 ✓")
        else:
            criteria_not_met.append(f"应纳税所得额 {sm.get('taxable_income_display', '')} > 300万元 ✗")
        if sm.get("industry_qualified"):
            criteria_met.append("行业类型：非限制和禁止行业 ✓")
        else:
            criteria_not_met.append("行业类型：限制和禁止行业 ✗")

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

    # 构建税收优惠列表
    preferential_policies = []
    if calc_result.is_small_micro:
        preferential_policies.append({
            "name": "小型微利企业所得税优惠",
            "description": "对小型微利企业减按25%计入应纳税所得额，按20%税率缴纳企业所得税，实际税负5%",
            "policy_basis": "《财政部 税务总局关于小微企业和个体工商户所得税优惠政策的公告》（财政部 税务总局公告2023年第6号）",
            "amount_wan": round(calc_result.taxable_income_after_loss * 0.20, 2) if calc_result.taxable_income_after_loss > 0 else 0,
        })
    if calc_result.enterprise_tax_type == EnterpriseTaxType.HIGH_TECH:
        preferential_policies.append({
            "name": "高新技术企业优惠税率",
            "description": "经认定的高新技术企业减按15%税率征收企业所得税",
            "policy_basis": "《中华人民共和国企业所得税法》第二十八条",
            "amount_wan": round(calc_result.taxable_income_after_loss * 0.10, 2) if calc_result.taxable_income_after_loss > 0 else 0,
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

    # 税额计算说明
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
        "accounting_profit_wan": calc_result.accounting_profit,
        "total_salary_wan": calc_request.total_salary,
        "adjustments_detail": calc_result.adjustments_detail,
        "total_adjustment_increase_wan": calc_result.total_adjustment_increase,
        "total_adjustment_decrease_wan": calc_result.total_adjustment_decrease,
        "net_adjustment_wan": round(calc_result.total_adjustment_increase - calc_result.total_adjustment_decrease, 2),
        "loss_carry_forward_used_wan": calc_result.loss_carry_forward_used,
        "loss_used_by_year": calc_result.loss_carry_forward_remaining,
        "taxable_income_before_loss_wan": calc_result.taxable_income_before_loss,
        "taxable_income_after_loss_wan": calc_result.taxable_income_after_loss,
        "tax_amount_wan": calc_result.tax_amount,
        "tax_reason": tax_reason,
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
            is_high_tech=request.is_high_tech,
            enterprise_type=request.enterprise_type,
            advertising_industry=IndustryType(request.advertising_industry) if request.advertising_industry in ["一般企业", "化妆品制造或销售", "医药制造", "饮料制造", "烟草", "限制行业"] else IndustryType.GENERAL,
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
        )

        # 构建以前年度亏损信息
        losses = None
        if request.prior_year_losses:
            losses = LossCarryForward(losses_by_year=request.prior_year_losses)

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

        return build_calculation_result(request, enterprise, calc_result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"计算失败：{str(e)}")


def _build_a200000_form_data(tax_return: TaxReturn, enterprise: Enterprise) -> A200000FormData:
    """根据申报记录和企业信息构建A200000申报表数据"""
    enterprise_info = EnterpriseInfo(
        name=enterprise.name,
        tax_number=enterprise.tax_number,
        industry_type=enterprise.industry_type or "",
        total_assets=enterprise.total_assets or 0.0,
        employee_count=employee_count_or_zero(enterprise),
        is_small_micro=_is_small_micro(enterprise),
    )
    
    # 数据库模型中 quarter 是 int 类型 (1-4)，year 是 int
    quarter = tax_return.quarter if tax_return.quarter else 4
    
    tax_period = TaxPeriod(
        year=tax_return.year,
        quarter=quarter,
    )
    
    form_data = A200000FormData(
        enterprise=enterprise_info,
        tax_period=tax_period,
        operating_revenue=0.0,
        operating_cost=0.0,
        total_profit=tax_return.taxable_income or 0.0,
        prior_year_loss=0.0,
        actual_paid_tax=0.0,
        specific_prepaid_tax=0.0,
        prior_year_overpaid=0.0,
    )
    
    # 设置税率
    if enterprise_info.is_small_micro:
        form_data.tax_rate = 0.25
        form_data.small_micro_reduction = form_data.total_profit * 0.20
    else:
        form_data.tax_rate = 0.25
    
    form_data.tax_payable = form_data.actual_profit * form_data.tax_rate
    form_data.tax_reduction = form_data.small_micro_reduction
    form_data.tax_to_pay_or_refund = form_data.tax_payable - form_data.tax_reduction
    form_data.final_tax_to_pay = form_data.tax_to_pay_or_refund
    
    return form_data


def employee_count_or_zero(enterprise: Enterprise) -> int:
    return enterprise.employee_count or 0


def _is_small_micro(enterprise: Enterprise) -> bool:
    """判断是否为小型微利企业"""
    if not enterprise:
        return False
    # 从业人数不超过300人，资产总额不超过5000万元
    if (enterprise.employee_count or 0) > 300:
        return False
    if (enterprise.total_assets or 0) > 5000:
        return False
    return True


@router.get("/{return_id}/export/excel")
async def export_tax_return_excel(return_id: int, db: Session = Depends(get_db)):
    """导出申报表为Excel文件"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    
    enterprise = db.query(Enterprise).filter(Enterprise.id == tax_return.enterprise_id).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")
    
    form_data = _build_a200000_form_data(tax_return, enterprise)
    
    excel_bytes = export_a200000_excel(form_data)
    
    filename = f"A200000_{enterprise.name}_{tax_return.tax_period}.xlsx"
    
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{return_id}/export/pdf")
async def export_tax_return_pdf(return_id: int, db: Session = Depends(get_db)):
    """导出申报表为PDF文件"""
    tax_return = db.query(TaxReturn).filter(TaxReturn.id == return_id).first()
    if not tax_return:
        raise HTTPException(status_code=404, detail="申报记录不存在")
    
    enterprise = db.query(Enterprise).filter(Enterprise.id == tax_return.enterprise_id).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")
    
    form_data = _build_a200000_form_data(tax_return, enterprise)
    
    pdf_bytes = export_a200000_pdf(form_data)
    
    filename = f"A200000_{enterprise.name}_{tax_return.tax_period}.pdf"
    
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
