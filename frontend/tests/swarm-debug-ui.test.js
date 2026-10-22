// swarm 任务 debug-ui-race：调试三页（debug-runs / debug-errors / debug-issues）UI 韧性回归。
// 覆盖第一轮复核发现的两类问题：
//   1) RunsView.openDetail 缺少请求序号守卫（与 VoucherListView.detailSeq / TasksView.loadSeq
//      同类竞态）：慢旧的详情响应会覆盖新详情，慢旧的失败响应会关掉用户新打开的抽屉；
//   2) 三页列表请求失败时的计数一致性与未捕获异常（计数须随列表归零，rejection 不得逃逸）。
// 本仓库前端测试无 jsdom/挂载能力，沿用 swarm-chat-resilience 的既有约定：
// node 环境下 mock 生命周期钩子与 API，直接驱动编译后的 setup() 拿绑定做行为断言。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunsView from '../src/modules/debug-runs/views/RunsView.vue'
import ErrorsView from '../src/modules/debug-errors/views/ErrorsView.vue'
import IssuesView from '../src/modules/debug-issues/views/IssuesView.vue'
import { debugApi } from '../src/api'

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
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    debugApi: {
      summary: vi.fn(),
      issues: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
      runs: { list: vi.fn(), get: vi.fn(), remove: vi.fn(), shotUrl: vi.fn(() => '') },
      clientErrors: { list: vi.fn(), clear: vi.fn(), report: vi.fn() },
    },
  }
})

function mount(view) {
  return view.setup({}, { expose: () => {}, emit: vi.fn() })
}

/** 手动裁决的 promise：用来精确控制「慢旧响应」的到达顺序。 */
function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('RunsView 详情抽屉：慢旧响应不得覆盖新详情（BUG-RACE-DBG-001）', () => {
  it('先开 A 后开 B、B 先返回：A 的迟到响应不覆盖 B 的详情', async () => {
    const ctx = mount(RunsView)
    const a = deferred()
    const b = deferred()
    debugApi.runs.get.mockImplementation((id) => (id === 'run-a' ? a.promise : b.promise))

    const pA = ctx.openDetail({ id: 'run-a' })
    const pB = ctx.openDetail({ id: 'run-b' })
    b.resolve({ data: { id: 'run-b', kind: 'recon', pages: [] } })
    await pB
    a.resolve({ data: { id: 'run-a', kind: 'edge', pages: [] } })
    await pA

    expect(ctx.detail.value?.id).toBe('run-b')
    expect(ctx.drawerVisible.value).toBe(true)
    expect(ctx.detailLoading.value).toBe(false)
  })

  it('先开 A 后开 B、B 先返回：A 的迟到失败不关闭 B 的抽屉', async () => {
    const ctx = mount(RunsView)
    const a = deferred()
    const b = deferred()
    debugApi.runs.get.mockImplementation((id) => (id === 'run-a' ? a.promise : b.promise))

    const pA = ctx.openDetail({ id: 'run-a' })
    const pB = ctx.openDetail({ id: 'run-b' })
    b.resolve({ data: { id: 'run-b', kind: 'recon', pages: [] } })
    await pB
    a.reject(new Error('network'))
    await expect(pA).resolves.toBeUndefined() // catch 吞掉 rejection，不逃逸成未捕获异常

    expect(ctx.drawerVisible.value).toBe(true)
    expect(ctx.detail.value?.id).toBe('run-b')
    expect(ctx.detailLoading.value).toBe(false)
  })

  it('请求在途时用户关闭抽屉：迟到的响应不会重开抽屉', async () => {
    const ctx = mount(RunsView)
    const a = deferred()
    debugApi.runs.get.mockReturnValue(a.promise)

    const p = ctx.openDetail({ id: 'run-a' })
    ctx.drawerVisible.value = false // 用户点抽屉关闭（v-model 回写）
    a.resolve({ data: { id: 'run-a', kind: 'recon', pages: [] } })
    await p

    expect(ctx.drawerVisible.value).toBe(false)
  })
})

describe('RunsView 详情抽屉：当前请求的正常路径不回归', () => {
  it('当前请求成功：写入详情、抽屉打开、加载态复位', async () => {
    const ctx = mount(RunsView)
    debugApi.runs.get.mockResolvedValue({
      data: { id: 'run-a', kind: 'recon', base: '/x', started_at: 't0', finished_at: 't1', pages: [], tests: [], shots: [] },
    })

    await ctx.openDetail({ id: 'run-a' })

    expect(ctx.drawerVisible.value).toBe(true)
    expect(ctx.detail.value?.id).toBe('run-a')
    expect(ctx.detailLoading.value).toBe(false)
  })

  it('当前请求失败：关闭抽屉并复位加载态（既有反馈不丢失）', async () => {
    const ctx = mount(RunsView)
    debugApi.runs.get.mockRejectedValue(new Error('network'))

    await ctx.openDetail({ id: 'run-a' })

    expect(ctx.drawerVisible.value).toBe(false)
    expect(ctx.detailLoading.value).toBe(false)
  })
})

describe('调试三页列表请求失败：计数归零且无未捕获异常', () => {
  it('RunsView 列表失败：清空列表、total 归零、loading 复位', async () => {
    const ctx = mount(RunsView)
    debugApi.runs.list.mockRejectedValue(new Error('network'))

    await expect(ctx.load()).resolves.toBeUndefined()

    expect(ctx.items.value).toEqual([])
    expect(ctx.total.value).toBe(0)
    expect(ctx.loading.value).toBe(false)
  })

  it('RunsView 列表成功：写入 items 与 total', async () => {
    const ctx = mount(RunsView)
    debugApi.runs.list.mockResolvedValue({ data: { items: [{ id: 'r1' }], total: 1 } })

    await ctx.load()

    expect(ctx.items.value).toHaveLength(1)
    expect(ctx.total.value).toBe(1)
  })

  it('ErrorsView 列表失败：清空列表、total 归零', async () => {
    const ctx = mount(ErrorsView)
    debugApi.clientErrors.list.mockRejectedValue(new Error('network'))

    await expect(ctx.load()).resolves.toBeUndefined()

    expect(ctx.items.value).toEqual([])
    expect(ctx.total.value).toBe(0)
    expect(ctx.loading.value).toBe(false)
  })

  it('ErrorsView 列表成功：新报错排在前', async () => {
    const ctx = mount(ErrorsView)
    debugApi.clientErrors.list.mockResolvedValue({
      data: { items: [{ ts: 't1', message: 'old' }, { ts: 't2', message: 'new' }], total: 2 },
    })

    await ctx.load()

    expect(ctx.items.value.map((x) => x.message)).toEqual(['new', 'old'])
    expect(ctx.total.value).toBe(2)
  })

  it('IssuesView 列表失败：清空列表、total 归零', async () => {
    const ctx = mount(IssuesView)
    debugApi.issues.list.mockRejectedValue(new Error('network'))

    await expect(ctx.loadIssues()).resolves.toBeUndefined()

    expect(ctx.items.value).toEqual([])
    expect(ctx.total.value).toBe(0)
    expect(ctx.loading.value).toBe(false)
  })

  it('IssuesView 汇总失败：summary 置空且不逃逸异常', async () => {
    const ctx = mount(IssuesView)
    debugApi.summary.mockRejectedValue(new Error('network'))

    await expect(ctx.loadSummary()).resolves.toBeUndefined()

    expect(ctx.summary.value).toBeNull()
  })
})
