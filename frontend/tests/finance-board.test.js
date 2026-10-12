import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-board/manifest'

describe('finance-board 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-board')
    expect(manifest.title).toBe('报表看板')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '报表看板',
      icon: 'DataBoard',
      path: '/finance/board',
      order: 70,
    })
  })

  it('getModuleRoutes 返回看板页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const r = routes.find((x) => x.path === '/finance/board')
    expect(r).toBeTruthy()
    expect(r.name).toBe('finance-board')
    expect(typeof r.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组报表看板菜单项（order=70）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/board')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('DataBoard')
    expect(item.order).toBe(70)
  })
})
