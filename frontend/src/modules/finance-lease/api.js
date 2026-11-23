// 租赁核算（承租人）模块 API 与展示映射：统一封装 /finance/lease 端点（api 实例
// baseURL 已含 /api，这里绝不重复拼接）。请求 promise 一律 return 给调用方，由面板
// 统一 try/catch（拦截器统一提示，业务 catch 不重复弹错）。
//
// 契约源 = 后端 backend/app/modules/finance_lease/schemas.py（字段只消费、不二次推导）：
//   contract     id/contract_no/name/lessee/project/lessor/start_date/end_date/annual_rate/
//                rate_basis/remark/attachment_note/status(draft|archived)/status_label/
//                payment_count/calculation_count/confirmed_calculation_count/
//                latest_calculation_id/latest_calculation_status/latest_calculation_stale
//   payment      sequence/source_row/period_start/period_end/months/amount/
//                start_flag/prepay_flag/is_free_rent/note
//   calculation  id/contract_id/status(calculated|confirmed)/status_label/input_hash/
//                rule_version/summary{total_payment,present_value,total_interest,
//                total_depreciation,payment_count,month_count,first_period,last_period}/
//                checks[{key,title,status(ok|failed|unknown),expected,actual,difference,message}]/
//                check_counts{ok,failed,unknown}/all_checks_passed/has_unknown_checks/
//                stale/confirmed_at/created_at/updated_at
//   assessment   sequence/source_row/period_start/period_end/months/amount/period_rate/
//                discount_factor/present_value/unamortized_finance/interest_u/liability_balance
//   period       period_month/sequence/depreciation/payable/interest/lease_payment/
//                unamortized_finance/liability_balance
//   checks       calculation_id/input_hash/rule_version/stale/checks/check_counts/
//                all_passed/has_unknown
//   import       preview 与 apply 都是 multipart 上传同一份文件；预览返回
//                {sheet_name,header_map,items,warnings[],errors[]}，确认才落库
//   export       默认拒绝过期/未全部通过的当前导出（409）；历史快照需 ?allow_stale=true
import api from '../../api'
import { saveBlob } from '../../utils/download'

// 测算/导入/导出属计算类请求，单独放宽超时（与后端引擎预算对齐）
export const ENGINE_TIMEOUT_MS = 120000

export const leaseApi = {
  summary: (params) => api.get('/finance/lease/summary', { params: params || {} }),
  contracts: (params) => api.get('/finance/lease/contracts', { params: params || {} }),
  contract: (id) => api.get(`/finance/lease/contracts/${id}`),
  createContract: (data) => api.post('/finance/lease/contracts', data),
  patchContract: (id, data) => api.patch(`/finance/lease/contracts/${id}`, data),
  deleteContract: (id) => api.delete(`/finance/lease/contracts/${id}`),
  payments: (id) => api.get(`/finance/lease/contracts/${id}/payments`),
  putPayments: (id, data) => api.put(`/finance/lease/contracts/${id}/payments`, data),
  previewImport: (id, formData) =>
    api.post(`/finance/lease/contracts/${id}/payments/import/preview`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: ENGINE_TIMEOUT_MS,
    }),
  applyImport: (id, formData) =>
    api.post(`/finance/lease/contracts/${id}/payments/import/apply`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: ENGINE_TIMEOUT_MS,
    }),
  calculations: (id) => api.get(`/finance/lease/contracts/${id}/calculations`),
  createCalculation: (id) =>
    api.post(`/finance/lease/contracts/${id}/calculations`, null, { timeout: ENGINE_TIMEOUT_MS }),
  calculation: (id) => api.get(`/finance/lease/calculations/${id}`),
  assessment: (id) => api.get(`/finance/lease/calculations/${id}/assessment`),
  periods: (id) => api.get(`/finance/lease/calculations/${id}/periods`),
  checks: (id) => api.get(`/finance/lease/calculations/${id}/checks`),
  confirm: (id) => api.post(`/finance/lease/calculations/${id}/confirm`),
  exportXlsx: (id, options = {}) =>
    api.get(`/finance/lease/calculations/${id}/export.xlsx`, {
      params: options.allowStale ? { allow_stale: true } : {},
      responseType: 'blob',
      timeout: ENGINE_TIMEOUT_MS,
    }),
  // BIP 只读对账：映射只写本模块表（mod_lease_bip_map），余额/凭证一律只读
  reconciliationMappings: (params) =>
    api.get('/finance/lease/reconciliation/mappings', { params: params || {} }),
  putReconciliationMappings: (data) => api.put('/finance/lease/reconciliation/mappings', data),
  reconciliationContext: () => api.get('/finance/lease/reconciliation/context'),
  reconciliationCheck: (data) => api.post('/finance/lease/reconciliation/check', data),
}

export { saveBlob }

// 后端多个端点直接返回数组（付款期/测算/评估行/期间行），列表端点返回 {total, items}
export function asList(data) {
  if (Array.isArray(data)) return data
  return data?.items || []
}

// ---------- 展示映射（未知/待定/已过期一律非绿色，绝不伪装通过） ----------

export function contractStatusMeta(status) {
  const map = {
    draft: { label: '草稿', type: 'info' },
    archived: { label: '已归档', type: 'info' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export function calculationStatusMeta(status) {
  const map = {
    calculated: { label: '已测算', type: 'info' },
    confirmed: { label: '已确认', type: 'success' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export function checkStateMeta(status) {
  const map = {
    ok: { label: '通过', type: 'success' },
    failed: { label: '差异', type: 'danger' },
    unknown: { label: '未知', type: 'warning' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export const STALE_HINT =
  '该测算结果已过期：当前合同或付款计划在测算后已变更，请重新测算后再确认；旧结果只能作为历史快照导出（导出时需显式允许）。'

export function isStaleCalculation(calc) {
  return Boolean(calc && calc.stale)
}

// 校验计数：兼容 calculation.check_counts / checks.check_counts / 裸计数对象
export function normalizeCounts(raw) {
  const source = raw?.check_counts || raw?.counts || raw || {}
  const num = (value) => {
    const n = Number(value)
    return Number.isFinite(n) && n > 0 ? n : 0
  }
  return { ok: num(source.ok), failed: num(source.failed), unknown: num(source.unknown) }
}

// 只有「当前输入（未过期）+ 全部检查通过」的已测算批次可确认；否则返回中文原因。
export function confirmBlockReason(calc, counts) {
  if (!calc) return '请先选择一次测算结果'
  if (calc.status === 'confirmed') return '该测算已确认，确认后的快照不可改写'
  if (calc.status !== 'calculated') return `当前状态「${calc.status || '未知'}」不可确认`
  if (isStaleCalculation(calc)) return '测算结果已过期，请先重新测算'
  const c = normalizeCounts(counts || calc)
  if (c.failed > 0) return `存在 ${c.failed} 项差异校验结论，处理差异后才能确认`
  if (c.unknown > 0) return `存在 ${c.unknown} 项未知结论（数据不足），补齐数据后才能确认`
  if (c.ok === 0) return '没有可确认的校验结论，请先重新测算'
  return ''
}

// 当前导出会被后端拒绝的情况：过期、存在差异/未知、all_checks_passed=false
export function needsAllowStale(calc, counts) {
  if (!calc) return false
  const c = normalizeCounts(counts || calc)
  return (
    isStaleCalculation(calc) || c.failed > 0 || c.unknown > 0 || calc.all_checks_passed === false
  )
}

// 导出前的警示文案（决定是否显式 allow_stale；不通过时后端 409）
export function exportWarning(calc, counts) {
  if (!calc) return ''
  const notices = []
  if (isStaleCalculation(calc)) notices.push('该测算结果已过期（按旧输入生成）')
  const c = normalizeCounts(counts || calc)
  if (c.failed > 0) notices.push(`存在 ${c.failed} 项差异结论`)
  if (c.unknown > 0) notices.push(`存在 ${c.unknown} 项未知结论（数据不足）`)
  if (!notices.length && calc.all_checks_passed === false) notices.push('校验未全部通过')
  if (!notices.length) return ''
  return `${notices.join('；')}。当前导出会被后端拒绝（409），将以「历史快照」口径显式允许后导出；是否继续？`
}

const safeName = (value) => String(value || '').replace(/[/\\:*?"<>|]/g, '_')
const dateStamp = (calc) =>
  String(calc?.created_at || calc?.confirmed_at || '')
    .slice(0, 10)
    .replace(/[^\d-]/g, '')

// 下载沿用 saveBlob 显式落名：中文名经代理链路不可靠，优先用后端 Content-Disposition
export function exportFileName(contract, calc) {
  const base = safeName(contract?.contract_no || '租赁合同')
  const stamp = dateStamp(calc)
  return `${base}_租赁核算${stamp ? `_${stamp}` : ''}.xlsx`
}

export function filenameFromDisposition(disposition) {
  if (!disposition) return ''
  const star = /filename\*\s*=\s*utf-8''([^;]+)/i.exec(disposition)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''))
    } catch {
      return ''
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(disposition)
  return plain ? plain[1].trim() : ''
}

export function exportFileNameFromResponse(response, contract, calc) {
  const headers = response?.headers || {}
  const disposition = headers['content-disposition'] || headers['Content-Disposition'] || ''
  return filenameFromDisposition(disposition) || exportFileName(contract, calc)
}

// ---------- 合同表单与固定 schema 请求体 ----------

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const amount2 = (value) => Number(value).toFixed(2)
// 后端 annual_rate = Numeric(9,6)：超 6 位小数会被 schema 拒绝
const rate6 = (value) => Number(value).toFixed(6)

export function emptyContractForm() {
  return {
    contract_no: '',
    name: '',
    lessee: '',
    project: '',
    lessor: '',
    start_date: null,
    end_date: null,
    annual_rate: null,
    rate_basis: '',
    remark: '',
    attachment_note: '',
  }
}

export function contractToForm(contract) {
  const form = emptyContractForm()
  if (!contract) return form
  const rate = contract.annual_rate
  return {
    ...form,
    contract_no: contract.contract_no || '',
    name: contract.name || '',
    lessee: contract.lessee || '',
    project: contract.project || '',
    lessor: contract.lessor || '',
    start_date: contract.start_date || null,
    end_date: contract.end_date || null,
    annual_rate: rate === null || rate === undefined || rate === '' ? null : Number(rate),
    rate_basis: contract.rate_basis || '',
    remark: contract.remark || '',
    attachment_note: contract.attachment_note || '',
  }
}

/**
 * 合同写入体（POST/PATCH 共用固定 schema，后端 extra="forbid"）：合同号、承租人、
 * 起止日与年折现率必填；年折现率按小数录入（3.5% 填 0.035），6 位小数对齐 Numeric(9,6)。
 */
export function buildContractPayload(form) {
  const f = form || {}
  const contractNo = String(f.contract_no || '').trim()
  if (!contractNo) throw new Error('合同号不能为空')
  const lessee = String(f.lessee || '').trim()
  if (!lessee) throw new Error('承租人主体不能为空')
  if (!DATE_RE.test(f.start_date || '')) throw new Error('租赁起始日必须为 YYYY-MM-DD')
  if (!DATE_RE.test(f.end_date || '')) throw new Error('租赁截止日必须为 YYYY-MM-DD')
  if (f.end_date < f.start_date) throw new Error('租赁截止日早于租赁起始日')
  if (f.annual_rate === null || f.annual_rate === undefined || f.annual_rate === '') {
    throw new Error('年折现率不能为空（例如 3.5% 填 0.035）')
  }
  const rate = Number(f.annual_rate)
  if (!Number.isFinite(rate) || rate <= 0 || rate > 1) {
    throw new Error('年折现率须为 0~1 之间的小数（例如 3.5% 填 0.035）')
  }
  return {
    contract_no: contractNo,
    name: String(f.name || '').trim(),
    lessee,
    project: String(f.project || '').trim(),
    lessor: String(f.lessor || '').trim(),
    start_date: f.start_date,
    end_date: f.end_date,
    annual_rate: rate6(rate),
    rate_basis: String(f.rate_basis || '').trim(),
    remark: String(f.remark || '').trim(),
    attachment_note: String(f.attachment_note || '').trim(),
  }
}

// ---------- 付款计划（整批替换） ----------

export function emptyPaymentRow(sequence = 1) {
  return {
    sequence,
    source_row: null,
    period_start: null,
    period_end: null,
    months: null,
    amount: null,
    start_flag: false,
    prepay_flag: false,
    is_free_rent: false,
    note: '',
  }
}

const toNumberOrNull = (value) =>
  value === null || value === undefined || value === '' ? null : Number(value)

export function paymentsToEditor(data) {
  return asList(data).map((item, index) => ({
    sequence: item.sequence === null || item.sequence === undefined ? index + 1 : Number(item.sequence),
    source_row: toNumberOrNull(item.source_row),
    period_start: item.period_start || null,
    period_end: item.period_end || null,
    months: toNumberOrNull(item.months),
    amount: toNumberOrNull(item.amount),
    start_flag: Boolean(item.start_flag),
    prepay_flag: Boolean(item.prepay_flag),
    is_free_rent: Boolean(item.is_free_rent),
    note: item.note || '',
  }))
}

export function renumberPayments(rows) {
  ;(rows || []).forEach((row, index) => {
    row.sequence = index + 1
  })
  return rows
}

/**
 * 付款计划批量替换请求体：免租期与 0 月期都是合法业务行，只过滤「整行空白」，
 * 不允许静默删除；期次必须 1..N 连续（与后端 validate_payment_sequences 同口径）。
 */
export function buildPaymentsPayload(rows) {
  const list = (rows || []).filter((row) => {
    if (!row) return false
    return Boolean(
      row.period_start ||
        row.period_end ||
        (row.amount !== null && row.amount !== undefined && row.amount !== '') ||
        (row.months !== null && row.months !== undefined && row.months !== '') ||
        row.is_free_rent ||
        String(row.note || '').trim(),
    )
  })
  if (!list.length) return { items: [] }
  const sorted = [...list].sort((a, b) => Number(a.sequence) - Number(b.sequence))
  const sequences = sorted.map((row) => Number(row.sequence))
  if (new Set(sequences).size !== sequences.length) {
    const duplicate = sequences.find((seq) => sequences.filter((s) => s === seq).length > 1)
    throw new Error(`付款期数重复：第 ${duplicate} 期（同一合同内期数必须唯一）`)
  }
  if (sequences.some((seq) => !Number.isInteger(seq) || seq < 1)) {
    throw new Error('付款期次必须为正整数')
  }
  const expected = Array.from({ length: sequences.length }, (_, i) => i + 1)
  const missing = expected.find((seq) => !sequences.includes(seq))
  if (missing) throw new Error(`付款期数必须从 1 连续编号，缺少第 ${missing} 期`)
  const extra = sequences.find((seq) => !expected.includes(seq))
  if (extra) throw new Error(`付款期数必须从 1 连续编号，第 ${extra} 期越界或跳号`)
  const items = sorted.map((row, index) => {
    const sequence = Number(row.sequence)
    if (!DATE_RE.test(row.period_start || '')) {
      throw new Error(`第 ${sequence} 期计租开始日必须为 YYYY-MM-DD`)
    }
    if (!DATE_RE.test(row.period_end || '')) {
      throw new Error(`第 ${sequence} 期计租结束日必须为 YYYY-MM-DD`)
    }
    if (row.period_end < row.period_start) {
      throw new Error(`第 ${sequence} 期计租结束日早于开始日`)
    }
    const amount = Number(row.amount)
    if (row.amount === null || row.amount === undefined || row.amount === '' || !Number.isFinite(amount)) {
      throw new Error(`第 ${sequence} 期付款金额不能为空`)
    }
    if (amount < 0) throw new Error(`第 ${sequence} 期付款金额不能为负数`)
    const months = toNumberOrNull(row.months)
    if (months !== null) {
      if (!Number.isInteger(months) || months < 0 || months > 1200) {
        throw new Error(`第 ${sequence} 期覆盖月数必须为 0~1200 的整数（留空表示由引擎按计租起止推导）`)
      }
    }
    return {
      sequence,
      source_row: toNumberOrNull(row.source_row),
      period_start: row.period_start,
      period_end: row.period_end,
      months,
      amount: amount2(amount),
      start_flag: Boolean(row.start_flag),
      prepay_flag: Boolean(row.prepay_flag),
      is_free_rent: Boolean(row.is_free_rent),
      note: String(row.note || '').trim(),
    }
  })
  return { items }
}

export function paymentsTotals(rows) {
  let amount = 0
  let months = 0
  for (const row of rows || []) {
    const a = Number(row?.amount)
    const m = Number(row?.months)
    if (Number.isFinite(a)) amount += a
    if (Number.isFinite(m)) months += m
  }
  return { amount: amount.toFixed(2), months }
}

// ---------- xlsx 预览（预览不落库，确认时重新上传同一文件才落库） ----------

export function previewRows(preview) {
  return preview?.items || []
}

export function previewWarnings(preview) {
  return preview?.warnings || []
}

export function previewErrors(preview) {
  return preview?.errors || []
}

export function previewHasErrors(preview) {
  return previewErrors(preview).length > 0
}

// 免租期 / 0 月期行数：预览必须原样保留这些行，确认导入前要显式提示
export function previewZeroRows(preview) {
  const isZero = (value) =>
    value !== null && value !== undefined && value !== '' && Number(value) === 0
  return previewRows(preview).filter(
    (row) => isZero(row?.amount) || isZero(row?.months) || row?.is_free_rent,
  ).length
}

// ---------- 校验结论 ----------

export function normalizeChecks(data) {
  const checks = data?.checks || data?.items || []
  let counts = normalizeCounts(data)
  if (!counts.ok && !counts.failed && !counts.unknown && checks.length) {
    counts = { ok: 0, failed: 0, unknown: 0 }
    for (const check of checks) {
      if (check.status === 'failed') counts.failed += 1
      else if (check.status === 'unknown') counts.unknown += 1
      else if (check.status === 'ok') counts.ok += 1
    }
  }
  let state
  if (data?.all_passed === true && counts.failed === 0 && counts.unknown === 0) state = 'ok'
  else if (counts.failed > 0) state = 'failed'
  else if (counts.unknown > 0) state = 'unknown'
  else if (checks.length && checks.every((check) => check.status === 'ok')) state = 'ok'
  else state = 'unknown'
  const unknownChecks = checks.filter((check) => check.status === 'unknown')
  return { state, counts, checks, unknownChecks }
}

export function checkRows(checks) {
  return (checks || []).map((check) => ({ ...check, meta: checkStateMeta(check.status) }))
}

// ---------- BIP 只读对账（承租人 / 核算账簿 / 期间；契约源 = reconciliation_schemas.py） ----------
//
// subject   subject_key/subject_key_label/subject_code/direction/expected/expected_source/
//           actual/difference/status(ok|failed|unknown)/status_label/source/source_label/reason
// contracts contract_id/contract_no/name/contract_status/selected/calculation_id/
//           calculation_status/calculation_input_hash/calculation_rule_version/stale/
//           covers_period/reason/present_value/accumulated_depreciation/remaining_payment/
//           unrecognized_finance/liability_balance
// coverage  contract_count/selected_count/unknown_count/reasons[]

export const RECON_TOLERANCE = '0.01'

// 五类科目位与默认余额方向：与后端 SUBJECT_KEYS / MAPPING_DIRECTIONS 一一对应
export const RECON_SUBJECT_KEYS = [
  { key: 'rou_asset', label: '使用权资产原值', defaultDirection: 'debit' },
  { key: 'rou_accum_dep', label: '使用权资产累计折旧', defaultDirection: 'credit' },
  { key: 'lease_payment', label: '租赁付款额（剩余付款）', defaultDirection: 'credit' },
  { key: 'unrecognized_finance', label: '未确认融资费用余额', defaultDirection: 'debit' },
  { key: 'lease_liability', label: '租赁负债净额', defaultDirection: 'credit' },
]

export const RECON_DIRECTIONS = [
  { value: 'debit', label: '借方' },
  { value: 'credit', label: '贷方' },
]

export function reconStatusMeta(status) {
  const map = {
    ok: { label: '一致', type: 'success' },
    failed: { label: '差异', type: 'danger' },
    unknown: { label: '未知', type: 'warning' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export function emptyMappingRows() {
  return RECON_SUBJECT_KEYS.map((item) => ({
    subject_key: item.key,
    label: item.label,
    subject_code: '',
    direction: item.defaultDirection,
  }))
}

// 已保存映射 → 固定五行编辑器（未配置的科目位保留空白行，不猜科目）
export function mappingsToRows(items) {
  const byKey = new Map((items || []).map((item) => [item.subject_key, item]))
  return emptyMappingRows().map((row) => {
    const found = byKey.get(row.subject_key)
    if (!found) return row
    return {
      ...row,
      subject_code: found.subject_code || '',
      direction: found.direction || row.direction,
    }
  })
}

export function buildMappingsPayload({ lessee, ledger, rows } = {}) {
  const lesseeText = String(lessee || '').trim()
  const ledgerText = String(ledger || '').trim()
  if (!lesseeText) throw new Error('请先选择承租人主体')
  if (!ledgerText) throw new Error('请先选择核算账簿')
  const items = []
  for (const row of rows || []) {
    const code = String(row?.subject_code || '').trim()
    if (!code) continue
    if (!RECON_DIRECTIONS.some((item) => item.value === row.direction)) {
      throw new Error(`「${row.label || row.subject_key}」的余额方向非法，只能是借方或贷方`)
    }
    items.push({ subject_key: row.subject_key, subject_code: code, direction: row.direction })
  }
  const codes = items.map((item) => item.subject_code)
  const repeated = codes.find((code) => codes.filter((other) => other === code).length > 1)
  if (repeated) {
    throw new Error(`BIP 科目「${repeated}」不能同时映射到多个科目位，否则对账会重复计数`)
  }
  return { lessee: lesseeText, ledger: ledgerText, items }
}

export function reconCheckBlockReason(form) {
  if (!String(form?.lessee || '').trim()) return '请选择承租人主体'
  if (!String(form?.ledger || '').trim()) return '请选择核算账簿'
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(String(form?.period || ''))) {
    return '请选择对账期间（YYYY-MM）'
  }
  return ''
}

export function normalizeReconcile(result) {
  const subjects = (result?.subjects || []).map((row) => ({
    ...row,
    meta: reconStatusMeta(row.status),
  }))
  const count = (key) => {
    if (result?.counts && Number.isFinite(Number(result.counts[key]))) {
      return Number(result.counts[key])
    }
    return subjects.filter((row) => row.status === key).length
  }
  const counts = { ok: count('ok'), failed: count('failed'), unknown: count('unknown') }
  return {
    ...result,
    subjects,
    counts,
    meta: reconStatusMeta(result?.status),
    unknownSubjects: subjects.filter((row) => row.status === 'unknown'),
    failedSubjects: subjects.filter((row) => row.status === 'failed'),
    contracts: result?.contracts || [],
  }
}

export function formatAmount(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return String(value)
  return number.toFixed(2)
}

// 覆盖明细的展示标签：未知/未采用一律非绿色，并原样展示后端中文原因
export function coverageMeta(row) {
  if (row?.selected) return { label: '已采用', type: 'success' }
  if (row?.stale) return { label: '已过期', type: 'danger' }
  if (row?.covers_period === false) return { label: '未覆盖', type: 'warning' }
  return { label: '未采用', type: 'warning' }
}

