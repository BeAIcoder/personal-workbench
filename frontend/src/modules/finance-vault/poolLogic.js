// 数据池清洗的可执行逻辑（纯函数）：清洗请求体、覆盖冲突提示、数据集删除/重命名契约、
// 页签解析、聚合图表门控。抽在这里是为了让前端测试能在无 jsdom 的 node 环境里做行为断言
// （见 tests/finance-vault-pool-actions.test.js），组件只负责把状态接进来。
export const TABLE_NAME_RE = /^[a-z0-9_]{1,64}$/

/**
 * 清洗结果默认落 {源表}_clean（与后端 cleaning.clean_apply 的默认值一致）。
 * 表名上限 64 位：长源表名按后缀长度截断，避免默认名超限导致预览/应用按钮被禁用。
 * 空源表名没有合法的默认结果名，返回空串（调用方据此禁用提交）。
 */
export function defaultResultTable(source) {
  const suffix = '_clean'
  const base = String(source ?? '').trim()
  if (!base) return ''
  return `${base.slice(0, 64 - suffix.length)}${suffix}`
}

/** 目标数据集名与源表相同：清洗/另存会替换源数据集本身，必须在请求前拒绝。 */
export function sameDataset(target, source) {
  const t = String(target ?? '').trim()
  const s = String(source ?? '').trim()
  return !!t && t === s
}

/** 数据集表名白名单：小写字母/数字/下划线，1–64 位（与后端 validate_table_name 对齐）。 */
export function validateTableName(name) {
  return TABLE_NAME_RE.test(String(name ?? '').trim())
}

/**
 * 清洗请求体：显式携带结果表名与 allow_overwrite。
 * 数据池 UI 要求显式填写结果表名（默认 {源表}_clean，空名禁用提交）；target_table 仍
 * 原样透传，后端对空值兜底为 {table}_clean。allow_overwrite 默认 false，绝不静默覆盖
 * 已存在的结果表（后端对已存在目标返回 409）。
 */
export function buildCleanPayload(recipe = {}, { resultTable = '', allowOverwrite = false } = {}) {
  return {
    ops: {
      normalize_dirty_text: recipe.normalize_dirty_text !== false,
      drop_all_empty_rows: recipe.drop_all_empty_rows !== false,
      drop_all_empty_columns: recipe.drop_all_empty_columns !== false,
      dedupe_rows: recipe.dedupe_rows !== false,
      required_columns: Array.isArray(recipe.required_columns) ? [...recipe.required_columns] : [],
    },
    invalid_policy: recipe.invalid_policy === 'null_cell' ? 'null_cell' : 'drop_row',
    mark_outliers_iqr: !!recipe.mark_outliers_iqr,
    target_table: String(resultTable ?? '').trim(),
    allow_overwrite: !!allowOverwrite,
  }
}

/** 从 axios 错误里取后端 detail 文本（FastAPI 的字符串 detail 或 {message} 对象）。 */
function conflictDetail(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail.trim()
  if (detail && typeof detail.message === 'string') return detail.message.trim()
  return ''
}

/**
 * 应用清洗 409 时弹窗内的可操作提示。
 * 409 并非只有「结果表已存在」——DuckDB 视图异常同样映射 409，因此必须按 detail 判定；
 * 源表同名（后端 detail 不含「已存在」）也不得引导覆盖源数据（前端另有请求前拒绝）。
 */
export function describeCleanConflict(error, resultTable) {
  if (error?.response?.status !== 409) return ''
  if (!conflictDetail(error).includes('已存在')) return ''
  const name = String(resultTable ?? '').trim() || '结果数据集'
  return `结果数据集「${name}」已存在，未覆盖：勾选「允许覆盖已存在的结果表」后重试，或改用其它结果表名。`
}

// ---------- 聚合另存为数据集（与 clean/apply 同口径：显式目标名 + allow_overwrite） ----------

/** 默认目标表名 = {源表}_agg；按 64 位上限截断；空源表名返回空串（调用方禁用提交）。 */
export function defaultAggregateTable(table) {
  const suffix = '_agg'
  const base = String(table ?? '').trim()
  if (!base) return ''
  return `${base.slice(0, 64 - suffix.length)}${suffix}`
}

/** 聚合另存请求体：保留聚合条件（分组/指标/过滤/排序），显式带目标表名与覆盖开关。 */
export function buildAggregateSavePayload(spec = {}, { targetTable = '', allowOverwrite = false } = {}) {
  return {
    ...spec,
    target_table: String(targetTable ?? '').trim(),
    allow_overwrite: !!allowOverwrite,
  }
}

/**
 * 聚合另存 409 时弹窗内的可操作提示：只有 detail 明确「已存在」才提示覆盖，
 * 视图异常/源表同名/无 detail 一律交给全局拦截器，避免误导。
 */
export function describeAggregateSaveConflict(error, targetTable) {
  if (error?.response?.status !== 409) return ''
  if (!conflictDetail(error).includes('已存在')) return ''
  const name = String(targetTable ?? '').trim() || '目标数据集'
  return `数据集「${name}」已存在，未覆盖：勾选「允许覆盖已存在的数据集」后重试，或改名后保存。`
}

// ---------- 数据集删除 / 重命名（与 backend finance_vault router/service 的真实契约对齐） ----------

export function tablesUrl(table) {
  return `/finance/vault/tables/${encodeURIComponent(table)}`
}

/** 删除/重命名前的使用情况预检：引用条数、一致性 issues、是否需要 force。 */
export function datasetUsageUrl(table) {
  return `${tablesUrl(table)}/usage`
}

/** 删除必须带 confirm=<数据集名> 二次确认；被清洗日志引用或有未登记残留时还需 force=true。 */
export function deleteUrl(table, confirm, force = false) {
  const name = String(confirm ?? '').trim()
  return `${tablesUrl(table)}?confirm=${encodeURIComponent(name)}&force=${force ? 'true' : 'false'}`
}

export function renameUrl(table) {
  return `${tablesUrl(table)}/rename`
}

/** 后端 RenameIn 的字段名是 new_table（不是 new_name）。 */
export function renameRequestBody(newTable) {
  return { new_table: String(newTable ?? '').trim() }
}

/** 归一化 /tables/{table}/usage 预检响应：字段缺失不炸，也不误报「需要强制」。 */
export function normalizeUsage(payload) {
  const references = payload?.references || {}
  return {
    cleanLogs: Number(references.clean_logs) || 0,
    issues: Array.isArray(payload?.issues) ? payload.issues.filter((i) => typeof i === 'string') : [],
    requiresForce: !!payload?.requires_force,
  }
}

export function deleteConfirmText(table, { cleanLogs = 0, issues = [] } = {}) {
  const lines = [
    `删除数据集「${table}」后不可恢复（需重新上传入池）。`,
    '该数据集及其物理修订表会被清理，其它数据集不会级联删除。',
  ]
  if (cleanLogs > 0) lines.push(`注意：有 ${cleanLogs} 条清洗日志引用它，删除后历史配方将失去来源。`)
  if (issues.length) lines.push(`数据一致性提示：${issues.join('；')}。`)
  return lines.join('\n')
}

export function forceDeleteConfirmText(table, cleanLogs = 0) {
  const why = cleanLogs > 0 ? `被 ${cleanLogs} 条清洗日志引用` : '存在未登记残留'
  return `数据集「${table}」${why}，删除不可撤销且历史配方将失去来源。\n确认强制删除？`
}

export function renameConfirmText(from, to) {
  return `将数据集「${from}」重命名为「${to}」？\n`
    + '清洗日志中的来源/结果表名会同步改名；其它数据集的「聚合自 …」来源备注不会改写。'
    + '目标已存在或视图异常时后端会拒绝（409）。'
}

// ---------- 页签 ----------

const TAB_NAMES = new Set(['tables', 'analyze', 'sql'])

/**
 * 页签白名单：URL 显式 ?tab= 优先，其次路由 meta.tab（/finance/vault/query 声明 sql），
 * 未知值一律回落数据池——避免 ?tab=foo 让 el-tabs 没有任何 pane 选中（空白内容区）。
 */
export function resolveTab({ queryTab, metaTab } = {}) {
  const fromQuery = typeof queryTab === 'string' ? queryTab.trim() : ''
  if (TAB_NAMES.has(fromQuery)) return fromQuery
  const fromMeta = typeof metaTab === 'string' ? metaTab.trim() : ''
  return TAB_NAMES.has(fromMeta) ? fromMeta : 'tables'
}

// ---------- 聚合图表 ----------

/** 饼图必须有分组维度：无维度的聚合结果只有指标列，画饼图没有可解释的扇区。 */
export function canUsePie(groupBy) {
  return Array.isArray(groupBy) && groupBy.filter(Boolean).length > 0
}

export function normalizeChartType(type, groupBy) {
  if (type === 'pie' && !canUsePie(groupBy)) return 'bar'
  if (type === 'line' || type === 'bar' || type === 'pie') return type
  return 'bar'
}
