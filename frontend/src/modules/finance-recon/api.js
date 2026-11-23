// 商户核销对账模块 API 与展示映射：统一封装 /finance/recon 端点（api 实例 baseURL
// 已含 /api，这里绝不重复拼接）；金额/状态映射与口径参数固定 schema 组装也在本文件，
// 供面板与测试共用。请求 promise 一律 return 给调用方，由面板统一 try/catch（拦截器
// 统一提示，业务 catch 不重复弹错）。
import api from '../../api'
import { saveBlob } from '../../utils/download'

export const SOURCE_KINDS = ['writeoff', 'claim', 'arrear', 'bill']
export const KIND_LABELS = { writeoff: '核销', claim: '认领', arrear: '欠费', bill: '账单' }

export const BILLING_METHOD_OPTIONS = [
  { value: 'whole_month', label: '整月计费' },
  { value: 'actual_days', label: '按实际自然日' },
  { value: 'custom_lines', label: '自定义明细' },
]
export const RECONSTRUCTION_CUTOFF_OPTIONS = [
  { value: 'through_as_of', label: '截至对账日' },
  { value: 'contract_end', label: '合同截止日（含撤场未到期费用）' },
]
export const DUE_RULE_OPTIONS = [
  { value: 'source_due_date', label: '源文件缴费截止日' },
  { value: 'expense_month_day', label: '费用月指定日' },
  { value: 'period_end_next_day', label: '费用期结束次日' },
]
export const PENALTY_START_RULE_OPTIONS = [
  { value: 'due_date', label: '缴费到期日' },
  { value: 'period_end_next_day', label: '费用期结束次日' },
]

export const reconApi = {
  batches: (limit = 50) => api.get('/finance/recon/batches', { params: { limit } }),
  batch: (id) => api.get(`/finance/recon/batches/${id}`),
  createBatch: (data) => api.post('/finance/recon/batches', data),
  patchBatch: (id, data) => api.patch(`/finance/recon/batches/${id}`, data),
  uploadSource: (id, kind, formData) =>
    api.post(`/finance/recon/batches/${id}/sources/${kind}`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    }),
  sources: (id) => api.get(`/finance/recon/batches/${id}/sources`),
  deleteSource: (batchId, sourceId) => api.delete(`/finance/recon/batches/${batchId}/sources/${sourceId}`),
  check: (id) => api.post(`/finance/recon/batches/${id}/check`, null, { timeout: 120000 }),
  summary: (id) => api.get(`/finance/recon/batches/${id}/summary`),
  records: (id, params) => api.get(`/finance/recon/batches/${id}/records`, { params }),
  terms: (id) => api.get(`/finance/recon/batches/${id}/terms`),
  putTerms: (id, contractNo, terms) =>
    api.put(`/finance/recon/batches/${id}/terms`, { contract_no: contractNo, terms }),
  matches: (id) => api.get(`/finance/recon/batches/${id}/matches`),
  createMatch: (id, data) => api.post(`/finance/recon/batches/${id}/matches`, data),
  patchMatch: (matchId, data) => api.patch(`/finance/recon/matches/${matchId}`, data),
  issues: (id, params) => api.get(`/finance/recon/batches/${id}/issues`, { params: params || {} }),
  createIssue: (id, data) => api.post(`/finance/recon/batches/${id}/issues`, data),
  patchIssue: (issueId, data) => api.patch(`/finance/recon/issues/${issueId}`, data),
  export: (id, fmt) =>
    api.get(
      `/finance/recon/batches/${id}/export/${fmt === 'md' ? 'report.md' : fmt === 'docx' ? 'report.docx' : 'xlsx'}`,
      { responseType: 'blob', timeout: 120000 },
    ),
}

// ---------- 展示映射（unknown/pending/stale 一律非绿色，绝不伪装通过） ----------

export function batchStatusMeta(status) {
  const map = {
    draft: { label: '草稿', type: 'info' },
    checking: { label: '检查中', type: 'warning' },
    ready: { label: '已就绪', type: 'success' },
    failed: { label: '失败', type: 'danger' },
  }
  return map[status] || { label: status || '未知', type: 'info' }
}

export function parseStatusMeta(status) {
  const map = {
    parsed: { label: '已入库', type: 'success' },
    failed: { label: '解析失败', type: 'danger' },
    pending: { label: '待解析', type: 'warning' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export function checkStateMeta(state) {
  const map = {
    pass: { label: '通过', type: 'success' },
    diff: { label: '不一致', type: 'danger' },
    unknown: { label: '待定', type: 'warning' },
    info: { label: '说明', type: 'info' },
  }
  return map[state] || { label: state || '待定', type: 'warning' }
}

export function matchStatusMeta(status) {
  const map = {
    suggested: { label: '待确认', type: 'warning' },
    confirmed: { label: '已确认', type: 'success' },
    rejected: { label: '已排除', type: 'info' },
  }
  return map[status] || { label: status || '待确认', type: 'warning' }
}

export function issueStatusMeta(status) {
  const map = {
    pending: { label: '待确认', type: 'warning' },
    confirmed: { label: '已确认', type: 'success' },
    dismissed: { label: '已排除', type: 'info' },
    resolved: { label: '已解除', type: 'info' },
  }
  return map[status] || { label: status || '待确认', type: 'warning' }
}

export function severityMeta(severity) {
  const map = {
    info: { label: '提示', type: 'info' },
    warn: { label: '警告', type: 'warning' },
    error: { label: '严重', type: 'danger' },
  }
  return map[severity] || { label: severity || '警告', type: 'warning' }
}

export function originLabel(origin) {
  return origin === 'machine' ? '机检' : origin === 'human' ? '人工' : origin || '—'
}

const amount2 = (value) => Number(value).toFixed(2)
const rate8 = (value) => Number(value).toFixed(8)

export function exportFileName(batch, fmt) {
  const base = `${String(batch?.name || '对账批次').replace(/[/\\:*?"<>|]/g, '_')}_商户核销对账_${batch?.as_of || ''}`
  return fmt === 'xlsx' ? `${base}.xlsx` : fmt === 'docx' ? `${base}.docx` : `${base}.md`
}

export { saveBlob }

// ---------- 口径参数固定 schema（terms 编辑与组装；不用无约束自由 JSON） ----------

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

export function emptyContractTerms() {
  return {
    contract_start: null,
    contract_end: null,
    billing_method: null,
    reconstruction_cutoff: null,
    monthly_fees: [],
    custom_lines: [],
    additional_charges: [],
    credits: [],
    due_rule: null,
    due_day: null,
    penalty: { rate_per_day: null, start_rule: null, cap: null, waived: false },
  }
}

// GET terms 的金额是字符串；编辑器数值控件需要 Number（null/'' 保持 null）
export function termsToEditor(terms) {
  const skeleton = emptyContractTerms()
  if (!terms) return skeleton
  const num = (value) => (value === null || value === undefined || value === '' ? null : Number(value))
  return {
    ...skeleton,
    ...terms,
    monthly_fees: (terms.monthly_fees || []).map((f) => ({ fee_type: f.fee_type, monthly_amount: num(f.monthly_amount) })),
    custom_lines: (terms.custom_lines || []).map((l) => ({
      fee_type: l.fee_type,
      period_start: l.period_start || null,
      period_end: l.period_end || null,
      amount: num(l.amount),
    })),
    additional_charges: (terms.additional_charges || []).map((c) => ({
      fee_type: c.fee_type,
      amount: num(c.amount),
      period_start: c.period_start || null,
      period_end: c.period_end || null,
      note: c.note || '',
    })),
    credits: (terms.credits || []).map((c) => ({
      credit_type: c.credit_type,
      amount: num(c.amount),
      note: c.note || '',
      confirmed: !!c.confirmed,
    })),
    penalty: {
      rate_per_day: num(terms.penalty?.rate_per_day),
      start_rule: terms.penalty?.start_rule || null,
      cap: num(terms.penalty?.cap),
      waived: !!terms.penalty?.waived,
    },
  }
}

/**
 * 把编辑器状态组装为固定 schema 的 PUT terms 请求体：
 * 金额两位小数字符串（费率 8 位，与后端 Decimal(12,8) 对齐）、日期 ISO 字符串；
 * 校验失败抛中文 Error（保存入口转 warning 提示），绝不发送无约束自由 JSON。
 */
export function buildTermsPayload(editor) {
  const e = editor || {}
  if (!e.reconstruction_cutoff) {
    throw new Error('请先选择合同应收重构截止口径（截至对账日 / 合同截止日）')
  }
  if (e.contract_start && e.contract_end && e.contract_end < e.contract_start) {
    throw new Error('合同截止日早于起始日')
  }
  if (e.billing_method === 'whole_month' || e.billing_method === 'actual_days') {
    if (!DATE_RE.test(e.contract_start || '') || !DATE_RE.test(e.contract_end || '')) {
      throw new Error('计费方式为「整月计费 / 按实际自然日」时需填写合同起始日与合同截止日')
    }
    if (!(e.monthly_fees || []).length) {
      throw new Error('计费方式为「整月计费 / 按实际自然日」时需至少填写一条月费标准')
    }
    e.monthly_fees.forEach((fee, index) => {
      if (!fee.fee_type || !String(fee.fee_type).trim()) {
        throw new Error(`月费标准第 ${index + 1} 行费用类型未填`)
      }
      if (fee.monthly_amount === null || fee.monthly_amount === undefined || fee.monthly_amount === '') {
        throw new Error(`月费标准第 ${index + 1} 行金额未填`)
      }
    })
  }
  if (e.billing_method === 'custom_lines' && !(e.custom_lines || []).length) {
    throw new Error('计费方式为「自定义明细」时需至少填写一条自定义计费明细')
  }
  ;(e.custom_lines || []).forEach((line, index) => {
    if (!line.fee_type || !String(line.fee_type).trim()) {
      throw new Error(`自定义计费明细第 ${index + 1} 行费用类型未填`)
    }
    if (!DATE_RE.test(line.period_start || '') || !DATE_RE.test(line.period_end || '')) {
      throw new Error(`自定义计费明细第 ${index + 1} 行费用期未填完整`)
    }
    if (line.period_end < line.period_start) {
      throw new Error(`自定义计费明细第 ${index + 1} 行费用期结束早于开始`)
    }
    if (line.amount === null || line.amount === undefined || line.amount === '') {
      throw new Error(`自定义计费明细第 ${index + 1} 行金额未填`)
    }
  })
  ;(e.additional_charges || []).forEach((charge, index) => {
    if (!charge.fee_type || !String(charge.fee_type).trim()) {
      throw new Error(`一次性费用第 ${index + 1} 行费用类型未填`)
    }
    if (charge.amount === null || charge.amount === undefined || charge.amount === '') {
      throw new Error(`一次性费用第 ${index + 1} 行金额未填`)
    }
  })
  ;(e.credits || []).forEach((credit, index) => {
    if (!credit.credit_type || !String(credit.credit_type).trim()) {
      throw new Error(`抵扣项第 ${index + 1} 行类型未填`)
    }
    if (credit.amount === null || credit.amount === undefined || credit.amount === '') {
      throw new Error(`抵扣项第 ${index + 1} 行金额未填`)
    }
  })
  return {
    contract_start: DATE_RE.test(e.contract_start || '') ? e.contract_start : null,
    contract_end: DATE_RE.test(e.contract_end || '') ? e.contract_end : null,
    billing_method: e.billing_method || null,
    reconstruction_cutoff: e.reconstruction_cutoff,
    monthly_fees: (e.monthly_fees || [])
      .filter((fee) => (fee.fee_type || '').trim() || fee.monthly_amount !== null)
      .map((fee) => ({ fee_type: String(fee.fee_type).trim(), monthly_amount: amount2(fee.monthly_amount || 0) })),
    custom_lines: (e.custom_lines || []).map((line) => ({
      fee_type: String(line.fee_type).trim(),
      period_start: line.period_start,
      period_end: line.period_end,
      amount: amount2(line.amount || 0),
    })),
    additional_charges: (e.additional_charges || []).map((charge) => ({
      fee_type: String(charge.fee_type).trim(),
      amount: amount2(charge.amount || 0),
      period_start: charge.period_start || null,
      period_end: charge.period_end || null,
      note: charge.note || '',
    })),
    credits: (e.credits || []).map((credit) => ({
      credit_type: String(credit.credit_type).trim(),
      amount: amount2(credit.amount || 0),
      note: credit.note || '',
      confirmed: !!credit.confirmed,
    })),
    due_rule: e.due_rule || null,
    due_day: e.due_day === null || e.due_day === undefined || e.due_day === '' ? null : Number(e.due_day),
    penalty: {
      rate_per_day: e.penalty?.rate_per_day === null || e.penalty?.rate_per_day === undefined ? null : rate8(e.penalty.rate_per_day),
      start_rule: e.penalty?.start_rule || null,
      cap: e.penalty?.cap === null || e.penalty?.cap === undefined ? null : amount2(e.penalty.cap),
      waived: !!e.penalty?.waived,
    },
  }
}
