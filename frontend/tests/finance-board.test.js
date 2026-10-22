import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-board/manifest'
import { shouldReloadAfterRun } from '../src/modules/finance-board/utils'

describe('finance-board 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-board')
    expect(manifest.title).toBe('报表看板')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '报表看板',
      icon: 'DataBoard',
      path: '/finance/board',
      order: 70,
    })
  })

  it('getModuleRoutes 返回看板页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const r = routes.find((x) => x.path === '/finance/board')
    expect(r).toBeTruthy()
    expect(r.name).toBe('finance-board')
    expect(typeof r.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组报表看板菜单项（order=70）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/board')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('DataBoard')
    expect(item.order).toBe(70)
  })
})

describe('finance-board 管线失败后的列表刷新', () => {
  it('有 HTTP 响应（如管线 503/409）时重拉快照，网络中断则保留旧列表', () => {
    expect(shouldReloadAfterRun({ response: { status: 503 } })).toBe(true)
    expect(shouldReloadAfterRun({ response: { status: 409 } })).toBe(true)
    expect(shouldReloadAfterRun(new Error('Network Error'))).toBe(false)
    expect(shouldReloadAfterRun(undefined)).toBe(false)
  })

  it('run() 的失败分支确实调用 load()（failed 快照立即可见）', () => {
    const src = readFileSync(
      new URL('../src/modules/finance-board/views/BoardView.vue', import.meta.url),
      'utf8',
    )
    const runBody = src.match(/async function run\(\) \{[\s\S]*?\n\}/)?.[0] || ''
    expect(runBody).toContain('if (shouldReloadAfterRun(err)) await load()')
  })
})

// 与 bugfix-frontend-finance 同范式的静态断言：无 jsdom/@vue/test-utils，
// 通过读取 .vue 源码校验失败快照日志查看接线。
describe('finance-board 失败快照日志查看（log_tail）', () => {
  const src = readFileSync(
    new URL('../src/modules/finance-board/views/BoardView.vue', import.meta.url),
    'utf8',
  )

  it('快照表格提供「日志」入口（有 log_tail 才显示，失败快照可点开）', () => {
    expect(src).toMatch(/v-if="row\.log_tail"/)
    expect(src).toMatch(/@click="showLog\(row\)"/)
  })

  it('日志以纯文本 <pre> 展示，禁止 v-html（防注入）', () => {
    expect(src).toContain('<pre class="log">')
    expect(src).toContain('{{ logContent }}')
    expect(src).not.toContain('v-html')
  })

  it('showLog 处理空值：无日志时给出占位文案', () => {
    expect(src).toMatch(/logContent\.value = row\.log_tail \|\| '[^']+'/)
  })

  it('iframe sandbox 契约保持不变', () => {
    expect(src).toContain('sandbox="allow-scripts"')
  })

  it('load() 请求失败时清空列表不崩溃', () => {
    expect(src).toMatch(/catch \{[\s\S]{0,80}items\.value = \[\]/)
  })
})
