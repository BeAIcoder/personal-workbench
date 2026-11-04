// Task 7 / FE L-129：Facility 导出现在用 window.location.href，
// 400/500 会把整页导航到 JSON 错误页，丢掉筛选与未保存表单。
// 改为与其它模块一致的 Blob 下载：失败只 toast，不离开当前页。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), defaults: { baseURL: '/api' } }))
const saveBlob = vi.hoisted(() => vi.fn(() => true))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('../src/api', () => ({ default: api, BACKEND_HINT: '测试后端提示' }))
vi.mock('../src/utils/download.js', () => ({ saveBlob, REVOKE_DELAY_MS: 10000 }))

const FacilityView = (await import('../src/modules/business-facility/views/FacilityView.vue')).default

function mount() {
  return FacilityView.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
  saveBlob.mockReturnValue(true)
})

describe('工程物业导出不离开当前页', () => {
  it('设备台账导出走 Blob 请求并显式落名', async () => {
    const ctx = mount()
    api.get.mockResolvedValueOnce({ data: new Blob(['xlsx']) })
    await ctx.exportEquipment()
    expect(api.get).toHaveBeenCalledWith('/business/facility/export/equipment.xlsx', expect.objectContaining({ responseType: 'blob' }))
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), '设备台账.xlsx')
  })

  it('合规导出带当前筛选参数（kind/status/as_of/equipment_id）', async () => {
    const ctx = mount()
    ctx.complianceQuery.kind = 'inspection'
    ctx.complianceQuery.status = 'overdue'
    ctx.complianceQuery.as_of = '2026-09-30'
    ctx.complianceQuery.equipment_id = 5
    api.get.mockResolvedValueOnce({ data: new Blob(['xlsx']) })
    await ctx.exportCompliance()
    const [url, config] = api.get.mock.calls[0]
    expect(url).toContain('/business/facility/export/compliance.xlsx?')
    expect(url).toContain('kind=inspection')
    expect(url).toContain('status=overdue')
    expect(url).toContain('as_of=2026-09-30')
    expect(url).toContain('equipment_id=5')
    expect(config).toMatchObject({ responseType: 'blob' })
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), '合规事项.xlsx')
  })

  it('导出失败（后端 400/500）不落文件、不导航、不抛未捕获异常', async () => {
    const ctx = mount()
    api.get.mockRejectedValueOnce(new Error('导出失败'))
    await expect(ctx.exportCompliance()).resolves.toBeUndefined()
    expect(saveBlob).not.toHaveBeenCalled()
  })
})
