import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { DATA_TEMPLATES, templateUrl } from '../src/utils/dataTemplates'

function source(rel) {
  return readFileSync(new URL(`../src/${rel}`, import.meta.url), 'utf8')
}

describe('数据模板清单与下载（静态契约）', () => {
  it('覆盖全部 10 份模板且文件名唯一（费用与现金流共用银行明细账模板）', () => {
    const files = Object.values(DATA_TEMPLATES).flat().map((t) => t.file)
    expect(files).toHaveLength(11)
    expect(new Set(files).size).toBe(10)
    expect(Object.keys(DATA_TEMPLATES).sort()).toEqual(
      ['bip', 'cashflow', 'expense', 'personal', 'pos', 'vault'],
    )
  })

  it('templateUrl 指向 /templates 并对中文文件名做 URL 编码', () => {
    expect(templateUrl('数据仓-入仓模板.xlsx')).toBe(
      `/api/templates/${encodeURIComponent('数据仓-入仓模板.xlsx')}`,
    )
  })

  it('各上传页均已接入「下载模板」链接', () => {
    const wired = {
      'modules/finance-vault/views/TableListView.vue': 'vault',
      'modules/finance-expense/views/UploadView.vue': 'expense',
      'modules/finance-pos/views/PosRunView.vue': 'pos',
      'modules/finance-cashflow/views/CashflowRunView.vue': 'cashflow',
      'modules/finance-ledger-bip/views/ImportWizardView.vue': 'bip',
      'modules/finance-ledger-personal/views/TransactionListView.vue': 'personal',
    }
    for (const [rel, type] of Object.entries(wired)) {
      const src = source(rel)
      expect(src).toContain('TemplateLinks')
      expect(src).toContain(`type="${type}"`)
    }
  })

  it('个人记账页有 CSV 导入入口（后端端点此前无前端入口）', () => {
    const src = source('modules/finance-ledger-personal/views/TransactionListView.vue')
    expect(src).toContain('导入 CSV')
    expect(src).toContain('/finance/ledger/personal/import/csv')
  })
})
