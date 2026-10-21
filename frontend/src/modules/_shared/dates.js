// 共享日期工具：各模块视图共用
// 必须用本地时区格式化；toISOString() 是 UTC，凌晨时段日期/月份会错一格
import dayjs from 'dayjs'

export function localToday(now) {
  return dayjs(now).format('YYYY-MM-DD')
}

export function localMonth(now) {
  return dayjs(now).format('YYYY-MM')
}

export function buildMonthOptions(now, count = 12) {
  const base = dayjs(now).startOf('month')
  const out = []
  for (let i = 0; i < count; i++) out.push(base.subtract(i, 'month').format('YYYY-MM'))
  return out
}
