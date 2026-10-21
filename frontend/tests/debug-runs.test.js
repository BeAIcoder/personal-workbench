import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/debug-runs/manifest'

describe('debug-runs 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('debug-runs')
    expect(manifest.title).toBe('测试运行')
    expect(manifest.menu).toMatchObject({
      group: '调试',
      title: '测试运行',
      icon: 'Aim',
      path: '/debug/runs',
      order: 81,
    })
  })

  it('getModuleRoutes 返回测试运行路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const r = routes.find((x) => x.path === '/debug/runs')
    expect(r).toBeTruthy()
    expect(r.name).toBe('debug-runs')
    expect(r.meta.title).toBe('测试运行')
    expect(typeof r.component).toBe('function')
  })

  it('getModuleMenus 返回「调试」组测试运行菜单项（order=81）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/debug/runs')
    expect(item).toBeTruthy()
    expect(item.group).toBe('调试')
    expect(item.title).toBe('测试运行')
    expect(item.order).toBe(81)
  })
})
