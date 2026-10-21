// 全局搜索的纯逻辑：建议列表整形、过期响应判定、日程命中与跳转日期解析。
// 抽成纯函数便于 vitest 在 node 环境直接覆盖（组件本身不挂载 DOM）。

export const SEARCH_EMPTY_TEXT = '未找到相关内容'

const SEARCH_GROUPS = [
  ['tasks', 'task'],
  ['schedules', 'schedule'],
  ['notes', 'note'],
]

// 把 /api/search 的三类结果按「任务 → 日程 → 笔记」顺序摊平；
// 无命中时返回一条 type=null 的占位项，点击后不跳转。
export function buildSuggestionItems(data) {
  const items = []
  for (const [key, type] of SEARCH_GROUPS) {
    for (const item of data?.[key] || []) {
      items.push({ ...item, type: item.type || type, value: item.title })
    }
  }
  if (!items.length) {
    return [{ value: SEARCH_EMPTY_TEXT, title: SEARCH_EMPTY_TEXT, snippet: '', type: null }]
  }
  return items
}

// 占位项没有 type：选中时把输入框恢复为用户实际输入的关键词，避免被
// 「未找到相关内容」覆盖后继续按这串文案搜索。
export function keywordAfterSuggestion(item, typedQuery) {
  return item && item.type ? String(item.value ?? item.title ?? '') : String(typedQuery ?? '')
}

// 请求序号守卫：只有最后一次发出的搜索允许回调，防止慢响应覆盖新关键词的建议。
export function createLatestGuard() {
  let latest = 0
  return {
    next: () => ++latest,
    isCurrent: (token) => token === latest,
  }
}

// 日程命中规则与后端 /api/search 一致：标题 / 地点 / 备注，忽略大小写。
export function matchesScheduleQuery(event, keyword) {
  const q = String(keyword ?? '').trim().toLowerCase()
  if (!q || !event) return false
  return [event.title, event.location, event.description].some((field) =>
    String(field ?? '').toLowerCase().includes(q),
  )
}

// 解析搜索跳转带来的 ?date=YYYY-MM-DD；非法值返回 null（保持当前选中日期）。
export function parseSearchDate(value) {
  const text = String(value ?? '').slice(0, 10)
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : null
}
