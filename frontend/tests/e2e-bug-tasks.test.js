// tasks 区域 E2E 深度探针（dev/e2e/probes/tasks.py）发现的回归测试。
// 覆盖：路由 query 与筛选同步（含 hash 同文档跳转残留）、?overdue 排序+逾期筛选、
// AI 分析跳转的 auto query 构造、列表请求失败时的异常兜底。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  buildTaskAnalysisRoute,
  buildTaskListParams,
  mergeTaskFilters,
  parseTasksQuery,
} from '../src/utils/tasks'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function readSrc(rel) {
  return readFileSync(join(root, rel), 'utf8')
}

function baseFilters() {
  return { status: null, priority: null, q: '', sort: 'created_desc', overdue: null }
}

describe('TASKS-UI-013 路由 status 回填且不残留上一次 query 的筛选', () => {
  it('?status=done 回填状态下拉', () => {
    const { filters, changed } = mergeTaskFilters(baseFilters(), { status: 'done' })
    expect(changed).toBe(true)
    expect(filters.status).toBe('done')
  })

  it('从 ?q=旧关键词 跳到 ?status=done 时清掉残留 q（探针截图里旧 q 导致 0 行）', () => {
    const stale = { ...baseFilters(), q: '路由参数-目标任务' }
    const { filters } = mergeTaskFilters(stale, { status: 'done' })
    expect(filters.q).toBe('')
    expect(filters.status).toBe('done')
  })

  it('回到无参数的 /tasks 时清空全部路由筛选', () => {
    const stale = { ...baseFilters(), q: 'x', status: 'done', overdue: true, sort: 'due_asc' }
    const { filters, changed } = mergeTaskFilters(stale, {})
    expect(changed).toBe(true)
    expect(filters).toMatchObject({ q: '', status: null, overdue: null, sort: 'created_desc' })
  })

  it('重复 query 参数取第一个值', () => {
    expect(parseTasksQuery({ status: ['done', 'todo'] }).status).toBe('done')
  })

  it('无变化时不重复触发列表加载', () => {
    expect(mergeTaskFilters(baseFilters(), {}).changed).toBe(false)
  })
})

describe('TASKS-UI-024 ?overdue=true 同时切换排序与逾期筛选', () => {
  it('排序切到 due_asc，并启用后端 overdue 筛选', () => {
    const { filters } = mergeTaskFilters(baseFilters(), { overdue: 'true' })
    expect(filters.sort).toBe('due_asc')
    expect(filters.overdue).toBe(true)
  })

  it('列表请求参数带 overdue=true 与 sort=due_asc', () => {
    const { filters } = mergeTaskFilters(baseFilters(), { overdue: 'true' })
    expect(buildTaskListParams(filters, 1, 10)).toMatchObject({
      overdue: true,
      sort: 'due_asc',
      page: 1,
      page_size: 10,
    })
  })

  it('普通路由不带 overdue 参数', () => {
    const params = buildTaskListParams(baseFilters(), 1, 10)
    expect('overdue' in params).toBe(false)
    expect(params.sort).toBe('created_desc')
  })

  it('离开 ?overdue 后恢复默认排序，避免排序粘滞', () => {
    const onOverdue = mergeTaskFilters(baseFilters(), { overdue: 'true' }).filters
    const { filters } = mergeTaskFilters(onOverdue, {})
    expect(filters.sort).toBe('created_desc')
    expect(filters.overdue).toBeNull()
  })
})

describe('TASKS-UI-026 AI 分析跳转保留 auto query', () => {
  const row = {
    title: 'AI分析-目标任务',
    status: 'todo',
    priority: 'medium',
    due_date: null,
    category: '',
    description: '核对进项发票',
  }

  it('跳转目标为 /assistant，query.auto 携带任务标题与描述', () => {
    const target = buildTaskAnalysisRoute(row)
    expect(target.path).toBe('/assistant')
    expect(target.query.auto).toContain('AI分析-目标任务')
    expect(target.query.auto).toContain('核对进项发票')
  })

  it('TasksView 用 router.push 的对象形式跳转（不再裸跳 /assistant）', () => {
    const src = readSrc('src/views/TasksView.vue')
    expect(src).toContain('router.push(buildTaskAnalysisRoute(row))')
    expect(src).not.toMatch(/router\.push\(\s*['"]\/assistant['"]\s*\)/)
  })
})

describe('TASKS-UI-030 列表加载失败不产生未捕获异常', () => {
  it('load() 捕获请求异常并由 finally 复位 loading', () => {
    const src = readSrc('src/views/TasksView.vue')
    const body = src.slice(src.indexOf('async function load'), src.indexOf('function openCreate'))
    expect(body).toContain('catch')
    expect(body).toContain('finally')
    expect(body).toContain('loading.value = false')
  })
})
