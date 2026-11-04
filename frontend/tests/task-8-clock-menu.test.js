// Task 8 / FE L-134、L-140：跨日日期/问候必须随时间刷新；应用内深链要展开父级，
// 但用户手动折叠过的分组不得被后续路由变化强行展开。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ mounted: [], beforeUnmount: [] }))
const route = vi.hoisted(() => ({ path: '/', query: {}, meta: {} }))
const api = vi.hoisted(() => ({
  get: vi.fn().mockResolvedValue({ data: { version: 'test' } }),
  defaults: { baseURL: '/api' },
}))

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
vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('../src/api', () => ({
  default: api,
  BACKEND_HINT: '测试后端提示',
  dashboardApi: { summary: vi.fn().mockResolvedValue({ data: {} }) },
  gmApi: { summary: vi.fn().mockResolvedValue({ data: { period: '2026-11', reminders: [] } }) },
  assistantApi: { config: { get: vi.fn(), saveGlobals: vi.fn() } },
}))

const { todayTextOf, greetingOf } = await import('../src/utils/clock.js')
const { menuOpenIndices } = await import('../src/utils/menuOpen.js')
const DashboardView = (await import('../src/views/DashboardView.vue')).default
const AppLayout = (await import('../src/components/AppLayout.vue')).default

function mount(component, props = {}) {
  return component.setup(props, { expose: () => {}, emit: () => {} })
}

function runMounted() {
  for (const fn of hooks.mounted.splice(0)) fn()
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  hooks.beforeUnmount.length = 0
  route.path = '/'
  route.query = {}
  vi.useRealTimers()
  vi.stubGlobal('window', {
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })
})

describe('跨日日期与问候纯函数', () => {
  it('同一时刻格式化稳定，跨午夜后日期与问候都变化', () => {
    const late = new Date('2026-11-04T23:59:00')
    const early = new Date('2026-11-05T00:01:00')
    expect(todayTextOf(late)).toContain('2026年11月04日')
    expect(todayTextOf(early)).toContain('2026年11月05日')
    expect(greetingOf(late)).toBe('晚上好')
    expect(greetingOf(early)).toBe('夜深了，注意休息')
  })
})

describe('Dashboard 跨日刷新', () => {
  it('挂载后按分钟更新 now，午夜后日期与问候同步刷新', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-04T23:59:00'))
    const ctx = mount(DashboardView)
    expect(ctx.todayText.value).toContain('2026年11月04日')
    expect(ctx.greeting.value).toBe('晚上好')

    runMounted()
    await vi.advanceTimersByTimeAsync(2 * 60 * 1000)

    expect(ctx.todayText.value).toContain('2026年11月05日')
    expect(ctx.greeting.value).toBe('夜深了，注意休息')
    for (const fn of hooks.beforeUnmount.splice(0)) fn()
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('菜单深链展开', () => {
  const moduleGroups = [
    {
      name: '财务',
      items: [{ path: '/finance/vault' }],
      subgroups: [{ name: '税务填报', items: [{ path: '/finance/tax' }] }],
    },
    { name: '经营', items: [{ path: '/business/leasing' }], subgroups: [] },
  ]
  const staticGroups = [{ name: 'AI 配置', items: [{ path: '/knowledge' }, { path: '/mcp' }] }]

  it('返回父分组与二级子分组索引；未知路径返回空', () => {
    expect(menuOpenIndices('/finance/tax', moduleGroups, staticGroups)).toEqual([
      'group-财务',
      'subgroup-财务-税务填报',
    ])
    expect(menuOpenIndices('/knowledge', moduleGroups, staticGroups)).toEqual(['group-AI 配置'])
    expect(menuOpenIndices('/missing', moduleGroups, staticGroups)).toEqual([])
  })

  it('应用内跳到新分组会展开；用户手动折叠后同一分组不再被强行展开', () => {
    const ctx = mount(AppLayout)
    const open = vi.fn()
    ctx.menuRef.value = { open }

    ctx.applyRouteOpen('/knowledge')
    expect(open).toHaveBeenCalledWith('group-AI 配置')

    open.mockClear()
    ctx.onMenuClose('group-AI 配置')
    ctx.applyRouteOpen('/mcp')
    expect(open).not.toHaveBeenCalled()

    ctx.onMenuOpen('group-AI 配置')
    expect(ctx.userClosed.has('group-AI 配置')).toBe(false)
  })
})
