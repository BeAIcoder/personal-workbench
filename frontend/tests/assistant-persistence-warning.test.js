// Task 2 fix round 1（I2）：成功回复的持久化失败必须作为「非终止告警」显示，
// 不得与错误气泡混淆，也不能被 done.reply 覆盖；流式 done 与非流式完整结果同口径。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import { ElMessage } from 'element-plus'
import ChatPanel from '../src/components/assistant/ChatPanel.vue'
import { persistenceNotice } from '../src/utils/assistant'

// node 环境无 DOM：mock 生命周期与 API，直接驱动编译后的 setup()
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onBeforeUnmount: () => {},
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
      history: vi.fn(),
    },
  }
})

const fetchMock = vi.fn()

function mountChat(sessionId = 's_persist_01') {
  const props = reactive({
    sessionId,
    agents: [],
    selectedAgent: null,
    llmInfo: {},
    models: [],
    workModes: {},
    reasoning: 'default',
    teamConfig: {},
  })
  const emit = vi.fn()
  const ctx = ChatPanel.setup(props, { expose: () => {}, emit })
  return { ctx, emit }
}

function sseOk(frames) {
  const encoder = new TextEncoder()
  const payloads = frames.map((f) => (typeof f === 'string' ? f : `data: ${JSON.stringify(f)}`))
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: () =>
          Promise.resolve(
            i < payloads.length
              ? { done: false, value: encoder.encode(payloads[i++] + '\n\n') }
              : { done: true, value: undefined },
          ),
      }),
    },
  }
}

function jsonOk(body) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json; charset=utf-8' },
    json: () => Promise.resolve(body),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

describe('持久化失败告警（done）', () => {
  it('done 带 persisted=false：正文正常显示，告警独立存在且不是错误气泡', async () => {
    const { ctx } = mountChat()
    ctx.draft.value = '保存失败要提示'
    fetchMock.mockResolvedValue(
      sseOk([
        { type: 'meta', agent: { label: '财税专家', color: '#123456' } },
        { type: 'delta', text: '半' },
        {
          type: 'done',
          reply: '完整回复',
          persisted: false,
          persistence_warning: '回复已生成，但保存到会话失败；请复制保留本次回复。',
        },
      ]),
    )

    await ctx.send()

    const ai = ctx.messages.value[1]
    expect(ai.content).toBe('完整回复')
    expect(ai.error).toBe(false)
    expect(ai.persistWarning).toContain('保存到会话失败')
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.loading.value).toBe(false)
  })

  it('done 带 persisted=true：不显示告警', async () => {
    const { ctx } = mountChat()
    ctx.draft.value = '保存成功'
    fetchMock.mockResolvedValue(
      sseOk([{ type: 'done', reply: '正常回复', persisted: true, persistence_warning: '' }]),
    )

    await ctx.send()

    const ai = ctx.messages.value[1]
    expect(ai.content).toBe('正常回复')
    expect(ai.persistWarning).toBe('')
    expect(ai.error).toBe(false)
  })
})

describe('非流式完整结果同口径', () => {
  it('JSON 成功响应：正文、思考、轨迹照常落地，持久化告警可见', async () => {
    const { ctx } = mountChat()
    ctx.draft.value = '非流式结果'
    fetchMock.mockResolvedValue(
      jsonOk({
        ok: true,
        reply: '非流式回复',
        thinking: '先想一下',
        trace: [{ tool: '查任务', args: {}, result: 'ok' }],
        routed_by: '指定',
        persisted: false,
        persistence_warning: '回复已生成，但保存到会话失败；请复制保留本次回复。',
      }),
    )

    await ctx.send()

    const ai = ctx.messages.value[1]
    expect(ai.content).toBe('非流式回复')
    expect(ai.thinking).toBe('先想一下')
    expect(ai.trace).toHaveLength(1)
    expect(ai.error).toBe(false)
    expect(ai.persistWarning).toContain('保存到会话失败')
    expect(ctx.loading.value).toBe(false)
  })

  it('persistenceNotice 只对明确 persisted=false 出提示，未知/成功一律空串', () => {
    expect(persistenceNotice({ persisted: false, persistence_warning: '告警' })).toBe('告警')
    expect(persistenceNotice({ persisted: true, persistence_warning: '旧告警' })).toBe('')
    expect(persistenceNotice({ persistence_warning: '形状不全' })).toBe('')
    expect(persistenceNotice({})).toBe('')
    expect(persistenceNotice(null)).toBe('')
  })
})
