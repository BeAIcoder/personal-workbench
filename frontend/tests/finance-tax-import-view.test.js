// T6D / 独立复核 T3-I-2：财报导入页必须按 persisted 分流——
// 「仅解析未落库」不得显示成功，且要提供企业选择走 /import/{enterprise_id} 的落库路径。
import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('../src/api', () => ({ default: api, BACKEND_HINT: '测试后端提示' }))

const { ElMessage } = await import('element-plus')
const { importOutcome, importToastType } = await import('../src/modules/finance-tax/importOutcome.js')
const FinancialImportView = (await import('../src/modules/finance-tax/views/FinancialImportView.vue')).default

const source = readFileSync(
  new URL('../src/modules/finance-tax/views/FinancialImportView.vue', import.meta.url),
  'utf8',
)

function mount() {
  return FinancialImportView.setup({}, { expose: () => {}, emit: () => {} })
}

function fakeFile(name = 'a.xlsx') {
  return { name }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('导入结果口径（纯函数）', () => {
  it('success 但未落库 → warning，不得算成功', () => {
    const out = importOutcome({ success: true, persisted: false })
    expect(out.tagType).toBe('warning')
    expect(out.tagText).toContain('未落库')
    expect(importToastType({ success: true, persisted: false })).toBe('warning')
  })

  it('success 且已落库 → success；解析失败 → danger', () => {
    expect(importOutcome({ success: true, persisted: true }).tagType).toBe('success')
    expect(importOutcome({ success: false, persisted: false }).tagType).toBe('danger')
    expect(importToastType({ success: false })).toBe('warning')
    expect(importToastType(null)).toBe('info')
  })
})

describe('财报导入按 persisted 分流（行为）', () => {
  it('标准模板未带企业身份：仅解析，提示 warning 且不弹 success', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile()
    api.post.mockResolvedValue({
      data: {
        success: true,
        persisted: false,
        summary: '解析成功（仅解析未落库）',
        warnings: [{ field: 'tax_number', message: '未识别企业身份，本次仅解析未落库' }],
      },
    })

    await ctx.doImport()

    expect(api.post.mock.calls[0][0]).toBe('/finance/tax/financial-import/import')
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
    expect(ctx.outcome.value.tagType).toBe('warning')
    expect(ctx.outcome.value.tagText).toContain('未落库')
  })

  it('选择企业后改走 /import/{enterprise_id}，落库成功才提示成功', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile()
    ctx.selectedEnterpriseId.value = 7
    api.post.mockResolvedValue({
      data: { success: true, persisted: true, enterprise_id: 7, summary: '已写入', warnings: [] },
    })

    await ctx.doImport()

    expect(api.post.mock.calls[0][0]).toBe('/finance/tax/financial-import/import/7')
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.warning).not.toHaveBeenCalled()
    expect(ctx.outcome.value.tagType).toBe('success')
  })

  it('企业列表来自 /finance/tax/enterprises/ 供选择', async () => {
    const ctx = mount()
    api.get.mockResolvedValue({ data: [{ id: 3, name: '合成甲公司' }] })
    await ctx.loadEnterprises()
    expect(api.get).toHaveBeenCalledWith('/finance/tax/enterprises/')
    expect(ctx.enterprises.value).toHaveLength(1)
  })
})

describe('财报导入页契约（静态）', () => {
  it('toast 类型由 importToastType 统一决定（复审 R2）', () => {
    expect(source).toContain("import { importOutcome, importToastType } from '../importOutcome'")
    expect(source).toContain('importToastType(result.value)')
  })

  it('提供企业选择下拉并绑定 selectedEnterpriseId', () => {
    expect(source).toContain('v-model="selectedEnterpriseId"')
    expect(source).toContain('el-select')
    expect(source).toContain('/finance/tax/financial-import/import/${')
  })

  it('提示企业身份是落库前提，不再承诺「解析成功会自动建档」', () => {
    expect(source).not.toContain('解析成功会自动创建或更新企业与其财务数据')
    expect(source).toContain('税号')
    expect(source).toContain('落库')
  })

  it('未落库时把 warning 置顶显示（警告块位于错误块之前）', () => {
    const warnIdx = source.indexOf('result.warnings')
    const errIdx = source.indexOf('result.errors')
    expect(warnIdx).toBeGreaterThan(-1)
    expect(errIdx).toBeGreaterThan(-1)
    expect(warnIdx).toBeLessThan(errIdx)
  })
})
