// 五个经营/职能模块的前端注册契约（路由/菜单/唯一性）
import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'

const BUSINESS = [
  { name: 'business-leasing', path: '/business/leasing', title: '招商管理', group: '经营', order: 71 },
  { name: 'business-operations', path: '/business/operations', title: '营运管理', group: '经营', order: 72 },
  { name: 'business-planning', path: '/business/planning', title: '企划管理', group: '经营', order: 73 },
  { name: 'business-legal', path: '/business/legal', title: '法务管理', group: '职能', order: 81 },
  { name: 'business-hr', path: '/business/hr', title: '行政人事', group: '职能', order: 82 },
]

describe('经营/职能模块注册', () => {
  it('五个模块的路由均已注册且为懒加载', () => {
    const routes = getModuleRoutes()
    for (const m of BUSINESS) {
      const route = routes.find((r) => r.path === m.path)
      expect(route, `${m.name} 路由缺失`).toBeTruthy()
      expect(route.name).toBe(m.name)
      expect(typeof route.component).toBe('function')
    }
  })

  it('五个模块的菜单分组/排序正确', () => {
    const menus = getModuleMenus()
    for (const m of BUSINESS) {
      const menu = menus.find((x) => x.path === m.path)
      expect(menu, `${m.name} 菜单缺失`).toBeTruthy()
      expect(menu.group).toBe(m.group)
      expect(menu.title).toBe(m.title)
      expect(menu.order).toBe(m.order)
    }
  })

  it('模块路由 path/name 全量唯一', () => {
    const routes = getModuleRoutes()
    expect(new Set(routes.map((r) => r.path)).size).toBe(routes.length)
    expect(new Set(routes.map((r) => r.name)).size).toBe(routes.length)
  })
})
