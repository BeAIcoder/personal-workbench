// Task 7 / FE L-132：思考档位保存失败必须回滚 UI，不能显示「已选高档位」而实际没保存。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onBeforeUnmount: () => {},
    onMounted: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    assistantApi: {
      ...actual.assistantApi,
      config: { ...actual.assistantApi.config, saveGlobals: vi.fn(), test: vi.fn() },
      history: vi.fn(),
      providers: { ...actual.assistantApi.providers, activate: vi.fn() },
    },
  }
})

const { assistantApi } = await import('../src/api')
const ChatPanel = (await import('../src/components/assistant/ChatPanel.vue')).default

function mount(reasoning = 'medium') {
  const props = reactive({
    sessionId: '',
    agents: [],
    selectedAgent: null,
    llmInfo: {},
    models: [],
    workModes: {},
    reasoning,
    teamConfig: {},
  })
  const emit = vi.fn()
  const ctx = ChatPanel.setup(props, { expose: () => {}, emit })
  return { ctx, props, emit }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('思考档位保存', () => {
  it('失败时回滚到上一次已保存档位，并保留错误提示', async () => {
    const { ctx } = mount('medium')
    ctx.reasoningEffort.value = 'high'
    assistantApi.config.saveGlobals.mockRejectedValueOnce(new Error('保存失败'))
    await ctx.saveReasoning()
    expect(ctx.reasoningEffort.value).toBe('medium')
  })

  it('成功后采用后端返回档位并通知父组件', async () => {
    const { ctx, emit } = mount('medium')
    ctx.reasoningEffort.value = 'high'
    assistantApi.config.saveGlobals.mockResolvedValueOnce({ data: { config: { reasoning_effort: 'high' } } })
    await ctx.saveReasoning()
    expect(ctx.reasoningEffort.value).toBe('high')
    expect(emit).toHaveBeenCalledWith('reasoning-saved', expect.objectContaining({ reasoning_effort: 'high' }))
  })

  it('选择「跟随模型」失败时也回滚，不会显示已清除', async () => {
    const { ctx } = mount('medium')
    ctx.reasoningEffort.value = 'default'
    assistantApi.config.saveGlobals.mockRejectedValueOnce(new Error('保存失败'))
    await ctx.saveReasoning()
    expect(ctx.reasoningEffort.value).toBe('medium')
  })
})

describe('并发保存序号（M5）', () => {
  it('同值两次保存：先发的失败晚到不得回滚后发的成功结果', async () => {
    const { ctx } = mount('medium')
    let rejectFirst
    const first = new Promise((resolve, reject) => { rejectFirst = reject })
    assistantApi.config.saveGlobals
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({ data: { config: { reasoning_effort: 'high' } } })

    ctx.reasoningEffort.value = 'high'
    const p1 = ctx.saveReasoning()
    const p2 = ctx.saveReasoning()
    await p2
    rejectFirst(new Error('旧请求失败'))
    await p1

    expect(ctx.reasoningEffort.value).toBe('high')
  })
})
