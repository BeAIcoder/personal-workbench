// 现金流回填月份：前端下拉与提交都用 1-12 口径（后端 Form(ge=1, le=12)）。
// 抽成纯函数以便单测——getMonth() 是 0-11，历史上直接用它导致 1 月提交 0 被 422、
// 其余月份整体偏小一个月。
export function currentMonth(now = new Date()) {
  return now.getMonth() + 1
}

// 回填年份：YYYY 字符串（与 el-date-picker type="year" value-format="YYYY" 一致），
// 后端 actual_year 为 int Form 参数，字符串会被正常转换；缺省会静默取当前年，
// 补录往年数据必须让用户显式选择。
export function currentYear(now = new Date()) {
  return String(now.getFullYear())
}

export function monthOptions(count = 12) {
  return Array.from({ length: count }, (_, i) => i + 1)
}
