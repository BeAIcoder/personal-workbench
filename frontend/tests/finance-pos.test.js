import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-pos/manifest'

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
