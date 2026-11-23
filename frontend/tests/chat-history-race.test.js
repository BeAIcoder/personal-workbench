// T6B 会话历史竞态：A 慢 B 快时只显示 B。
// 同一 ChatPanel 上连续切换会话，慢的旧响应（成功或失败）都不得覆盖/干扰最新会话。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessage } from 'element-plus'
import { assistantApi } from '../src/api'
import ChatPanel from '../src/components/assistant/ChatPanel.vue'

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

vi.mock('../src/api', () => ({
  API_BASE: '/api',
  assistantApi: {
    history: vi.fn(),
  },
}))

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function historyPayload(text) {
  return { data: { messages: [{ role: 'user', content: text, created_at: '2026-11-11T10:00:00' }] } }
}

function mountChat() {
  return ChatPanel.setup(
    {
      sessionId: 's_a',
      agents: [],
      selectedAgent: null,
      llmInfo: {},
      models: [],
      workModes: {},
      reasoning: 'default',
    },
    { expose: () => {}, emit: vi.fn() },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('会话历史加载竞态', () => {
  it('A 慢 B 快：B 先返回后，A 的迟到成功不得覆盖 B 的消息', async () => {
    const a = deferred()
    const b = deferred()
    assistantApi.history.mockImplementation((sid) => (sid === 's_a' ? a.promise : b.promise))
    const ctx = mountChat()

    const pendingA = ctx.loadHistory('s_a')
    const pendingB = ctx.loadHistory('s_b')

    b.resolve(historyPayload('B 的消息'))
    await pendingB
    expect(ctx.messages.value.map((m) => m.content)).toEqual(['B 的消息'])

    a.resolve(historyPayload('A 的消息'))
    await pendingA
    expect(ctx.messages.value.map((m) => m.content)).toEqual(['B 的消息'])
  })

  it('A 慢 B 快：B 成功后的迟到 A 失败不弹错误提示', async () => {
    const a = deferred()
    const b = deferred()
    assistantApi.history.mockImplementation((sid) => (sid === 's_a' ? a.promise : b.promise))
    const ctx = mountChat()

    const pendingA = ctx.loadHistory('s_a')
    const pendingB = ctx.loadHistory('s_b')

    b.resolve(historyPayload('B 的消息'))
    await pendingB

    a.reject(new Error('network down'))
    await pendingA
    expect(ctx.messages.value.map((m) => m.content)).toEqual(['B 的消息'])
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('B 失败后迟到的 A 成功也不得显示旧会话消息', async () => {
    const a = deferred()
    const b = deferred()
    assistantApi.history.mockImplementation((sid) => (sid === 's_a' ? a.promise : b.promise))
    const ctx = mountChat()

    const pendingA = ctx.loadHistory('s_a')
    const pendingB = ctx.loadHistory('s_b')

    b.reject(new Error('B failed'))
    await pendingB
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ctx.messages.value).toEqual([])

    a.resolve(historyPayload('A 的消息'))
    await pendingA
    expect(ctx.messages.value).toEqual([])
  })

  it('最新请求失败照常提示一次', async () => {
    const b = deferred()
    assistantApi.history.mockReturnValue(b.promise)
    const ctx = mountChat()

    const pendingB = ctx.loadHistory('s_b')
    b.reject(new Error('boom'))
    await pendingB
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
  })

  it('清空气泡（新会话/删除会话）后，在途历史响应不得回填', async () => {
    const a = deferred()
    assistantApi.history.mockReturnValue(a.promise)
    const ctx = mountChat()

    const pending = ctx.loadHistory('s_a')
    ctx.clearMessages()
    a.resolve(historyPayload('A 的消息'))
    await pending
    expect(ctx.messages.value).toEqual([])
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('连续切换时按请求先后决定：最后发起的请求获胜', async () => {
    const a = deferred()
    const b = deferred()
    const c = deferred()
    const bySid = { s_a: a, s_b: b, s_c: c }
    assistantApi.history.mockImplementation((sid) => bySid[sid].promise)
    const ctx = mountChat()

    const pendingA = ctx.loadHistory('s_a')
    const pendingB = ctx.loadHistory('s_b')
    const pendingC = ctx.loadHistory('s_c')

    c.resolve(historyPayload('C 的消息'))
    await pendingC
    a.resolve(historyPayload('A 的消息'))
    await pendingA
    expect(ctx.messages.value.map((m) => m.content)).toEqual(['C 的消息'])
    b.resolve(historyPayload('B 的消息'))
    await pendingB
    expect(ctx.messages.value.map((m) => m.content)).toEqual(['C 的消息'])
  })
})
