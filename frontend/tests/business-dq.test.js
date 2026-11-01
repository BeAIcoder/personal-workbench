// business-dq 前端模块契约测试
import { describe, expect, it } from 'vitest'
import manifest from '../src/modules/business-dq/manifest.js'
import { buildAssistantAutoRoute } from '../src/utils/assistantEntry.js'

describe('business-dq 模块', () => {
  it('manifest 形状与菜单归属', () => {
    expect(manifest.name).toBe('business-dq')
    expect(manifest.routes).toHaveLength(1)
    expect(manifest.routes[0].path).toBe('/business/dq')
    expect(manifest.routes[0].meta.title).toBe('数据质量巡检')
    expect(manifest.menu.group).toBe('职能')
    expect(manifest.menu.order).toBe(83)
  })

  it('模块唯一性与注册中心兼容', async () => {
    const { getModuleRoutes, getModuleMenus } = await import('../src/modules/index.js')
    const routes = getModuleRoutes()
    const paths = routes.map(r => r.path)
    expect(paths.filter(p => p === '/business/dq')).toHaveLength(1)
    const menus = getModuleMenus()
    expect(menus.filter(m => m.path === '/business/dq')).toHaveLength(1)
  })

  it('buildAssistantAutoRoute 保留 query 对象形式', () => {
    const route = buildAssistantAutoRoute('你好')
    expect(route).toEqual({ path: '/assistant', query: { auto: '你好' } })
    expect(buildAssistantAutoRoute('')).toEqual({ path: '/assistant', query: { auto: '' } })
  })
})
