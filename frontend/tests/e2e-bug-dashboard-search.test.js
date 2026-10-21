// dashboard-search 区域 E2E 探测发现的 bug 回归测试。
// 对应探测脚本 dev/e2e/probes/dashboard-search.py：
// - SEARCH-UI-005 日程命中点击后 /schedules 忽略 q（不筛选、不定位日期）
// - SEARCH-UI-006 无结果占位项被选中后把输入框改成「未找到相关内容」
// - SEARCH-UI-007 慢响应覆盖新关键词的建议（过期响应竞态）
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  SEARCH_EMPTY_TEXT,
  buildSuggestionItems,
  createLatestGuard,
  keywordAfterSuggestion,
  matchesScheduleQuery,
  parseSearchDate,
} from '../src/utils/search'
import { mergeTaskFilters, parseTasksQuery } from '../src/utils/tasks'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function readSrc(rel) {
  return readFileSync(join(root, rel), 'utf8')
}

describe('SEARCH-UI-005 日程搜索命中带入筛选与日期', () => {
  it('matchesScheduleQuery 与后端一致：标题 / 地点 / 备注任一命中，忽略大小写', () => {
    const ev = { title: '月度会议', location: 'A301', description: '讨论 Q4 预算' }
    expect(matchesScheduleQuery(ev, '月度')).toBe(true)
    expect(matchesScheduleQuery(ev, 'a301')).toBe(true)
    expect(matchesScheduleQuery(ev, 'q4')).toBe(true)
    expect(matchesScheduleQuery(ev, '  预算  ')).toBe(true)
    expect(matchesScheduleQuery(ev, '不存在')).toBe(false)
    expect(matchesScheduleQuery(ev, '  ')).toBe(false)
    expect(matchesScheduleQuery(null, '月度')).toBe(false)
  })

  it('parseSearchDate 只接受 YYYY-MM-DD，非法值返回 null', () => {
    expect(parseSearchDate('2026-11-15')).toBe('2026-11-15')
    expect(parseSearchDate('2026-11-15T10:00:00')).toBe('2026-11-15')
    expect(parseSearchDate('2026/11/15')).toBeNull()
    expect(parseSearchDate('')).toBeNull()
    expect(parseSearchDate(undefined)).toBeNull()
  })

  it('GlobalSearch 对日程结果附带 date 参数；ScheduleView 消费 q/date 并过滤月历', () => {
    const search = readSrc('src/components/GlobalSearch.vue')
    expect(search).toContain('parseSearchDate(item.time)')
    expect(search).toContain('query.date = date')
    const view = readSrc('src/views/ScheduleView.vue')
    expect(view).toContain('matchesScheduleQuery')
    expect(view).toContain('visibleEvents')
    expect(view).toContain('route.query')
    // 空态提示应说明搜索无命中，而不是笼统的「暂无日程」
    expect(view).toContain('未找到与')
  })
})

describe('SEARCH-UI-006 无结果占位项不得污染搜索框', () => {
  it('占位项无 type：选中后恢复用户输入的关键词', () => {
    const [placeholder] = buildSuggestionItems({ tasks: [], schedules: [], notes: [] })
    expect(placeholder.type).toBeNull()
    expect(placeholder.value).toBe(SEARCH_EMPTY_TEXT)
    expect(keywordAfterSuggestion(placeholder, '不存在的词')).toBe('不存在的词')
  })

  it('正常结果保留标题作为 value', () => {
    const [item] = buildSuggestionItems({
      tasks: [{ id: 1, type: 'task', title: '增值税申报', snippet: '核对进项' }],
      schedules: [],
      notes: [],
    })
    expect(item.type).toBe('task')
    expect(item.value).toBe('增值税申报')
    expect(keywordAfterSuggestion(item, '增值税')).toBe('增值税申报')
  })

  it('GlobalSearch 的 onSelect 对占位项提前返回并回填 lastQuery', () => {
    const src = readSrc('src/components/GlobalSearch.vue')
    expect(src).toContain('if (!item.type)')
    expect(src).toContain('keywordAfterSuggestion(item, lastQuery.value)')
  })
})

describe('SEARCH-UI-007 过期搜索响应不得覆盖新建议', () => {
  it('createLatestGuard：旧 token 失效，新 token 生效', () => {
    const guard = createLatestGuard()
    const first = guard.next()
    const second = guard.next()
    expect(guard.isCurrent(first)).toBe(false)
    expect(guard.isCurrent(second)).toBe(true)
  })

  it('GlobalSearch 在回调前检查序号，且异常分支同样检查', () => {
    const src = readSrc('src/components/GlobalSearch.vue')
    const checks = src.match(/searchGuard\.isCurrent\(token\)/g) || []
    expect(checks.length).toBeGreaterThanOrEqual(2)
    expect(src).toContain('const token = searchGuard.next()')
  })
})

describe('DASH-UI-004 概览跳转 ?sort=due_asc 必须生效', () => {
  it('parseTasksQuery 解析显式排序；overdue 优先；非法值忽略', () => {
    expect(parseTasksQuery({ sort: 'due_asc' }).sort).toBe('due_asc')
    expect(parseTasksQuery({ sort: 'priority_desc' }).sort).toBe('priority_desc')
    expect(parseTasksQuery({ sort: 'bogus' }).sort).toBeNull()
    expect(parseTasksQuery({}).sort).toBeNull()
    // ?overdue=true 始终按截止时间升序，不被冲突的 sort 覆盖
    expect(parseTasksQuery({ overdue: 'true', sort: 'created_desc' }).sort).toBe('due_asc')
  })

  it('mergeTaskFilters 应用显式排序，并支持排序切换', () => {
    const base = { q: '', status: null, overdue: null, sort: 'created_desc' }
    const next = mergeTaskFilters(base, { sort: 'due_asc' })
    expect(next.changed).toBe(true)
    expect(next.filters.sort).toBe('due_asc')
    // 同文档内从 due_asc 切到 priority_desc 也要生效
    const again = mergeTaskFilters(next.filters, { sort: 'priority_desc' })
    expect(again.changed).toBe(true)
    expect(again.filters.sort).toBe('priority_desc')
  })

  it('离开 ?overdue 且未显式指定排序时恢复默认排序', () => {
    const current = { q: '', status: null, overdue: true, sort: 'due_asc' }
    const reset = mergeTaskFilters(current, {})
    expect(reset.filters.overdue).toBeNull()
    expect(reset.filters.sort).toBe('created_desc')
    // 显式 sort 时不得被 overdue 复位逻辑覆盖
    const explicit = mergeTaskFilters(current, { sort: 'priority_desc' })
    expect(explicit.filters.overdue).toBeNull()
    expect(explicit.filters.sort).toBe('priority_desc')
  })

  it('TasksView 用 router.replace/push 合并 query 且概览入口使用 ?sort=due_asc', () => {
    const view = readSrc('src/views/TasksView.vue')
    expect(view).toContain('mergeTaskFilters')
    const dash = readSrc('src/views/DashboardView.vue')
    expect(dash).toContain('/tasks?sort=due_asc')
  })
})

describe('buildSuggestionItems 结果整形', () => {
  it('按任务 → 日程 → 笔记顺序摊平，并保留 type/snippet', () => {
    const items = buildSuggestionItems({
      tasks: [{ id: 1, type: 'task', title: 'T', snippet: 'ts' }],
      schedules: [{ id: 2, type: 'schedule', title: 'S', snippet: 'ss', time: '2026-11-15T10:00:00' }],
      notes: [{ id: 3, type: 'note', title: 'N', snippet: 'ns' }],
    })
    expect(items.map((x) => x.type)).toEqual(['task', 'schedule', 'note'])
    expect(items.map((x) => x.snippet)).toEqual(['ts', 'ss', 'ns'])
  })

  it('缺失分组字段时安全返回占位项', () => {
    expect(buildSuggestionItems(undefined)).toHaveLength(1)
    expect(buildSuggestionItems({ tasks: null })[0].type).toBeNull()
  })
})
