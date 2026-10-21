import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/debug-errors/manifest'

describe('debug-errors 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('debug-errors')
    expect(manifest.title).toBe('前端报错')
    expect(manifest.menu).toMatchObject({
      group: '调试',
      title: '前端报错',
      icon: 'Monitor',
      path: '/debug/errors',
      order: 82,
    })
  })

  it('getModuleRoutes 返回前端报错路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const r = routes.find((x) => x.path === '/debug/errors')
    expect(r).toBeTruthy()
    expect(r.name).toBe('debug-errors')
    expect(r.meta.title).toBe('前端报错')
    expect(typeof r.component).toBe('function')
  })

  it('getModuleMenus 返回「调试」组前端报错菜单项（order=82）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/debug/errors')
    expect(item).toBeTruthy()
    expect(item.group).toBe('调试')
    expect(item.title).toBe('前端报错')
    expect(item.order).toBe(82)
  })

  it('「调试」组三个菜单项齐备且按 order 排序', () => {
    const debugMenus = getModuleMenus().filter((m) => m.group === '调试')
    expect(debugMenus.map((m) => m.path)).toEqual(['/debug/issues', '/debug/runs', '/debug/errors'])
  })
})
