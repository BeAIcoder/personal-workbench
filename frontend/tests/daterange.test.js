import { describe, expect, it } from 'vitest'
import { monthRange } from '../src/utils/daterange'

describe('日历月份范围', () => {
  it('当月前后各扩 7 天，覆盖日历溢出格子', () => {
    expect(monthRange('2026-09-07')).toEqual({ start: '2026-08-25', end: '2026-10-07' })
  })

  it('跨年月份正确', () => {
    expect(monthRange('2026-01-15')).toEqual({ start: '2025-12-25', end: '2026-02-07' })
    expect(monthRange('2026-12-31')).toEqual({ start: '2026-11-24', end: '2027-01-07' })
  })
})
