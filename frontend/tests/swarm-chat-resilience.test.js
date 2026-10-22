// swarm 任务 frontend-chat-resilience：聊天韧性回归（基线缺陷 F03 及配套交互）。
// 覆盖：
//  1) 发送失败：恢复草稿与待发附件（沿用原 previewUrl，不重复创建 objectURL），回滚乐观气泡；
//  2) 用户 abort 与真失败区分：abort 只标记「已停止生成」，不弹错误、不恢复草稿附件；
//  3) 流式生成中到达 auto 指令：给明确提示、不清 URL、生成结束后自动补跑（可重试）；
//  4) 历史加载失败有反馈；发送中 Enter/按钮有明确提示；
//  5) 附件上传前 10MB 客户端校验（既有行为回归锁）；
//  6) Blob URL 延迟回收，避免进行中的图片破图；
//  7) SSE 正常流协议与成功路径不回归；
//  8) 失败回滚覆盖请求期间新输入的草稿/新附件（合并保留，不互相覆盖），且只回滚本次气泡；
//  9) 显式文本发送（建议/auto）不清空用户草稿；清 URL 与真正发出之间无异步窗口可丢指令。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { assistantApi } from '../src/api'
import AssistantView from '../src/views/AssistantView.vue'
import ChatPanel from '../src/components/assistant/ChatPanel.vue'

// node 环境无 DOM：mock 生命周期钩子与 API，直接驱动编译后的 setup()
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onBeforeUnmount: () => {},
  }
})

const routeState = vi.hoisted(() => ({ query: {} }))
const routerReplace = vi.hoisted(() => vi.fn())

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ replace: routerReplace }),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    assistantApi: {
      ...actual.assistantApi,
      history: vi.fn(),
      config: {
        ...actual.assistantApi.config,
        upload: vi.fn(),
        saveGlobals: vi.fn(),
      },
      sessions: {
        list: vi.fn(),
        create: vi.fn(),
        rename: vi.fn(),
        remove: vi.fn(),
        archive: vi.fn(),
        unarchive: vi.fn(),
      },
    },
  }
})

const fetchMock = vi.fn()
let urlSeq = 0

function mountChat(sessionId = 's_chat_01') {
  return ChatPanel.setup(
    {
      sessionId,
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

// AssistantView 的 chatRef 由父组件持有：loading 用真实 ref 暴露（与 defineExpose 的
// ref 经 proxyRefs 解包后的形态一致），父级 watch(() => chatRef.value?.loading) 才能感知翻转
function mountViewWithChat() {
  const ctx = AssistantView.setup({}, { expose: () => {}, emit: () => {} })
  const loadingRef = ref(false)
  const chat = {
    abortStream: vi.fn(),
    clearMessages: vi.fn(),
    loadHistory: vi.fn(),
    send: vi.fn(),
    get loading() {
      return loadingRef.value
    },
  }
  ctx.chatRef.value = chat
  return { ctx, chat, setLoading: (v) => (loadingRef.value = v) }
}

// 一个图片附件：走后端上传成功 + 本地 objectURL 预览
async function attachImage(ctx, name = 'chart.png') {
  assistantApi.config.upload.mockResolvedValue({
    data: { name, path: `/tmp/${name}`, kind: 'image' },
  })
  await ctx.onFilePicked({ raw: { name, size: 2048, type: 'image/png' } })
}

// SSE 成功响应：字符串帧原样发送（如 data: [DONE]），对象帧按 data: {json} 编码
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

const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  vi.clearAllMocks()
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  routeState.query = {}
  urlSeq = 0
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:mock/' + ++urlSeq)
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('发送失败恢复草稿与待发附件（F03）', () => {
  it('网络失败：恢复草稿与附件（同一 previewUrl）、回滚乐观气泡、错误 toast', async () => {
    const ctx = mountChat()
    await attachImage(ctx)
    const previewUrl = ctx.attachments.value[0].previewUrl
    expect(previewUrl).toBe('blob:mock/1')
    ctx.draft.value = '帮我分析这个图表'
    fetchMock.mockRejectedValue(new Error('Network Error'))

    await expect(ctx.send()).resolves.toBeUndefined()

    // 输入区回到发送前状态，可直接重试
    expect(ctx.draft.value).toBe('帮我分析这个图表')
    expect(ctx.attachments.value).toHaveLength(1)
    expect(ctx.attachments.value[0].previewUrl).toBe(previewUrl)
    // 恢复不重复创建 objectURL（全程只创建过一次）
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    // 未送达的这对气泡回滚，不在对话里留残骸
    expect(ctx.messages.value).toHaveLength(0)
    expect(ElMessage.error).toHaveBeenCalledWith('Network Error')
    expect(ctx.loading.value).toBe(false)
  })

  it('HTTP 500：解析 detail 文案提示，草稿与附件同样恢复', async () => {
    const ctx = mountChat()
    await attachImage(ctx)
    ctx.draft.value = '总结上周'
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      text: () => Promise.resolve(JSON.stringify({ detail: '模型未配置，请先到设置中添加' })),
    })

    await ctx.send()

    expect(ElMessage.error).toHaveBeenCalledWith('模型未配置，请先到设置中添加')
    expect(ctx.draft.value).toBe('总结上周')
    expect(ctx.attachments.value).toHaveLength(1)
    expect(ctx.messages.value).toHaveLength(0)
  })

  it('失败后重发成功：沿用同一 objectURL，请求体带恢复的附件', async () => {
    const ctx = mountChat()
    await attachImage(ctx)
    ctx.draft.value = '帮我分析这个图表'
    fetchMock.mockRejectedValueOnce(new Error('Network Error'))
    await ctx.send()
    expect(ctx.attachments.value).toHaveLength(1)

    fetchMock.mockResolvedValue(
      sseOk([
        { type: 'meta', agent: { label: '财税专家', color: '#123456' } },
        { type: 'delta', text: '正' },
        { type: 'done', reply: '正文' },
      ]),
    )
    await ctx.send()

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(ctx.messages.value).toHaveLength(2)
    expect(ctx.messages.value[0].images).toEqual(['blob:mock/1'])
    const body = JSON.parse(fetchMock.mock.calls[1][1].body)
    expect(body.attachments).toEqual([{ name: 'chart.png', path: '/tmp/chart.png' }])
    expect(ctx.messages.value[1].content).toBe('正文')
    expect(ctx.loading.value).toBe(false)
  })
})

describe('失败回滚覆盖请求期间的新输入（第二轮复核）', () => {
  it('请求期间新打字+新附件：草稿合并保留、附件全部回到输入区、不重复创建 objectURL', async () => {
    const ctx = mountChat()
    await attachImage(ctx, 'first.png')
    const firstUrl = ctx.attachments.value[0].previewUrl
    ctx.draft.value = '先看这张图'
    let rejectFetch
    fetchMock.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectFetch = reject
      }),
    )
    const pending = ctx.send()
    // 发送即清空输入区；请求进行中用户继续输入并追加附件（输入区此时并未禁用）
    expect(ctx.attachments.value).toEqual([])
    expect(ctx.draft.value).toBe('')
    ctx.draft.value = '补充：还要看预算表'
    await attachImage(ctx, 'second.png')

    rejectFetch(new Error('Network Error'))
    await expect(pending).resolves.toBeUndefined()

    // 失败原文与请求期间新输入合并保留（原文在前），互不覆盖
    expect(ctx.draft.value).toBe('先看这张图\n补充：还要看预算表')
    // 失败附件与请求期间新增附件都回到输入区；恢复沿用原 previewUrl，不重复创建 objectURL
    expect(ctx.attachments.value.map((a) => a.name)).toEqual(['first.png', 'second.png'])
    expect(ctx.attachments.value[0].previewUrl).toBe(firstUrl)
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2)
    expect(ctx.messages.value).toHaveLength(0)
    expect(ctx.loading.value).toBe(false)
  })

  it('失败回滚只移除本次未送达的气泡，既有对话不动', async () => {
    const ctx = mountChat()
    fetchMock.mockResolvedValueOnce(sseOk([{ type: 'done', reply: '第一轮回复' }]))
    ctx.draft.value = '第一轮'
    await ctx.send()
    expect(ctx.messages.value).toHaveLength(2)

    ctx.draft.value = '第二轮'
    fetchMock.mockRejectedValueOnce(new Error('boom'))
    await ctx.send()

    expect(ctx.messages.value).toHaveLength(2)
    expect(ctx.messages.value[0].content).toBe('第一轮')
    expect(ctx.messages.value[1].content).toBe('第一轮回复')
    expect(ctx.draft.value).toBe('第二轮')
  })
})

describe('显式文本发送不清空用户草稿（第二轮复核）', () => {
  it('建议/auto 形态发送：用户正在输入的草稿原样保留', async () => {
    const ctx = mountChat()
    ctx.draft.value = '我正在输入的问题'
    fetchMock.mockResolvedValue(sseOk([{ type: 'done', reply: '好的' }]))

    await ctx.send('逾期任务有哪些')  // 建议按钮 / auto 指令的调用形态

    expect(ctx.messages.value.map((m) => m.content)).toEqual(['逾期任务有哪些', '好的'])
    expect(ctx.draft.value).toBe('我正在输入的问题')
  })

  it('发送草稿本身：仍然清空输入框（既有行为不回归）', async () => {
    const ctx = mountChat()
    ctx.draft.value = '草稿本身'
    fetchMock.mockResolvedValue(sseOk([{ type: 'done', reply: '好的' }]))

    await ctx.send()

    expect(ctx.draft.value).toBe('')
  })
})

describe('区分用户 abort 与真失败', () => {
  it('用户 abort：标记已停止生成、不算错误、不弹错误 toast、不恢复草稿附件', async () => {
    const ctx = mountChat()
    fetchMock.mockImplementation(
      (_url, opts) =>
        new Promise((_resolve, reject) => {
          opts.signal.addEventListener('abort', () => {
            const e = new Error('aborted')
            e.name = 'AbortError'
            reject(e)
          })
        }),
    )
    await attachImage(ctx)
    ctx.draft.value = '帮我看图'
    const pending = ctx.send()
    // 发送即清空输入区（附件已随请求发出）
    expect(ctx.attachments.value).toEqual([])
    expect(ctx.draft.value).toBe('')

    ctx.abortStream()
    await expect(pending).resolves.toBeUndefined()

    const aiMsg = ctx.messages.value[1]
    expect(aiMsg.content).toBe('（已停止生成）')
    expect(aiMsg.error).toBe(false)
    expect(aiMsg.streaming).toBe(false)
    expect(ElMessage.error).not.toHaveBeenCalled()
    // abort 是用户主动打断而非失败：草稿与附件不恢复
    expect(ctx.draft.value).toBe('')
    expect(ctx.attachments.value).toEqual([])
    expect(ctx.loading.value).toBe(false)
  })
})

describe('流式生成中 auto 指令不静默清除', () => {
  it('生成中：给明确提示、不清 URL、不立即执行', async () => {
    const { ctx, chat, setLoading } = mountViewWithChat()
    setLoading(true)
    routeState.query.auto = '总结上月费用'
    await ctx.tryAutoRun()

    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('总结上月费用'))
    expect(chat.send).not.toHaveBeenCalled()
    // URL 上的 auto 参数保留：刷新/重进仍可重试
    expect(routerReplace).not.toHaveBeenCalled()
    expect(routeState.query.auto).toBe('总结上月费用')
  })

  it('生成结束后：自动补跑保留的指令，执行后才清 URL', async () => {
    const { ctx, chat, setLoading } = mountViewWithChat()
    setLoading(true)
    routeState.query.auto = '总结上月费用'
    await ctx.tryAutoRun()
    expect(chat.send).not.toHaveBeenCalled()

    setLoading(false)
    await flush()
    await flush()

    expect(chat.send).toHaveBeenCalledWith('总结上月费用')
    expect(routerReplace).toHaveBeenCalledTimes(1)
  })

  it('指令执行瞬间用户手动发送：不静默丢弃（URL 已清但指令必须先真正发出）', async () => {
    const { ctx, chat, setLoading } = mountViewWithChat()
    routeState.query.auto = '总结上月费用'
    let loading = false
    const delivered = []
    chat.send.mockImplementation((p) => {
      // 真实 ChatPanel 的 loading 守卫：生成中再发会被拦下且无人补跑
      if (loading) return
      loading = true
      setLoading(true)
      delivered.push(p)
    })

    const running = ctx.tryAutoRun()
    loading = true  // 用户恰好在指令执行瞬间手动发了一条
    await running
    setLoading(false)
    await flush()
    await flush()

    // 指令必须在清 URL 之前真正发出，不能落进「URL 已清、pendingAuto 已清、发送被守卫拦下」的静默丢失
    expect(delivered).toEqual(['总结上月费用'])
    expect(routerReplace).toHaveBeenCalledTimes(1)
  })
})

describe('历史加载失败有反馈', () => {
  it('history 拒绝：ElMessage.error 明确提示，不再静默忽略', async () => {
    const ctx = mountChat('s_hist')
    assistantApi.history.mockRejectedValue(new Error('Network Error'))

    await expect(ctx.loadHistory('s_hist')).resolves.toBeUndefined()

    expect(ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('历史'))
    expect(ctx.messages.value).toEqual([])
  })

  it('history 成功：正常渲染历史消息', async () => {
    const ctx = mountChat('s_hist_ok')
    assistantApi.history.mockResolvedValue({
      data: {
        messages: [
          { role: 'user', content: '你好', created_at: '2026-10-22T10:00:00' },
          { role: 'assistant', content: '在的', agent_label: '助手', created_at: '2026-10-22T10:00:05' },
        ],
      },
    })

    await ctx.loadHistory('s_hist_ok')

    expect(ctx.messages.value).toHaveLength(2)
    expect(ctx.messages.value[1].agentLabel).toBe('助手')
    expect(ElMessage.error).not.toHaveBeenCalled()
  })
})

describe('发送中 Enter/按钮明确提示', () => {
  it('生成中再次发送：明确提示且不发起新请求', async () => {
    const ctx = mountChat()
    fetchMock.mockImplementation(
      (_url, opts) =>
        new Promise((_resolve, reject) => {
          opts.signal.addEventListener('abort', () => {
            const e = new Error('aborted')
            e.name = 'AbortError'
            reject(e)
          })
        }),
    )
    ctx.draft.value = '第一条'
    const pending = ctx.send()
    expect(ctx.loading.value).toBe(true)

    ctx.draft.value = '第二条'
    await ctx.send()

    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('正在生成'))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    // 清理：打断第一条，避免悬挂
    ctx.abortStream()
    await expect(pending).resolves.toBeUndefined()
  })
})

describe('附件 10MB 客户端校验（既有行为回归锁）', () => {
  it('超过 10MB：上传前拦截并提示，不产生附件', async () => {
    const ctx = mountChat()
    await ctx.onFilePicked({ raw: { name: 'big.png', size: 10 * 1024 * 1024 + 1, type: 'image/png' } })

    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('10MB'))
    expect(assistantApi.config.upload).not.toHaveBeenCalled()
    expect(ctx.attachments.value).toEqual([])
    expect(ctx.uploading.value).toBe(false)
  })

  it('恰好 10MB：放行进入上传', async () => {
    const ctx = mountChat()
    assistantApi.config.upload.mockResolvedValue({ data: { name: 'ok.csv', path: '/tmp/ok.csv', kind: 'file' } })
    await ctx.onFilePicked({ raw: { name: 'ok.csv', size: 10 * 1024 * 1024, type: 'text/csv' } })

    expect(assistantApi.config.upload).toHaveBeenCalledTimes(1)
    expect(ctx.attachments.value).toHaveLength(1)
  })
})

describe('Blob URL 延迟回收', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('移除附件：不同步 revoke，延迟后回收（进行中的图片不破图）', async () => {
    const ctx = mountChat()
    await attachImage(ctx)
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()

    ctx.removeAttachment(0)
    // 同步 revoke 会打断可能仍在加载的 <img>
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()

    vi.advanceTimersByTime(5000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock/1')
  })

  it('清空气泡：待发附件的预览地址保留，已入气泡的延迟回收', async () => {
    const ctx = mountChat()
    await attachImage(ctx)
    ctx.clearMessages()

    vi.advanceTimersByTime(5000)
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    expect(ctx.attachments.value[0].previewUrl).toBe('blob:mock/1')
  })
})

describe('SSE 正常流协议不回归', () => {
  it('meta/thinking/delta/tool/done 全链路渲染，畸形帧跳过，忙态复位', async () => {
    const ctx = mountChat('s_sse')
    ctx.draft.value = '你好'
    fetchMock.mockResolvedValue(
      sseOk([
        { type: 'meta', agent: { label: '财税专家', color: '#123456' }, routed_by: '指定', mode: 'deep' },
        { type: 'thinking_delta', text: '想一想' },
        { type: 'delta', text: '正' },
        { type: 'delta', text: '文' },
        'data: not-json-at-all',
        { type: 'tool_start', name: '查任务' },
        { type: 'done', reply: '正文', thinking: '想一想', trace: [{ tool: '查任务', args: { a: 1 }, result: 'ok' }], routed_by: '指定' },
        'data: [DONE]',
      ]),
    )

    await ctx.send()

    expect(ctx.messages.value).toHaveLength(2)
    const ai = ctx.messages.value[1]
    expect(ai.content).toBe('正文')
    expect(ai.thinking).toBe('想一想')
    expect(ai.agentLabel).toBe('财税专家')
    expect(ai.color).toBe('#123456')
    expect(ai.routedBy).toBe('智能路由')
    expect(ai.mode).toBe('deep')
    expect(ai.trace).toHaveLength(1)
    expect(ai.streaming).toBe(false)
    expect(ai.error).toBe(false)
    expect(ctx.loading.value).toBe(false)
    expect(ctx.draft.value).toBe('')
  })
})
