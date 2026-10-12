import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-ledger-personal/manifest'

describe('finance-ledger-personal 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-ledger-personal')
    expect(manifest.title).toBe('个人记账')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '个人记账',
      icon: 'Notebook',
      path: '/finance/ledger-personal',
      order: 40,
    })
  })

  it('getModuleRoutes 返回记账/流水/统计三页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    for (const [path, name] of [
      ['/finance/ledger-personal', 'finance-ledger-personal'],
      ['/finance/ledger-personal/transactions', 'finance-ledger-personal-transactions'],
      ['/finance/ledger-personal/stats', 'finance-ledger-personal-stats'],
    ]) {
      const r = routes.find((x) => x.path === path)
      expect(r).toBeTruthy()
      expect(r.name).toBe(name)
      expect(typeof r.component).toBe('function')
    }
  })

  it('getModuleMenus 返回「财务」组个人记账菜单项（order=40）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/ledger-personal')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('Notebook')
    expect(item.order).toBe(40)
  })
})
