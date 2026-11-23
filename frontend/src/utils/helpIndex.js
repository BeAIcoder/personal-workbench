// 帮助索引：侧栏全部可见菜单 + 隐藏示例模块 → 《使用说明》章节。
// heading 必须与 使用说明.md 的 `### ` 标题逐字一致（heading == `${section} ${sectionTitle}`），
// 章节号用于在帮助页定位、复制后在手册里 Ctrl+F 搜索。手册是唯一权威，本文件只做导航。
export const MANUAL_FILE = '使用说明.md'

function help(section, sectionTitle, title, path, summary, extra = {}) {
  return {
    section,
    sectionTitle,
    heading: `${section} ${sectionTitle}`,
    title,
    path,
    summary,
    ...extra,
  }
}

export const HELP_GROUPS = [
  {
    name: '基础页面',
    items: [
      help('2.1', '工作台概览', '工作台概览', '/', '一页看清今天要做的事：任务、日程、笔记与经营提醒聚合。'),
      help('2.2', '任务管理', '任务管理', '/tasks', '记录、跟踪、完成日常待办，支持状态/优先级筛选与排序。'),
      help('2.3', '日程管理', '日程管理', '/schedules', '按日历安排会议、跟进与提醒，按天查看日程。'),
      help('2.4', '笔记管理', '笔记管理', '/notes', '沉淀流程、纪要与 AI 简报，内容同时进入知识检索。'),
      help('3.1', 'AI 助手（对话页）', 'AI 助手', '/assistant', '多专家对话、会话管理、模型与工作模式切换。'),
    ],
  },
  {
    name: 'AI 配置',
    items: [
      help('4.1', '知识与记忆', '知识与记忆', '/knowledge', '制度/手册检索（答案带出处）与跨会话长期记忆。'),
      help('4.2', 'MCP 工具', 'MCP 工具', '/mcp', '给 AI 接上外部工具服务器。'),
      help('4.3', '技能包管理', '技能包管理', '/skill-packs', '用 SKILL.md 沉淀标准做法，让专家按流程执行。'),
      help('4.4', '专家团文件', '专家团文件', '/expert-packs', '专家定义的导入、导出与备份。'),
    ],
  },
  {
    name: '定时任务',
    items: [
      help('5.1', '定时任务', '定时任务', '/jobs', '到点自动执行 Agent 问题或模块动作，可回看运行结果。'),
    ],
  },
  {
    name: '财务',
    items: [
      help('6.1', '数据池清洗', '数据池清洗', '/finance/vault', 'xlsx/csv 入池、列画像、配方清洗、整合分析与只读 SQL。'),
    ],
    subgroups: [
      {
        name: '账务处理',
        items: [
          help('6.2', '费用分类', '费用分类', '/finance/expense', '银行辅助明细账自动归类，生成费用汇总与待复核清单。'),
          help('6.3', '发票处理', '发票处理', '/finance/invoice', '数电发票解析、按项目归类、重命名、清单与 ZIP 打包。'),
          help('6.4', 'POS 对账', 'POS 对账', '/finance/pos', 'SHOP 对账单、明细账、认领记录、凭证列表四源对拍；含刷卡凭证合并（§6.4.1）。'),
          help('6.5', '银行流水模板', '银行流水模板', '/finance/bankflow', '建行/中行流水转 BIP「银行对账单」导入模板。'),
          help('6.16', '银行回单整理', '银行回单整理', '/finance/receipt', '按账期扫描建行/平安/中行回单，逐笔复核五类口径后，按银行账户分册生成 A4 PDF 与清单。'),
          help('6.17', '商户核销对账', '商户核销对账', '/finance/recon', '核销/认领/欠费/账单四源导入、合同范围勾选、勾稽检查、匹配与问题人工复核，导出 xlsx/Markdown/Word 对账报告。'),
          help('6.18', '租赁核算', '租赁核算', '/finance/lease', '承租人使用权资产/租赁负债初始与正常后续计量：合同与付款计划、测算与期间表、三组勾稽校验、五页签底稿导出，以及 BIP 科目余额只读对账（映射/方向/覆盖 unknown）。'),
          help('6.19', '长期待摊', '长期待摊', '/finance/deferred', '租入资产改良支出的直线摊销：净额/月摊/末期尾差披露、超租赁期风险确认、历史快照与三页签底稿。'),
          help('6.20', '工会经费计提', '工会经费计提', '/finance/union', '手工工资基数 + 批次费率的工会经费计提：本期计提、上期正负差异、本期建议入账，缺输入 unknown、确认只读与三页签底稿。'),
        ],
      },
      {
        name: '记账',
        items: [
          help('6.6', '个人记账', '个人记账', '/finance/ledger-personal', '个人/家庭日常收支、月度统计与预算执行。'),
          help('6.7', '企业记账（BIP）', '企业记账（BIP）', '/finance/ledger-bip', 'BIP 凭证、辅助明细账、辅助余额表导入、查询与入池。'),
        ],
      },
      {
        name: '税务填报',
        items: [
          help('6.8', '企业所得税', '企业所得税', '/finance/tax', '企业档案、税额测算、申报记录与 A200000 预缴表。'),
          help('6.9', '印花税填报', '印花税填报', '/finance/stamp-duty', '13 类税目计税、六税两费减半与申报底稿。'),
          help('6.10', '增值税填报', '增值税填报', '/finance/vat', '一般纳税人/小规模双模式计税与申报底稿。'),
          help('6.11', '个人所得税填报', '个人所得税填报', '/finance/iit', '工资薪金累计预扣法逐月模拟与扣缴底稿。'),
          help('6.12', '残保金填报', '残保金填报', '/finance/dsf', '按在职人数、工资总额、残疾职工人数与当地社平测算残保金。'),
        ],
      },
      {
        name: '报表分析',
        items: [
          help('6.13', '报表看板', '报表看板', '/finance/board', '一键刷新经营报表管线，内嵌最新看板并保留历史快照。'),
          help('6.14', '现金流填报', '现金流填报', '/finance/cashflow', '把源数据回填到现金流量滚动预测模板并输出报告。'),
        ],
      },
    ],
  },
  {
    name: '经营',
    items: [
      help('7.1', '总经理经营聚合', '总经理经营聚合', '/business/gm', '按月只读汇总招商、营运、企划、法务、人事、工程物业六域。'),
      help('7.2', '招商管理', '招商管理', '/business/leasing', '铺位台账、招商线索、签约占用与跟进漏斗。'),
      help('7.3', '营运管理', '营运管理', '/business/operations', '合同台账、按期间出账、登记收款、账龄与工单。'),
      help('7.4', '企划管理', '企划管理', '/business/planning', '活动台账、预算明细、ROI 复盘、年度日历与供应商。'),
      help('7.5', '工程物业', '工程物业', '/business/facility', '设备一机一档、年检/消防/维保到期提醒与工单联动。'),
      help('7.6', '经营管理', '经营管理', '/business/manage', '四份经营管理报表导入、指标/明细补录与图表展示。'),
    ],
  },
  {
    name: '职能',
    items: [
      help('7.7', '法务管理', '法务管理', '/business/legal', '合同审查、诉讼仲裁案件、用印台账与风险看板。'),
      help('7.8', '行政人事', '行政人事', '/business/hr', '员工花名册、月度考勤、薪资核算与导出。'),
      help('7.9', '数据质量', '数据质量', '/business/dq', '七域×五维只读巡检，跨表勾稽与数据健康度。'),
    ],
  },
  {
    name: '调试',
    items: [
      help('8.1', '问题记录', '问题记录', '/debug/issues', '登记已知问题、修复状态与证据，形成闭环台账。'),
      help('8.2', '测试运行', '测试运行', '/debug/runs', '回看每轮验收运行、结果与失败证据。'),
      help('8.3', '前端报错', '前端报错', '/debug/errors', '自动收集页面脚本错误，便于排查。'),
    ],
  },
  {
    name: '开发样板',
    items: [
      help('2.0', '菜单地图（与侧栏一一对应）', '模块示例', '/finance/hello', '开发用模块示例，不进侧栏，仅保留直达路由；日常使用可忽略。', { hidden: true }),
    ],
  },
]

// 扁平索引（含 group/subgroup），供帮助页与页内 HelpLink 查找。
export const HELP_ENTRIES = HELP_GROUPS.flatMap((group) => [
  ...(group.items || []).map((item) => ({ ...item, group: group.name, subgroup: '' })),
  ...(group.subgroups || []).flatMap((sub) => sub.items.map((item) => ({ ...item, group: group.name, subgroup: sub.name }))),
])

export function helpEntryBySection(section) {
  const key = String(section || '').trim()
  if (!key) return null
  return HELP_ENTRIES.find((entry) => entry.section === key) || null
}

// 精确路径优先；详情子路由按最长菜单前缀匹配（如 /finance/expense/runs/1 → /finance/expense）。
// 根路径 `/` 只做精确匹配，否则所有路径都会被它命中。
export function helpEntryByPath(path) {
  const key = String(path || '').split('?')[0].split('#')[0]
  if (!key) return null
  const exact = HELP_ENTRIES.find((entry) => entry.path === key)
  if (exact) return exact
  let best = null
  for (const entry of HELP_ENTRIES) {
    if (entry.path === '/' || !key.startsWith(`${entry.path}/`)) continue
    if (!best || entry.path.length > best.path.length) best = entry
  }
  return best
}

// 复制到手册搜索框的定位串，如「§6.1 数据池清洗」。
export function manualLocator(entry) {
  if (!entry) return ''
  return `§${entry.section} ${entry.sectionTitle}`
}

export function countHelpEntries() {
  return HELP_ENTRIES.length
}
