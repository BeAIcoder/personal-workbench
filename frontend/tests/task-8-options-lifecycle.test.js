// Task 8 / FE L-135、L-140：固定 200 条下拉改远程搜索并明确上限；
// 技能市场定时器与图表 resize 监听必须在卸载时清理。
import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ mounted: [], beforeUnmount: [] }))
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { baseURL: '/api' } }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: (fn) => hooks.mounted.push(fn),
    onUnmounted: (fn) => hooks.beforeUnmount.push(fn),
    onBeforeUnmount: (fn) => hooks.beforeUnmount.push(fn),
  }
})
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {}, path: '/' }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('../src/api', () => ({
  default: api,
  BACKEND_HINT: '测试后端提示',
  assistantApi: { config: { get: vi.fn(), saveGlobals: vi.fn() } },
}))

const { bindChartResize } = await import('../src/utils/chartResize.js')
const { ensureOption, createRemoteOptionSearch } = await import('../src/utils/remoteOptions.js')
const FacilityView = (await import('../src/modules/business-facility/views/FacilityView.vue')).default
const OperationsView = (await import('../src/modules/business-operations/views/OperationsView.vue')).default
const SkillPackView = (await import('../src/views/SkillPackView.vue')).default
const TableListView = (await import('../src/modules/finance-vault/views/TableListView.vue')).default
const vaultQuerySource = readFileSync(
  new URL('../src/modules/finance-vault/views/QueryView.vue', import.meta.url), 'utf8')
const facilitySource = readFileSync(
  new URL('../src/modules/business-facility/views/FacilityView.vue', import.meta.url), 'utf8')
const operationsSource = readFileSync(
  new URL('../src/modules/business-operations/views/OperationsView.vue', import.meta.url), 'utf8')
const statsSource = readFileSync(
  new URL('../src/modules/finance-ledger-personal/views/StatsView.vue', import.meta.url), 'utf8')

function mount(component) {
  return component.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  hooks.beforeUnmount.length = 0
  vi.useRealTimers()
  api.get.mockResolvedValue({ data: { items: [], total: 0 } })
  api.put.mockResolvedValue({ data: {} })
  api.post.mockResolvedValue({ data: {} })
})

describe('关联下拉远程搜索（L-140）', () => {
  it('工程设备下拉按关键字查 50 条，命中上限给出明确提示', async () => {
    const ctx = mount(FacilityView)
    api.get.mockResolvedValueOnce({ data: { items: Array.from({ length: 50 }, (_, i) => ({ id: i + 1 })), total: 120 } })
    await ctx.loadEquipmentOptions('泵')
    expect(api.get).toHaveBeenLastCalledWith('/business/facility/equipment', {
      params: { page: 1, page_size: 50, keyword: '泵' },
    })
    expect(ctx.equipmentOptions.value).toHaveLength(50)
    expect(ctx.equipmentOptionsHint.value).toContain('仅显示前 50 项')
    expect(ctx.equipmentOptionsHint.value).toContain('120')
  })

  it('营运铺位下拉按关键字查 50 条并保留铺位标签', async () => {
    const ctx = mount(OperationsView)
    api.get.mockResolvedValueOnce({
      data: { items: [{ id: 7, building: 'A', floor: '2', unit_no: 'U-7' }], total: 88 },
    })
    await ctx.loadUnitOptions('U-7')
    expect(api.get).toHaveBeenLastCalledWith('/business/leasing/units', {
      params: { page: 1, page_size: 50, keyword: 'U-7' },
    })
    expect(ctx.unitOptions.value).toEqual([{ id: 7, label: 'A/2 U-7', unit_no: 'U-7' }])
    expect(ctx.unitOptionsHint.value).toContain('仅显示前 50 项')
  })

  it('模板接线：三个下拉使用 filterable remote，且不再固定 200 条', () => {
    expect(facilitySource).toContain('remote-method="searchEquipmentOptions"')
    expect(facilitySource).toContain('filterable')
    expect(operationsSource).toContain('remote-method="searchUnitOptions"')
    expect(operationsSource).toContain('remote-method="searchOrderOptions"')
    expect(operationsSource).toContain('remote-method="searchEquipmentOptions"')
    expect(facilitySource).not.toMatch(/page_size:\s*200/)
    expect(operationsSource).not.toMatch(/page_size:\s*200/)
  })
  it('设备/合规导出忙态互不影响，同一导出重复点击被忽略', async () => {
    const ctx = mount(FacilityView)
    const pending = []
    api.get.mockImplementation(() => new Promise((resolve) => pending.push(resolve)))

    const equipment = ctx.exportEquipment()
    const duplicate = ctx.exportEquipment()
    expect(pending).toHaveLength(1)
    expect(ctx.exportingEquipment.value).toBe(true)
    expect(ctx.exportingCompliance.value).toBe(false)

    const compliance = ctx.exportCompliance()
    expect(pending).toHaveLength(2)
    expect(ctx.exportingCompliance.value).toBe(true)

    pending[0]({ data: new Blob([]) })
    await Promise.all([equipment, duplicate])
    expect(ctx.exportingEquipment.value).toBe(false)
    expect(ctx.exportingCompliance.value).toBe(true)

    pending[1]({ data: new Blob([]) })
    await compliance
    expect(ctx.exportingCompliance.value).toBe(false)
  })
})

describe('编辑已有记录时的已选项（Fix round 1 / I-1）', () => {
  it('合同先按关键字搜索（50/120 上限）再编辑：当前铺位补进选项并显示名称，保存 id 正确', async () => {
    const ctx = mount(OperationsView)
    api.get.mockResolvedValueOnce({
      data: { items: Array.from({ length: 50 }, (_, i) => ({ id: i + 1, unit_no: `A-${i + 1}`, building: 'A' })), total: 120 },
    })
    await ctx.loadUnitOptions('A')
    expect(ctx.unitOptionsHint.value).toContain('120')

    ctx.openContract({
      id: 1, contract_no: 'HT-1', tenant: '租户', unit_id: 999, unit_no: 'U-999',
      area: 10, start_date: '2026-01-01', end_date: '2026-12-31',
      rent_monthly: 1000, property_fee_monthly: 100, deposit: 0, status: 'active', note: '',
    })
    expect(ctx.unitOptions.value[0]).toMatchObject({ id: 999, label: 'U-999', unit_no: 'U-999' })
    expect(ctx.unitOptions.value).toHaveLength(51)

    api.put.mockResolvedValue({ data: {} })
    await ctx.saveContract()
    expect(api.put).toHaveBeenLastCalledWith(
      '/business/operations/contracts/1',
      expect.objectContaining({ unit_id: 999, unit_no: 'U-999' }),
    )
  })

  it('工单编辑：父工单/设备不在当前 50 条时补选项，保存 id 正确', async () => {
    const ctx = mount(OperationsView)
    ctx.orderOptions.value = [{ id: 1, code: 'WO-1', description: '已有' }]
    ctx.equipmentOptions.value = [{ id: 1, code: 'EQ-1', name: '已有设备' }]
    ctx.openOrder({
      id: 2, type: 'repair', priority: 'medium', status: 'open', location: '', tenant: '',
      assignee: '', description: '异响', due_at: null,
      parent_id: 888, parent_code: 'WO-888', equipment_id: 999, equipment_code: 'EQ-999',
    })
    expect(ctx.orderOptions.value[0]).toMatchObject({ id: 888, code: 'WO-888' })
    expect(ctx.orderOptionLabel(ctx.orderOptions.value[0])).toBe('WO-888')
    expect(ctx.equipmentOptions.value[0]).toMatchObject({ id: 999, code: 'EQ-999' })
    expect(ctx.equipmentOptionLabel(ctx.equipmentOptions.value[0])).toBe('EQ-999')

    api.put.mockResolvedValue({ data: {} })
    await ctx.saveOrder()
    expect(api.put).toHaveBeenLastCalledWith(
      '/business/operations/work-orders/2',
      expect.objectContaining({ parent_id: 888, equipment_id: 999 }),
    )
  })

  it('合规事项编辑：设备不在当前 50 条时补选项并显示编号', async () => {
    const ctx = mount(FacilityView)
    api.get.mockResolvedValueOnce({
      data: { items: Array.from({ length: 50 }, (_, i) => ({ id: i + 1, code: `EQ-${i + 1}`, name: '设备' })), total: 120 },
    })
    await ctx.loadEquipmentOptions('EQ-55')
    ctx.openCompliance({
      id: 3, equipment_id: 55, equipment_code: 'EQ-55', kind: 'fire', title: '消防检查',
      due_date: '2026-12-01', done_date: null, note: '',
    })
    expect(ctx.equipmentOptions.value[0]).toMatchObject({ id: 55, code: 'EQ-55' })
    expect(ctx.equipmentLabel(ctx.equipmentOptions.value[0])).toBe('EQ-55')

    api.put.mockResolvedValue({ data: {} })
    await ctx.saveCompliance()
    expect(api.put).toHaveBeenLastCalledWith(
      '/business/facility/compliance/3',
      expect.objectContaining({ equipment_id: 55 }),
    )
  })

  it('ensureOption 只在缺失时插入，已有同 id 不重复', () => {
    const list = [{ id: 1, label: 'A' }]
    expect(ensureOption(list, { id: 1, label: 'A2' })).toBe(list)
    expect(ensureOption(list, { id: 2, label: 'B' })).toEqual([{ id: 2, label: 'B' }, { id: 1, label: 'A' }])
    expect(ensureOption(list, null)).toBe(list)
  })
})

describe('远程搜索防抖与竞态（Fix round 1）', () => {
  it('连续输入只发最后一次；旧请求令牌失效', async () => {
    vi.useFakeTimers()
    const calls = []
    const search = createRemoteOptionSearch((keyword, token) => calls.push([keyword, token]), 250)
    search.search('A')
    const firstToken = search.search('B')
    expect(search.isCurrent(firstToken)).toBe(true)
    await vi.advanceTimersByTimeAsync(249)
    expect(calls).toEqual([])
    await vi.advanceTimersByTimeAsync(1)
    expect(calls).toEqual([['B', 2]])
    search.search('C')
    expect(search.isCurrent(firstToken)).toBe(false)
    await vi.advanceTimersByTimeAsync(250)
    expect(calls).toEqual([['B', 2], ['C', 3]])
    vi.useRealTimers()
  })
})

describe('卸载清理（L-135）', () => {
  it('技能市场防抖定时器在组件卸载时清除', () => {
    vi.useFakeTimers()
    const ctx = mount(SkillPackView)
    ctx.openMarket()
    expect(vi.getTimerCount()).toBe(1)
    for (const fn of hooks.beforeUnmount.splice(0)) fn()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('图表 resize 监听可绑定、触发 resize 并在解绑时移除同一处理器', () => {
    const target = { addEventListener: vi.fn(), removeEventListener: vi.fn() }
    const trend = { resize: vi.fn() }
    const pie = { resize: vi.fn() }
    const detach = bindChartResize(() => [trend, pie], target)
    expect(target.addEventListener).toHaveBeenCalledWith('resize', expect.any(Function))
    const handler = target.addEventListener.mock.calls[0][1]
    handler()
    expect(trend.resize).toHaveBeenCalledTimes(1)
    expect(pie.resize).toHaveBeenCalledTimes(1)
    detach()
    expect(target.removeEventListener).toHaveBeenCalledWith('resize', handler)
  })

  it('StatsView 真实接入 resize 助手并在卸载时解绑', () => {
    expect(statsSource).toContain('bindChartResize')
    expect(statsSource).toMatch(/onUnmounted\(\(\) => \{[\s\S]*detachResize/)
  })
})

describe('长任务与下载（Task 8 补充）', () => {
  it('数据仓入仓上传使用 120s 超时，避免大表被全局 10s 截断', async () => {
    const ctx = mount(TableListView)
    ctx.form.table = 't_upload'
    ctx.form.file = new File(['x'], 'x.xlsx')
    api.post.mockResolvedValueOnce({ data: { rows: 1 } })
    api.get.mockResolvedValue({ data: { items: [] } })
    await ctx.submitUpload()
    expect(api.post).toHaveBeenCalledWith(
      expect.stringContaining('/finance/vault/ingest/file'),
      expect.any(FormData),
      expect.objectContaining({ timeout: 120000 }),
    )
  })

  it('Vault CSV 走统一 saveBlob（挂 DOM + 延迟 revoke），不直接 revoke', () => {
    expect(vaultQuerySource).toContain('saveBlob')
    expect(vaultQuerySource).not.toContain('revokeObjectURL')
  })
})
