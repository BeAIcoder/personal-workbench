// T6B 会话列表竞态：快速切换「进行中/已归档」或连续刷新时，
// 旧请求的迟到响应不得覆盖最新列表（A 慢 B 快只显示 B）。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { assistantApi } from '../src/api'
import AssistantView from '../src/views/AssistantView.vue'

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
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

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function listPayload(...ids) {
  return { data: { sessions: ids.map((id) => ({ session_id: id, title: id, msg_count: 0 })) } }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function mountView() {
  return AssistantView.setup({}, { expose: () => {}, emit: () => {} })
}

function listIds(ctx) {
  return ctx.sessions.value.map((s) => s.session_id)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('会话列表加载竞态', () => {
  it('进行中列表慢、已归档列表快：迟到响应不得覆盖已归档 tab', async () => {
    const pending = []
    assistantApi.sessions.list.mockImplementation(() => {
      const d = deferred()
      pending.push(d)
      return d.promise
    })
    const ctx = mountView()

    const first = ctx.loadSessions()
    ctx.switchArchivedTab(true)
    expect(assistantApi.sessions.list).toHaveBeenCalledTimes(2)
    expect(assistantApi.sessions.list.mock.calls[0][0]).toEqual({ archived: 'false' })
    expect(assistantApi.sessions.list.mock.calls[1][0]).toEqual({ archived: 'true' })

    pending[1].resolve(listPayload('s_archived'))
    await flush()
    expect(listIds(ctx)).toEqual(['s_archived'])

    pending[0].resolve(listPayload('s_active'))
    await first
    expect(listIds(ctx)).toEqual(['s_archived'])
  })

  it('连续刷新时最后发起的列表请求获胜', async () => {
    const pending = []
    assistantApi.sessions.list.mockImplementation(() => {
      const d = deferred()
      pending.push(d)
      return d.promise
    })
    const ctx = mountView()

    const first = ctx.loadSessions()
    const second = ctx.loadSessions()
    const third = ctx.loadSessions()

    pending[2].resolve(listPayload('s_new'))
    await third
    expect(listIds(ctx)).toEqual(['s_new'])

    pending[0].resolve(listPayload('s_old'))
    await first
    expect(listIds(ctx)).toEqual(['s_new'])

    pending[1].resolve(listPayload('s_middle'))
    await second
    expect(listIds(ctx)).toEqual(['s_new'])
  })

  it('旧请求失败不影响新请求已加载的列表', async () => {
    const pending = []
    assistantApi.sessions.list.mockImplementation(() => {
      const d = deferred()
      pending.push(d)
      return d.promise
    })
    const ctx = mountView()

    const first = ctx.loadSessions()
    const second = ctx.loadSessions()

    pending[1].resolve(listPayload('s_new'))
    await second
    expect(listIds(ctx)).toEqual(['s_new'])

    pending[0].reject(new Error('old request failed'))
    await first
    expect(listIds(ctx)).toEqual(['s_new'])
  })
})
