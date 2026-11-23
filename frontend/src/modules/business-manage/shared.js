// 经营管理模块共用格式化/查找工具（纯函数）。
export function toNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function fmtNumber(value, digits = 2) {
  const parsed = toNumber(value)
  if (parsed === null) return '—'
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(parsed)
}

export function fmtInt(value) {
  const parsed = toNumber(value)
  if (parsed === null) return '—'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(parsed)
}

// 比率：兼容 0–1 小数与 0–100 百分数，null 显示「—」
export function fmtRate(value, digits = 2) {
  const parsed = toNumber(value)
  if (parsed === null) return '—'
  // 比率按小数存（0–5）；只有明显超出 5 的值才当作已是百分点
  const point = Math.abs(parsed) <= 5 ? parsed * 100 : parsed
  return `${point.toFixed(digits)}%`
}

export function metricValue(metric) {
  if (!metric) return null
  const numeric = toNumber(metric.value_num)
  if (numeric !== null) return numeric
  if (metric.value_text !== null && metric.value_text !== undefined && metric.value_text !== '') {
    return metric.value_text
  }
  return null
}

export function fmtMetric(metric) {
  if (!metric) return '—'
  const numeric = toNumber(metric.value_num)
  const label = String(metric.label || '')
  const unit = String(metric.unit || '')
  if (numeric !== null) {
    if (unit === '%' || /率|比/.test(label)) return fmtRate(numeric)
    const formatted = fmtNumber(numeric)
    return unit ? `${formatted} ${unit}` : formatted
  }
  const text = typeof metric.value_text === 'string' ? metric.value_text.trim() : ''
  return text || '—'
}

export function findMetric(items, keywords, labelKey = 'label') {
  const list = Array.isArray(items) ? items : []
  const words = (Array.isArray(keywords) ? keywords : [keywords]).filter(Boolean)
  for (const word of words) {
    const exact = list.find((item) => String(item?.[labelKey] || '') === word)
    if (exact) return exact
  }
  for (const word of words) {
    const hit = list.find((item) => String(item?.[labelKey] || '').includes(word))
    if (hit) return hit
  }
  return null
}

export function sanitizeFilename(value) {
  return String(value || '')
    .replace(/[\\/:*?"<>|\r\n]/g, '-')
    .trim()
}

export function uniqueValues(values) {
  return [...new Set((values || []).filter((value) => value !== null && value !== undefined && value !== ''))]
}

// 报表行按 section 分组：{ sectionKey: rows[] }
export function groupBySection(rows) {
  const map = new Map()
  for (const row of Array.isArray(rows) ? rows : []) {
    const key = row?.section || ''
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(row)
  }
  return map
}

export function sumNumbers(values) {
  const list = (Array.isArray(values) ? values : []).map(toNumber).filter((value) => value !== null)
  return list.length ? list.reduce((sum, value) => sum + value, 0) : null
}

export function averageNumbers(values) {
  const list = (Array.isArray(values) ? values : []).map(toNumber).filter((value) => value !== null)
  return list.length ? list.reduce((sum, value) => sum + value, 0) / list.length : null
}

export const OVERVIEW_SERIES_KEYS = [
  'occupancy_trend',
  'collection_trend',
  'leasing_progress',
  'kpi_completion',
  'litigation_by_stage',
  'litigation_top',
  'warning_reasons',
  'project_compare',
]

export function emptyOverview() {
  return {
    periods: [],
    kpis: [],
    series: Object.fromEntries(OVERVIEW_SERIES_KEYS.map((key) => [key, []])),
  }
}

export function normalizeOverview(data) {
  const series = data?.series || {}
  return {
    periods: Array.isArray(data?.periods) ? data.periods : [],
    kpis: Array.isArray(data?.kpis) ? data.kpis : [],
    series: Object.fromEntries(
      OVERVIEW_SERIES_KEYS.map((key) => [key, Array.isArray(series[key]) ? series[key] : []]),
    ),
  }
}
