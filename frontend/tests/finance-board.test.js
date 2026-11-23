import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-board/manifest'
import { isTerminalRunStatus, shouldReloadAfterRun } from '../src/modules/finance-board/utils'

const boardSrc = readFileSync(
  new URL('../src/modules/finance-board/views/BoardView.vue', import.meta.url),
  'utf8',
)

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
    const runBody = boardSrc.match(/async function run\(\) \{[\s\S]*?\n\}/)?.[0] || ''
    expect(runBody).toContain('if (shouldReloadAfterRun(err)) await load()')
  })
})

// 异步运行契约（#4）：POST /finance/board/runs → 202 → 轮询 GET /finance/board/runs/{id}；
// 旧后端 404 时回退同步 /run。正常路径不再挂一个 1500s 的阻塞请求。
describe('finance-board 异步运行契约', () => {
  const runBody = boardSrc.match(/async function run\(\) \{[\s\S]*?\n\}/)?.[0] || ''

  it('先提交异步运行入口，而不是直接调用同步 /run', () => {
    expect(runBody).toContain("api.post('/finance/board/runs'")
    expect(runBody).toContain('runSync()')
  })

  it('异步提交只等短期超时（≤60s），不再把长预算压在一次请求上', () => {
    expect(runBody).toContain('SUBMIT_TIMEOUT_MS')
    const submit = boardSrc.match(/const SUBMIT_TIMEOUT_MS = (\d+)/)?.[1]
    expect(Number(submit)).toBeGreaterThan(0)
    expect(Number(submit)).toBeLessThanOrEqual(60000)
    expect(runBody).not.toContain('1500000')
  })

  it('404/405 时回退同步 /run，且同步回退仍覆盖后端最坏预算', () => {
    expect(runBody).toContain('status === 404 || status === 405')
    // 数字字面量是跨层契约：check_contracts / test_engineering_contracts 直接解析这一行
    const literal = boardSrc.match(/api\.post\('\/finance\/board\/run', \{\}, \{ timeout: (\d+) \}\)/)?.[1]
    expect(Number(literal)).toBeGreaterThan(1385000)
    expect(Number(literal)).toBeLessThanOrEqual(1500000)
  })

  it('轮询入口与短超时，并在终态/记录失效/预算耗尽三条路径收尾', () => {
    expect(boardSrc).toContain('api.get(`/finance/board/runs/${encodeURIComponent(runId)}`')
    expect(boardSrc).toContain('POLL_REQUEST_TIMEOUT_MS')
    const pollTimeout = Number(boardSrc.match(/const POLL_REQUEST_TIMEOUT_MS = (\d+)/)?.[1])
    expect(pollTimeout).toBeGreaterThan(0)
    expect(pollTimeout).toBeLessThanOrEqual(30000)
    expect(boardSrc).toContain('isTerminalRunStatus')
    expect(boardSrc).toContain('运行记录已失效')
    expect(boardSrc).toContain('已停止等待')
  })

  it('卸载时递增轮询代次，旧循环不再回写界面状态', () => {
    expect(boardSrc).toContain('onBeforeUnmount(() => { pollToken += 1 })')
    expect(boardSrc).toContain('if (token !== pollToken) return')
  })
})

describe('finance-board 运行终态判定', () => {
  it('running/pending 等继续轮询，ok/failed 与未知非空状态视为终态', () => {
    for (const s of ['running', 'pending', 'queued', 'started', 'in_progress', 'RUNNING']) {
      expect(isTerminalRunStatus(s), s).toBe(false)
    }
    for (const s of ['ok', 'failed', 'error', 'unknown', 'timeout']) {
      expect(isTerminalRunStatus(s), s).toBe(true)
    }
    // 空状态代表后端还没返回结果，继续等，不能误判成功
    expect(isTerminalRunStatus('')).toBe(false)
    expect(isTerminalRunStatus(undefined)).toBe(false)
  })
})

// 与 bugfix-frontend-finance 同范式的静态断言：无 jsdom/@vue/test-utils，
// 通过读取 .vue 源码校验失败快照日志查看接线。
describe('finance-board 失败快照日志查看（log_tail）', () => {
  const src = boardSrc

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

// iframe 高度跟随视口：固定 1200px 会造成「页面套小窗再内部滚动」
describe('finance-board 嵌入页 iframe 高度', () => {
  const src = boardSrc

  it('board-frame 不再使用固定 1200px，改为跟随视口且有最小高度', () => {
    expect(src).not.toContain('1200px')
    expect(src).toContain('height: calc(100vh - 240px)')
    expect(src).toContain('min-height: 560px')
  })
})
