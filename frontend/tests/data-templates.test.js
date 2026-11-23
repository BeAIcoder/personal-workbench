import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import axios from 'axios'
import api from '../src/api'
import * as dataTemplates from '../src/utils/dataTemplates'
import { DATA_TEMPLATES, downloadTemplate, templatePath } from '../src/utils/dataTemplates'

const TemplateLinks = (await import('../src/components/TemplateLinks.vue')).default

function source(rel) {
  return readFileSync(new URL(`../src/${rel}`, import.meta.url), 'utf8')
}

describe('数据模板清单与下载（静态契约）', () => {
  it('覆盖全部 15 份模板且文件名唯一（费用与现金流共用银行明细账模板）', () => {
    const files = Object.values(DATA_TEMPLATES).flat().map((t) => t.file)
    expect(files).toHaveLength(16)
    expect(new Set(files).size).toBe(15)
    expect(Object.keys(DATA_TEMPLATES).sort()).toEqual(
      ['bip', 'cashflow', 'expense', 'lease', 'manage', 'personal', 'pos', 'vault'],
    )
  })

  it('templatePath 指向 /templates 并对中文文件名做 URL 编码', () => {
    expect(templatePath('数据仓-入仓模板.xlsx')).toBe(
      `/templates/${encodeURIComponent('数据仓-入仓模板.xlsx')}`,
    )
  })

  it('templateUrl 已无生产调用方，删除导出避免两套 URL 语义', () => {
    expect(dataTemplates.templateUrl).toBeUndefined()
  })

  it('各上传页均已接入「下载模板」链接', () => {
    const wired = {
      'modules/finance-vault/views/parts/TablesPanel.vue': 'vault',
      'modules/finance-expense/views/UploadView.vue': 'expense',
      'modules/finance-pos/views/PosRunView.vue': 'pos',
      'modules/finance-cashflow/views/CashflowRunView.vue': 'cashflow',
      'modules/finance-ledger-bip/views/ImportWizardView.vue': 'bip',
      'modules/finance-ledger-personal/views/TransactionListView.vue': 'personal',
      'modules/finance-lease/views/ContractPanel.vue': 'lease',
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

// 旧实现先弹「已开始下载」再点 <a download>：服务端 404/500 也会假成功，
// 且中文名依赖浏览器猜。改为 blob 请求，确认 200 后才落文件并提示。
describe('模板下载以服务端响应为准', () => {
  const blob = new Blob(['xlsx'])

  it('成功：请求 → 落文件 → 才提示成功（顺序固定）', async () => {
    const order = []
    const request = { get: vi.fn(async () => { order.push('request'); return { data: blob } }) }
    const save = vi.fn(() => order.push('save'))
    const notify = { success: vi.fn(() => order.push('success')) }

    const ok = await downloadTemplate('POS对账-凭证列表-模板.xlsx', { request, save, notify })

    expect(ok).toBe(true)
    expect(request.get).toHaveBeenCalledWith(
      templatePath('POS对账-凭证列表-模板.xlsx'),
      expect.objectContaining({ responseType: 'blob' }),
    )
    expect(save).toHaveBeenCalledWith(blob, 'POS对账-凭证列表-模板.xlsx')
    expect(notify.success).toHaveBeenCalledWith('已下载：POS对账-凭证列表-模板.xlsx')
    expect(order).toEqual(['request', 'save', 'success'])
  })

  it('失败：不落文件、不提示成功、不抛出（拦截器统一提示）', async () => {
    const request = { get: vi.fn(async () => { throw new Error('404') }) }
    const save = vi.fn()
    const notify = { success: vi.fn() }

    const ok = await downloadTemplate('不存在.xlsx', { request, save, notify })

    expect(ok).toBe(false)
    expect(save).not.toHaveBeenCalled()
    expect(notify.success).not.toHaveBeenCalled()
  })

  it('空文件名不发请求', async () => {
    const request = { get: vi.fn() }
    expect(await downloadTemplate('', { request })).toBe(false)
    expect(request.get).not.toHaveBeenCalled()
  })

  it('源码不再用 <a download> 先行触发（提示必须等响应）', () => {
    const src = source('utils/dataTemplates.js')
    expect(src).toContain("responseType: 'blob'")
    expect(src).not.toContain('a.click()')
    expect(src).not.toContain('createElement')
  })
})

// 防 C1 复发：不能只断言「传了 templatePath 的返回值」（测试与实现同错）。
// 用真实 api 实例 + axios adapter 捕获最终 URL：axios 会把 baseURL('/api') 与
// 相对路径拼成最终地址，双 /api 会在这里直接暴露。
describe('模板下载真实 URL 组合（axios adapter）', () => {
  const FILE = '数据仓-入仓模板.xlsx'

  it('最终请求 /api/templates/<file>，不出现 /api/api', async () => {
    const captured = []
    const originalAdapter = api.defaults.adapter
    api.defaults.adapter = async (config) => {
      captured.push(axios.getUri(config))
      return {
        data: new Blob(['xlsx']),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      }
    }
    const save = vi.fn()
    const notify = { success: vi.fn() }
    try {
      const ok = await downloadTemplate(FILE, { save, notify })
      expect(ok).toBe(true)
    } finally {
      api.defaults.adapter = originalAdapter
    }

    expect(captured).toHaveLength(1)
    expect(captured[0]).toBe(`/api/templates/${encodeURIComponent(FILE)}`)
    expect(captured[0]).not.toContain('/api/api/')
  })
})

// 租赁核算付款计划模板：后端白名单已列入 15 份模板之一，前端必须同源登记并真实渲染入口。
describe('租赁核算付款计划模板', () => {
  const LEASE_FILE = '租赁核算-付款计划-模板.xlsx'

  it('清单登记唯一文件，下载走白名单路径并以中文名落盘', async () => {
    expect(DATA_TEMPLATES.lease).toEqual([{ file: LEASE_FILE, label: '付款计划模板' }])
    const blob = new Blob(['xlsx'])
    const request = { get: vi.fn(async () => ({ data: blob })) }
    const save = vi.fn()
    const notify = { success: vi.fn() }

    expect(await downloadTemplate(LEASE_FILE, { request, save, notify })).toBe(true)
    expect(request.get).toHaveBeenCalledWith(templatePath(LEASE_FILE), { responseType: 'blob' })
    expect(request.get.mock.calls[0][0]).toBe(
      `/templates/${encodeURIComponent(LEASE_FILE)}`,
    )
    expect(save).toHaveBeenCalledWith(blob, LEASE_FILE)
    expect(notify.success).toHaveBeenCalledWith(`已下载：${LEASE_FILE}`)
  })

  it('TemplateLinks type="lease" 真实渲染出模板链接且不串到其他模块', async () => {
    const app = createSSRApp({ render: () => h(TemplateLinks, { type: 'lease' }) })
    const html = await renderToString(app)
    expect(html).toContain('下载模板')
    expect(html).toContain('付款计划模板')
    expect(html).not.toContain('POS对账')
    expect(html).not.toContain('BIP账簿')
  })
})
