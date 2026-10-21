import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import { STATIC_MENUS, STATIC_MENU_GROUPS, TAIL_MENUS } from '../src/components/AppLayout.vue'

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

  it('静态菜单：一级 5 项 + 「AI 配置」分组 4 项 + 定时任务（文案/图标/顺序不丢失）', () => {
    expect(STATIC_MENUS.map((m) => m.title)).toEqual([
      '工作台概览', '任务管理', '日程管理', '笔记管理', 'AI 助手',
    ])
    expect(STATIC_MENUS.map((m) => m.path)).toEqual([
      '/', '/tasks', '/schedules', '/notes', '/assistant',
    ])
    expect(STATIC_MENUS[0].icon).toBe('Odometer')
    // 「AI 配置」子菜单：菜单名与各页面 meta.title 一致（技能包管理命名统一）
    expect(STATIC_MENU_GROUPS).toHaveLength(1)
    const group = STATIC_MENU_GROUPS[0]
    expect(group.name).toBe('AI 配置')
    expect(group.items.map((m) => m.path)).toEqual(['/knowledge', '/mcp', '/skill-packs', '/expert-packs'])
    expect(group.items.map((m) => m.title)).toEqual(['知识与记忆', 'MCP 工具', '技能包管理', '专家团文件'])
    // 定时任务保持一级菜单原位（渲染在静态分组之后）
    expect(TAIL_MENUS.map((m) => m.path)).toEqual(['/jobs'])
    expect(TAIL_MENUS[0].icon).toBe('Timer')
  })
})
