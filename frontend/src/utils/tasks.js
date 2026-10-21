import { fmtDT, priorityLabel, statusLabel } from './format'

// hash 路由下 query 值可能是数组（重复参数），统一取第一个
function firstValue(value) {
  return Array.isArray(value) ? value[0] : value
}

// 路由 query → 任务筛选条件。
// ?overdue=true 既启用后端逾期筛选，也切换为按截止时间排序；
// ?sort=due_asc（概览「即将到期任务」查看全部/列表项跳转）需显式生效。
const TASK_SORTS = new Set(['created_desc', 'due_asc', 'priority_desc'])

export function parseTasksQuery(query) {
  const q = firstValue(query?.q)
  const status = firstValue(query?.status)
  const overdueRaw = String(firstValue(query?.overdue) ?? '').toLowerCase()
  const overdue = overdueRaw === 'true' || overdueRaw === '1'
  const sortRaw = firstValue(query?.sort)
  const sort = overdue ? 'due_asc' : (TASK_SORTS.has(sortRaw) ? String(sortRaw) : null)
  return {
    q: q ? String(q) : '',
    status: status ? String(status) : null,
    overdue: overdue || null,
    sort,
  }
}

// 把路由 query 合并进当前筛选条件。
// 同文档 hash 跳转不会重建组件，query 中缺失的键必须清掉旧值，否则筛选会残留。
export function mergeTaskFilters(current, query) {
  const incoming = parseTasksQuery(query)
  const next = { ...current }
  let changed = false
  for (const key of ['q', 'status', 'overdue']) {
    if (incoming[key] !== next[key]) {
      next[key] = incoming[key]
      changed = true
    }
  }
  if (incoming.sort && incoming.sort !== next.sort) {
    next.sort = incoming.sort
    changed = true
  } else if (!incoming.overdue && current.overdue && !incoming.sort && next.sort === 'due_asc') {
    // 离开 ?overdue 且 query 未显式指定排序时回到默认排序，避免排序粘滞
    next.sort = 'created_desc'
    changed = true
  }
  return { filters: next, changed }
}

// 首屏是否还需要显式 load(1)。
// onMounted 只在 query 非空时走 applyRoute()，但未知参数 / overdue=false / 非法 sort
// 不会改变任何筛选（changed=false），applyRoute 不会发请求，此时必须回落加载，
// 否则深链首屏恒为空列表。
export function needsInitialLoad(path, query, appliedRoute) {
  if (path !== '/tasks') return true
  if (!query || Object.keys(query).length === 0) return true
  return !appliedRoute
}

// 任务列表请求参数：跳过空筛选，page/page_size 始终带上
export function buildTaskListParams(filters, page, pageSize) {
  const params = {}
  for (const [key, value] of Object.entries(filters)) {
    if (value !== null && value !== undefined && value !== '') params[key] = value
  }
  return { ...params, page, page_size: pageSize }
}

// AI 分析入口：必须用 router.push 的对象形式保留 auto 查询参数
export function buildTaskAnalysisRoute(row) {
  const due = row.due_date ? '，截止 ' + fmtDT(row.due_date) : '（无截止时间）'
  const prompt =
    `请分析这个任务并给出执行建议，把分析要点存为一条笔记（标签用「AI分析」），` +
    `如需跟进请一并创建后续任务：「${row.title}」（状态 ${statusLabel[row.status]}，优先级 ${priorityLabel[row.priority]}${due}，分类 ${row.category || '无'}）。` +
    (row.description ? `任务描述：${row.description}` : '')
  return { path: '/assistant', query: { auto: prompt } }
}
