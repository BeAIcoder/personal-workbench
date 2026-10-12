import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-ledger-bip/manifest'

describe('finance-ledger-bip 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-ledger-bip')
    expect(manifest.title).toBe('企业记账')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '企业记账（BIP）',
      icon: 'OfficeBuilding',
      path: '/finance/ledger-bip',
      order: 50,
    })
  })

  it('getModuleRoutes 返回导入/凭证/汇总三页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    for (const [path, name] of [
      ['/finance/ledger-bip', 'finance-ledger-bip'],
      ['/finance/ledger-bip/vouchers', 'finance-ledger-bip-vouchers'],
      ['/finance/ledger-bip/summary', 'finance-ledger-bip-summary'],
    ]) {
      const r = routes.find((x) => x.path === path)
      expect(r).toBeTruthy()
      expect(r.name).toBe(name)
      expect(typeof r.component).toBe('function')
    }
  })

  it('getModuleMenus 返回「财务」组企业记账菜单项（order=50）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/ledger-bip')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('OfficeBuilding')
    expect(item.order).toBe(50)
  })
})
