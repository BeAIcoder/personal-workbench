// 个人记账视图共用的纯函数（组件逻辑抽离，便于单测）

// 统计占比：后端 percent 已是 0-100 的百分数，直接格式化即可；
// 早期版本前端又 ×100，75% 会显示成 7500.0%、进度条直接拉满
export function formatPercent(percent) {
  return Number(percent || 0).toFixed(1)
}

// 分类是否属于当前交易类型：切换 收入/支出 时用于清理残留的旧分类，
// 否则旧分类与交易类型不一致，提交会被后端 400 拒绝
export function categoryMatchesKind(category, kind) {
  return !!category && category.kind === kind
}

// 统计页「当月支出分类构成」只取支出分类：后端 by_category 同时返回收入/支出，
// 原样渲染会把收入分类混进支出表与饼图，而 percent 的分母仍是支出，数字对不上
export function expenseCategories(list) {
  return (Array.isArray(list) ? list : []).filter((item) => item && item.kind === 'expense')
}
