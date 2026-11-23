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
  ElMessageBox: { prompt: vi.fn() },
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

const { ElMessage, ElMessageBox } = await import('element-plus')
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
    return { target: { files, value: 'C:/path/to/fakepath' } }
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

describe('finance-pos 执行失败补填项目重试（--project）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function mount() {
    return PosRunView.setup({}, { expose: () => {}, emit: () => {} })
  }

  const row = { id: 7, status: 'uploaded' }
  const PROJECT_ERR = { response: { status: 503, data: { detail: '引擎步骤失败：analyze_pos_reconciliation.py' } } }
  const okResp = { data: { status: 'ok', artifacts: ['POS项目台账.xlsx'], error: null } }

  it('识别失败（detail 含 --project）→ 弹窗补填 → 带 ?project 重试且成功', async () => {
    const api = (await import('../src/api')).default
    api.get.mockResolvedValue({ data: { items: [] } })
    api.post
      .mockRejectedValueOnce({ ...PROJECT_ERR, response: { ...PROJECT_ERR.response, data: { detail: 'ValueError: 无法从辅助明细账识别项目，请使用 --project 指定' } } })
      .mockResolvedValueOnce(okResp)
    ElMessageBox.prompt.mockResolvedValue({ value: '项目A' })

    const ctx = mount()
    await ctx.execute(row)

    expect(ElMessageBox.prompt).toHaveBeenCalledTimes(1)
    expect(ElMessageBox.prompt.mock.calls[0][0]).toContain('识别或匹配项目')
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(api.post.mock.calls[1][0]).toBe('/finance/pos/runs/7/execute?project=%E9%A1%B9%E7%9B%AEA')
    expect(ElMessage.success).toHaveBeenCalledWith('执行完成，产物 1 个')
  })

  it('首次执行成功时不弹窗、POST 不带 query 参数（既有行为不变）', async () => {
    const api = (await import('../src/api')).default
    api.get.mockResolvedValue({ data: { items: [] } })
    api.post.mockResolvedValueOnce(okResp)

    const ctx = mount()
    await ctx.execute(row)

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post.mock.calls[0][0]).toBe('/finance/pos/runs/7/execute')
    expect(ElMessageBox.prompt).not.toHaveBeenCalled()
  })

  it('非项目类失败不弹窗', async () => {
    const api = (await import('../src/api')).default
    api.get.mockResolvedValue({ data: { items: [] } })
    api.post.mockRejectedValueOnce(PROJECT_ERR)

    const ctx = mount()
    await ctx.execute(row)

    expect(ElMessageBox.prompt).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('弹窗取消或留空 → 不重试', async () => {
    const api = (await import('../src/api')).default
    api.get.mockResolvedValue({ data: { items: [] } })
    api.post.mockRejectedValueOnce({ ...PROJECT_ERR, response: { ...PROJECT_ERR.response, data: { detail: '请使用 --project 指定' } } })
    ElMessageBox.prompt.mockRejectedValueOnce(new Error('cancel'))

    const ctx = mount()
    await ctx.execute(row)

    expect(ElMessageBox.prompt).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenCalledTimes(1)
  })
})
