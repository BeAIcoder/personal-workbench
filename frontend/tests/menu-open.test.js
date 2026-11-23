// T6B 导航契约：侧栏激活与自动展开按「最长路径前缀」匹配。
// 详情子路由（如 /finance/expense/runs/42、/business/manage/weekly）不在菜单里，
// 但必须高亮并展开其所属菜单项/分组；前缀按路径段边界匹配，避免 /finance/expenseX
// 误命中 /finance/expense。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ mounted: [], beforeUnmount: [] }))
const route = vi.hoisted(() => ({ path: '/', query: {}, meta: {} }))
const api = vi.hoisted(() => ({
  get: vi.fn().mockResolvedValue({ data: { version: 'test' } }),
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
  searchApi: { suggest: vi.fn() },
  dashboardApi: { summary: vi.fn().mockResolvedValue({ data: {} }) },
  gmApi: { summary: vi.fn().mockResolvedValue({ data: { period: '2026-11', reminders: [] } }) },
  assistantApi: { config: { get: vi.fn(), saveGlobals: vi.fn() } },
}))

const { menuActivePath, menuOpenIndices } = await import('../src/utils/menuOpen.js')
const { getModuleMenuGroups } = await import('../src/modules')
const AppLayout = (await import('../src/components/AppLayout.vue')).default

const moduleGroups = [
  {
    name: '财务',
    items: [{ path: '/finance/vault' }, { path: '/finance' }],
    subgroups: [
      { name: '账务处理', items: [{ path: '/finance/expense' }, { path: '/finance/invoice' }] },
    ],
  },
  { name: '经营', items: [{ path: '/business/manage' }], subgroups: [] },
]
const staticGroups = [{ name: 'AI 配置', items: [{ path: '/knowledge' }, { path: '/mcp' }] }]
const topMenus = [{ path: '/' }, { path: '/tasks' }, { path: '/assistant' }]

function mount(component, props = {}) {
  return component.setup(props, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
  hooks.mounted.length = 0
  hooks.beforeUnmount.length = 0
  route.path = '/'
  route.query = {}
  api.get.mockResolvedValue({ data: { version: 'test' } })
})

describe('最长前缀激活', () => {
  it('详情子路由命中父菜单项', () => {
    expect(menuActivePath('/finance/expense/runs/42', topMenus, moduleGroups, staticGroups)).toBe('/finance/expense')
    expect(menuActivePath('/business/manage/weekly', topMenus, moduleGroups, staticGroups)).toBe('/business/manage')
    expect(menuActivePath('/tasks/42', topMenus, moduleGroups, staticGroups)).toBe('/tasks')
  })

  it('多级前缀取最长：/finance/expense 优先于 /finance', () => {
    expect(menuActivePath('/finance/expense/runs/42', topMenus, moduleGroups, staticGroups)).not.toBe('/finance')
    expect(menuActivePath('/finance/invoice/batches/7', topMenus, moduleGroups, staticGroups)).toBe('/finance/invoice')
  })

  it('按路径段边界匹配：近似前缀不误命中', () => {
    const boundaryGroups = [
      { name: '财务', items: [], subgroups: [{ name: '账务处理', items: [{ path: '/finance/expense' }] }] },
    ]
    expect(menuActivePath('/finance/expenseX', [], boundaryGroups, [])).toBe('')
    expect(menuActivePath('/tasks-archive', topMenus, moduleGroups, staticGroups)).toBe('')
  })

  it('根路径只做精确匹配，未知路由不高亮工作台概览', () => {
    expect(menuActivePath('/', topMenus, moduleGroups, staticGroups)).toBe('/')
    expect(menuActivePath('/unknown/detail', topMenus, moduleGroups, staticGroups)).toBe('')
  })

  it('真实模块菜单：财务详情子路由与经营管理子路由都能命中', () => {
    const realGroups = getModuleMenuGroups()
    expect(menuActivePath('/finance/expense/runs/42', [], realGroups, [])).toBe('/finance/expense')
    expect(menuActivePath('/finance/invoice/batches/7', [], realGroups, [])).toBe('/finance/invoice')
    expect(menuActivePath('/business/manage/weekly', [], realGroups, [])).toBe('/business/manage')
  })
})

describe('最长前缀自动展开', () => {
  it('详情子路由展开父分组与二级子分组', () => {
    expect(menuOpenIndices('/finance/expense/runs/42', moduleGroups, staticGroups)).toEqual([
      'group-财务',
      'subgroup-财务-账务处理',
    ])
    expect(menuOpenIndices('/business/manage/weekly', moduleGroups, staticGroups)).toEqual(['group-经营'])
  })

  it('精确匹配行为保持不变（现有深链回归）', () => {
    expect(menuOpenIndices('/finance/expense', moduleGroups, staticGroups)).toEqual([
      'group-财务',
      'subgroup-财务-账务处理',
    ])
    expect(menuOpenIndices('/knowledge', moduleGroups, staticGroups)).toEqual(['group-AI 配置'])
    expect(menuOpenIndices('/missing', moduleGroups, staticGroups)).toEqual([])
  })

  it('AppLayout 的 activeMenu 用最长前缀，详情页仍有高亮项', () => {
    // mock 的 route 是普通对象（非响应式），每次改路径需重新 mount 计算 activeMenu
    const activeFor = (path) => {
      route.path = path
      return mount(AppLayout).activeMenu.value
    }
    expect(activeFor('/finance/expense/runs/42')).toBe('/finance/expense')
    expect(activeFor('/business/manage/weekly')).toBe('/business/manage')
    expect(activeFor('/tasks/42')).toBe('/tasks')
    // 未知路由没有高亮项：回落到原始路径（不命中任何菜单）
    expect(activeFor('/unknown/detail')).toBe('/unknown/detail')
  })

  it('应用内跳到详情子路由时展开父级', () => {
    route.path = '/'
    const ctx = mount(AppLayout)
    const open = vi.fn()
    ctx.menuRef.value = { open }

    ctx.applyRouteOpen('/finance/expense/runs/42')
    expect(open).toHaveBeenCalledWith('group-财务')
    expect(open).toHaveBeenCalledWith('subgroup-财务-账务处理')

    open.mockClear()
    ctx.onMenuClose('group-经营')
    ctx.applyRouteOpen('/business/manage/weekly')
    expect(open).not.toHaveBeenCalled()
  })
})
