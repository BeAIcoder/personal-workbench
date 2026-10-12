import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import { STATIC_MENUS } from '../src/components/AppLayout.vue'

describe('前端模块注册', () => {
  it('getModuleRoutes 返回 finance-hello 模块路由（/finance/hello，懒加载组件）', () => {
    const routes = getModuleRoutes()
    const hello = routes.find((r) => r.path === '/finance/hello')
    expect(hello).toBeTruthy()
    expect(hello.name).toBe('finance-hello')
    expect(hello.meta.title).toBe('模块示例')
    expect(typeof hello.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」分组菜单项', () => {
    const menus = getModuleMenus()
    const hello = menus.find((m) => m.path === '/finance/hello')
    expect(hello).toBeTruthy()
    expect(hello.group).toBe('财务')
    expect(hello.title).toBe('模块示例')
    expect(hello.order).toBe(99)
  })

  it('静态菜单 10 项不丢失（文案/图标/顺序不变）', () => {
    expect(STATIC_MENUS.map((m) => m.title)).toEqual([
      '工作台概览', '任务管理', '日程管理', '笔记管理', 'AI 助手', '知识与记忆', 'MCP 工具', '技能包', '专家团文件', '定时任务',
    ])
    expect(STATIC_MENUS.map((m) => m.path)).toEqual([
      '/', '/tasks', '/schedules', '/notes', '/assistant', '/knowledge', '/mcp', '/skill-packs', '/expert-packs', '/jobs',
    ])
    expect(STATIC_MENUS[0].icon).toBe('Odometer')
  })
})
