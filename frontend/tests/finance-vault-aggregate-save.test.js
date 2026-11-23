// 数据池清洗 · 聚合另存为数据集：目标表名 + allow_overwrite + 同名 409 的弹窗内补救。
//
// 背景：后端 aggregate/save 已与 clean/apply 对齐——目标已存在时默认 409（不静默 replace），
// 但前端仍用 prompt 收集表名且不带 allow_overwrite，用户遇到 409 无任何补救手段。
// 与 T6A 同约定：纯函数做行为断言，组件侧对 .vue 源码做结构化断言（无 jsdom）。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  buildAggregateSavePayload,
  defaultAggregateTable,
  describeAggregateSaveConflict,
  sameDataset,
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

const PANEL = read('src/modules/finance-vault/views/parts/AnalyzePanel.vue')

describe('聚合另存：目标表名与 allow_overwrite 的纯逻辑', () => {
  it('默认目标表名 = {源表}_agg，超长时截断且仍落在 64 位白名单内；空源表名不产生默认名', () => {
    expect(defaultAggregateTable('t_expense')).toBe('t_expense_agg')
    const name = defaultAggregateTable('a'.repeat(64))
    expect(name.length).toBe(64)
    expect(name.endsWith('_agg')).toBe(true)
    expect(validateTableName(name)).toBe(true)
    expect(defaultAggregateTable('')).toBe('')
  })

  it('buildAggregateSavePayload 保留聚合条件并显式带 target_table/allow_overwrite', () => {
    const spec = {
      group_by: ['区域'],
      metrics: [{ column: '金额', agg: 'sum' }],
      filters: [],
      order_by: '',
      order_dir: 'desc',
    }
    expect(buildAggregateSavePayload(spec, { targetTable: '  t_agg  ', allowOverwrite: true })).toEqual({
      ...spec,
      target_table: 't_agg',
      allow_overwrite: true,
    })
    expect(buildAggregateSavePayload(spec, {})).toMatchObject({ target_table: '', allow_overwrite: false })
    // 不修改调用方传入的 spec
    expect(spec).not.toHaveProperty('target_table')
  })

  it('409 只有 detail 明确「已存在」才提示覆盖；视图异常/同源/无 detail 不误导', () => {
    const conflict = { response: { status: 409, data: { detail: '结果表已存在：t_agg（请改名，或显式允许覆盖）' } } }
    const msg = describeAggregateSaveConflict(conflict, 't_agg')
    expect(msg).toContain('t_agg')
    expect(msg).toContain('已存在')
    expect(msg).toContain('允许覆盖')
    expect(msg).toContain('改名')
    expect(describeAggregateSaveConflict({ response: { status: 409, data: { detail: '数据集视图不可用，请重新入仓' } } }, 't_agg')).toBe('')
    expect(describeAggregateSaveConflict({ response: { status: 409, data: { detail: '结果表名与源表相同，需显式允许覆盖' } } }, 't_agg')).toBe('')
    expect(describeAggregateSaveConflict({ response: { status: 409 } }, 't_agg')).toBe('')
    expect(describeAggregateSaveConflict({ response: { status: 400 } }, 't_agg')).toBe('')
    expect(describeAggregateSaveConflict('cancel', 't_agg')).toBe('')
    // 表名缺失时也有可读文案
    expect(describeAggregateSaveConflict({ response: { status: 409, data: { detail: '结果表已存在：x' } } }, '')).toContain('目标数据集')
  })
})

describe('AnalyzePanel：另存为弹窗（目标表名 + 覆盖勾选 + 409 补救援）', () => {
  it('模板暴露目标表名、覆盖勾选与冲突提示，入口改用弹窗', () => {
    expect(PANEL).toContain('v-model="saveTarget"')
    expect(PANEL).toContain('v-model="saveAllowOverwrite"')
    expect(PANEL).toContain('v-else-if="saveHint"')
    expect(PANEL).toContain(':disabled="!saveTargetValid"')
    expect(PANEL).toContain('@click="openSaveAs"')
  })

  it('打开时回填默认表名并复位覆盖勾选与冲突提示', () => {
    const body = fnBody(PANEL, 'function openSaveAs()')
    expect(body).toContain('defaultAggregateTable(table.value)')
    expect(body).toContain('saveAllowOverwrite.value = false')
    expect(body).toContain('saveHint.value = \'\'')
    expect(body).toContain('saveDialog.value = true')
  })

  it('提交带 target_table/allow_overwrite，成功后才关弹窗并刷新数据集列表', () => {
    const body = fnBody(PANEL, 'async function submitSaveAs()')
    expect(body).toContain('buildAggregateSavePayload(payload()')
    expect(body).toContain('allowOverwrite: saveAllowOverwrite.value')
    expect(body).toContain('await load()')
    const close = body.indexOf('saveDialog.value = false')
    const post = body.indexOf('await api.post')
    expect(post).toBeGreaterThanOrEqual(0)
    expect(close).toBeGreaterThan(post)
  })

  it('409 在弹窗内提示并保留弹窗（不无补救地只弹 Toast）', () => {
    const body = fnBody(PANEL, 'async function submitSaveAs()')
    expect(body).toContain('describeAggregateSaveConflict(err, saveTarget.value)')
    expect(body).not.toContain('ElMessage.error')
  })

  it('不再用 prompt 盲填表名（旧实现没有覆盖入口，409 后卡死）', () => {
    expect(PANEL).not.toContain('ElMessageBox')
    expect(PANEL).not.toContain('target_table: value')
  })
})

describe('AnalyzePanel：目标表名不得等于源表（覆盖源数据的安全边界）', () => {
  it('saveTargetValid 请求前拒绝同源，并在弹窗内明确提示不能覆盖源数据', () => {
    const same = lineWith(PANEL, 'const saveSameAsSource = computed(')
    expect(same).toContain('sameDataset(saveTarget.value, table.value)')
    const valid = lineWith(PANEL, 'const saveTargetValid = computed(')
    expect(valid).toContain('!saveSameAsSource.value')
    expect(PANEL).toContain('v-if="saveSameAsSource"')
    expect(PANEL).toContain('目标表名不能与源表相同')
    expect(PANEL).toContain(':disabled="!saveTargetValid"')
  })
})
