// business-dq 前端模块契约测试
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import manifest from '../src/modules/business-dq/manifest.js'
import { issueTotalOf } from '../src/modules/business-dq/summaryCount.js'
import { domainSummary } from '../src/modules/business-dq/domainSummary.js'
import { buildAssistantAutoRoute } from '../src/utils/assistantEntry.js'

const dqSource = readFileSync(
  new URL('../src/modules/business-dq/views/DqView.vue', import.meta.url),
  'utf8',
)

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

describe('DQ 问题总数纯函数', () => {
  it('优先使用后端全量 issue_count（不被 top20 截断影响）', () => {
    expect(issueTotalOf({ issue_count: 42, top_issues: [{ count: 1 }] })).toBe(42)
  })

  it('issue_count 为 0 时返回 0 而不是回退累加', () => {
    expect(issueTotalOf({ issue_count: 0, top_issues: [{ count: 5 }] })).toBe(0)
  })

  it('旧响应无 issue_count 时回退累加 top_issues.count', () => {
    expect(issueTotalOf({ top_issues: [{ count: 3 }, { count: 4 }] })).toBe(7)
  })

  it('空数据与非法字段安全返回 0', () => {
    expect(issueTotalOf(undefined)).toBe(0)
    expect(issueTotalOf({ top_issues: [{ count: 'x' }, {}] })).toBe(0)
  })
})


describe('DQ 巡检基准日可访问性（M13 复审）', () => {
  it('日期选择器有真实 label for 与 aria-label 关联，不是只改文案', () => {
    expect(dqSource).toMatch(/<label[^>]*for="dq-as-of"/)
    expect(dqSource).toMatch(/id="dq-as-of"/)
    expect(dqSource).toContain('aria-label="巡检基准日"')
  })
})


describe('DQ 每域 E/W/I 计数（L-176）', () => {
  it('1 条 warning 显示 W1，而不是 W0', () => {
    expect(domainSummary({ available: true, error: 0, warning: 1, info: 0 })).toBe('E0/W1/I0')
  })

  it('error/warning/info 分别计数', () => {
    expect(domainSummary({ available: true, error: 2, warning: 3, info: 4 })).toBe('E2/W3/I4')
  })

  it('不可用域显示未就绪，缺失计数按 0', () => {
    expect(domainSummary({ available: false })).toBe('未就绪')
    expect(domainSummary({ available: true })).toBe('E0/W0/I0')
  })
})
