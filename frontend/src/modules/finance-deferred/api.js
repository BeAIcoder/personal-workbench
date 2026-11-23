// 长期待摊（租入资产改良支出）模块 API 与展示映射：统一封装 /finance/deferred
// 端点（api 实例 baseURL 已含 /api，这里绝不重复拼接）。请求 promise 一律 return
// 给调用方，由面板统一 try/catch（拦截器统一提示，业务 catch 不重复弹错）。
//
// 契约源 = 后端 backend/app/modules/finance_deferred/schemas.py（字段只消费、不二次推导）：
//   item      id/project_code/name/project/amount_incl_tax/tax_rate/amount_net/tax_amount/
//             start_month/months/amortization_end_month/lease_end_date/remark/
//             status(draft|archived)/status_label/run_count/confirmed_run_count/
//             latest_run_id/latest_run_status/latest_run_stale
//   run       id/item_id/status(calculated|confirmed)/status_label/input_hash/rule_version/
//             summary{amount_incl_tax,tax_rate,tax_amount,amount_net,monthly_amount,months,
//             start_month,end_month,amortized_total,rounding_difference,lease_end_date,
//             expiry_after_lease,risk_flags[],period_count}/
//             checks[{key,title,status(ok|warning|failed|unknown),expected,actual,
//             difference,message}]/check_counts{ok,warning,failed,unknown}/
//             all_checks_passed/has_warnings/has_unknown_checks/stale/
//             risk_acknowledged/risk_note/confirmed_at/created_at/updated_at
//   period    id/period_month/sequence/monthly_amount/cumulative/balance/remaining_months
//   checks    run_id/input_hash/rule_version/stale/checks/check_counts/all_passed/
//             has_warnings/has_unknown
//   export    过期或存在差异/未知时 409；历史快照需 ?allow_stale=true
import api from '../../api'
import { saveBlob } from '../../utils/download'

// 测算/导出属计算类请求，单独放宽超时（与后端真实计算预算对齐）
export const ENGINE_TIMEOUT_MS = 120000

export const deferredApi = {
  summary: (params) => api.get('/finance/deferred/summary', { params: params || {} }),
  items: (params) => api.get('/finance/deferred/items', { params: params || {} }),
  item: (id) => api.get(`/finance/deferred/items/${id}`),
  createItem: (data) => api.post('/finance/deferred/items', data),
  patchItem: (id, data) => api.patch(`/finance/deferred/items/${id}`, data),
  deleteItem: (id) => api.delete(`/finance/deferred/items/${id}`),
  runs: (itemId) => api.get(`/finance/deferred/items/${itemId}/runs`),
  createRun: (itemId) =>
    api.post(`/finance/deferred/items/${itemId}/runs`, null, { timeout: ENGINE_TIMEOUT_MS }),
  run: (id) => api.get(`/finance/deferred/runs/${id}`),
  periods: (id) => api.get(`/finance/deferred/runs/${id}/periods`),
  checks: (id) => api.get(`/finance/deferred/runs/${id}/checks`),
  confirm: (id, payload) => api.post(`/finance/deferred/runs/${id}/confirm`, payload || {}),
  exportXlsx: (id, options = {}) =>
    api.get(`/finance/deferred/runs/${id}/export.xlsx`, {
      params: options.allowStale ? { allow_stale: true } : {},
      responseType: 'blob',
      timeout: ENGINE_TIMEOUT_MS,
    }),
}

export { saveBlob }

// 后端多个端点直接返回数组（测算/期间行），列表端点返回 {total, items}
export function asList(data) {
  if (Array.isArray(data)) return data
  return data?.items || []
}

// ---------- 展示映射（未知一律非绿；警告不得伪装成通过） ----------

export function itemStatusMeta(status) {
  const map = {
    draft: { label: '草稿', type: 'info' },
    archived: { label: '已归档', type: 'info' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export function runStatusMeta(status) {
  const map = {
    calculated: { label: '已测算', type: 'info' },
    confirmed: { label: '已确认', type: 'success' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export function checkStateMeta(status) {
  const map = {
    ok: { label: '通过', type: 'success' },
    warning: { label: '警告', type: 'warning' },
    failed: { label: '差异', type: 'danger' },
    unknown: { label: '未知', type: 'warning' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export const STALE_HINT =
  '该测算结果已过期：项目参数在测算后已变更，请重新测算后再确认；旧结果只能作为历史快照导出（需显式允许）。'

export const RISK_HINT =
  '存在风险警告（摊销到期晚于租赁到期日、或末期尾差）：确认前必须勾选「已确认风险」并填写风险说明；系统不自动缩短摊销年限、不吸收尾差。'

export function isStaleRun(run) {
  return Boolean(run && run.stale)
}

// 校验计数：兼容 run.check_counts / checks.check_counts / 裸计数对象
export function normalizeCounts(raw) {
  const source = raw?.check_counts || raw?.counts || raw || {}
  const num = (value) => {
    const n = Number(value)
    return Number.isFinite(n) && n > 0 ? n : 0
  }
  return {
    ok: num(source.ok),
    warning: num(source.warning),
    failed: num(source.failed),
    unknown: num(source.unknown),
  }
}

// 只有「当前输入（未过期）+ 无差异/未知」的已测算批次可确认；warning 另需显式风险确认。
export function confirmBlockReason(run, counts) {
  if (!run) return '请先选择一次测算结果'
  if (run.status === 'confirmed') return '该测算已确认，确认后的快照不可改写'
  if (run.status !== 'calculated') return `当前状态「${run.status || '未知'}」不可确认`
  if (isStaleRun(run)) return '测算结果已过期，请先重新测算'
  const c = normalizeCounts(counts || run)
  if (c.failed > 0) return `存在 ${c.failed} 项差异校验结论，处理差异后才能确认`
  if (c.unknown > 0) return `存在 ${c.unknown} 项未知结论（数据不足），补齐数据后才能确认`
  if (c.ok === 0 && c.warning === 0) return '没有可确认的校验结论，请先重新测算'
  return ''
}

export function needsRiskAck(run, counts) {
  if (!run) return false
  return normalizeCounts(counts || run).warning > 0
}

// 风险说明未填 → 确认请求本身会被后端 409 拒绝，前端先给同样口径的中文原因
export function riskNoteBlockReason(run, counts, acknowledged, note) {
  if (!run) return '请先选择一次测算结果'
  if (!needsRiskAck(run, counts)) return ''
  if (!acknowledged) return '存在风险警告，必须勾选「已确认风险」'
  if (!String(note || '').trim()) return '已勾选确认风险，但风险说明为空；请写明接受该风险的依据'
  return ''
}

// 当前导出会被后端拒绝的情况：过期、存在差异/未知；warning 允许导出（已披露）
export function needsAllowStale(run, counts) {
  if (!run) return false
  const c = normalizeCounts(counts || run)
  return isStaleRun(run) || c.failed > 0 || c.unknown > 0
}

export function exportWarning(run, counts) {
  if (!run) return ''
  const notices = []
  if (isStaleRun(run)) notices.push('该测算结果已过期（按旧参数生成）')
  const c = normalizeCounts(counts || run)
  if (c.failed > 0) notices.push(`存在 ${c.failed} 项差异结论`)
  if (c.unknown > 0) notices.push(`存在 ${c.unknown} 项未知结论（数据不足）`)
  if (!notices.length) {
    if (c.warning > 0) {
      return `存在 ${c.warning} 项风险警告（${run.risk_acknowledged ? '已确认风险' : '尚未确认风险'}）；` +
        '底稿会如实披露警告，是否继续导出？'
    }
    return ''
  }
  return `${notices.join('；')}。当前导出会被后端拒绝（409），将以「历史快照」口径显式允许后导出；是否继续？`
}

const safeName = (value) => String(value || '').replace(/[/\\:*?"<>|]/g, '_')
const dateStamp = (run) =>
  String(run?.created_at || run?.confirmed_at || '')
    .slice(0, 10)
    .replace(/[^\d-]/g, '')

// 下载沿用 saveBlob 显式落名：中文名经代理链路不可靠，优先用后端 Content-Disposition
export function exportFileName(item, run) {
  const base = safeName(item?.project_code || '长期待摊')
  const stamp = dateStamp(run)
  return `${base}_长期待摊${stamp ? `_${stamp}` : ''}.xlsx`
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

export function exportFileNameFromResponse(response, item, run) {
  const headers = response?.headers || {}
  const disposition = headers['content-disposition'] || headers['Content-Disposition'] || ''
  return filenameFromDisposition(disposition) || exportFileName(item, run)
}

// ---------- 项目表单与固定 schema 请求体 ----------

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const PERIOD_RE = /^\d{4}-(?:0[1-9]|1[0-2])$/
const amount2 = (value) => Number(value).toFixed(2)
// 后端 tax_rate = Numeric(6,4)：超 4 位小数会被 schema 拒绝
const rate4 = (value) => Number(value).toFixed(4)

export function emptyItemForm() {
  return {
    project_code: '',
    name: '',
    project: '',
    amount_incl_tax: null,
    tax_rate: 0.06,
    start_month: null,
    months: 12,
    lease_end_date: null,
    remark: '',
  }
}

export function itemToForm(item) {
  const form = emptyItemForm()
  if (!item) return form
  const gross = item.amount_incl_tax
  const rate = item.tax_rate
  return {
    ...form,
    project_code: item.project_code || '',
    name: item.name || '',
    project: item.project || '',
    amount_incl_tax: gross === null || gross === undefined || gross === '' ? null : Number(gross),
    tax_rate: rate === null || rate === undefined || rate === '' ? null : Number(rate),
    start_month: item.start_month || null,
    months: item.months === null || item.months === undefined ? null : Number(item.months),
    lease_end_date: item.lease_end_date || null,
    remark: item.remark || '',
  }
}

/** 摊销到期月 = 起摊月 + 月数 − 1（YYYY-MM）；非法输入返回空串。 */
export function amortizationEndMonth(startMonth, months) {
  if (!PERIOD_RE.test(startMonth || '')) return ''
  const count = Number(months)
  if (!Number.isInteger(count) || count < 1 || count > 600) return ''
  const year = Number(startMonth.slice(0, 4))
  const month = Number(startMonth.slice(5, 7))
  const index = year * 12 + (month - 1) + (count - 1)
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`
}

/**
 * 项目写入体（POST/PATCH 共用固定 schema，后端 extra="forbid"）：项目编码/名称/项目/
 * 含税金额/税率/起摊月/摊销月数/租赁到期日必填；税率按小数录入（6% 填 0.06）。
 */
export function buildItemPayload(form) {
  const f = form || {}
  const projectCode = String(f.project_code || '').trim()
  if (!projectCode) throw new Error('项目编码不能为空')
  const name = String(f.name || '').trim()
  if (!name) throw new Error('项目名称不能为空')
  const project = String(f.project || '').trim()
  if (!project) throw new Error('所属项目不能为空')
  if (f.amount_incl_tax === null || f.amount_incl_tax === undefined || f.amount_incl_tax === '') {
    throw new Error('含税金额不能为空')
  }
  const gross = Number(f.amount_incl_tax)
  if (!Number.isFinite(gross) || gross < 0) throw new Error('含税金额必须为不小于 0 的数字')
  if (f.tax_rate === null || f.tax_rate === undefined || f.tax_rate === '') {
    throw new Error('税率不能为空（例如 6% 填 0.06，免税填 0）')
  }
  const rate = Number(f.tax_rate)
  if (!Number.isFinite(rate) || rate < 0 || rate > 1) {
    throw new Error('税率须为 0~1 之间的小数（例如 6% 填 0.06）')
  }
  if (!PERIOD_RE.test(f.start_month || '')) throw new Error('起摊月必须为 YYYY-MM')
  const months = Number(f.months)
  if (!Number.isInteger(months) || months < 1 || months > 600) {
    throw new Error('摊销月数必须为 1~600 的整数')
  }
  if (!DATE_RE.test(f.lease_end_date || '')) throw new Error('租赁到期日必须为 YYYY-MM-DD')
  return {
    project_code: projectCode,
    name,
    project,
    amount_incl_tax: amount2(gross),
    tax_rate: rate4(rate),
    start_month: f.start_month,
    months,
    lease_end_date: f.lease_end_date,
    remark: String(f.remark || '').trim(),
  }
}

// ---------- 展示辅助 ----------

export function formatRate(value) {
  if (value === null || value === undefined || value === '') return '—'
  const rate = Number(value)
  if (!Number.isFinite(rate)) return '—'
  return `${(rate * 100).toFixed(2)}%`
}

export function formatMoney(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return number.toFixed(2)
}

export function checkRows(checks) {
  return (checks || []).map((check) => ({ ...check, meta: checkStateMeta(check.status) }))
}

export function normalizeChecks(data) {
  const checks = data?.checks || data?.items || []
  let counts = normalizeCounts(data)
  if (!counts.ok && !counts.warning && !counts.failed && !counts.unknown && checks.length) {
    counts = { ok: 0, warning: 0, failed: 0, unknown: 0 }
    for (const check of checks) {
      if (Object.prototype.hasOwnProperty.call(counts, check.status)) counts[check.status] += 1
      else counts.unknown += 1
    }
  }
  let state
  if (counts.failed > 0) state = 'failed'
  else if (counts.unknown > 0) state = 'unknown'
  else if (counts.warning > 0) state = 'warning'
  else if (counts.ok > 0) state = 'ok'
  else state = 'unknown'
  return { state, counts, checks }
}
