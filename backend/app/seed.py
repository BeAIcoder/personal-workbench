"""示例数据：仅在数据库为空时写入（首次启动或 python -m app.init_db --reset）。"""
from datetime import datetime, timedelta

from .models import Note, Schedule, Task


def seed_if_empty(db) -> dict | None:
    """数据库为空时写入示例数据，返回各表写入数量；已有数据则返回 None。"""
    if db.query(Task).count() or db.query(Schedule).count() or db.query(Note).count():
        return None

    now = datetime.now()

    def day(offset: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
        return (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=second, microsecond=0)

    tasks = [
        Task(title="整理本月增值税进项发票并认证", description="从电子税务局导出进项明细，逐张核对发票信息后完成认证。", status="in_progress", priority="urgent", due_date=day(1, 17, 0), category="税务申报"),
        Task(title="编写第三季度财务分析报告", description="含收入、成本、费用、现金流四个板块，输出同比环比分析。", status="todo", priority="high", due_date=day(5, 18, 0), category="财务分析"),
        Task(title="核对银行对账单与账面余额", description="重点核对未达账项，编制银行余额调节表。", status="todo", priority="medium", due_date=day(-1, 12, 0), category="日常核算"),
        Task(title="提交员工差旅费报销单审核", description="9 月份累计 12 张报销单待审。", status="done", priority="medium", category="报销审核", completed_at=day(-2, 16, 30)),
        Task(title="月末结账：计提折旧与摊销", description="固定资产折旧、待摊费用摊销，更新台账。", status="done", priority="high", category="月末结账", completed_at=day(-1, 10, 0)),
        Task(title="更新客户应收账款账龄表", description="按 30/60/90/180 天分段统计，标记超期客户。", status="todo", priority="medium", due_date=day(3, 17, 0), category="往来管理"),
        Task(title="准备税务稽查资料清单", description="按稽查通知书要求整理近两年凭证、合同、申报表。", status="todo", priority="urgent", due_date=day(10, 17, 0), category="税务申报"),
        Task(title="审核新员工工资核算表", description="核对社保公积金基数与个税专项附加扣除。", status="done", priority="high", category="薪酬核算", completed_at=day(-3, 15, 0)),
        Task(title="归档上季度会计凭证", description="打印装订凭证，扫描电子档并备份。", status="todo", priority="low", due_date=day(14, 17, 0), category="档案管理"),
        Task(title="学习数电发票新规线上培训", description="税务总局直播课，约 2 小时。", status="in_progress", priority="low", due_date=day(7, 20, 0), category="学习提升"),
    ]

    schedules = [
        Schedule(title="月度经营分析会议", description="汇报上月财务数据与预算执行情况。", location="三楼会议室", start_time=day(0, 9, 30), end_time=day(0, 11, 0), color="#409EFF"),
        Schedule(title="与银行客户经理沟通授信续期", description="带上近六个月流水与纳税记录。", location="银行城东支行", start_time=day(0, 14, 0), end_time=day(0, 15, 0), color="#E6A23C"),
        Schedule(title="团队周例会", description="同步本周工作安排。", location="财务部办公室", start_time=day(1, 10, 0), end_time=day(1, 11, 30), color="#409EFF"),
        Schedule(title="季度税务申报培训", description="线上直播课，重点讲数电票新规。", location="线上", start_time=day(2, 15, 0), end_time=day(2, 17, 0), color="#67C23A"),
        Schedule(title="财务软件升级验收", description="与软件商确认凭证接口改造效果。", location="机房", start_time=day(3, 13, 30), end_time=day(3, 15, 0), color="#909399"),
        Schedule(title="审计师进场沟通会", description="年审计划沟通，准备资料清单。", location="五楼大会议室", start_time=day(5, 9, 0), end_time=day(5, 11, 30), color="#F56C6C"),
        Schedule(title="年度健康体检", description="空腹，早上不要进食。", location="市第一人民医院体检中心", start_time=day(7, 0, 0), end_time=day(7, 23, 59, 59), all_day=True, color="#9C27B0"),
    ]

    notes = [
        Note(title="月末结账流程清单", pinned=True, tags=["结账", "流程"], content=(
            "每月 1-5 日完成上月结账，按以下顺序执行：\n"
            "1. 收票：催收各部门未报销发票，检查进项发票是否全部认证\n"
            "2. 银行：下载对账单，编制银行余额调节表\n"
            "3. 往来：核对应收应付明细，清理长期挂账\n"
            "4. 计提：折旧、摊销、工资、社保、税金及附加\n"
            "5. 结转：损益结转，生成总账、明细账\n"
            "6. 报表：资产负债表、利润表勾稽核对\n"
            "7. 归档：凭证打印装订，电子档备份"
        )),
        Note(title="常用税费申报期限速查表", pinned=True, tags=["税务", "速查"], content=(
            "增值税：一般纳税人每月 15 日前（遇节假日顺延）\n"
            "企业所得税：季度终了 15 日内预缴；年度汇算清缴 5 月 31 日前\n"
            "个人所得税：次月 15 日前\n"
            "印花税：按季申报，季度终了 15 日内\n"
            "社保公积金：每月 15 日前\n"
            "提示：每月月初先确认数电发票剩余授信额度。"
        )),
        Note(title="发票报销审核要点", tags=["报销", "发票"], content=(
            "1. 发票抬头、税号是否正确\n"
            "2. 数电票需在电子税务局查验真伪\n"
            "3. 连号发票关注是否拆分报销\n"
            "4. 业务附件齐全：审批单、合同、行程单\n"
            "5. 超标准部分需部门负责人特批"
        )),
        Note(title="数电发票常见问题记录", tags=["发票", "数电"], content=(
            "- 授信额度不足：可在电子税务局申请调整\n"
            "- 红字发票：对方已入账的需对方确认后才能开具\n"
            "- 铁路电子客票可作为进项抵扣凭证\n"
            "- 同一购买方信息可在开票模块维护成常用信息"
        )),
        Note(title="财务常用 Excel 函数技巧", tags=["Excel", "技巧"], content=(
            "- XLOOKUP / VLOOKUP：银行流水与账面记录匹配\n"
            "- SUMIFS：科目 + 月份多条件求和\n"
            "- EOMONTH：计算月末日期，配合账龄表使用\n"
            "- 数据透视表：科目余额表快速汇总分析"
        )),
    ]

    db.add_all(tasks)
    db.add_all(schedules)
    db.add_all(notes)
    db.commit()
    return {"tasks": len(tasks), "schedules": len(schedules), "notes": len(notes)}
