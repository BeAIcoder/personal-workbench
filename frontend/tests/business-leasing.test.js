import { beforeEach, describe, expect, it, vi } from 'vitest'

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
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    defaults: { baseURL: '/api' },
  },
}))

const { default: api } = await import('../src/api')
const { ElMessage } = await import('element-plus')
const LeasingView = (await import('../src/modules/business-leasing/views/LeasingView.vue')).default

function mount() {
  return LeasingView.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  api.get.mockImplementation((url) => {
    if (url.endsWith('/units/summary')) {
      return Promise.resolve({ data: {
        total_units: 26, total_area: 2600, vacancy_rate: 0.2,
        leasing_rate: 0.5, opening_rate: 0.25, pipeline_area: 300,
        rate_note: '口径说明', buildings: ['A栋'], by_status: [],
      } })
    }
    if (url.endsWith('/funnel')) {
      return Promise.resolve({ data: { total: 26, active: 20, signed: 2, conversion_rate: 2 / 26, stages: [] } })
    }
    if (url.endsWith('/leads/due')) return Promise.resolve({ data: [] })
    if (url.endsWith('/units')) {
      return Promise.resolve({ data: {
        items: [{ id: 7, building: 'A栋', floor: '1F', unit_no: 'A-107', status: 'vacant' }],
        total: 26, page: 1, page_size: 20,
      } })
    }
    if (url.endsWith('/leads')) return Promise.resolve({ data: { items: [], total: 26, page: 1, page_size: 20 } })
    return Promise.resolve({ data: [] })
  })
  api.post.mockResolvedValue({ data: {} })
  api.put.mockResolvedValue({ data: {} })
})

describe('招商铺位与线索分页请求', () => {
  it('铺位列表携带页码/页大小，并在翻页时请求第二页', async () => {
    const ctx = mount()
    await ctx.loadUnits()
    expect(api.get).toHaveBeenLastCalledWith('/business/leasing/units', {
      params: expect.objectContaining({ page: 1, page_size: 20 }),
    })
    expect(ctx.unitTotal.value).toBe(26)

    await ctx.changeUnitPage(2)
    expect(api.get).toHaveBeenLastCalledWith('/business/leasing/units', {
      params: expect.objectContaining({ page: 2, page_size: 20 }),
    })
    expect(ctx.unitQuery.page).toBe(2)
  })

  it('线索列表携带页码/页大小，并在翻页时请求第二页', async () => {
    const ctx = mount()
    await ctx.loadLeads()
    expect(api.get).toHaveBeenLastCalledWith('/business/leasing/leads', {
      params: expect.objectContaining({ page: 1, page_size: 20 }),
    })
    expect(ctx.leadTotal.value).toBe(26)

    await ctx.changeLeadPage(2)
    expect(api.get).toHaveBeenLastCalledWith('/business/leasing/leads', {
      params: expect.objectContaining({ page: 2, page_size: 20 }),
    })
    expect(ctx.leadQuery.page).toBe(2)
  })

  it('意向铺位选项独立加载前 200 条，不受当前列表页影响', async () => {
    const ctx = mount()
    await ctx.loadUnitOptions()
    expect(api.get).toHaveBeenLastCalledWith('/business/leasing/units', {
      params: { page: 1, page_size: 200 },
    })
    expect(ctx.unitOptions.value).toHaveLength(1)
    expect(ctx.unitOptions.value[0].id).toBe(7)
  })
})

describe('招商租决与开业日期表单请求', () => {
  it('编辑线索时保留并提交意向铺位与全部租决字段', async () => {
    const ctx = mount()
    ctx.openLead({
      id: 9, company: '合成租户', contact: '', phone: '', industry: '',
      demand_area: 100, budget: 20000, stage: 'signed', owner: '小王',
      next_action_at: '', note: '', unit_id: 7, rent_form: 'guaranteed_percentage',
      free_rent_months: 2, escalation_rate: 3.5, fitout_subsidy: 12000,
      lease_term_months: 36,
    })
    await ctx.saveLead()
    expect(api.put).toHaveBeenCalledWith('/business/leasing/leads/9', expect.objectContaining({
      unit_id: 7,
      rent_form: 'guaranteed_percentage',
      free_rent_months: 2,
      escalation_rate: 3.5,
      fitout_subsidy: 12000,
      lease_term_months: 36,
    }))
  })

  it('签约但未选择铺位时给出提示且不发送请求', async () => {
    const ctx = mount()
    ctx.openLead({
      id: null, company: '缺铺位租户', contact: '', phone: '', industry: '',
      demand_area: 0, budget: 0, stage: 'signed', owner: '', next_action_at: '', note: '',
    })
    await ctx.saveLead()
    expect(ElMessage.warning).toHaveBeenCalledWith('已签约线索必须选择意向铺位')
    expect(api.post).not.toHaveBeenCalled()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('编辑铺位时提交开业日期', async () => {
    const ctx = mount()
    ctx.openUnit({
      id: 7, building: 'A栋', floor: '1F', unit_no: 'A-107', area: 100,
      rent_price: 80, property_fee: 10, status: 'leased', opened_at: '2026-10-01', note: '',
    })
    await ctx.saveUnit()
    expect(api.put).toHaveBeenCalledWith('/business/leasing/units/7', expect.objectContaining({
      opened_at: '2026-10-01',
    }))
  })
})
