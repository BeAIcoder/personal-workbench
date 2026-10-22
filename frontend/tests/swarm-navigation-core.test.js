// swarm 任务 frontend-navigation-core 的回归测试（对应 2026-10-22 审计台账 F04 / F05）。
// 覆盖：
//   F04  税务统计/政策/教材、BIP 科目汇总页的可见入口（企业/申报/凭证页）
//   F05a 全局搜索防抖 + 过期响应竞态守卫
//   F05b Ctrl+K 快捷键缺 key 防护
//   F05c 删除确认后才判忙（确认期间连点的重复删除窗口）
//   F05d 日程跨天归日 / 空结束时间 / 时间格式
//   F05e 凭证分录详情抽屉的过期响应竞态
// 本仓库前端测试运行在 node 环境、不挂载 DOM：组件内抽出的纯函数用动态 import 做行为断言，
// 模板/装配层用结构化源码断言（与 interaction-submit-race / e2e-bug-* 既有约定一致）。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { getModuleRoutes } from '../src/modules'
import { createLatestGuard } from '../src/utils/search'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function readSrc(rel) {
  return readFileSync(join(root, rel), 'utf8')
}

/** 按大括号配对取出某个函数体（含函数头），避免用「下一个函数名」这类脆弱切片。 */
function fnBody(src, headerNeedle) {
  const start = src.indexOf(headerNeedle)
  if (start < 0) throw new Error(`未找到函数：${headerNeedle}`)
  const open = src.indexOf('{', start)
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return src.slice(start, i + 1)
    }
  }
  throw new Error(`函数体大括号未闭合：${headerNeedle}`)
}

/** 去掉行注释：结构性断言只看代码，不被解释性注释里的字面量误判。 */
function stripComments(code) {
  return code.replace(/\/\/[^\n]*/g, '')
}

describe('F04 已有路由但无可见入口的页面：企业/申报/凭证页加入口', () => {
  it('企业列表页提供统计/政策/教材三个入口（旧代码无任何入口）', () => {
    const src = readSrc('src/modules/finance-tax/views/EnterpriseListView.vue')
    expect(src, '企业列表页缺少统计分析入口').toContain('finance-tax-statistics')
    expect(src, '企业列表页缺少政策管理入口').toContain('finance-tax-policies')
    expect(src, '企业列表页缺少教材参考入口').toContain('finance-tax-textbook')
  })

  it('申报记录页提供统计分析入口', () => {
    const src = readSrc('src/modules/finance-tax/views/TaxReturnListView.vue')
    expect(src, '申报记录页缺少统计分析入口').toContain('finance-tax-statistics')
  })

  it('凭证查询页提供科目汇总入口', () => {
    const src = readSrc('src/modules/finance-ledger-bip/views/VoucherListView.vue')
    expect(src, '凭证查询页缺少科目汇总入口').toContain('finance-ledger-bip-summary')
  })

  it('入口数据可导航：TAX_PAGE_ENTRIES 三项均指向已注册路由', async () => {
    const { TAX_PAGE_ENTRIES } = await import('../src/modules/finance-tax/views/EnterpriseListView.vue')
    const routes = getModuleRoutes()
    expect(TAX_PAGE_ENTRIES.map((e) => e.name)).toEqual([
      'finance-tax-statistics',
      'finance-tax-policies',
      'finance-tax-textbook',
    ])
    for (const entry of TAX_PAGE_ENTRIES) {
      expect(
        routes.some((r) => r.name === entry.name),
        `入口 ${entry.title} 指向的路由 ${entry.name} 未注册`,
      ).toBe(true)
    }
  })

  it('入口数据可导航：申报页与凭证页入口同样指向已注册路由', async () => {
    const routes = getModuleRoutes()
    const { TAX_PAGE_ENTRIES: returnEntries } = await import('../src/modules/finance-tax/views/TaxReturnListView.vue')
    const { BIP_PAGE_ENTRIES } = await import('../src/modules/finance-ledger-bip/views/VoucherListView.vue')
    for (const entry of [...returnEntries, ...BIP_PAGE_ENTRIES]) {
      expect(
        routes.some((r) => r.name === entry.name),
        `入口 ${entry.title} 指向的路由 ${entry.name} 未注册`,
      ).toBe(true)
    }
    expect(returnEntries.map((e) => e.name)).toEqual(['finance-tax-statistics'])
    expect(BIP_PAGE_ENTRIES.map((e) => e.name)).toEqual(['finance-ledger-bip-summary'])
  })
})

describe('F05a 全局搜索：防抖调度与过期响应竞态', () => {
  it('查询经过防抖调度（旧代码每次击键直发 /api/search）', () => {
    const src = readSrc('src/components/GlobalSearch.vue')
    expect(src, '未引入防抖：每次输入都会立即发起搜索请求').toMatch(/createDebouncer|debounce/i)
    expect(src).toContain('debounce.schedule(')
  })

  it('空白输入立即清空建议，并取消在途防抖（旧代码无取消路径）', () => {
    const src = readSrc('src/components/GlobalSearch.vue')
    const body = stripComments(fnBody(src, 'async function fetchSuggestions('))
    expect(body).toMatch(/\.cancel\(\)/)
  })

  it('过期响应守卫保持：回调前与异常分支都检查序号', () => {
    const src = readSrc('src/components/GlobalSearch.vue')
    const checks = src.match(/searchGuard\.isCurrent\(token\)/g) || []
    expect(checks.length).toBeGreaterThanOrEqual(2)
    expect(src).toContain('const token = searchGuard.next()')
  })

  it('createLatestGuard 复用：旧 token 失效、新 token 生效（竞态不回归）', () => {
    const guard = createLatestGuard()
    const first = guard.next()
    const second = guard.next()
    expect(guard.isCurrent(first)).toBe(false)
    expect(guard.isCurrent(second)).toBe(true)
  })

  it('createDebouncer：连续 schedule 只执行最后一次，延迟未到不触发', async () => {
    const { createDebouncer, SEARCH_DEBOUNCE_MS } = await import('../src/components/GlobalSearch.vue')
    vi.useFakeTimers()
    try {
      const debounce = createDebouncer()
      const spy = vi.fn()
      debounce.schedule(() => spy('a'))
      debounce.schedule(() => spy('ab'))
      debounce.schedule(() => spy('abc'))
      expect(spy).not.toHaveBeenCalled()
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1)
      expect(spy).not.toHaveBeenCalled()
      vi.advanceTimersByTime(1)
      expect(spy).toHaveBeenCalledTimes(1)
      expect(spy).toHaveBeenCalledWith('abc')
    } finally {
      vi.useRealTimers()
    }
  })

  it('createDebouncer：cancel 阻止在途回调', async () => {
    const { createDebouncer } = await import('../src/components/GlobalSearch.vue')
    vi.useFakeTimers()
    try {
      const debounce = createDebouncer()
      const spy = vi.fn()
      debounce.schedule(spy)
      debounce.cancel()
      vi.advanceTimersByTime(5000)
      expect(spy).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('F05b Ctrl+K 快捷键：缺 key 不得抛错', () => {
  it('快捷键判定不假设 e.key 存在（旧代码 e.key.toLowerCase() 遇缺失 key 抛 TypeError）', () => {
    const src = readSrc('src/components/AppLayout.vue')
    expect(src).not.toMatch(/e\.key\.toLowerCase\(\)/)
    expect(src).toContain('isSearchHotkey')
  })

  it('isSearchHotkey：Ctrl/Cmd + k 命中，事件缺 key 时返回 false 而不抛错', async () => {
    const { isSearchHotkey } = await import('../src/components/AppLayout.vue')
    expect(isSearchHotkey({ ctrlKey: true, key: 'k' })).toBe(true)
    expect(isSearchHotkey({ metaKey: true, key: 'K' })).toBe(true)
    expect(() => isSearchHotkey({ ctrlKey: true })).not.toThrow()
    expect(isSearchHotkey({ ctrlKey: true })).toBe(false)
    expect(isSearchHotkey({ key: 'k' })).toBe(false)
    expect(isSearchHotkey({ ctrlKey: true, key: 'j' })).toBe(false)
    expect(isSearchHotkey(null)).toBe(false)
  })
})

describe('F05c 删除确认后才判忙：连点重复删除窗口', () => {
  it('remove() 的忙态检查位于确认框之前（旧代码确认后才判忙）', () => {
    const body = stripComments(fnBody(readSrc('src/views/TasksView.vue'), 'async function remove(row)'))
    const busy = body.indexOf('if (rowBusy) return')
    const confirm = body.indexOf('ElMessageBox.confirm')
    expect(busy, 'remove() 缺少忙态守卫').toBeGreaterThanOrEqual(0)
    expect(confirm, 'remove() 缺少删除确认').toBeGreaterThanOrEqual(0)
    expect(busy, '忙态检查在确认框之后：确认期间连点会绕过守卫重复删除').toBeLessThan(confirm)
  })

  it('remove() 保持 await+catch，且成功提示在删除请求之后', () => {
    const body = fnBody(readSrc('src/views/TasksView.vue'), 'async function remove(row)')
    expect(body).toContain('await ')
    expect(body).toContain('catch')
    const success = body.indexOf("ElMessage.success('已删除')")
    const removeCall = body.indexOf('taskApi.remove(')
    expect(success).toBeGreaterThan(removeCall)
  })
})

describe('F05d 日程：跨天归日 / 空结束时间 / 时间格式', () => {
  it('eventsByDay 按跨天区间归日（旧代码只按 start_time 归一天，隔夜日程次日不可见）', () => {
    const src = readSrc('src/views/ScheduleView.vue')
    const body = stripComments(fnBody(src, 'const eventsByDay = computed('))
    expect(body).toContain('scheduleDayKeys')
  })

  it('AI 备会不再直接 slice 结束时间（空 end_time 会抛 TypeError）', () => {
    const src = readSrc('src/views/ScheduleView.vue')
    expect(src, 'aiPrepare 仍直接 slice end_time').not.toContain('ev.end_time.slice')
    const body = stripComments(fnBody(src, 'function aiPrepare(ev)'))
    expect(body).toContain('formatEventTimeRange')
  })

  it('事件行时间展示改用统一区间格式化（空结束时间不出现悬空连接符）', () => {
    const src = readSrc('src/views/ScheduleView.vue')
    expect(src, '仍在使用只取时分的 timeOnly').not.toContain('timeOnly')
    expect(src).toContain('formatEventTimeRange(ev.start_time, ev.end_time)')
  })

  it('scheduleDayKeys：跨天日程覆盖开始日与结束日', async () => {
    const { scheduleDayKeys } = await import('../src/views/ScheduleView.vue')
    expect(scheduleDayKeys('2026-10-22T23:00:00', '2026-10-23T01:00:00')).toEqual([
      '2026-10-22',
      '2026-10-23',
    ])
    expect(scheduleDayKeys('2026-10-31T22:00:00', '2026-11-01T02:00:00')).toEqual([
      '2026-10-31',
      '2026-11-01',
    ])
  })

  it('scheduleDayKeys：同日/缺结束/脏数据只落开始日，非法开始返回空', async () => {
    const { scheduleDayKeys } = await import('../src/views/ScheduleView.vue')
    expect(scheduleDayKeys('2026-10-22T09:00:00', '2026-10-22T10:00:00')).toEqual(['2026-10-22'])
    expect(scheduleDayKeys('2026-10-22T09:00:00', null)).toEqual(['2026-10-22'])
    expect(scheduleDayKeys('2026-10-22T09:00:00', '')).toEqual(['2026-10-22'])
    expect(scheduleDayKeys('2026-10-22T09:00:00', '2026-10-20T10:00:00')).toEqual(['2026-10-22'])
    expect(scheduleDayKeys('', '')).toEqual([])
  })

  it('formatEventTimeRange：无结束时间只显示开始，不出现悬空「 - 」', async () => {
    const { formatEventTimeRange } = await import('../src/views/ScheduleView.vue')
    expect(formatEventTimeRange('2026-10-22T23:00:00', '')).toBe('2026-10-22 23:00')
    expect(formatEventTimeRange('2026-10-22T23:00:00', null)).toBe('2026-10-22 23:00')
  })

  it('formatEventTimeRange：同日结束只带时分（保持原展示），跨天结束带月日', async () => {
    const { formatEventTimeRange } = await import('../src/views/ScheduleView.vue')
    expect(formatEventTimeRange('2026-10-22T09:00:00', '2026-10-22T10:00:00')).toBe('2026-10-22 09:00 - 10:00')
    expect(formatEventTimeRange('2026-10-22T23:00:00', '2026-10-23T01:00:00')).toBe('2026-10-22 23:00 - 10-23 01:00')
  })

  it('formatEventTimeRange：结束早于开始的脏数据退回只显示开始', async () => {
    const { formatEventTimeRange } = await import('../src/views/ScheduleView.vue')
    expect(formatEventTimeRange('2026-10-22T09:00:00', '2026-10-21T10:00:00')).toBe('2026-10-22 09:00')
    expect(formatEventTimeRange('', '')).toBe('')
  })
})

describe('F05e 凭证分录详情抽屉：过期响应竞态', () => {
  it('showDetail 带请求序号守卫，过期响应不覆盖新详情（旧代码无守卫）', () => {
    const body = stripComments(
      fnBody(readSrc('src/modules/finance-ledger-bip/views/VoucherListView.vue'), 'async function showDetail(id)'),
    )
    expect(body).toMatch(/detailSeq/)
    expect(body).toMatch(/if\s*\(seq !== detailSeq\)\s*return/)
  })

  it('showDetail 失败仍保持静默 catch（不重复弹窗，拦截器已提示）', () => {
    const body = fnBody(readSrc('src/modules/finance-ledger-bip/views/VoucherListView.vue'), 'async function showDetail(id)')
    expect(body).toContain('catch')
    expect(body).not.toContain('ElMessage.error')
  })
})
