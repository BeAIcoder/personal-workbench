import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-vault/manifest'

describe('finance-vault 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-vault')
    expect(manifest.title).toBe('数据仓')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '数据仓',
      icon: 'Coin',
      path: '/finance/vault',
      order: 30,
    })
  })

  it('getModuleRoutes 返回表清单页与 SQL 查询页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const list = routes.find((r) => r.path === '/finance/vault')
    expect(list).toBeTruthy()
    expect(list.name).toBe('finance-vault')
    expect(typeof list.component).toBe('function')

    const query = routes.find((r) => r.path === '/finance/vault/query')
    expect(query).toBeTruthy()
    expect(query.name).toBe('finance-vault-query')
    expect(typeof query.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组数据仓菜单项（order=30）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/vault')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('Coin')
    expect(item.order).toBe(30)
  })
})
