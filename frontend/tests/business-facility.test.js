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
const FacilityView = (await import('../src/modules/business-facility/views/FacilityView.vue')).default

function mount() {
  return FacilityView.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  api.get.mockImplementation((url) => {
    if (url.endsWith('/summary')) {
      return Promise.resolve({ data: {
        equipment_count: 12, category_a: 2, compliance_30: 3, overdue: 1,
      } })
    }
    if (url.endsWith('/equipment')) {
      return Promise.resolve({ data: {
        total: 21, page: 1, page_size: 20,
        items: [{ id: 1, code: 'EQ-001', name: '冷水机组', category: 'A', status: 'running' }],
      } })
    }
    if (url.endsWith('/compliance')) {
      return Promise.resolve({ data: {
        total: 5, page: 1, page_size: 20, as_of: '2026-10-31',
        items: [{ id: 3, title: '电梯年检', kind: 'annual_inspection', status: 'pending' }],
      } })
    }
    return Promise.resolve({ data: {} })
  })
  api.post.mockResolvedValue({ data: {} })
  api.put.mockResolvedValue({ data: {} })
})

describe('工程物业页面真实请求', () => {
  it('设备列表携带筛选/分页并支持翻页与独立设备选项', async () => {
    const ctx = mount()
    await ctx.loadEquipment()
    expect(api.get).toHaveBeenLastCalledWith('/business/facility/equipment', {
      params: expect.objectContaining({ page: 1, page_size: 20 }),
    })
    expect(ctx.equipmentTotal.value).toBe(21)

    await ctx.changeEquipmentPage(2)
    expect(api.get).toHaveBeenLastCalledWith('/business/facility/equipment', {
      params: expect.objectContaining({ page: 2, page_size: 20 }),
    })

    await ctx.loadEquipmentOptions()
    expect(api.get).toHaveBeenLastCalledWith('/business/facility/equipment', {
      params: { page: 1, page_size: 50 },
    })
    expect(ctx.equipmentOptions.value).toHaveLength(1)
  })

  it('合规列表使用截止日和真实分页，看板与请求同期', async () => {
    const ctx = mount()
    ctx.complianceQuery.as_of = '2026-10-31'
    await ctx.loadCompliance()
    expect(api.get).toHaveBeenCalledWith('/business/facility/compliance', {
      params: expect.objectContaining({ as_of: '2026-10-31', page: 1, page_size: 20 }),
    })
    expect(ctx.complianceTotal.value).toBe(5)
    await ctx.loadSummary()
    expect(api.get).toHaveBeenLastCalledWith('/business/facility/summary', {
      params: { as_of: '2026-10-31' },
    })
  })

  it('编辑设备和合规记录提交完整字段', async () => {
    const ctx = mount()
    ctx.openEquipment({
      id: 1, code: 'EQ-001', name: '冷水机组', category: 'A',
      location: 'B1', install_date: '2024-01-15', status: 'running', note: '',
    })
    await ctx.saveEquipment()
    expect(api.put).toHaveBeenCalledWith('/business/facility/equipment/1', expect.objectContaining({
      code: 'EQ-001', name: '冷水机组', category: 'A', install_date: '2024-01-15',
    }))

    ctx.openCompliance({
      id: 3, equipment_id: 1, kind: 'fire', title: '消防检查',
      due_date: '2026-11-01', done_date: null, note: '',
    })
    await ctx.saveCompliance()
    expect(api.put).toHaveBeenCalledWith('/business/facility/compliance/3', expect.objectContaining({
      equipment_id: 1, kind: 'fire', due_date: '2026-11-01',
    }))
  })

  it('导出保持当前筛选参数', async () => {
    const ctx = mount()
    ctx.complianceQuery.kind = 'fire'
    ctx.complianceQuery.status = 'overdue'
    ctx.complianceQuery.equipment_id = 7
    expect(ctx.complianceExportUrl()).toBe(
      '/api/business/facility/export/compliance.xlsx?kind=fire&status=overdue&as_of='
      + ctx.complianceQuery.as_of + '&equipment_id=7',
    )
  })
})
