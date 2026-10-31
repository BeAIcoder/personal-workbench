import { readFileSync } from 'node:fs'
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
    get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(),
    defaults: { baseURL: '/api' },
  },
}))

const { default: api } = await import('../src/api')
const { ElMessageBox } = await import('element-plus')
const PlanningView = (await import('../src/modules/business-planning/views/PlanningView.vue')).default
const planningSource = readFileSync(
  new URL('../src/modules/business-planning/views/PlanningView.vue', import.meta.url),
  'utf8',
)

function mount() {
  return PlanningView.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.resetAllMocks()
  hooks.mounted.length = 0
  api.get.mockImplementation((url) => {
    if (url.endsWith('/summary')) {
      return Promise.resolve({ data: {
        campaigns: 1, budget_total: 100, actual_total: 100,
        over_budget_count: 0, avg_roi: 2, manual_roi_count: 1,
      } })
    }
    if (url.endsWith('/campaigns')) {
      return Promise.resolve({ data: {
        items: [{ id: 7, name: '合成活动', roi: 2, roi_source: 'calculated' }],
        total: 26, page: 1, page_size: 20,
      } })
    }
    if (url.endsWith('/campaigns/calendar')) return Promise.resolve({ data: { year: 2026, months: [] } })
    if (url.endsWith('/vendors')) return Promise.resolve({ data: [] })
    return Promise.resolve({ data: [] })
  })
  api.post.mockResolvedValue({ data: {} })
  api.put.mockResolvedValue({ data: {} })
  api.delete.mockResolvedValue({ data: {} })
})

describe('企划活动真实请求与分页', () => {
  it('列表携带页码与页大小，翻页请求第二页', async () => {
    const ctx = mount()
    await ctx.loadCampaigns()
    expect(api.get).toHaveBeenLastCalledWith('/business/planning/campaigns', {
      params: expect.objectContaining({ page: 1, page_size: 20 }),
    })
    expect(ctx.campaignTotal.value).toBe(26)

    await ctx.changeCampaignPage(2)
    expect(ctx.query.page).toBe(2)
    expect(api.get).toHaveBeenLastCalledWith('/business/planning/campaigns', {
      params: expect.objectContaining({ page: 2, page_size: 20 }),
    })
  })

  it('筛选回到第一页并保留筛选条件', async () => {
    const ctx = mount()
    ctx.query.page = 4
    ctx.query.status = 'done'
    ctx.query.keyword = '店庆'
    await ctx.searchCampaigns()
    expect(ctx.query.page).toBe(1)
    expect(api.get).toHaveBeenLastCalledWith('/business/planning/campaigns', {
      params: expect.objectContaining({ page: 1, page_size: 20, status: 'done', keyword: '店庆' }),
    })
  })

  it('编辑保留并提交 ROI 原始输入、等级与目标', async () => {
    const ctx = mount()
    ctx.openCampaign({
      id: 7, name: '店庆', year: 2026, category: 'promo', status: 'done',
      start_date: '2026-05-01', end_date: '2026-05-03', owner: '', channel: '', target: '',
      budget_items: [{ item: '执行', budget: 100, actual: 100 }],
      grade: 'A', sales_baseline: 1000, sales_during: 1500,
      merchant_share: 50, roi_target: 2, metrics: { roi: 1.2, review: '' },
    })
    expect(ctx.campaignForm.grade).toBe('A')
    expect(ctx.campaignForm.sales_baseline).toBe(1000)
    await ctx.saveCampaign()
    expect(api.put).toHaveBeenCalledWith('/business/planning/campaigns/7', expect.objectContaining({
      grade: 'A', sales_baseline: 1000, sales_during: 1500,
      merchant_share: 50, roi_target: 2,
    }))
  })
})

describe('企划档期冲突确认', () => {
  function prepareNewCampaign(ctx) {
    ctx.openCampaign()
    Object.assign(ctx.campaignForm, {
      name: '新活动', start_date: '2026-05-15', end_date: '2026-05-20',
    })
  }

  it('409 展示冲突活动名，确认后仅重试显式 allow_conflict=true', async () => {
    const ctx = mount()
    prepareNewCampaign(ctx)
    api.post
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: '档期冲突：原活动；如需并行请设置 allow_conflict=true' } } })
      .mockResolvedValueOnce({ data: {} })
    ElMessageBox.confirm.mockResolvedValue('confirm')

    await ctx.saveCampaign()
    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(ElMessageBox.confirm.mock.calls[0][0]).toContain('原活动')
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(api.post.mock.calls[0][1].allow_conflict).toBeUndefined()
    expect(api.post.mock.calls[1][1].allow_conflict).toBe(true)
  })

  it('取消冲突确认不重试也不关闭编辑框', async () => {
    const ctx = mount()
    prepareNewCampaign(ctx)
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: '档期冲突：原活动' } } })
    ElMessageBox.confirm.mockRejectedValue(new Error('cancel'))

    await ctx.saveCampaign()
    expect(api.post).toHaveBeenCalledOnce()
    expect(ctx.campaignDialog.value).toBe(true)
    expect(ctx.saving.value).toBe(false)
  })

  it('非 409 错误不触发档期覆盖', async () => {
    const ctx = mount()
    prepareNewCampaign(ctx)
    api.post.mockRejectedValueOnce({ response: { status: 400, data: { detail: '输入错误' } } })
    await ctx.saveCampaign()
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledOnce()
  })
})

describe('企划 ROI 口径提示', () => {
  it('明确统一公式、手填不参与平均与无目标不定级', () => {
    const ctx = mount()
    expect(ctx.roiMethodNote).toContain('(活动期间销售 - 基准销售) / (实际投入 + 商户分摊)')
    expect(ctx.roiMethodNote).toContain('手填 ROI 不参与平均')
    expect(ctx.roiMethodNote).toContain('未设 ROI 目标时不判定达成或等级')
  })
})

describe('企划金额精度与年度切换', () => {
  it('切换年度回到第一页并按新年度重新请求', async () => {
    const ctx = mount()
    const nextYear = ctx.year.value + 1
    ctx.year.value = nextYear
    ctx.query.page = 4

    await ctx.changeYear()
    expect(ctx.query.page).toBe(1)
    expect(api.get).toHaveBeenCalledWith('/business/planning/summary', {
      params: { year: nextYear },
    })
    expect(api.get).toHaveBeenCalledWith('/business/planning/campaigns', {
      params: expect.objectContaining({ year: nextYear, page: 1, page_size: 20 }),
    })
  })

  it('销售与商户分摊输入限制两位小数，ROI 目标保持比率精度', () => {
    const lines = planningSource.split('\n')
    for (const field of ['sales_baseline', 'sales_during', 'merchant_share']) {
      const line = lines.find((item) => item.includes(`v-model="campaignForm.${field}"`))
      expect(line, field).toBeTruthy()
      expect(line).toContain(':precision="2"')
    }
    const targetLine = lines.find((item) => item.includes('v-model="campaignForm.roi_target"'))
    expect(targetLine).not.toContain(':precision="2"')
  })
})
