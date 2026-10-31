import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ mounted: [] }))

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

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('../src/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(),
    defaults: { baseURL: '/api' },
  },
}))

const { default: api } = await import('../src/api')
const { ElMessage } = await import('element-plus')
const OperationsView = (await import('../src/modules/business-operations/views/OperationsView.vue')).default

function mount() {
  return OperationsView.setup({}, { expose: () => {}, emit: () => {} })
}

const summaryData = {
  contracts_active: 3, leased_area: 300, receivable: 1000, received: 400,
  arrears: 600, collection_rate: 0.4, arrears_contracts: 2,
  expiring_30: 1, expired_active_contracts: 1, open_work_orders: 2,
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  vi.stubGlobal('window', { open: vi.fn() })
  api.get.mockImplementation((url, config = {}) => {
    if (url.endsWith('/summary')) return Promise.resolve({ data: { ...summaryData } })
    if (url.endsWith('/contracts/expiring')) {
      return Promise.resolve({ data: [{ id: 8, contract_no: 'HT-8', end_date: '2026-12-30' }] })
    }
    if (url.endsWith('/contracts')) {
      return Promise.resolve({ data: { items: [{ id: 8, contract_no: 'HT-8' }], total: 26, page: 1, page_size: 20 } })
    }
    if (url.endsWith('/bills/aging')) {
      return Promise.resolve({ data: {
        as_of: config.params.as_of,
        buckets: {
          not_due: { amount: 100, count: 1, contracts: 1 },
          '1_30': { amount: 200, count: 1, contracts: 1 },
          '31_90': { amount: 0, count: 0, contracts: 0 },
          over_90: { amount: 300, count: 1, contracts: 1 },
        },
        items: [{ id: 3, overdue_days: 91, bucket: 'over_90', arrears: 300 }],
      } })
    }
    if (url.endsWith('/bills')) {
      return Promise.resolve({ data: { items: [{ id: 3, arrears: 300 }], total: 26, page: 1, page_size: 20 } })
    }
    if (url.endsWith('/business/facility/equipment')) {
      return Promise.resolve({ data: { items: [{ id: 7, code: 'EQ-001', name: '冷水机组' }], total: 1 } })
    }
    if (url.endsWith('/work-orders')) {
      if (config.params?.page_size === 200) {
        return Promise.resolve({ data: { items: [{ id: 1, code: 'WO00001', description: '父工单' }], total: 1 } })
      }
      return Promise.resolve({ data: {
        items: [
          { id: 2, code: 'WO00002', description: '超期投诉', overdue: true, due_at: '2026-01-01 09:00:00' },
          { id: 3, code: 'WO00003', description: '正常工单', overdue: false, due_at: null },
        ],
        total: 26, page: 1, page_size: 20,
      } })
    }
    if (url.endsWith('/followups')) {
      return Promise.resolve({ data: [{ id: 9, content: '首次处理', result: '待复查', satisfaction: 4 }] })
    }
    return Promise.resolve({ data: [] })
  })
  api.post.mockResolvedValue({ data: {} })
  api.put.mockResolvedValue({ data: {} })
  api.delete.mockResolvedValue({ data: {} })
})

afterEach(() => vi.unstubAllGlobals())

describe('营运列表真实请求与分页', () => {
  it.each([
    ['contracts', 'loadContracts', 'changeContractPage', 'contractQuery', 'contractTotal'],
    ['bills', 'loadBills', 'changeBillPage', 'billQuery', 'billTotal'],
    ['work-orders', 'loadOrders', 'changeOrderPage', 'orderQuery', 'orderTotal'],
  ])('%s 列表请求分页并翻页', async (resource, load, change, query, total) => {
    const ctx = mount()
    await ctx[load]()
    expect(api.get).toHaveBeenLastCalledWith(`/business/operations/${resource}`, {
      params: expect.objectContaining({ page: 1, page_size: 20 }),
    })
    expect(ctx[total].value).toBe(26)

    await ctx[change](2)
    expect(ctx[query].page).toBe(2)
    expect(api.get).toHaveBeenLastCalledWith(`/business/operations/${resource}`, {
      params: expect.objectContaining({ page: 2, page_size: 20 }),
    })
  })

  it('账单筛选回第一页并联动期间 KPI', async () => {
    const ctx = mount()
    ctx.billQuery.page = 3
    ctx.billQuery.period = '2026-10'
    ctx.billQuery.status = 'partial'
    await ctx.searchBills()
    expect(ctx.billQuery.page).toBe(1)
    expect(api.get).toHaveBeenCalledWith('/business/operations/bills', {
      params: expect.objectContaining({ period: '2026-10', status: 'partial', page: 1 }),
    })
    expect(api.get).toHaveBeenCalledWith('/business/operations/summary', {
      params: { period: '2026-10' },
    })
  })

  it('合同与工单筛选均回第一页', async () => {
    const ctx = mount()
    ctx.contractQuery.page = 4
    ctx.contractQuery.status = 'active'
    await ctx.searchContracts()
    expect(ctx.contractQuery.page).toBe(1)
    expect(api.get).toHaveBeenLastCalledWith('/business/operations/contracts', {
      params: expect.objectContaining({ status: 'active', page: 1 }),
    })

    ctx.orderQuery.page = 5
    ctx.orderQuery.status = 'open'
    await ctx.searchOrders()
    expect(ctx.orderQuery.page).toBe(1)
    expect(api.get).toHaveBeenLastCalledWith('/business/operations/work-orders', {
      params: expect.objectContaining({ status: 'open', page: 1 }),
    })
  })
})

describe('营运到期、账龄与 SLA 警告', () => {
  it('60 天选择真实请求并更新到期清单', async () => {
    const ctx = mount()
    ctx.expiringDays.value = 60
    await ctx.loadExpiring()
    expect(api.get).toHaveBeenLastCalledWith('/business/operations/contracts/expiring', {
      params: { days: 60 },
    })
    expect(ctx.expiring.value).toHaveLength(1)
  })

  it('固定截止日请求账龄并暴露四桶与超 90 天警告', async () => {
    const ctx = mount()
    ctx.agingAsOf.value = '2026-10-31'
    await ctx.loadAging()
    expect(api.get).toHaveBeenLastCalledWith('/business/operations/bills/aging', {
      params: { as_of: '2026-10-31' },
    })
    expect(ctx.aging.value.buckets.over_90.amount).toBe(300)
    expect(ctx.aging.value.items[0].overdue_days).toBe(91)
    expect(ctx.agingWarning.value).toContain('超 90 天')
    expect(ctx.agingWarning.value).toContain('300.00')
  })

  it('工单超期与已过期履约合同形成可见警告计数', async () => {
    const ctx = mount()
    await ctx.loadOrders()
    await ctx.loadSummary()
    expect(ctx.overdueOrderCount.value).toBe(1)
    expect(ctx.summary.value.expired_active_contracts).toBe(1)
  })
})

describe('营运工单留痕与导出', () => {
  it('投诉父工单选项独立请求前 200 条', async () => {
    const ctx = mount()
    await ctx.loadOrderOptions()
    expect(api.get).toHaveBeenLastCalledWith('/business/operations/work-orders', {
      params: { page: 1, page_size: 200 },
    })
    expect(ctx.orderOptions.value[0].code).toBe('WO00001')
  })

  it('编辑工单保留并提交 urgent/due_at/parent_id', async () => {
    const ctx = mount()
    ctx.openOrder({
      id: 2, type: 'complaint', priority: 'urgent', status: 'processing',
      location: 'A栋', tenant: '合成租户', assignee: '小王', description: '投诉',
      due_at: '2026-11-01 09:00:00', parent_id: 1,
    })
    await ctx.saveOrder()
    expect(api.put).toHaveBeenCalledWith('/business/operations/work-orders/2', expect.objectContaining({
      priority: 'urgent', due_at: '2026-11-01 09:00:00', parent_id: 1,
    }))
  })

  it('打开跟进记录并新增后重新加载', async () => {
    const ctx = mount()
    await ctx.openFollowups({ id: 2, code: 'WO00002' })
    expect(api.get).toHaveBeenLastCalledWith('/business/operations/work-orders/2/followups')
    expect(ctx.followups.value).toHaveLength(1)

    ctx.followupForm.content = '第二次处理'
    ctx.followupForm.result = '已修复'
    ctx.followupForm.handled_at = '2026-10-31T10:30:00'
    ctx.followupForm.satisfaction = 5
    await ctx.saveFollowup()
    expect(api.post).toHaveBeenCalledWith('/business/operations/work-orders/2/followups', {
      handled_at: '2026-10-31T10:30:00', content: '第二次处理', result: '已修复', satisfaction: 5,
    })
    expect(api.get).toHaveBeenLastCalledWith('/business/operations/work-orders/2/followups')
  })

  it('空跟进内容给出警告且不发请求', async () => {
    const ctx = mount()
    await ctx.openFollowups({ id: 2, code: 'WO00002' })
    ctx.followupForm.content = '  '
    api.post.mockClear()
    await ctx.saveFollowup()
    expect(ElMessage.warning).toHaveBeenCalledWith('跟进内容不能为空')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('工单导出与已应用筛选一致且不携带分页参数', async () => {
    const ctx = mount()
    Object.assign(ctx.orderQuery, { status: 'open', type: 'complaint', keyword: '投诉', page: 3 })
    await ctx.searchOrders()
    ctx.exportOrders()
    expect(window.open).toHaveBeenCalledWith(
      '/api/business/operations/export/work-orders.xlsx?status=open&type=complaint&keyword=%E6%8A%95%E8%AF%89',
      '_blank',
    )
  })

  it('未点击筛选的草稿条件不会改变当前表格对应的导出', async () => {
    const ctx = mount()
    Object.assign(ctx.orderQuery, { status: 'open', type: 'complaint', keyword: '投诉' })
    await ctx.searchOrders()
    const displayedOrders = ctx.orders.value
    const requestCount = api.get.mock.calls.length

    Object.assign(ctx.orderQuery, { status: 'closed', type: 'repair', keyword: '未提交' })
    ctx.exportOrders()

    expect(api.get.mock.calls).toHaveLength(requestCount)
    expect(ctx.orders.value).toBe(displayedOrders)
    expect(window.open).toHaveBeenCalledWith(
      '/api/business/operations/export/work-orders.xlsx?status=open&type=complaint&keyword=%E6%8A%95%E8%AF%89',
      '_blank',
    )
  })
})

describe('工单关联工程设备', () => {
  it('加载设备选项并在工单保存时提交 equipment_id', async () => {
    const ctx = mount()
    await ctx.loadEquipmentOptions()
    expect(api.get).toHaveBeenLastCalledWith('/business/facility/equipment', {
      params: { page: 1, page_size: 200 },
    })
    expect(ctx.equipmentOptions.value[0].code).toBe('EQ-001')

    ctx.openOrder({
      id: 2, type: 'repair', priority: 'urgent', status: 'open',
      location: 'B1', tenant: '', assignee: '', description: '异响',
      due_at: null, parent_id: null, equipment_id: 7,
    })
    await ctx.saveOrder()
    expect(api.put).toHaveBeenCalledWith('/business/operations/work-orders/2', expect.objectContaining({
      equipment_id: 7,
    }))
  })
})
