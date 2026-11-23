// 数据池清洗 · T6A 回归：清洗报告弹窗、结果表名/覆盖勾选、数据集删除与重命名、
// SQL 页签 meta、无分组维度禁用饼图。
//
// 本仓库前端测试没有 jsdom/挂载能力（见 interaction-submit-race.test.js 的说明），
// 因此可执行的行为断言集中在 src/modules/finance-vault/poolLogic.js 的纯函数上；
// 组件侧沿用既有约定，对 .vue 源码做结构化断言（函数体按大括号配对切片）。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import manifest from '../src/modules/finance-vault/manifest'
import {
  buildCleanPayload,
  canUsePie,
  datasetUsageUrl,
  defaultResultTable,
  deleteConfirmText,
  deleteUrl,
  describeCleanConflict,
  forceDeleteConfirmText,
  normalizeChartType,
  normalizeUsage,
  renameConfirmText,
  renameRequestBody,
  renameUrl,
  resolveTab,
  sameDataset,
  tablesUrl,
  validateTableName,
} from '../src/modules/finance-vault/poolLogic'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(root, rel), 'utf8')

/** 按大括号配对取函数体（含函数头），避免用「下一个函数名」做脆弱切片。 */
function fnBody(src, headerNeedle) {
  const start = src.indexOf(headerNeedle)
  if (start < 0) throw new Error(`未找到函数：${headerNeedle}`)
  const open = src.indexOf('{', start)
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return src.slice(start, i + 1)
    }
  }
  throw new Error(`函数体大括号未闭合：${headerNeedle}`)
}

/** 取包含指定标记的那一行（单行 computed 声明没有大括号，fnBody 不适用）。 */
function lineWith(src, needle) {
  const line = src.split('\n').find((l) => l.includes(needle))
  if (!line) throw new Error(`未找到行：${needle}`)
  return line
}

const TABLE_PANEL = 'src/modules/finance-vault/views/parts/TablesPanel.vue'
const ANALYZE_PANEL = 'src/modules/finance-vault/views/parts/AnalyzePanel.vue'
const POOL_VIEW = 'src/modules/finance-vault/views/PoolView.vue'

const RECIPE = {
  normalize_dirty_text: true,
  drop_all_empty_rows: false,
  drop_all_empty_columns: true,
  dedupe_rows: true,
  required_columns: ['金额'],
  invalid_policy: 'null_cell',
  mark_outliers_iqr: true,
}

describe('SQL 页签：路由 meta 与 URL 优先级', () => {
  it('query 路由声明 meta.tab=sql，直达 /finance/vault/query 即 SQL 页签', () => {
    const query = manifest.routes.find((r) => r.path === '/finance/vault/query')
    expect(query).toBeTruthy()
    expect(query.meta).toEqual({ tab: 'sql' })
  })

  it('URL 显式 tab 优先于 meta：在 query 路由上切回「数据池」不会被 meta 拉回 SQL', () => {
    expect(resolveTab({ queryTab: 'tables', metaTab: 'sql' })).toBe('tables')
    expect(resolveTab({ queryTab: 'sql', metaTab: 'sql' })).toBe('sql')
    expect(resolveTab({ queryTab: undefined, metaTab: 'sql' })).toBe('sql')
    expect(resolveTab({})).toBe('tables')
    // 重复 query（?tab=a&tab=b）不是单一字符串，按缺省处理
    expect(resolveTab({ queryTab: ['a', 'b'], metaTab: 'sql' })).toBe('sql')
    // 白名单：未知页签回落 meta / tables，避免 el-tabs 无 pane 选中
    expect(resolveTab({ queryTab: 'foo', metaTab: 'sql' })).toBe('sql')
    expect(resolveTab({ queryTab: 'foo' })).toBe('tables')
    expect(resolveTab({ metaTab: 'bogus' })).toBe('tables')
  })

  it('PoolView 用 resolveTab 解析页签，切页签时把当前页签写回 URL', () => {
    const src = read(POOL_VIEW)
    expect(src).toContain('resolveTab({')
    expect(src).toContain('route.meta && route.meta.tab')
    // 切回 tables 时也必须显式写 URL，否则 meta.tab=sql 会把页签拉回 SQL
    expect(src).toMatch(/tab:\s*v\s*\}/)
  })
})

describe('清洗请求体：结果表名与 allow_overwrite', () => {
  it('默认结果表名 = {源表}_clean，超长按 64 位截断；空源表名不产生默认名', () => {
    expect(defaultResultTable('t_expense')).toBe('t_expense_clean')
    const name = defaultResultTable('a'.repeat(64))
    expect(name.length).toBe(64)
    expect(name.endsWith('_clean')).toBe(true)
    expect(validateTableName(name)).toBe(true)
    // 空源表名没有合法的默认结果名：返回空串，由调用方禁用提交（不再把 '_clean' 当正常预期）
    expect(defaultResultTable('')).toBe('')
  })

  it('buildCleanPayload 透传结果表名（去空格）与 allow_overwrite', () => {
    const payload = buildCleanPayload(RECIPE, { resultTable: '  t_expense_ok  ', allowOverwrite: true })
    expect(payload.target_table).toBe('t_expense_ok')
    expect(payload.allow_overwrite).toBe(true)
    expect(payload.ops).toEqual({
      normalize_dirty_text: true,
      drop_all_empty_rows: false,
      drop_all_empty_columns: true,
      dedupe_rows: true,
      required_columns: ['金额'],
    })
    expect(payload.invalid_policy).toBe('null_cell')
    expect(payload.mark_outliers_iqr).toBe(true)
  })

  it('未勾选覆盖时 allow_overwrite 必须为 false（不得静默覆盖已有结果表）', () => {
    const payload = buildCleanPayload(RECIPE, { resultTable: 't_expense_clean' })
    expect(payload.allow_overwrite).toBe(false)
  })

  it('结果表名留空交给后端默认，非法策略回退 drop_row', () => {
    const payload = buildCleanPayload({ ...RECIPE, invalid_policy: 'bogus' }, {})
    expect(payload.target_table).toBe('')
    expect(payload.invalid_policy).toBe('drop_row')
    expect(payload.ops.required_columns).toEqual(['金额'])
  })

  it('结果表名白名单：小写字母/数字/下划线，1–64 位', () => {
    expect(validateTableName('t_expense_2026')).toBe(true)
    expect(validateTableName('  t_ok  ')).toBe(true)
    expect(validateTableName('T_UPPER')).toBe(false)
    expect(validateTableName('t-dash')).toBe(false)
    expect(validateTableName('t space')).toBe(false)
    expect(validateTableName('')).toBe(false)
    expect(validateTableName('a'.repeat(65))).toBe(false)
  })

  it('409 只有 detail 明确「已存在」才提示覆盖；视图异常/同源/无 detail 一律不误导', () => {
    const conflict = { response: { status: 409, data: { detail: '结果表已存在：t_expense_clean（请改名，或显式允许覆盖）' } } }
    const msg = describeCleanConflict(conflict, 't_expense_clean')
    expect(msg).toContain('t_expense_clean')
    expect(msg).toContain('已存在')
    expect(msg).toContain('允许覆盖')
    // DuckDB 视图异常同样映射 409：绝不能提示「勾选覆盖」
    const duck = { response: { status: 409, data: { detail: '数据集视图不可用，请重新入仓：x' } } }
    expect(describeCleanConflict(duck, 't_expense_clean')).toBe('')
    // 源表同名（后端 detail 不含「已存在」）也不得引导覆盖——前端另有请求前拒绝
    const same = { response: { status: 409, data: { detail: '结果表名与源表相同，需显式允许覆盖（allow_overwrite=true）' } } }
    expect(describeCleanConflict(same, 't_expense')).toBe('')
    // 无 detail 的 409 无法判定为冲突，不能假设可覆盖
    expect(describeCleanConflict({ response: { status: 409 } }, 'x')).toBe('')
    expect(describeCleanConflict({ response: { status: 400 } }, 'x')).toBe('')
    expect(describeCleanConflict({ response: { status: 500 } }, 'x')).toBe('')
    expect(describeCleanConflict('cancel', 'x')).toBe('')
    // 结果表名缺失时也要有可读文案
    expect(describeCleanConflict({ response: { status: 409, data: { detail: '结果表已存在：x' } } }, '')).toContain('结果数据集')
  })

  it('sameDataset：目标与源表同名（请求前必须拒绝，禁止引导覆盖源数据）', () => {
    expect(sameDataset(' t_a ', 't_a')).toBe(true)
    expect(sameDataset('t_b', 't_a')).toBe(false)
    expect(sameDataset('', '')).toBe(false)
    expect(sameDataset(undefined, 't_a')).toBe(false)
    expect(sameDataset('t_a', '')).toBe(false)
  })
})

describe('数据结构删除/重命名：与后端真实契约为准', () => {
  it('删除走 DELETE ?confirm=&force=，预检走 /usage，重命名走 POST /rename', () => {
    expect(tablesUrl('t_expense')).toBe('/finance/vault/tables/t_expense')
    expect(tablesUrl('t a/b')).toBe('/finance/vault/tables/t%20a%2Fb')
    expect(datasetUsageUrl('t_expense')).toBe('/finance/vault/tables/t_expense/usage')
    expect(deleteUrl('t_expense', 't_expense')).toBe('/finance/vault/tables/t_expense?confirm=t_expense&force=false')
    expect(deleteUrl('t a', ' t a ', true)).toBe('/finance/vault/tables/t%20a?confirm=t%20a&force=true')
    expect(renameUrl('t a/b')).toBe('/finance/vault/tables/t%20a%2Fb/rename')
    // 后端 RenameIn 字段是 new_table（不是 new_name）
    expect(renameRequestBody('  t_next  ')).toEqual({ new_table: 't_next' })
  })

  it('normalizeUsage 归一化预检响应：引用条数/一致性提示/是否需强制', () => {
    expect(normalizeUsage({
      references: { clean_logs: 3, as_source: 1, as_result: 2 },
      issues: ['元数据已登记但数据仓视图缺失', 42],
      requires_force: true,
    })).toEqual({ cleanLogs: 3, issues: ['元数据已登记但数据仓视图缺失'], requiresForce: true })
    expect(normalizeUsage(null)).toEqual({ cleanLogs: 0, issues: [], requiresForce: false })
    expect(normalizeUsage({ references: { clean_logs: 'x' } })).toEqual({ cleanLogs: 0, issues: [], requiresForce: false })
  })

  it('删除确认文案说明不可恢复、不级联，并把引用条数与一致性提示摆到明面', () => {
    const plain = deleteConfirmText('t_expense')
    expect(plain).toContain('t_expense')
    expect(plain).toContain('不可恢复')
    expect(plain).toContain('不会级联')
    const text = deleteConfirmText('t_expense', { cleanLogs: 2, issues: ['视图缺失'] })
    expect(text).toContain('2 条清洗日志')
    expect(text).toContain('引用')
    expect(text).toContain('视图缺失')
  })

  it('强制删除二次确认说明引用/残留与不可撤销', () => {
    const refs = forceDeleteConfirmText('t_expense', 2)
    expect(refs).toContain('t_expense')
    expect(refs).toContain('2 条清洗日志')
    expect(refs).toContain('不可撤销')
    expect(forceDeleteConfirmText('t_expense')).toContain('未登记残留')
  })

  it('重命名确认文案：清洗日志引用会同步改名，来源备注不改写，冲突时后端拒绝', () => {
    const text = renameConfirmText('t_old', 't_new')
    expect(text).toContain('t_old')
    expect(text).toContain('t_new')
    expect(text).toContain('同步改名')
    expect(text).toContain('不会改写')
    expect(text).toContain('拒绝')
  })
})

describe('聚合图表：无分组维度禁用饼图', () => {
  it('canUsePie 只认至少一个非空分组维度', () => {
    expect(canUsePie([])).toBe(false)
    expect(canUsePie([''])).toBe(false)
    expect(canUsePie(['', ''])).toBe(false)
    expect(canUsePie(['区域'])).toBe(true)
    expect(canUsePie(undefined)).toBe(false)
  })

  it('normalizeChartType 把不可用的饼图回退柱状，其余类型原样保留', () => {
    expect(normalizeChartType('pie', [])).toBe('bar')
    expect(normalizeChartType('pie', ['区域'])).toBe('pie')
    expect(normalizeChartType('line', [])).toBe('line')
    expect(normalizeChartType('bar', ['区域'])).toBe('bar')
    expect(normalizeChartType(undefined, ['区域'])).toBe('bar')
  })
})

describe('TablesPanel：清洗报告弹窗与数据集恢复能力', () => {
  const src = read(TABLE_PANEL)

  it('cleanReportDialog 已声明（此前模板引用但未声明，运行时报未定义）', () => {
    expect(src).toMatch(/const cleanReportDialog = ref\(false\)/)
  })

  it('预览清洗后就打开清洗报告弹窗', () => {
    const body = fnBody(src, 'async function runCleanPreview()')
    expect(body).toContain('cleanReportDialog.value = true')
    expect(body).toContain('cleanReport.value = data')
  })

  it('应用清洗后打开报告弹窗并展示最终结果表名', () => {
    const body = fnBody(src, 'async function applyClean()')
    expect(body).toContain('cleanReportDialog.value = true')
    expect(body).toContain('data.result_table')
    expect(body).toContain('await load()')
  })

  it('打开/复用清洗上下文时复位报告与覆盖勾选', () => {
    const reset = fnBody(src, 'function resetCleanContext(table)')
    expect(reset).toContain('cleanReport.value = null')
    expect(reset).toContain('cleanReportDialog.value = false')
    expect(reset).toContain('cleanResultTable.value = defaultResultTable(table)')
    expect(reset).toContain('cleanAllowOverwrite.value = false')
    expect(reset).toContain('cleanConflictHint.value = \'\'')
  })

  it('清洗弹窗暴露结果表名与覆盖勾选，应用按钮校验表名', () => {
    expect(src).toContain('v-model="cleanResultTable"')
    expect(src).toContain('v-model="cleanAllowOverwrite"')
    expect(src).toContain(':disabled="!cleanReport || !cleanResultTableValid"')
    expect(fnBody(src, 'function cleanPayload()')).toContain('buildCleanPayload(recipe')
    const payloadBody = fnBody(src, 'function cleanPayload()')
    expect(payloadBody).toContain('resultTable: cleanResultTable.value')
    expect(payloadBody).toContain('allowOverwrite: cleanAllowOverwrite.value')
  })

  it('应用清洗 409 在弹窗内给出覆盖提示，不重复弹 Toast', () => {
    const body = fnBody(src, 'async function applyClean()')
    expect(body).toContain('describeCleanConflict(err, cleanResultTable.value)')
    expect(body).not.toContain('ElMessage.error')
    expect(src).toContain('v-if="cleanConflictHint"')
  })

  it('历史配方可回填结果表名并回到清洗弹窗（二次清洗可恢复）', () => {
    const body = fnBody(src, 'function reuseRecipe(row)')
    expect(body).toContain('row.result_table')
    expect(body).toContain('cleanDialog.value = true')
  })

  it('数据集行提供删除/重命名入口，且都有二次确认与失败兜底', () => {
    expect(src).toContain('@click="renameDataset(row)"')
    expect(src).toContain('@click="deleteDataset(row)"')

    const del = fnBody(src, 'async function deleteDataset(row)')
    expect(del).toContain('loadDatasetUsage(row.table)')
    expect(del).toContain('deleteConfirmText(row.table')
    expect(del).toContain('forceDeleteConfirmText(row.table')
    expect(del).toContain('api.delete(deleteUrl(row.table, row.table, force))')
    expect(del).toContain('await load()')
    expect(del).toMatch(/catch\s*\{/)

    const usage = fnBody(src, 'async function loadDatasetUsage(table)')
    expect(usage).toContain('normalizeUsage(data)')
    expect(usage).toContain('return null')
    expect(usage).toMatch(/catch\s*\{/)

    const ren = fnBody(src, 'async function renameDataset(row)')
    expect(ren).toContain('ElMessageBox.prompt')
    expect(ren).toContain('renameConfirmText(row.table, next)')
    expect(ren).toContain('api.post(renameUrl(row.table), renameRequestBody(next))')
    expect(ren).toContain('validateTableName(next)')
    expect(ren).toContain('await load()')
    expect(ren).toMatch(/catch\s*\{/)
  })
})

describe('AnalyzePanel：饼图按分组维度门控', () => {
  const src = read(ANALYZE_PANEL)

  it('饼图按钮绑定禁用态，图表类型经 normalizeChartType 归一', () => {
    expect(src).toContain('const pieEnabled = computed(() => canUsePie(group_by.value))')
    expect(src).toMatch(/:disabled="!pieEnabled"/)
    expect(src).toContain('const chartKind = computed(() => normalizeChartType(chartType.value, group_by.value))')
    expect(src).toContain("chartKind.value === 'pie'")
    expect(src).toContain("chartKind.value === 'line'")
  })

  it('分组维度被清空时把已选饼图回退柱状（避免禁用态仍渲染饼图）', () => {
    expect(src).toContain("if (chartType.value === 'pie' && !pieEnabled.value) chartType.value = 'bar'")
  })
})

describe('TablesPanel：源表覆盖边界、不可达分支与删除/重命名在途态', () => {
  const src = read(TABLE_PANEL)

  it('清洗目标与源表同名时请求前拒绝，并明确提示不能覆盖源数据', () => {
    const same = lineWith(src, 'const cleanSameAsSource = computed(')
    expect(same).toContain('sameDataset(cleanResultTable.value, cleanSource.value)')
    const valid = lineWith(src, 'const cleanResultTableValid = computed(')
    expect(valid).toContain('!cleanSameAsSource.value')
    expect(src).toContain('v-if="cleanSameAsSource"')
    expect(src).toContain('结果表名不能与源表相同')
    expect(src).toContain('不能覆盖源数据')
  })

  it('预览不再保留不可达的「留空用后端默认名」分支（表名必须显式）', () => {
    const body = fnBody(src, 'async function runCleanPreview()')
    expect(body).not.toContain('target_table_default')
    expect(body).toContain('if (!cleanResultTableValid.value) return')
  })

  it('不再导入未使用的 tablesUrl（URL 全部由 usage/delete/rename 组合）', () => {
    expect(src).not.toContain('tablesUrl')
    expect(src).toContain('datasetUsageUrl')
    expect(src).toContain('deleteUrl(row.table, row.table, force)')
  })

  it('删除/重命名共享在途标记：双击只发一次请求，按钮 loading/disabled', () => {
    expect(src).toContain('const datasetBusy = ref(\'\')')
    expect(src).toContain(':disabled="!!datasetBusy"')
    expect(src).toContain(':loading="busyWith(\'rename\', row.table)"')
    expect(src).toContain(':loading="busyWith(\'delete\', row.table)"')

    const busy = fnBody(src, 'function busyWith(kind, table)')
    expect(busy).toContain('datasetBusy.value === `${kind}:${table}`')

    for (const [header, tag] of [['async function deleteDataset(row)', 'delete'], ['async function renameDataset(row)', 'rename']]) {
      const body = fnBody(src, header)
      expect(body).toContain('if (datasetBusy.value) return')
      expect(body).toContain(`datasetBusy.value = \`${tag}:\${row.table}\``)
      expect(body).toMatch(/finally\s*\{[^}]*datasetBusy\.value = ''/s)
    }
  })
})
