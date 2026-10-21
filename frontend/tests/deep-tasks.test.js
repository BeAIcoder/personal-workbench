// deep2 tasks 区域回归：带 query 的深链首屏必须加载列表。
//
// TASKS-D2-008 探针发现：`#/tasks?foo=bar`、`?overdue=false`、`?sort=bogus`
// 这类 query 不会改变任何筛选条件，mergeTaskFilters 返回 changed=false，
// onMounted 只看 query 非空就调用 applyRoute()，而 applyRoute 在 changed=false
// 时直接 return，既不加载也不回落 load(1) —— 首屏列表恒为空（共 0 条）。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { mergeTaskFilters, needsInitialLoad } from '../src/utils/tasks'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function baseFilters() {
  return { status: null, priority: null, q: '', sort: 'created_desc', overdue: null }
}

describe('TASKS-D2-008 深链 query 未改变筛选时仍要首屏加载', () => {
  it('未知参数 / overdue=false / 非法 sort 均不改变筛选', () => {
    for (const query of [{ foo: 'bar' }, { overdue: 'false' }, { sort: 'bogus' }]) {
      expect(mergeTaskFilters(baseFilters(), query).changed).toBe(false)
    }
  })

  it('query 非空但 applyRoute 未生效时必须回落加载', () => {
    expect(needsInitialLoad('/tasks', { foo: 'bar' }, false)).toBe(true)
    expect(needsInitialLoad('/tasks', { overdue: 'false' }, false)).toBe(true)
  })

  it('applyRoute 已触发加载时不重复请求', () => {
    expect(needsInitialLoad('/tasks', { status: 'done' }, true)).toBe(false)
  })

  it('无 query / 非 tasks 路由始终加载', () => {
    expect(needsInitialLoad('/tasks', {}, false)).toBe(true)
    expect(needsInitialLoad('/assistant', { auto: 'x' }, false)).toBe(true)
  })

  it('TasksView 的 onMounted 使用 needsInitialLoad 兜底', () => {
    const src = readFileSync(join(root, 'src/views/TasksView.vue'), 'utf8')
    expect(src).toContain('needsInitialLoad')
    const mounted = src.slice(src.indexOf('onMounted('), src.indexOf('watch('))
    expect(mounted).toContain('load(1)')
  })
})
