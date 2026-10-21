import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/debug-issues/manifest'

describe('debug-issues 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('debug-issues')
    expect(manifest.title).toBe('问题记录')
    expect(manifest.menu).toMatchObject({
      group: '调试',
      title: '问题记录',
      icon: 'Warning',
      path: '/debug/issues',
      order: 80,
    })
  })

  it('getModuleRoutes 返回问题记录路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const r = routes.find((x) => x.path === '/debug/issues')
    expect(r).toBeTruthy()
    expect(r.name).toBe('debug-issues')
    expect(r.meta.title).toBe('问题记录')
    expect(typeof r.component).toBe('function')
  })

  it('getModuleMenus 返回「调试」组问题记录菜单项（order=80）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/debug/issues')
    expect(item).toBeTruthy()
    expect(item.group).toBe('调试')
    expect(item.title).toBe('问题记录')
    expect(item.order).toBe(80)
  })
})
