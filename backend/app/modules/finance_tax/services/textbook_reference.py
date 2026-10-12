"""
教材考纲对照服务
整理《初级会计实务》《中级会计实务》《税法（二）》所得税相关知识点，
并与系统功能进行对照映射。
"""
from typing import List, Dict, Optional
from pydantic import BaseModel


class KnowledgePoint(BaseModel):
    """知识点模型"""
    id: str
    title: str
    description: str
    ability_requirement: str  # 掌握/熟悉/了解
    sub_points: List[str] = []
    system_mapping: Optional[str] = None  # 对应系统功能模块
    calculation_formula: Optional[str] = None
    tax_policy_reference: Optional[str] = None


class TextbookChapter(BaseModel):
    """教材章节模型"""
    textbook_name: str
    chapter: str
    chapter_number: str
    ability_overview: str
    knowledge_points: List[KnowledgePoint]
    key_difficulties: List[str] = []
    exam_frequency: str = ""  # 考试频率：高频/中频/低频


class SystemFunctionMapping(BaseModel):
    """系统功能映射"""
    feature_name: str
    feature_path: str
    description: str
    related_knowledge_points: List[str] = []
    calculation_logic: str


# ============================================================
# 《初级会计实务》所得税费用知识点
# ============================================================

PRIMARY_ACCOUNTING_KNOWLEDGE = TextbookChapter(
    textbook_name="初级会计实务",
    chapter="所得税费用",
    chapter_number="第五章",
    ability_overview="掌握所得税费用的计算方法，能够独立完成企业所得税的基本核算",
    knowledge_points=[
        KnowledgePoint(
            id="primary-001",
            title="所得税费用计算方法",
            description="企业所得税采用资产负债表债务法进行核算。所得税费用由当期所得税和递延所得税两部分组成。",
            ability_requirement="掌握",
            sub_points=[
                "所得税费用 = 当期所得税 + 递延所得税费用（或 - 递延所得税收益）",
                "当期所得税 = 应纳税所得额 × 适用税率",
                "递延所得税 = 递延所得税负债 - 递延所得税资产",
            ],
            system_mapping="企业所得税计算引擎",
            calculation_formula="所得税费用 = 当期所得税 + 递延所得税",
        ),
        KnowledgePoint(
            id="primary-002",
            title="应纳税所得额计算",
            description="应纳税所得额是在会计利润基础上，按照税法规定进行纳税调整后得出的。",
            ability_requirement="掌握",
            sub_points=[
                "应纳税所得额 = 利润总额 + 纳税调整增加额 - 纳税调减少额 - 弥补以前年度亏损",
                "利润总额 = 营业收入 - 营业成本 - 税金及附加 - 期间费用 + 其他收益",
                "纳税调整包括永久性差异和暂时性差异",
            ],
            system_mapping="纳税调整计算模块",
            calculation_formula="应纳税所得额 = 利润总额 + 调增项 - 调减项 - 弥补亏损",
        ),
        KnowledgePoint(
            id="primary-003",
            title="纳税调整项",
            description="常见的纳税调整项目及其计算规则。",
            ability_requirement="掌握",
            sub_points=[
                "业务招待费：按发生额60%扣除，最高不超过当年销售（营业）收入的5‰",
                "广告费和业务宣传费：一般企业不超过当年销售（营业）收入15%",
                "公益性捐赠：不超过年度利润总额12%的部分准予扣除",
                "职工福利费：不超过工资薪金总额14%",
                "职工教育经费：不超过工资薪金总额8%",
                "工会经费：不超过工资薪金总额2%",
                "罚款、滞纳金：不得税前扣除",
            ],
            system_mapping="纳税调整项明细计算",
            tax_policy_reference="《企业所得税法》及其实施条例",
        ),
        KnowledgePoint(
            id="primary-004",
            title="小型微利企业优惠",
            description="符合条件的小型微利企业享受企业所得税优惠。",
            ability_requirement="熟悉",
            sub_points=[
                "从业人数不超过300人",
                "资产总额不超过5000万元",
                "应纳税所得额不超过300万元",
                "减按25%计入应纳税所得额，按20%税率缴纳，实际税负5%",
            ],
            system_mapping="小型微利企业判定模块",
            calculation_formula="应纳税额 = 应纳税所得额 × 25% × 20% = 应纳税所得额 × 5%",
        ),
        KnowledgePoint(
            id="primary-005",
            title="税率与税额计算",
            description="企业所得税税率及计算。",
            ability_requirement="掌握",
            sub_points=[
                "基本税率：25%",
                "小型微利企业：实际税负5%",
                "高新技术企业：15%",
                "非居民企业：20%（实际10%）",
            ],
            system_mapping="税额计算引擎",
            calculation_formula="应纳税额 = 应纳税所得额 × 适用税率",
        ),
    ],
    key_difficulties=[
        "纳税调整项的计算与限额判定",
        "小型微利企业条件的综合判定",
    ],
    exam_frequency="高频",
)


# ============================================================
# 《中级会计实务》第十七章所得税知识点
# ============================================================

INTERMEDIATE_ACCOUNTING_KNOWLEDGE = TextbookChapter(
    textbook_name="中级会计实务",
    chapter="所得税",
    chapter_number="第十七章",
    ability_overview="深入理解计税基础与暂时性差异，掌握递延所得税资产/负债的确认与计量",
    knowledge_points=[
        KnowledgePoint(
            id="intermediate-001",
            title="计税基础与暂时性差异",
            description="资产的计税基础是指企业收回资产账面价值过程中，计算应纳税所得额时按照税法规定可以自应税经济利益中抵扣的金额。",
            ability_requirement="掌握",
            sub_points=[
                "资产计税基础 = 未来可税前扣除的金额",
                "负债计税基础 = 账面价值 - 未来可税前扣除的金额",
                "应纳税暂时性差异：资产账面价值 > 计税基础，或负债账面价值 < 计税基础",
                "可抵扣暂时性差异：资产账面价值 < 计税基础，或负债账面价值 > 计税基础",
            ],
            system_mapping="递延所得税计算模块",
            calculation_formula="暂时性差异 = 账面价值 - 计税基础",
        ),
        KnowledgePoint(
            id="intermediate-002",
            title="递延所得税资产",
            description="根据可抵扣暂时性差异确认递延所得税资产。",
            ability_requirement="掌握",
            sub_points=[
                "递延所得税资产 = 可抵扣暂时性差异 × 适用税率",
                "确认条件：未来期间很可能取得足够的应纳税所得额",
                "需要复核账面价值，如不再满足条件应减记",
            ],
            system_mapping="递延所得税资产计算",
            calculation_formula="递延所得税资产 = 可抵扣暂时性差异 × 税率",
        ),
        KnowledgePoint(
            id="intermediate-003",
            title="递延所得税负债",
            description="根据应纳税暂时性差异确认递延所得税负债。",
            ability_requirement="掌握",
            sub_points=[
                "递延所得税负债 = 应纳税暂时性差异 × 适用税率",
                "除特定交易外，应确认所有应纳税暂时性差异产生的递延所得税负债",
                "不确认的情形：商誉的初始确认、非企业合并且不影响会计利润和应纳税所得额的交易",
            ],
            system_mapping="递延所得税负债计算",
            calculation_formula="递延所得税负债 = 应纳税暂时性差异 × 税率",
        ),
        KnowledgePoint(
            id="intermediate-004",
            title="所得税费用计算",
            description="所得税费用 = 当期所得税 + 递延所得税",
            ability_requirement="掌握",
            sub_points=[
                "当期所得税 = 应纳税所得额 × 当期适用税率",
                "递延所得税 = （期末递延所得税负债 - 期初递延所得税负债）-（期末递延所得税资产 - 期初递延所得税资产）",
                "计入当期损益的所得税费用不包括计入所有者权益的交易",
            ],
            system_mapping="综合所得税计算",
            calculation_formula="所得税费用 = 当期所得税 + 递延所得税负债变动 - 递延所得税资产变动",
        ),
        KnowledgePoint(
            id="intermediate-005",
            title="亏损结转",
            description="企业发生的亏损可以向后结转，用以后年度的所得弥补。",
            ability_requirement="熟悉",
            sub_points=[
                "一般企业：亏损结转年限为5年",
                "高新技术企业和科技型中小企业：结转年限延长至10年",
                "结转期间连续计算，不得中断",
                "弥补亏损以应纳税所得额为限",
            ],
            system_mapping="亏损结转计算模块",
            tax_policy_reference="《关于延长高新技术企业和科技型中小企业亏损结转年限的通知》（财税〔2018〕76号）",
        ),
        KnowledgePoint(
            id="intermediate-006",
            title="特殊交易的所得税处理",
            description="企业合并、股权激励等特殊交易的所得税会计处理。",
            ability_requirement="了解",
            sub_points=[
                "企业合并中取得的资产和负债按公允价值确认，产生暂时性差异",
                "股份支付形成的费用在会计和税法确认时间不同",
                "超额亏损的所得税处理",
            ],
            system_mapping="暂不支持",
        ),
    ],
    key_difficulties=[
        "计税基础的确定与暂时性差异的判断",
        "递延所得税资产/负债的确认条件与计量",
        "亏损结转的年限判定与计算",
    ],
    exam_frequency="高频",
)


# ============================================================
# 《税法（二）》企业所得税知识点
# ============================================================

TAX_LAW_KNOWLEDGE = TextbookChapter(
    textbook_name="税法（二）",
    chapter="企业所得税",
    chapter_number="第一章",
    ability_overview="全面掌握企业所得税法律制度，包括纳税人、税率、应纳税所得额计算、税收优惠等",
    knowledge_points=[
        KnowledgePoint(
            id="tax-001",
            title="纳税人与征税范围",
            description="企业所得税的纳税人分为居民企业和非居民企业。",
            ability_requirement="掌握",
            sub_points=[
                "居民企业：依法在中国境内成立，或实际管理机构在中国境内的企业",
                "非居民企业：依外国法律成立且实际管理机构不在中国境内，但在中国境内设立机构场所的企业",
                "居民企业就其来源于中国境内外的所得缴纳企业所得税",
                "非居民企业就其来源于中国境内的所得缴纳企业所得税",
            ],
            system_mapping="企业信息录入模块",
            tax_policy_reference="《企业所得税法》第一条、第二条",
        ),
        KnowledgePoint(
            id="tax-002",
            title="税率与优惠政策",
            description="企业所得税税率体系及优惠政策。",
            ability_requirement="掌握",
            sub_points=[
                "基本税率：25%",
                "小型微利企业：减按25%计入应纳税所得额，20%税率（实际5%）",
                "高新技术企业：15%",
                "西部大开发鼓励类企业：15%",
                "技术先进型服务企业：15%",
                "非居民企业预提所得税：10%",
            ],
            system_mapping="税率计算与优惠判定模块",
            calculation_formula="实际应纳税额 = 应纳税所得额 × 优惠税率",
        ),
        KnowledgePoint(
            id="tax-003",
            title="应纳税所得额计算",
            description="企业所得税应纳税所得额的计算方法和各项扣除标准。",
            ability_requirement="掌握",
            sub_points=[
                "直接法：应纳税所得额 = 收入总额 - 不征税收入 - 免税收入 - 各项扣除 - 允许弥补的以前年度亏损",
                "间接法：应纳税所得额 = 利润总额 + 纳税调整增加额 - 纳税调整减少额",
                "收入总额包括销售货物收入、提供劳务收入、转让财产收入等九类",
                "不征税收入：财政拨款、行政事业性收费、政府性基金等",
                "免税收入：国债利息收入、符合条件的股息红利等",
            ],
            system_mapping="财务数据录入与纳税调整模块",
            calculation_formula="应纳税所得额 = 收入总额 - 不征税收入 - 免税收入 - 扣除项目 - 亏损弥补",
        ),
        KnowledgePoint(
            id="tax-004",
            title="扣除项目标准",
            description="各项费用支出的税前扣除标准。",
            ability_requirement="掌握",
            sub_points=[
                "工资薪金：合理工资薪金全额扣除",
                "职工福利费：工资薪金总额14%以内",
                "工会经费：工资薪金总额2%以内",
                "职工教育经费：工资薪金总额8%以内，超过部分可结转",
                "业务招待费：发生额60%且不超过销售收入5‰",
                "广告费和业务宣传费：销售收入15%以内（特殊行业30%）",
                "公益性捐赠：利润总额12%以内",
                "研发费用：100%加计扣除",
                "固定资产折旧：按税法规定年限和方法",
            ],
            system_mapping="纳税调整项计算模块",
        ),
        KnowledgePoint(
            id="tax-005",
            title="税收优惠",
            description="企业所得税各项税收优惠政策。",
            ability_requirement="熟悉",
            sub_points=[
                "免税收入：国债利息、符合条件的居民企业间股息红利",
                "减计收入：综合利用资源生产产品收入减按90%计入",
                "加计扣除：研发费用100%加计扣除",
                "加速折旧：特定固定资产可加速折旧或一次性扣除",
                "税额抵免：专用设备投资额的10%抵免税额",
                "小型微利企业优惠",
                "高新技术企业优惠",
                "西部大开发税收优惠",
            ],
            system_mapping="税收优惠计算模块",
        ),
        KnowledgePoint(
            id="tax-006",
            title="最新政策变化（2025年4月-2026年3月）",
            description="近期企业所得税相关政策变化汇总。",
            ability_requirement="熟悉",
            sub_points=[
                "小型微利企业所得税优惠政策延续：2027年12月31日前，年应纳税所得额不超过300万元的部分，减按25%计入，20%税率",
                "研发费用加计扣除比例保持100%：制造业、科技型中小企业研发费用加计扣除比例维持100%",
                "设备器具一次性扣除政策延续：单位价值不超过500万元的设备器具可一次性税前扣除",
                "增值税小规模纳税人减免政策与企业所得税联动调整",
                "个体工商户经营所得减半征收政策延续",
            ],
            system_mapping="政策更新与优惠判定模块",
            tax_policy_reference="财政部 税务总局公告2023年第6号、2024年相关政策延续公告",
        ),
        KnowledgePoint(
            id="tax-007",
            title="亏损结转",
            description="企业亏损结转弥补的年限和计算规则。",
            ability_requirement="掌握",
            sub_points=[
                "一般企业：5年结转年限",
                "高新技术企业和科技型中小企业：10年结转年限",
                "受疫情影响困难行业企业（如餐饮、旅游）：8年结转年限",
                "结转期间连续计算，不得中断",
                "境外亏损不得用境内所得弥补",
            ],
            system_mapping="亏损结转计算模块",
            calculation_formula="弥补亏损后应纳税所得额 = 弥补前所得 - 可弥补亏损额",
        ),
        KnowledgePoint(
            id="tax-008",
            title="特别纳税调整",
            description="关联企业间转让定价和反避税规定。",
            ability_requirement="了解",
            sub_points=[
                "关联方交易应遵循独立交易原则",
                "转让定价方法：可比非受控价格法、再销售价格法、成本加成法等",
                "预约定价安排",
                "受控外国企业规则",
                "一般反避税条款",
            ],
            system_mapping="暂不支持",
        ),
    ],
    key_difficulties=[
        "各项扣除限额的计算与比较",
        "税收优惠政策的适用条件判定",
        "亏损结转年限的分类判定",
    ],
    exam_frequency="高频",
)


# ============================================================
# 系统功能映射
# ============================================================

SYSTEM_FUNCTION_MAPPINGS: List[SystemFunctionMapping] = [
    SystemFunctionMapping(
        feature_name="企业所得税计算引擎",
        feature_path="/tax-return",
        description="根据企业财务数据自动计算企业所得税，包括小型微利企业判定、税率适用、税额计算等",
        related_knowledge_points=["primary-001", "primary-005", "intermediate-004", "tax-002"],
        calculation_logic="应纳税所得额 × 适用税率 = 应纳税额",
    ),
    SystemFunctionMapping(
        feature_name="纳税调整计算",
        feature_path="/tax-adjustment-form",
        description="自动计算各项纳税调整项，包括业务招待费、广告费、捐赠等限额扣除",
        related_knowledge_points=["primary-003", "tax-004"],
        calculation_logic="各项费用按税法规定限额计算，超限部分调增应纳税所得额",
    ),
    SystemFunctionMapping(
        feature_name="小型微利企业判定",
        feature_path="/enterprise-form",
        description="根据从业人数、资产总额、应纳税所得额等条件自动判定是否符合小型微利企业条件",
        related_knowledge_points=["primary-004", "tax-002"],
        calculation_logic="从业人数≤300人 ∧ 资产总额≤5000万 ∧ 应纳税所得额≤300万 ∧ 非限制行业",
    ),
    SystemFunctionMapping(
        feature_name="亏损结转计算",
        feature_path="/tax-return",
        description="自动计算以前年度亏损结转弥补，支持不同年限判定",
        related_knowledge_points=["intermediate-005", "tax-007"],
        calculation_logic="按年限优先顺序弥补亏损，高新技术/科技型10年，一般企业5年",
    ),
    SystemFunctionMapping(
        feature_name="递延所得税计算",
        feature_path="/tax-return",
        description="根据暂时性差异计算递延所得税资产和负债",
        related_knowledge_points=["intermediate-001", "intermediate-002", "intermediate-003"],
        calculation_logic="递延所得税 = 暂时性差异 × 适用税率",
    ),
    SystemFunctionMapping(
        feature_name="财务数据导入",
        feature_path="/financial-data-form",
        description="支持财务报表数据导入，自动提取营业收入、成本、费用等关键数据",
        related_knowledge_points=["primary-002", "tax-003"],
        calculation_logic="从资产负债表、利润表提取数据，计算利润总额",
    ),
    SystemFunctionMapping(
        feature_name="申报表生成",
        feature_path="/tax-return",
        description="根据计算结果自动生成企业所得税申报表（A200000等）",
        related_knowledge_points=["primary-001", "tax-003"],
        calculation_logic="将计算结果填入申报表对应栏次",
    ),
]


# ============================================================
# 服务函数
# ============================================================

def get_textbook_references() -> List[TextbookChapter]:
    """获取所有教材考纲参考数据"""
    return [
        PRIMARY_ACCOUNTING_KNOWLEDGE,
        INTERMEDIATE_ACCOUNTING_KNOWLEDGE,
        TAX_LAW_KNOWLEDGE,
    ]


def get_textbook_by_name(textbook_name: str) -> Optional[TextbookChapter]:
    """根据教材名称获取考纲参考数据"""
    textbooks = get_textbook_references()
    for textbook in textbooks:
        if textbook.textbook_name == textbook_name:
            return textbook
    return None


def get_system_mappings() -> List[SystemFunctionMapping]:
    """获取系统功能映射列表"""
    return SYSTEM_FUNCTION_MAPPINGS


def get_knowledge_point_by_id(kp_id: str) -> Optional[KnowledgePoint]:
    """根据知识点ID获取详情"""
    textbooks = get_textbook_references()
    for textbook in textbooks:
        for kp in textbook.knowledge_points:
            if kp.id == kp_id:
                return kp
    return None
