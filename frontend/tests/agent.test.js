import axios from 'axios'
import { describe, expect, it, vi } from 'vitest'
import { assistantApi } from '../src/api'
import { agentModelLabel, isValidAgentName, normalizeKeywords } from '../src/utils/agent'

vi.mock('axios', () => {
  const instance = {
    post: vi.fn(),
    interceptors: { response: { use: vi.fn() } },
  }
  return { default: { create: () => instance } }
})

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn() },
}))

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

describe('路由预览 API', () => {
  it('把可选 AI 仲裁开关序列化为 include_llm，默认 false', () => {
    const api = axios.create()
    api.post.mockClear()

    assistantApi.agents.routeTest('安排')
    assistantApi.agents.routeTest('安排', true)

    expect(api.post).toHaveBeenNthCalledWith(1, '/assistant/agents/route-test', {
      text: '安排',
      include_llm: false,
    })
    expect(api.post).toHaveBeenNthCalledWith(2, '/assistant/agents/route-test', {
      text: '安排',
      include_llm: true,
    })
  })
})
