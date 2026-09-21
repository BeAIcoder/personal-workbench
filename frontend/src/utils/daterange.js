import dayjs from 'dayjs'

/**
 * 计算日历视图需要拉取的时间范围：
 * 当月前后各多取 7 天，覆盖日历网格里上/下月溢出的格子。
 */
export function monthRange(date) {
  const base = dayjs(date)
  return {
    start: base.startOf('month').subtract(7, 'day').format('YYYY-MM-DD'),
    end: base.endOf('month').add(7, 'day').format('YYYY-MM-DD'),
  }
}
