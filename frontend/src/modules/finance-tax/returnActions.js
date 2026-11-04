// 企业所得税申报记录的界面动作判定与表单换算（纯函数，供视图与 Vitest 共用）。
// 口径：草稿可更正/删除，已提交受保护；A200000 是季度预缴表，年度记录不可导出。

export function canEdit(row) {
  return Boolean(row) && row.status !== 'submitted'
}

export function canDelete(row) {
  return canEdit(row)
}

// 只有「预缴 + 有季度」能导出 A200000：年度记录需 A100000（未接入），
// 历史缺季度的预缴记录要先在「更正」里补季度（后端同口径 409）。
export function canExportA200000(row) {
  if (!row || row.return_type !== '预缴') return false
  return row.quarter !== null && row.quarter !== undefined
}

export function exportBlockedHint(row) {
  if (row && row.return_type === '预缴') {
    return '该预缴记录缺少申报季度（历史数据）：请点「更正」补填年度/季度后再导出'
  }
  return '年度申报需使用 A100000 年度申报表（本系统尚未接入），不能导出季度预缴表'
}

// 元 → 万元：保留 6 位小数（0.000001 万元 = 0.01 元），
// 保证「元 → 万元 → 元」更正回填不丢分位（复核 M-4）。
export function toWan(yuan) {
  const n = Number(yuan)
  if (!Number.isFinite(n) || n === 0) return 0
  return Number((n / 10000).toFixed(6))
}

export function editFormFrom(row) {
  return {
    tax_period: String(row.year ?? ''),
    quarter: row.quarter ? `Q${row.quarter}` : '',
    report_type: row.return_type === '预缴' ? 'A类' : 'B类',
    total_revenue: toWan(row.operating_revenue),
    other_income: toWan(row.other_income),
    total_costs: toWan(row.operating_cost),
    total_expenses: toWan(row.total_expenses),
    tax_adjustments: toWan(row.tax_adjustments),
    tax_preferential: toWan(row.tax_preferential),
    preferential_choice: '',
  }
}

// 更正提交体：年度从数字控件回填时必须转成 4 位字符串（后端 TaxReturnCreate 为 str），
// 类型与季度原样提交，允许把历史「预缴 + 空季度」记录补成合法季度。
export function editPayloadFrom(form, enterpriseId) {
  return {
    enterprise_id: enterpriseId,
    tax_period: String(form.tax_period ?? ''),
    quarter: form.report_type === 'A类' ? (form.quarter || null) : null,
    report_type: form.report_type,
    total_revenue: form.total_revenue,
    other_income: form.other_income,
    total_costs: form.total_costs,
    total_expenses: form.total_expenses,
    tax_adjustments: form.tax_adjustments,
    tax_preferential: form.tax_preferential,
    preferential_choice: form.preferential_choice,
  }
}
