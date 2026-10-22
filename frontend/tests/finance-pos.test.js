import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-pos/manifest'

// 行为测试沿用仓库既有约定（同 swarm-finance-safety.test.js）：node 环境无 jsdom，
// mock 生命周期钩子与依赖后直接驱动编译后的 setup()
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

const { ElMessage } = await import('element-plus')
const PosRunView = (await import('../src/modules/finance-pos/views/PosRunView.vue')).default

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

describe('finance-pos 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-pos')
    expect(manifest.title).toBe('POS 对账')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: 'POS 对账',
      icon: 'CreditCard',
      path: '/finance/pos',
      order: 90,
    })
  })

  it('getModuleRoutes 返回运行页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const r = routes.find((x) => x.path === '/finance/pos')
    expect(r).toBeTruthy()
    expect(r.name).toBe('finance-pos')
    expect(typeof r.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组 POS 对账菜单项（order=90）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/pos')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('CreditCard')
    expect(item.order).toBe(90)
  })
})

describe('finance-pos 上传前大小提示（swarm pos-hardening）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function mount() {
    return PosRunView.setup({}, { expose: () => {}, emit: () => {} })
  }

  function pick(files) {
    return { target: { files, value: 'C:\\fakepath' } }
  }

  it('正常小文件原样进入表单、不弹提示（既有流程不变）', () => {
    const ctx = mount()
    ctx.onFiles(pick([{ name: 'a.xlsx', size: 1024 }]))
    expect(ctx.form.files.map((f) => f.name)).toEqual(['a.xlsx'])
    expect(ElMessage.warning).not.toHaveBeenCalled()
  })

  it('单个文件超过 50MB：提示并跳过该文件，其余文件保留', () => {
    const ctx = mount()
    ctx.onFiles(pick([
      { name: 'big.xlsx', size: 51 * 1024 * 1024 },
      { name: 'ok.xlsx', size: 2048 },
    ]))
    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
    expect(ElMessage.warning.mock.calls[0][0]).toContain('big.xlsx')
    expect(ctx.form.files.map((f) => f.name)).toEqual(['ok.xlsx'])
  })

  it('合计超过 100MB：提示并跳过超限文件，先到的合法文件保留', () => {
    const ctx = mount()
    ctx.onFiles(pick([
      { name: 'a.xlsx', size: 40 * 1024 * 1024 },
      { name: 'b.xlsx', size: 40 * 1024 * 1024 },
      { name: 'c.xlsx', size: 40 * 1024 * 1024 },
    ]))
    expect(ctx.form.files.map((f) => f.name)).toEqual(['a.xlsx', 'b.xlsx'])
    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
    expect(ElMessage.warning.mock.calls[0][0]).toContain('c.xlsx')
  })

  it('所选文件全部超限时表单清空（创建按钮保持禁用）', () => {
    const ctx = mount()
    ctx.onFiles(pick([{ name: 'big.xlsx', size: 51 * 1024 * 1024 }]))
    expect(ctx.form.files).toHaveLength(0)
    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
  })

  it('新建弹窗模板含大小上限提示（50MB/100MB）', () => {
    const src = readFileSync(join(root, 'src/modules/finance-pos/views/PosRunView.vue'), 'utf8')
    const tpl = src.split('<script setup>')[0]
    expect(tpl).toContain('50MB')
    expect(tpl).toContain('100MB')
  })
})
