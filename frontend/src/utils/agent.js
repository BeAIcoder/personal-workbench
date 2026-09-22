// 专家 name 规则：小写字母开头，仅小写字母/数字/下划线（与后端一致）
export const AGENT_NAME_PATTERN = /^[a-z][a-z0-9_]*$/

export function isValidAgentName(name) {
  return AGENT_NAME_PATTERN.test(name || '')
}

// 关键词输入规整：去空白、去重，保持原顺序
export function normalizeKeywords(list) {
  const seen = new Set()
  const out = []
  for (const raw of list || []) {
    const k = String(raw || '').trim()
    if (!k || seen.has(k)) continue
    seen.add(k)
    out.push(k)
  }
  return out
}

// 在模型列表中找专家绑定的模型显示名；model_id 为空 = 跟随全局默认
export function agentModelLabel(agent, models) {
  if (agent.model_id == null) return '跟随全局默认'
  const m = (models || []).find((x) => x.id === agent.model_id)
  return m ? m.label || m.model : `模型 #${agent.model_id}`
}
