// Task 7 / FE L-120、L-121、L-125：分页列表必须能到达后续页并展示总数。
// 组件测试直接驱动编译后的 setup()（仓库既有模式，node 环境无需 jsdom），
// 用真实的分页响应（HR 25 条、法务 51 条、发票 51 条）验证请求参数与状态。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ mounted: [] }))
const route = vi.hoisted(() => ({ query: {}, params: {} }))
const router = vi.hoisted(() => ({ push: vi.fn(), back: vi.fn() }))
const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))
const messageBox = vi.hoisted(() => ({ confirm: vi.fn() }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: (fn) => hooks.mounted.push(fn),
    onUnmounted: () => {},
    onBeforeUnmount: () => {},
  }
})
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => router }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: messageBox,
}))
vi.mock('../src/api', () => ({
  default: api,
  BACKEND_HINT: '测试后端提示',
  assistantApi: { config: { get: vi.fn(), saveGlobals: vi.fn() } },
}))

const { toPageParams, pageState, totalPages, nextPageAfterDelete } = await import('../src/modules/_shared/pagedList.js')
const HrView = (await import('../src/modules/business-hr/views/HrView.vue')).default
const LegalView = (await import('../src/modules/business-legal/views/LegalView.vue')).default
const BatchListView = (await import('../src/modules/finance-invoice/views/BatchListView.vue')).default

function mount(component) {
  return component.setup({}, { expose: () => {}, emit: () => {} })
}

function rows(count, prefix = 'r') {
  return Array.from({ length: count }, (_, i) => ({ id: i + 1, name: `${prefix}${i + 1}` }))
}

function paged(items, total, page, pageSize) {
  return { data: { items, total, page, page_size: pageSize } }
}

function lastCall(path) {
  const calls = api.get.mock.calls.filter(([url]) => url === path)
  return calls[calls.length - 1]
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  route.query = {}
  route.params = {}
  messageBox.confirm.mockResolvedValue('confirm')
  api.delete.mockResolvedValue({ data: { ok: true } })
  api.post.mockResolvedValue({ data: { ok: true } })
  api.get.mockImplementation(async (url, config = {}) => {
    const page = Number(config.params?.page || 1)
    const size = Number(config.params?.page_size || 20)
    if (url === '/business/hr/summary') return { data: {} }
    if (url === '/business/hr/employees') {
      const all = rows(25, 'emp')
      return paged(all.slice((page - 1) * size, page * size), 25, page, size)
    }
    if (url === '/business/hr/employees/expiring') return { data: [] }
    if (url === '/business/hr/attendance' || url === '/business/hr/payroll') return { data: [] }
    if (url === '/business/legal/summary') return { data: {} }
    if (['/business/legal/contracts', '/business/legal/cases', '/business/legal/seals'].includes(url)) {
      const all = rows(51, url.split('/').pop())
      return paged(all.slice((page - 1) * size, page * size), 51, page, size)
    }
    if (url === '/finance/invoice/engine/status') return { data: { available: true } }
    if (url === '/finance/invoice/batches') {
      const all = rows(51, 'batch').map((b) => ({ ...b, status: 'parsed' }))
      return paged(all.slice((page - 1) * size, page * size), 51, page, size)
    }
    return { data: [] }
  })
})

describe('分页纯逻辑（25/51 条真实语义）', () => {
  it('请求参数始终带 page/page_size，并保留筛选条件', () => {
    expect(toPageParams({ keyword: '张' }, 2, 25)).toEqual({ keyword: '张', page: 2, page_size: 25 })
    expect(toPageParams({}, 0, 0)).toEqual({ page: 1, page_size: 20 })
  })

  it('响应归一：25 条 / pageSize 20 → 2 页，最后一页 5 条', () => {
    const state = pageState({ items: rows(5), total: 25, page: 2, page_size: 20 })
    expect(state.items).toHaveLength(5)
    expect(state.total).toBe(25)
    expect(state.page).toBe(2)
    expect(totalPages(25, 20)).toBe(2)
  })

  it('旧后端裸数组响应兼容为单页列表（不静默显示空）', () => {
    const state = pageState([{ id: 1 }, { id: 2 }], { page: 1, pageSize: 20 })
    expect(state.items).toHaveLength(2)
    expect(state.total).toBe(2)
  })

  it('响应缺 total 时回退当前页条数，不显示 NaN', () => {
    const state = pageState({ items: rows(3) }, { page: 1, pageSize: 20 })
    expect(state.total).toBe(3)
    expect(state.page).toBe(1)
  })

  it('删除最后一页唯一一条后回到上一页', () => {
    expect(nextPageAfterDelete(2, 25, 20)).toBe(2) // 25→24 仍是第 2 页
    expect(nextPageAfterDelete(2, 21, 20)).toBe(1) // 21→20 只剩 1 页
    expect(nextPageAfterDelete(3, 51, 25)).toBe(2) // 51→50 → 2 页
    expect(nextPageAfterDelete(1, 0, 20)).toBe(1)
  })
})

describe('人事花名册分页（25 条）', () => {
  it('首次加载请求 page=1/page_size=20 并保存 total', async () => {
    const ctx = mount(HrView)
    await ctx.loadEmployees()
    expect(lastCall('/business/hr/employees')[1].params).toMatchObject({ page: 1, page_size: 20 })
    expect(ctx.employees.value).toHaveLength(20)
    expect(ctx.empTotal.value).toBe(25)
  })

  it('翻到第 2 页可取到第 21-25 人', async () => {
    const ctx = mount(HrView)
    await ctx.loadEmployees()
    ctx.page.value = 2
    await ctx.loadEmployees()
    expect(lastCall('/business/hr/employees')[1].params.page).toBe(2)
    expect(ctx.employees.value.map((e) => e.name)).toEqual(['emp21', 'emp22', 'emp23', 'emp24', 'emp25'])
  })

  it('筛选后回到第 1 页，不把旧页码带到新条件', async () => {
    const ctx = mount(HrView)
    ctx.page.value = 2
    ctx.empQuery.keyword = '张'
    await ctx.searchEmployees()
    expect(lastCall('/business/hr/employees')[1].params).toMatchObject({ page: 1, keyword: '张' })
  })

  it('回车筛选与按钮走同一路径：都回第 1 页', async () => {
    const ctx = mount(HrView)
    ctx.page.value = 2
    ctx.empQuery.keyword = '李'
    await ctx.onEmployeeFilterEnter()
    expect(lastCall('/business/hr/employees')[1].params).toMatchObject({ page: 1, keyword: '李' })
  })

  it('删除第 2 页最后一条后请求第 1 页', async () => {
    const ctx = mount(HrView)
    ctx.page.value = 2
    ctx.empTotal.value = 21
    ctx.employees.value = [{ id: 21, name: '末位员工' }]
    await ctx.removeEmployee({ id: 21, name: '末位员工' })
    expect(lastCall('/business/hr/employees')[1].params.page).toBe(1)
  })
})

describe('法务三个列表分页（各 51 条）', () => {
  it('合同审查：total=51，第 3 页 11 条', async () => {
    const ctx = mount(LegalView)
    await ctx.loadReviews()
    expect(ctx.reviewTotal.value).toBe(51)
    ctx.reviewPage.value = 3
    await ctx.loadReviews()
    expect(ctx.reviews.value).toHaveLength(11)
    expect(lastCall('/business/legal/contracts')[1].params.page).toBe(3)
  })

  it('案件：独立页码与总数，切列表互不影响', async () => {
    const ctx = mount(LegalView)
    await ctx.loadReviews()
    ctx.casePage.value = 2
    await ctx.loadCases()
    expect(ctx.cases.value).toHaveLength(20)
    expect(ctx.caseTotal.value).toBe(51)
    expect(ctx.reviewPage.value).toBe(1)
  })

  it('用印：第 3 页 11 条并按条件请求', async () => {
    const ctx = mount(LegalView)
    ctx.sealQuery.keyword = '公章'
    await ctx.loadSeals()
    await ctx.searchSeals()
    expect(ctx.sealTotal.value).toBe(51)
    ctx.sealPage.value = 3
    await ctx.loadSeals()
    expect(ctx.seals.value).toHaveLength(11)
    expect(lastCall('/business/legal/seals')[1].params).toMatchObject({ keyword: '公章', page: 3 })
  })

  it('三个列表的回车筛选都回第 1 页', async () => {
    const ctx = mount(LegalView)
    ctx.reviewPage.value = 2
    await ctx.onReviewFilterEnter()
    expect(lastCall('/business/legal/contracts')[1].params.page).toBe(1)
    ctx.casePage.value = 2
    await ctx.onCaseFilterEnter()
    expect(lastCall('/business/legal/cases')[1].params.page).toBe(1)
    ctx.sealPage.value = 2
    await ctx.onSealFilterEnter()
    expect(lastCall('/business/legal/seals')[1].params.page).toBe(1)
  })
})

describe('发票批次分页与删除入口（51 条）', () => {
  it('按后端 {total,items} 契约加载并显示总数', async () => {
    const ctx = mount(BatchListView)
    await ctx.load()
    expect(lastCall('/finance/invoice/batches')[1].params).toMatchObject({ page: 1, page_size: 50 })
    expect(ctx.total.value).toBe(51)
    expect(ctx.items.value).toHaveLength(50)
  })

  it('第 2 页取到最后 1 条（首屏保持 50 条）', async () => {
    const ctx = mount(BatchListView)
    ctx.page.value = 2
    await ctx.load()
    expect(ctx.items.value).toHaveLength(1)
  })

  it('删除有二次确认，说明会删明细、留档和导出包', async () => {
    const ctx = mount(BatchListView)
    await ctx.removeBatch({ id: 9, name: '重复批次' })
    expect(messageBox.confirm).toHaveBeenCalledTimes(1)
    const text = messageBox.confirm.mock.calls[0][0]
    expect(text).toContain('重复批次')
    expect(text).toContain('明细')
    expect(text).toContain('留档')
    expect(text).toContain('导出')
    expect(api.delete).toHaveBeenCalledWith('/finance/invoice/batches/9', { timeout: 120000 })
  })

  it('删除最后一页唯一一条后回到上一页重新加载', async () => {
    const ctx = mount(BatchListView)
    ctx.page.value = 3
    ctx.total.value = 101
    ctx.items.value = [{ id: 101, name: '最后一条' }]
    ctx.pageSize.value = 50
    await ctx.removeBatch({ id: 101, name: '最后一条' })
    expect(lastCall('/finance/invoice/batches')[1].params.page).toBe(2)
    expect(api.delete).toHaveBeenCalledWith('/finance/invoice/batches/101', { timeout: 120000 })
  })
})
