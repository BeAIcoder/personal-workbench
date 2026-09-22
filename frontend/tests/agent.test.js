import { describe, expect, it } from 'vitest'
import { agentModelLabel, isValidAgentName, normalizeKeywords } from '../src/utils/agent'

describe('专家 name 校验', () => {
  it('合法名称', () => {
    expect(isValidAgentName('tax_expert')).toBe(true)
    expect(isValidAgentName('a1')).toBe(true)
    expect(isValidAgentName('z')).toBe(true)
  })

  it('非法名称：大写开头/数字开头/特殊字符/空', () => {
    expect(isValidAgentName('Tax')).toBe(false)
    expect(isValidAgentName('1tax')).toBe(false)
    expect(isValidAgentName('tax-expert')).toBe(false)
    expect(isValidAgentName('税务')).toBe(false)
    expect(isValidAgentName('')).toBe(false)
    expect(isValidAgentName(null)).toBe(false)
  })
})

describe('关键词规整', () => {
  it('去空白去重并保持顺序', () => {
    expect(normalizeKeywords([' 增值税 ', '增值税', '', '报税', null, '报税'])).toEqual(['增值税', '报税'])
    expect(normalizeKeywords(undefined)).toEqual([])
  })
})

describe('专家绑定模型显示', () => {
  const models = [{ id: 1, label: 'deepseek-v4' }, { id: 2, model: 'qwen3' }]
  it('model_id 为空 = 跟随全局默认', () => {
    expect(agentModelLabel({ model_id: null }, models)).toBe('跟随全局默认')
  })
  it('命中模型列表用 label，兜底用 model 或编号', () => {
    expect(agentModelLabel({ model_id: 1 }, models)).toBe('deepseek-v4')
    expect(agentModelLabel({ model_id: 2 }, models)).toBe('qwen3')
    expect(agentModelLabel({ model_id: 9 }, models)).toBe('模型 #9')
  })
})
