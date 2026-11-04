// 定时任务页的纯展示逻辑：调度文案、专家名映射、模式与运行状态映射

export const modeLabel = { standard: '标准执行', readonly: '只读咨询', deep: '深度研究' }

export const runStatusLabel = { ok: '成功', error: '失败', skipped: '跳过', unknown: '结果未知', '': '未运行' }
export const runStatusTag = { ok: 'success', error: 'danger', skipped: 'info', unknown: 'warning', '': 'info' }

// 定时任务无人值守：标准/深度模式会执行写入类工具，保存前必须让用户明确知道
export function modeWarning(mode) {
  if (mode === 'standard' || mode === 'deep') {
    return '定时任务无人值守：该模式会执行写入类工具，可能修改工作台数据，请确认后再保存。'
  }
  return ''
}

// 调度方式展示：interval「每 N 分钟」（整小时折算为小时），daily「每天 HH:MM」
export function formatSchedule(job) {
  if (!job) return '-'
  if (job.schedule_type === 'daily') return job.daily_at ? `每天 ${job.daily_at}` : '-'
  if (job.schedule_type === 'cron') return job.cron_expr ? `Cron ${job.cron_expr}` : '-'
  const n = job.interval_minutes
  if (!n) return '-'
  if (n >= 60 && n % 60 === 0) return `每 ${n / 60} 小时`
  return `每 ${n} 分钟`
}

// agent_name → 专家中文名；null/空 = 智能路由；列表里找不到时回退显示原始 name
export function agentLabel(name, agents) {
  if (!name) return '智能路由'
  const agent = (agents || []).find((a) => a.name === name)
  return agent ? agent.display_name || agent.name : name
}

// 专家下拉 option 展示：emoji + 中文名
export function agentOptionLabel(agent) {
  return `${agent.emoji || '🤖'} ${agent.display_name || agent.name}`
}
