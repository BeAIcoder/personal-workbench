import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildMonthOptions, localMonth, localToday } from '../src/modules/_shared/dates'
import { applyFilters } from '../src/modules/_shared/filters'
import { takeFile, takeFiles } from '../src/modules/_shared/fileInput'

// B6 行为测试需要统计弹窗次数：mock element-plus 后再引入带拦截器的 api
vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))
const { ElMessage } = await import('element-plus')
const { default: api } = await import('../src/api')

// 运行环境为中国时区（UTC+8）：凌晨 0-8 点 UTC 日期比本地晚一天，
// 月初 1 号凌晨 UTC 月份比本地晚一个月
describe('本地日期工具（B1/B2/B3：UTC 偏差修复）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('localToday 凌晨 0:30 返回本地当天，而非 UTC 的昨天', () => {
    vi.setSystemTime(new Date(2026, 2, 1, 0, 30)) // 本地 2026-03-01 00:30
    expect(localToday()).toBe('2026-03-01')
  })

  it('localToday 中午场景不回归', () => {
    vi.setSystemTime(new Date(2026, 9, 19, 12, 0)) // 本地 2026-10-19 12:00
    expect(localToday()).toBe('2026-10-19')
  })

  it('localMonth 月初凌晨返回本地当月，而非 UTC 的上月', () => {
    vi.setSystemTime(new Date(2026, 2, 1, 0, 30)) // 本地 2026-03-01 00:30
    expect(localMonth()).toBe('2026-03')
  })

  it('buildMonthOptions 首项为当前本地月份（每月 1 号凌晨不前移）', () => {
    vi.setSystemTime(new Date(2026, 2, 1, 0, 30)) // 本地 2026-03-01 00:30
    const options = buildMonthOptions()
    expect(options).toHaveLength(12)
    expect(options[0]).toBe('2026-03')
    expect(options[1]).toBe('2026-02')
  })

  it('buildMonthOptions 跨年连续递减', () => {
    vi.setSystemTime(new Date(2026, 0, 15, 10, 0)) // 本地 2026-01-15
    const options = buildMonthOptions()
    expect(options.slice(0, 3)).toEqual(['2026-01', '2025-12', '2025-11'])
  })
})

describe('凭证查询筛选（B4：改筛选必须复位页码）', () => {
  it('applyFilters 将 page 复位为 1 后再重新加载', async () => {
    const page = { value: 5 }
    const calls = []
    const reload = vi.fn(async () => calls.push(page.value))
    await applyFilters(page, reload)
    expect(page.value).toBe(1)
    expect(reload).toHaveBeenCalledTimes(1)
    // 关键顺序：reload 执行时看到的必须是复位后的第 1 页
    expect(calls).toEqual([1])
  })
})

describe('文件选择输入框（B5：取值后必须重置，否则复选同一文件不触发 change）', () => {
  function fakeEvent(files) {
    return { target: { files, value: 'C:\fakepath\a.xlsx' } }
  }

  it('takeFile 返回首个文件并清空 input.value', () => {
    const e = fakeEvent([{ name: 'a.xlsx' }])
    const file = takeFile(e)
    expect(file).toEqual({ name: 'a.xlsx' })
    expect(e.target.value).toBe('')
  })

  it('takeFile 无文件时返回 null 且仍重置', () => {
    const e = fakeEvent([])
    expect(takeFile(e)).toBeNull()
    expect(e.target.value).toBe('')
  })

  it('takeFiles 返回文件数组并清空 input.value', () => {
    const e = fakeEvent([{ name: 'a.pdf' }, { name: 'b.pdf' }])
    const files = takeFiles(e)
    expect(files).toHaveLength(2)
    expect(e.target.value).toBe('')
  })

  it('同一文件可连续两次选中（重置后 change 再次触发的效果）', () => {
    const e = fakeEvent([{ name: 'same.xlsx' }])
    takeFile(e)
    e.target.files = [{ name: 'same.xlsx' }]
    e.target.value = 'C:\fakepath\same.xlsx'
    expect(takeFile(e)).toEqual({ name: 'same.xlsx' })
    expect(e.target.value).toBe('')
  })
})

describe('错误提示（B6：拦截器统一弹窗，业务 catch 不再重复弹）', () => {
  const err400 = { response: { status: 400, data: { detail: '无效请求' } } }
  let origAdapter
  beforeEach(() => {
    ElMessage.error.mockClear()
    origAdapter = api.defaults.adapter
    api.defaults.adapter = () => Promise.reject(err400)
  })
  afterEach(() => {
    api.defaults.adapter = origAdapter
  })

  it('请求失败、业务 catch 保持沉默时，错误只弹一次（拦截器）', async () => {
    let recovered = false
    try {
      await api.get('/anything')
    } catch {
      recovered = true // 修复后的形态：只做状态恢复
    }
    expect(recovered).toBe(true)
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith('无效请求')
  })

  it('旧模式（catch 内再弹一次）会双重弹窗——bug 机制复现', async () => {
    try {
      await api.get('/anything')
    } catch (e) {
      ElMessage.error(e?.response?.data?.detail || '保存失败') // 修复前各视图的写法
    }
    expect(ElMessage.error).toHaveBeenCalledTimes(2)
  })

  it('已修复的视图文件 catch 块内不再出现 ElMessage.error', () => {
    const viewFiles = [
      'finance-ledger-personal/views/QuickAddView.vue',
      'finance-ledger-personal/views/TransactionListView.vue',
      'finance-ledger-bip/views/VoucherListView.vue',
      'finance-ledger-bip/views/ImportWizardView.vue',
      'finance-ledger-bip/views/BalanceSummaryView.vue',
      'finance-vault/views/TableListView.vue',
      'finance-vault/views/QueryView.vue',
      'finance-invoice/views/BatchListView.vue',
      'finance-invoice/views/BatchDetailView.vue',
      'finance-pos/views/PosRunView.vue',
      'finance-cashflow/views/CashflowRunView.vue',
      'finance-tax/views/FinancialImportView.vue',
      'finance-tax/views/PolicyListView.vue',
      'finance-tax/views/EnterpriseFormView.vue',
      'finance-tax/views/TaxCalculateView.vue',
      'finance-board/views/BoardView.vue',
    ]
    const offenders = []
    for (const f of viewFiles) {
      const src = readFileSync(new URL(`../src/modules/${f}`, import.meta.url), 'utf8')
      const blocks = src.match(/catch\s*(\([^)]*\))?\s*\{(?:[^{}]|\{[^{}]*\})*\}/g) || []
      for (const b of blocks) {
        if (b.includes('ElMessage.error')) offenders.push(f)
      }
    }
    expect(offenders).toEqual([])
  })
})
