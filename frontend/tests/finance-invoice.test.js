import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-invoice/manifest'

describe('finance-invoice 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-invoice')
    expect(manifest.title).toBe('发票处理')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '发票处理',
      icon: 'Tickets',
      path: '/finance/invoice',
      order: 20,
    })
  })

  it('getModuleRoutes 返回批次列表页与详情页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const list = routes.find((r) => r.path === '/finance/invoice')
    expect(list).toBeTruthy()
    expect(list.name).toBe('finance-invoice')
    expect(typeof list.component).toBe('function')

    const detail = routes.find((r) => r.path === '/finance/invoice/batches/:id')
    expect(detail).toBeTruthy()
    expect(detail.name).toBe('finance-invoice-detail')
    expect(typeof detail.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组发票处理菜单项（order=20）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/invoice')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('Tickets')
    expect(item.order).toBe(20)
  })
})
