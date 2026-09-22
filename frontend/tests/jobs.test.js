import { describe, expect, it } from 'vitest'
import { agentLabel, agentOptionLabel, formatSchedule, modeLabel, runStatusLabel, runStatusTag } from '../src/utils/jobs'

describe('调度方式展示', () => {
  it('interval：分钟与整小时折算', () => {
    expect(formatSchedule({ schedule_type: 'interval', interval_minutes: 30 })).toBe('每 30 分钟')
    expect(formatSchedule({ schedule_type: 'interval', interval_minutes: 120 })).toBe('每 2 小时')
    expect(formatSchedule({ schedule_type: 'interval', interval_minutes: 90 })).toBe('每 90 分钟')
  })

  it('daily：每天 HH:MM', () => {
    expect(formatSchedule({ schedule_type: 'daily', daily_at: '08:30' })).toBe('每天 08:30')
  })

  it('缺字段返回 -', () => {
    expect(formatSchedule({ schedule_type: 'interval', interval_minutes: null })).toBe('-')
    expect(formatSchedule({ schedule_type: 'daily', daily_at: null })).toBe('-')
    expect(formatSchedule(null)).toBe('-')
  })
})

describe('专家名映射', () => {
  const agents = [
    { name: 'tax_expert', display_name: '税务专家', emoji: '🧮' },
    { name: 'finance_bot', display_name: '', emoji: '📊' },
  ]
  it('null = 智能路由', () => {
    expect(agentLabel(null, agents)).toBe('智能路由')
    expect(agentLabel('', agents)).toBe('智能路由')
  })
  it('命中列表用中文名，display_name 为空回退 name', () => {
    expect(agentLabel('tax_expert', agents)).toBe('税务专家')
    expect(agentLabel('finance_bot', agents)).toBe('finance_bot')
  })
  it('列表里找不到时显示原始 name', () => {
    expect(agentLabel('ghost', agents)).toBe('ghost')
  })
  it('下拉 option：emoji + 中文名', () => {
    expect(agentOptionLabel(agents[0])).toBe('🧮 税务专家')
  })
})

describe('模式与运行状态映射', () => {
  it('工作模式中文映射', () => {
    expect(modeLabel.standard).toBe('标准执行')
    expect(modeLabel.readonly).toBe('只读咨询')
    expect(modeLabel.deep).toBe('深度研究')
  })
  it('运行状态：ok 绿 / error 红 / skipped 灰', () => {
    expect(runStatusTag.ok).toBe('success')
    expect(runStatusTag.error).toBe('danger')
    expect(runStatusTag.skipped).toBe('info')
    expect(runStatusLabel['']).toBe('未运行')
  })
})
