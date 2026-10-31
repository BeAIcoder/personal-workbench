import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'

const hooks = vi.hoisted(() => ({ mounted: [] }))
const router = vi.hoisted(() => ({ push: vi.fn() }))
const route = vi.hoisted(() => ({ query: {} }))

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

vi.mock('vue-router', () => ({ useRouter: () => router, useRoute: () => route }))
vi.mock('../src/components/BaseChart.vue', () => ({ default: {} }))
vi.mock('../src/api', () => ({
  default: { get: vi.fn(), defaults: { baseURL: '/api' } },
  dashboardApi: { summary: vi.fn() },
  gmApi: { summary: vi.fn() },
  BACKEND_HINT: '测试后端提示',
}))

const { dashboardApi, gmApi } = await import('../src/api')
const GmView = (await import('../src/modules/business-gm/views/GmView.vue')).default
const DashboardView = (await import('../src/views/DashboardView.vue')).default

function mount(component) {
  return component.setup({}, { expose: () => {}, emit: () => {} })
}

function gmResponse() {
  return {
    period: '2026-05',
    as_of: '2026-05-31',
    domains: {
      leasing: {
        available: true,
        data: { units: { vacancy_rate: 0.25 } },
        gap_reasons: [],
      },
      operations: { available: true, data: {}, gap_reasons: [] },
      planning: { available: true, data: {}, gap_reasons: [] },
      legal: { available: true, data: {}, gap_reasons: [] },
      hr: { available: true, data: {}, gap_reasons: [] },
      facility: {
        available: false,
        data: null,
        gap_reasons: ['工程物业模块尚未提供 facility_summary；Task 9 接入后自动汇总'],
      },
    },
    metrics: [
      { key: 'leasing.vacancy_rate', domain: 'leasing', value: 0.25, reason: null },
      {
        key: 'operations.noi', domain: 'operations', label: '净营业收入（NOI）',
        value: null, reason: '缺少同源营业收入与经营成本明细',
      },
    ],
    metric_dictionary: {
      'leasing.vacancy_rate': {
        label: '空置率', formula: '空置铺位面积 / 铺位总面积', grain: '项目汇总',
        window: '当前快照', source: 'business_leasing.service.unit_summary',
        population: '全部铺位台账', caveats: [],
      },
    },
    reminders: [{
      code: 'operations.expiry_concentration', level: 'warning',
      metric_key: 'operations.expiry_12m_area_ratio', value: 0.3,
      message: '未来 12 个月到期合同面积占比 30.00%，超过 25% 警戒线',
    }],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  route.query = {}
  gmApi.summary.mockResolvedValue({ data: gmResponse() })
  dashboardApi.summary.mockResolvedValue({ data: {
    task: { todo: 1, in_progress: 0, done: 2, overdue: 0, completion_rate: 66.7 },
    schedule: { today_count: 0 },
    trend_7d: [], upcoming_tasks: [], today_schedules: [], recent_notes: [],
  } })
})

describe('business-gm 模块自动发现', () => {
  it('注册总经理经营聚合路由与经营菜单', () => {
    const route = getModuleRoutes().find((item) => item.path === '/business/gm')
    expect(route).toBeTruthy()
    expect(route.name).toBe('business-gm')
    expect(typeof route.component).toBe('function')
    const menu = getModuleMenus().find((item) => item.path === '/business/gm')
    expect(menu).toMatchObject({ group: '经营', title: '总经理经营聚合', order: 70 })
  })
})

describe('GM 页面真实请求与渲染状态', () => {
  it('按所选期间请求并生成带来源的经营指标', async () => {
    const ctx = mount(GmView)
    ctx.period.value = '2026-05'
    await ctx.load()
    expect(gmApi.summary).toHaveBeenCalledWith('2026-05')
    expect(ctx.loadFailed.value).toBe(false)
    const leasing = ctx.domainCards.value.find((item) => item.key === 'leasing')
    expect(leasing.available).toBe(true)
    expect(leasing.metrics[0].value).toBe(0.25)
    expect(ctx.metricLabel(leasing.metrics[0])).toBe('空置率')
    expect(ctx.metricSource(leasing.metrics[0])).toContain('business_leasing.service.unit_summary')
    expect(ctx.formatMetric(leasing.metrics[0])).toBe('25.00%')
  })

  it('消费 Dashboard 带入的合法期间参数', () => {
    route.query = { period: '2024-02' }
    const ctx = mount(GmView)
    expect(ctx.period.value).toBe('2024-02')
  })

  it('欠费合同数按计数而非金额格式显示', () => {
    const ctx = mount(GmView)
    expect(ctx.formatMetric({ key: 'operations.arrears_contracts', value: 2 })).toBe('2')
  })

  it('请求失败进入可重试错误态且不保留旧指标', async () => {
    const ctx = mount(GmView)
    await ctx.load()
    expect(ctx.summary.value).not.toBeNull()
    gmApi.summary.mockRejectedValueOnce(new Error('synthetic network failure'))
    await expect(ctx.load()).resolves.toBeUndefined()
    expect(ctx.loadFailed.value).toBe(true)
    expect(ctx.summary.value).toBeNull()
    expect(ctx.loading.value).toBe(false)
  })

  it('工程域暂不可用时展示原因且不把缺失指标变成零', async () => {
    const ctx = mount(GmView)
    await ctx.load()
    const facility = ctx.domainCards.value.find((item) => item.key === 'facility')
    expect(facility.available).toBe(false)
    expect(facility.data).toBeNull()
    expect(facility.gapReasons[0]).toContain('facility_summary')
    const noi = ctx.domainCards.value.find((item) => item.key === 'operations')
      .metrics.find((item) => item.key === 'operations.noi')
    expect(noi.value).toBeNull()
    expect(ctx.formatMetric(noi)).toBe('—')
    expect(noi.reason).toContain('缺少')
  })
})

describe('Dashboard 经营提醒', () => {
  it('只采用 GM 高优先提醒并跳转同一期间页面', async () => {
    const response = gmResponse()
    response.reminders.push({ code: 'informational', level: 'info', message: '普通说明' })
    gmApi.summary.mockResolvedValueOnce({ data: response })
    const ctx = mount(DashboardView)
    await ctx.loadGmReminders()
    expect(ctx.gmReminders.value).toHaveLength(1)
    expect(ctx.gmReminders.value[0].code).toBe('operations.expiry_concentration')
    ctx.openGm()
    expect(router.push).toHaveBeenCalledWith({
      path: '/business/gm', query: { period: '2026-05' },
    })
  })

  it('经营提醒失败不拖垮 Dashboard 主数据', async () => {
    gmApi.summary.mockRejectedValueOnce(new Error('synthetic gm failure'))
    const ctx = mount(DashboardView)
    await Promise.all([ctx.load(), ctx.loadGmReminders()])
    expect(ctx.data.value.task.todo).toBe(1)
    expect(ctx.loadFailed.value).toBe(false)
    expect(ctx.gmLoadFailed.value).toBe(true)
    expect(ctx.gmReminders.value).toEqual([])
  })
})
