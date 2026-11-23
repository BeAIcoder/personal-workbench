// 工会经费计提模块 API 与展示映射：统一封装 /finance/union 端点（api 实例 baseURL 已含
// /api，这里绝不重复拼接）。请求 promise 一律 return 给调用方，由视图统一 try/catch
// （拦截器统一提示，业务 catch 不重复弹错）。
//
// 契约源 = 后端 backend/app/modules/finance_union/schemas.py（字段只消费、不二次推导）：
//   batch        id/entity/period/rate/status(draft|confirmed)/status_label/remark/
//                line_count/unknown_line_count/current_accrued_total/prior_variance_total/
//                current_book_amount_total/confirmable/confirmed_at/created_at/updated_at
//   line         department/current_wage_base/prior_accrual/prior_actual/line_status(ok|unknown)/
//                line_status_label/current_accrued/prior_variance/current_book_amount/
//                missing_fields[]/note
//   preview      line_count/ok_line_count/unknown_line_count/lines[]/totals{current_wage_base,
//                current_accrued,prior_accrual,prior_actual,prior_variance,current_book_amount}/
//                checks[{key,title,status(ok|failed|unknown),expected,actual,difference,message}]/
//                check_counts/all_passed/has_unknown/confirmable/confirm_reason/rule_version
//   checks       batch_id/status/rule_version/checks[]/check_counts/all_passed/has_unknown/
//                confirmable/confirm_reason
//   summary      total_batches/draft_count/confirmed_count/unknown_line_total/
//                current_accrued_total/prior_variance_total/current_book_amount_total/batches[]
//
// 三条前端硬规则：
// 1. 费率只来自批次（行级没有覆盖入口），表单里费率必填、不预填任何默认值；
// 2. 空输入 ≠ 0：留空提交 null（未录入），0 提交 "0.00"，两者语义不同；
// 3. 缺数据行一律按 unknown 展示，不得显示为 0 或绿色通过。
import api from '../../api'
import { saveBlob } from '../../utils/download'

export const unionApi = {
  summary: (params) => api.get('/finance/union/summary', { params: params || {} }),
  batches: (params) => api.get('/finance/union/batches', { params: params || {} }),
  batch: (id) => api.get(`/finance/union/batches/${id}`),
  createBatch: (data) => api.post('/finance/union/batches', data),
  patchBatch: (id, data) => api.patch(`/finance/union/batches/${id}`, data),
  deleteBatch: (id) => api.delete(`/finance/union/batches/${id}`),
  lines: (id) => api.get(`/finance/union/batches/${id}/lines`),
  putLines: (id, data) => api.put(`/finance/union/batches/${id}/lines`, data),
  preview: (id) => api.get(`/finance/union/batches/${id}/preview`),
  checks: (id) => api.get(`/finance/union/batches/${id}/checks`),
  confirm: (id) => api.post(`/finance/union/batches/${id}/confirm`),
  exportXlsx: (id) => api.get(`/finance/union/batches/${id}/export.xlsx`, { responseType: 'blob' }),
}

export { saveBlob }

// 口径声明：页面与导出共用同一句，避免两处口径漂移
export const SCOPE_NOTICE =
  '工资基数手工录入，不从 HR 取数；不含 60/40 分成、缴纳凭据和税前扣除判断。'

export const CONFIRM_NOTE = '确认后批次整体只读：行、费率、备注与删除全部被拒绝。'

// ---------- 展示映射（未知/null 一律非绿色，绝不伪装通过） ----------

export function batchStatusMeta(status) {
  const map = {
    draft: { label: '草稿', type: 'info' },
    confirmed: { label: '已确认', type: 'success' },
  }
  return map[status] || { label: status || '未知', type: 'warning' }
}

export function lineStatusMeta(status) {
  const map = {
    ok: { label: '可计算', type: 'success' },
    unknown: { label: '缺数据', type: 'warning' },
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

export function asList(data) {
  if (Array.isArray(data)) return data
  return data?.items || []
}

// ---------- 金额与费率的显示 / 组装 ----------

const PERIOD_RE = /^\d{4}-(?:0[1-9]|1[0-2])$/
const amount2 = (value) => Number(value).toFixed(2)

// 空 = 未录入（null）；0 = 显式 0（"0.00"）。两者不可互相替代。
export function toAmount(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? amount2(number) : null
}

export function toEditorAmount(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function formatAmount(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '—'
}

export function formatRate(value) {
  if (value === null || value === undefined || value === '') return '—'
  const rate = Number(value)
  if (!Number.isFinite(rate)) return '—'
  return `${(rate * 100).toFixed(2)}%`
}

// ---------- 批次表单 ----------

export function emptyBatchForm() {
  // 费率刻意留空：后端要求显式 rate，前端绝不预填 2% 之类的“顺手默认值”
  return { entity: '', period: null, rate: null, remark: '' }
}

export function batchToForm(batch) {
  const form = emptyBatchForm()
  if (!batch) return form
  return {
    entity: batch.entity || '',
    period: batch.period || null,
    rate: toEditorAmount(batch.rate),
    remark: batch.remark || '',
  }
}

export function buildBatchPayload(form) {
  const source = form || {}
  const entity = String(source.entity || '').trim()
  if (!entity) throw new Error('计提主体不能为空')
  const period = String(source.period || '')
  if (!PERIOD_RE.test(period)) throw new Error('所属期间必须为 YYYY-MM（例如 2026-11）')
  if (source.rate === null || source.rate === undefined || source.rate === '') {
    throw new Error('计提率必须显式填写（2% 填 0.02），系统不提供默认费率')
  }
  const rate = Number(source.rate)
  if (!Number.isFinite(rate) || rate <= 0 || rate > 1) {
    throw new Error('计提率须为 0~1 之间的小数（2% 填 0.02）')
  }
  return {
    entity,
    period,
    rate: rate.toFixed(6),
    remark: String(source.remark || '').trim(),
  }
}

// ---------- 部门行 ----------

export function emptyLineRow() {
  return {
    department: '',
    current_wage_base: null,
    prior_accrual: null,
    prior_actual: null,
  }
}

export function linesToEditor(lines) {
  return asList(lines).map((line) => ({
    department: line.department || '',
    current_wage_base: toEditorAmount(line.current_wage_base),
    prior_accrual: toEditorAmount(line.prior_accrual),
    prior_actual: toEditorAmount(line.prior_actual),
    line_status: line.line_status,
    line_status_label: line.line_status_label,
    current_accrued: line.current_accrued,
    prior_variance: line.prior_variance,
    current_book_amount: line.current_book_amount,
    missing_fields: line.missing_fields || [],
    note: line.note || '',
  }))
}

/**
 * 部门行整批保存请求体：只过滤「整行完全空白」的行，显式 0 原样保留。
 * 空输入提交 null（未录入），不得替换成 0。
 */
export function buildLinesPayload(rows) {
  const list = (rows || []).filter((row) => {
    if (!row) return false
    const hasAmount = ['current_wage_base', 'prior_accrual', 'prior_actual'].some(
      (key) => row[key] !== null && row[key] !== undefined && row[key] !== '',
    )
    return Boolean(String(row.department || '').trim()) || hasAmount
  })
  const items = list.map((row, index) => {
    const department = String(row.department || '').trim()
    if (!department) throw new Error(`第 ${index + 1} 行缺少部门名称`)
    for (const key of ['current_wage_base', 'prior_accrual', 'prior_actual']) {
      const raw = row[key]
      if (raw === null || raw === undefined || raw === '') continue
      const number = Number(raw)
      if (!Number.isFinite(number)) throw new Error(`第 ${index + 1} 行金额不是数字`)
      if (number < 0) throw new Error(`第 ${index + 1} 行金额不能为负数`)
    }
    return {
      department,
      current_wage_base: toAmount(row.current_wage_base),
      prior_accrual: toAmount(row.prior_accrual),
      prior_actual: toAmount(row.prior_actual),
    }
  })
  const duplicates = items
    .map((item) => item.department)
    .filter((name, index, all) => all.indexOf(name) !== index)
  if (duplicates.length) throw new Error(`部门「${duplicates[0]}」重复，同一批次内部门必须唯一`)
  return { items }
}

// ---------- 预览与确认 ----------

export function normalizeCounts(raw) {
  const source = raw?.check_counts || raw?.counts || raw || {}
  const num = (value) => {
    const n = Number(value)
    return Number.isFinite(n) && n > 0 ? n : 0
  }
  return { ok: num(source.ok), failed: num(source.failed), unknown: num(source.unknown) }
}

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
  return { state, counts, checks }
}

/**
 * 能否确认：唯一判定以后端 confirmable / confirm_reason 为准，
 * 前端只补三条本地兜底（未选批次、已确认、缺数据行），不自行发明规则。
 */
export function confirmBlockReason(preview) {
  if (!preview) return '请先选择一个批次'
  if (preview.status === 'confirmed') return '该批次已确认，确认后不可重复确认或修改'
  if (preview.confirmable === false) {
    return preview.confirm_reason || '当前批次不满足确认条件'
  }
  if (Number(preview.unknown_line_count) > 0) {
    return `存在 ${preview.unknown_line_count} 行缺数据（工资基数/上期数未填全），补齐后才能确认`
  }
  return ''
}

export function totalsRows(totals) {
  const source = totals || {}
  return [
    { label: '本期工资基数合计', value: formatAmount(source.current_wage_base) },
    { label: '本期应计提', value: formatAmount(source.current_accrued) },
    { label: '上期已计提合计', value: formatAmount(source.prior_accrual) },
    { label: '上期实际数合计', value: formatAmount(source.prior_actual) },
    { label: '上期差异合计', value: formatAmount(source.prior_variance) },
    { label: '本期建议入账合计', value: formatAmount(source.current_book_amount) },
  ]
}

// ---------- 下载 ----------

const safeName = (value) => String(value || '').replace(/[/\\:*?"<>|]/g, '_')

export function exportFileName(batch) {
  return `${safeName(batch?.entity || '工会经费')}_工会经费计提_${safeName(batch?.period || '')}.xlsx`
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

// 后端同时给 ASCII filename 与 filename*；优先取中文名，取不到时用本地兜底名
export function exportFileNameFromResponse(response, batch) {
  const headers = response?.headers || {}
  const disposition = headers['content-disposition'] || headers['Content-Disposition'] || ''
  return filenameFromDisposition(disposition) || exportFileName(batch)
}
