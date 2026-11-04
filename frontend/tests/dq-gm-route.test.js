// Task 7 / FE L-137（as_of 入口）与 L-140（GM 路由 period 监听）：
// 后端已支持 DQ as_of 与 GM 期间参数，前端必须真的发出去/跟随路由变化。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive } from 'vue'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), defaults: { baseURL: '/api' } }))
const gmApi = vi.hoisted(() => ({ summary: vi.fn() }))
const route = vi.hoisted(() => ({ query: {} }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ push: vi.fn() }) }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('../src/api', () => ({
  default: api,
  gmApi,
  BACKEND_HINT: '测试后端提示',
  assistantApi: { config: { get: vi.fn(), saveGlobals: vi.fn() } },
}))

const DqView = (await import('../src/modules/business-dq/views/DqView.vue')).default
const GmView = (await import('../src/modules/business-gm/views/GmView.vue')).default

beforeEach(() => {
  vi.clearAllMocks()
  route.query = reactive({})
})

describe('数据质量巡检 as_of', () => {
  it('选择基准日后请求带 as_of，问题总数取后端全量 issue_count', async () => {
    const ctx = DqView.setup({}, { expose: () => {}, emit: () => {} })
    ctx.asOf.value = '2026-05-31'
    api.get.mockResolvedValueOnce({ data: { overall_score: 92, issue_count: 42, top_issues: [{ count: 1 }], domains: {} } })
    await ctx.load()
    expect(api.get).toHaveBeenCalledWith('/business/dq/summary', { params: { as_of: '2026-05-31' } })
    expect(ctx.issueTotal.value).toBe(42)
  })

  it('未选基准日不发送空 as_of（后端按当天巡检）', async () => {
    const ctx = DqView.setup({}, { expose: () => {}, emit: () => {} })
    api.get.mockResolvedValueOnce({ data: { overall_score: 100, issue_count: 0, top_issues: [], domains: {} } })
    await ctx.load()
    const params = api.get.mock.calls[0][1]?.params || {}
    expect(params.as_of).toBeUndefined()
  })
})

describe('总经理聚合跟随路由 period', () => {
  it('路由 query 变为合法期间时重新加载', async () => {
    const ctx = GmView.setup({}, { expose: () => {}, emit: () => {} })
    gmApi.summary.mockResolvedValue({ data: { period: '2024-02', domains: {}, metrics: [], reminders: [] } })
    const changed = ctx.applyRoutePeriod({ period: '2024-02' })
    expect(changed).toBe(true)
    expect(ctx.period.value).toBe('2024-02')
    expect(gmApi.summary).toHaveBeenCalledWith('2024-02')
  })

  it('路由 watch 真实接线：query 变化触发重新加载', async () => {
    route.query = reactive({})
    const ctx = GmView.setup({}, { expose: () => {}, emit: () => {} })
    gmApi.summary.mockResolvedValue({ data: { period: '2024-02', domains: {}, metrics: [], reminders: [] } })
    route.query.period = '2024-02'
    await nextTick()
    expect(ctx.period.value).toBe('2024-02')
    expect(gmApi.summary).toHaveBeenCalledWith('2024-02')
  })

  it('同值再入可强制刷新（route.query 与当前期间相同）', async () => {
    route.query = { period: '2026-05' }
    const ctx = GmView.setup({}, { expose: () => {}, emit: () => {} })
    expect(ctx.applyRoutePeriod({ period: '2026-05' })).toBe(false)
    expect(ctx.applyRoutePeriod({ period: '2026-05' }, { reloadSame: true })).toBe(true)
    expect(gmApi.summary).toHaveBeenCalledWith('2026-05')
  })

  it('非法期间/相同期间不触发请求', async () => {
    route.query = { period: '2026-05' }
    const ctx = GmView.setup({}, { expose: () => {}, emit: () => {} })
    expect(ctx.period.value).toBe('2026-05')
    expect(ctx.applyRoutePeriod({ period: '2024-13' })).toBe(false)
    expect(ctx.applyRoutePeriod({ period: '2026-05' })).toBe(false)
    expect(gmApi.summary).not.toHaveBeenCalled()
  })

  it('非字符串期间（数组/null/数字）与空 query 一律忽略，不崩也不请求', () => {
    const ctx = GmView.setup({}, { expose: () => {}, emit: () => {} })
    expect(ctx.applyRoutePeriod({ period: ['2024-02'] })).toBe(false)
    expect(ctx.applyRoutePeriod({ period: null })).toBe(false)
    expect(ctx.applyRoutePeriod({ period: 202402 })).toBe(false)
    expect(ctx.applyRoutePeriod({})).toBe(false)
    expect(gmApi.summary).not.toHaveBeenCalled()
  })
})
