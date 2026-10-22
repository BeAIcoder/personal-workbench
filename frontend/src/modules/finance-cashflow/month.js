// 现金流回填月份：前端下拉与提交都用 1-12 口径（后端 Form(ge=1, le=12)）。
// 抽成纯函数以便单测——getMonth() 是 0-11，历史上直接用它导致 1 月提交 0 被 422、
// 其余月份整体偏小一个月。
export function currentMonth(now = new Date()) {
  return now.getMonth() + 1
}

export function monthOptions(count = 12) {
  return Array.from({ length: count }, (_, i) => i + 1)
}
