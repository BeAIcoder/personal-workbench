import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-expense/manifest'

describe('finance-expense 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-expense')
    expect(manifest.title).toBe('费用分类')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '费用分类',
      icon: 'Histogram',
      path: '/finance/expense',
      order: 10,
    })
  })

  it('getModuleRoutes 返回上传向导页与结果页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const upload = routes.find((r) => r.path === '/finance/expense')
    expect(upload).toBeTruthy()
    expect(upload.name).toBe('finance-expense')
    expect(upload.meta.title).toBe('费用分类')
    expect(typeof upload.component).toBe('function')

    const detail = routes.find((r) => r.path === '/finance/expense/runs/:id')
    expect(detail).toBeTruthy()
    expect(detail.name).toBe('finance-expense-run')
    expect(typeof detail.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组费用分类菜单项（order=10）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/expense')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.title).toBe('费用分类')
    expect(item.icon).toBe('Histogram')
    expect(item.order).toBe(10)
  })
})
