// swarm 任务 transaction-request-fix 回归测试。
// 覆盖 TransactionListView.vue 两类缺陷：
//   T1 load() 的 await Promise.all 无 try/catch：账户/分类任一请求失败即 reject，
//      onMounted 的「await load(); await reload()」被一起中断——流水永远不加载，
//      页面停在空表格；rejection 逃逸到 window unhandledrejection，还会污染
//      /api/debug/client-errors（守卫口径见 no-unhandled-request.test.js）。
//   T2 响应缺字段时（items/total/accounts/categories 为 null/undefined）直接赋值，
//      null 流入模板「total > items.length」与 ag-grid rowData。
// 运行环境为 node（vite.config.js test.environment=node，无 jsdom/挂载能力），
// 沿用既有约定：mock 生命周期钩子与 API 后直接驱动编译后的 setup()
// （同 swarm-finance-safety.test.js）。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ mounted: [] }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    // 编译后的 SFC（dev 模板）会读 useSSRContext().modules：node 环境无 SSR 上下文，
    // 不 mock 会让真实 useSSRContext 返回 undefined 而在 mount 阶段崩溃
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: (fn) => {
      hooks.mounted.push(fn)
    },
    onUnmounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

vi.mock('../src/api', () => {
  const api = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }
  return { default: api, API_BASE: '/api' }
})

const { default: api } = await import('../src/api')
const TransactionListView = (await import('../src/modules/finance-ledger-personal/views/TransactionListView.vue')).default

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  api.get.mockImplementation((url) => {
    if (url.includes('/accounts')) return Promise.resolve({ data: [{ id: 'a1', name: '现金账户' }] })
    if (url.includes('/categories')) return Promise.resolve({ data: [{ id: 'c1', name: '餐饮' }] })
    if (url.includes('/transactions')) return Promise.resolve({ data: { items: [{ id: 't1' }], total: 1 } })
    return Promise.resolve({ data: {} })
  })
})

function mount() {
  return TransactionListView.setup({}, { expose: () => {}, emit: () => {} })
}

// 复刻真实挂载流程：onMounted(async () => { await load(); await reload() })
function runMounted() {
  expect(hooks.mounted).toHaveLength(1)
  return hooks.mounted[0]()
}

describe('T1 TransactionListView：load() 异常收口', () => {
  it('账户/分类请求全部失败时 load() 不再向外抛异常', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/accounts')) return Promise.reject(new Error('boom'))
      if (url.includes('/categories')) return Promise.reject(new Error('boom'))
      if (url.includes('/transactions')) return Promise.resolve({ data: { items: [], total: 0 } })
      return Promise.resolve({ data: {} })
    })
    const ctx = mount()
    await expect(ctx.load()).resolves.toBeUndefined()
    expect(ctx.accounts.value).toEqual([])
    expect(ctx.categories.value).toEqual([])
  })

  it('仅账户失败时 load() 也不抛（Promise.all 原子语义：两个下拉都留空）', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/accounts')) return Promise.reject(new Error('boom'))
      if (url.includes('/categories')) return Promise.resolve({ data: [{ id: 'c1', name: '餐饮' }] })
      if (url.includes('/transactions')) return Promise.resolve({ data: { items: [], total: 0 } })
      return Promise.resolve({ data: {} })
    })
    const ctx = mount()
    await expect(ctx.load()).resolves.toBeUndefined()
    expect(ctx.accounts.value).toEqual([])
    expect(ctx.categories.value).toEqual([])
  })

  it('全部成功时账户/分类正常赋值（收口不影响成功路径）', async () => {
    const ctx = mount()
    await ctx.load()
    expect(ctx.accounts.value).toEqual([{ id: 'a1', name: '现金账户' }])
    expect(ctx.categories.value).toEqual([{ id: 'c1', name: '餐饮' }])
  })
})

describe('T1 TransactionListView：onMounted 加载链不中断', () => {
  it('账户/分类失败后流水请求照常发出，列表正常渲染', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/accounts')) return Promise.reject(new Error('boom'))
      if (url.includes('/categories')) return Promise.reject(new Error('boom'))
      if (url.includes('/transactions')) return Promise.resolve({ data: { items: [{ id: 't1' }], total: 1 } })
      return Promise.resolve({ data: {} })
    })
    const ctx = mount()
    await expect(runMounted()).resolves.toBeUndefined()
    expect(api.get).toHaveBeenCalledWith('/finance/ledger/personal/transactions', expect.anything())
    expect(ctx.items.value).toEqual([{ id: 't1' }])
    expect(ctx.total.value).toBe(1)
    expect(ctx.loading.value).toBe(false)
  })

  it('全部成功时账户/分类/流水都赋值', async () => {
    const ctx = mount()
    await runMounted()
    expect(ctx.accounts.value).toEqual([{ id: 'a1', name: '现金账户' }])
    expect(ctx.categories.value).toEqual([{ id: 'c1', name: '餐饮' }])
    expect(ctx.items.value).toEqual([{ id: 't1' }])
    expect(ctx.total.value).toBe(1)
  })
})

describe('T2 TransactionListView：响应空值兜底', () => {
  it('load：账户/分类响应 data 为 null 时兜底为空数组', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/accounts')) return Promise.resolve({ data: null })
      if (url.includes('/categories')) return Promise.resolve({ data: null })
      if (url.includes('/transactions')) return Promise.resolve({ data: { items: [], total: 0 } })
      return Promise.resolve({ data: {} })
    })
    const ctx = mount()
    await ctx.load()
    expect(ctx.accounts.value).toEqual([])
    expect(ctx.categories.value).toEqual([])
  })

  it('reload：响应缺 items/total 字段时兜底为空数组与 0', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/transactions')) return Promise.resolve({ data: {} })
      return Promise.resolve({ data: [] })
    })
    const ctx = mount()
    await ctx.reload()
    expect(ctx.items.value).toEqual([])
    expect(ctx.total.value).toBe(0)
  })

  it('reload：items/total 为 null 时 null 不流入 rowData 与模板比较', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/transactions')) return Promise.resolve({ data: { items: null, total: null } })
      return Promise.resolve({ data: [] })
    })
    const ctx = mount()
    await ctx.reload()
    expect(ctx.items.value).toEqual([])
    expect(ctx.total.value).toBe(0)
  })

  it('reload：流水请求失败时 loading 释放、列表保持原状（既有行为锁定）', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/transactions')) return Promise.reject(new Error('boom'))
      return Promise.resolve({ data: [] })
    })
    const ctx = mount()
    await ctx.reload()
    expect(ctx.loading.value).toBe(false)
    expect(ctx.items.value).toEqual([])
  })
})
