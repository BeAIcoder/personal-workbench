import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-cashflow/manifest'

// 行为测试沿用仓库既有约定（同 finance-pos.test.js）：node 环境无 jsdom，
// mock 生命周期钩子与依赖后直接驱动编译后的 setup()，验证分页请求/翻页/变页大小。
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onUnmounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}))

vi.mock('../src/api', () => {
  const api = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }
  return { default: api, API_BASE: '/api' }
})

const api = (await import('../src/api')).default
const CashflowRunView = (await import('../src/modules/finance-cashflow/views/CashflowRunView.vue')).default

describe('finance-cashflow 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-cashflow')
    expect(manifest.title).toBe('现金流填报')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '现金流填报',
      icon: 'TrendCharts',
      path: '/finance/cashflow',
      order: 80,
    })
  })

  it('getModuleRoutes 返回运行页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const r = routes.find((x) => x.path === '/finance/cashflow')
    expect(r).toBeTruthy()
    expect(r.name).toBe('finance-cashflow')
    expect(typeof r.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组现金流填报菜单项（order=80）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/cashflow')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('TrendCharts')
    expect(item.order).toBe(80)
  })
})

// 与 bugfix-frontend-finance 同范式的静态断言：无 jsdom/@vue/test-utils，
// 通过读取 .vue 源码校验分页接线（load 传参、读取真实 total、模板渲染分页器）。
describe('finance-cashflow 列表分页接线（hardening）', () => {
  const src = readFileSync(
    new URL('../src/modules/finance-cashflow/views/CashflowRunView.vue', import.meta.url),
    'utf8',
  )

  it('load() 请求携带 page/page_size 分页参数', () => {
    expect(src).toMatch(
      /cashflow\/runs'[\s\S]{0,120}params:\s*\{\s*page:\s*page\.value[\s\S]{0,80}page_size:\s*pageSize\.value/,
    )
  })

  it('load() 读取后端返回的真实 total', () => {
    expect(src).toMatch(/total\.value\s*=\s*data\.total/)
  })

  it('模板渲染分页器并绑定 total/current-page/page-size 与切换事件', () => {
    expect(src).toContain('<el-pagination')
    expect(src).toMatch(/:total="total"/)
    expect(src).toMatch(/:current-page="page"/)
    expect(src).toMatch(/:page-size="pageSize"/)
    expect(src).toMatch(/@current-change="onPageChange"/)
    expect(src).toMatch(/@size-change="onSizeChange"/)
  })
})

// 行为测试（swarm cashflow-review-fix 补强）：mock api 直驱 setup()，
// 验证分页参数真实下发、翻页/变页大小行为与失败清空——而非仅静态源码断言。
describe('finance-cashflow 列表分页行为（review-fix 补强）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function mount() {
    return CashflowRunView.setup({}, { expose: () => {}, emit: () => {} })
  }

  const flush = () => new Promise((r) => setTimeout(r, 0))

  it('load() 按 page/page_size 请求并写入 items 与后端真实 total', async () => {
    api.get.mockResolvedValue({ data: { items: [{ id: 2 }, { id: 1 }], total: 9 } })
    const ctx = mount()
    await ctx.load()
    expect(api.get).toHaveBeenCalledTimes(1)
    const [url, cfg] = api.get.mock.calls[0]
    expect(url).toBe('/finance/cashflow/runs')
    expect(cfg.params).toEqual({ page: 1, page_size: 20 })
    expect(ctx.items.value.map((i) => i.id)).toEqual([2, 1])
    expect(ctx.total.value).toBe(9)
  })

  it('onPageChange 切页后带新 page 重新请求', async () => {
    api.get.mockResolvedValue({ data: { items: [], total: 9 } })
    const ctx = mount()
    await ctx.load()
    api.get.mockClear()
    ctx.onPageChange(3)
    expect(ctx.page.value).toBe(3)
    await flush()
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get.mock.calls[0][1].params).toEqual({ page: 3, page_size: 20 })
  })

  it('onSizeChange 改页大小后复位到第 1 页并带新 page_size 请求', async () => {
    api.get.mockResolvedValue({ data: { items: [], total: 9 } })
    const ctx = mount()
    await ctx.load()
    api.get.mockClear()
    ctx.onSizeChange(50)
    expect(ctx.pageSize.value).toBe(50)
    expect(ctx.page.value).toBe(1)
    await flush()
    expect(api.get.mock.calls[0][1].params).toEqual({ page: 1, page_size: 50 })
  })

  it('load() 请求失败时清空列表与 total（不崩溃）', async () => {
    api.get.mockRejectedValue(new Error('boom'))
    const ctx = mount()
    await ctx.load()
    expect(ctx.items.value).toEqual([])
    expect(ctx.total.value).toBe(0)
  })
})
