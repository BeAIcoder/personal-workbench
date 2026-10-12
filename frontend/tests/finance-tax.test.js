import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-tax/manifest'

describe('finance-tax 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-tax')
    expect(manifest.title).toBe('企业所得税')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '企业所得税',
      icon: 'OfficeBuilding',
      path: '/finance/tax',
      order: 60,
    })
  })

  it('getModuleRoutes 返回 10 条路由（列表/表单/计算/申报/导入/统计/政策/教材）', () => {
    const routes = getModuleRoutes()
    const paths = [
      '/finance/tax',
      '/finance/tax/enterprises/new',
      '/finance/tax/enterprises/:id/edit',
      '/finance/tax/calculate/:enterpriseId',
      '/finance/tax/returns',
      '/finance/tax/returns/:id',
      '/finance/tax/import',
      '/finance/tax/statistics',
      '/finance/tax/policies',
      '/finance/tax/textbook',
    ]
    for (const path of paths) {
      const r = routes.find((x) => x.path === path)
      expect(r, `缺少路由 ${path}`).toBeTruthy()
      expect(typeof r.component).toBe('function')
    }
  })

  it('getModuleMenus 返回「财务」组企业所得税菜单项（order=60）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/tax')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('OfficeBuilding')
    expect(item.order).toBe(60)
  })
})
