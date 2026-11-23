import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import manifest from '../src/modules/finance-invoice/manifest'

const detailSource = readFileSync(
  new URL('../src/modules/finance-invoice/views/BatchDetailView.vue', import.meta.url),
  'utf8',
)

describe('finance-invoice 项目拆分与重新归类（静态契约）', () => {
  it('详情页提供重新归类、项目汇总与按项目下载', () => {
    expect(detailSource).toContain('/reclassify')
    expect(detailSource).toContain('/projects')
    expect(detailSource).toContain('/projects/download?project=')
    expect(detailSource).toContain('重新归类')
    expect(detailSource).toContain('项目拆分')
    expect(detailSource).toContain('未归类')
  })

  it('重新归类有二次确认（作废旧导出包），长耗时请求放宽超时', () => {
    expect(detailSource).toContain('ElMessageBox.confirm')
    expect(detailSource).toContain('timeout: 180000')
    expect(detailSource).toContain('encodeURIComponent(project)')
  })

  it('下载走 Blob + saveBlob 显式落名（不依赖 Content-Disposition）', () => {
    expect(detailSource).toContain("responseType: 'blob'")
    expect(detailSource).toContain('saveBlob(data, filename)')
    expect(detailSource).toContain('download_name')
    expect(detailSource).toContain('zip_file')
  })

  it('项目 PDF：下载与打印共用合并接口，落名取后端 pdf_name', () => {
    expect(detailSource).toContain('/projects/pdf?project=')
    expect(detailSource).toContain('pdf_name')
    expect(detailSource).toContain('下载 PDF')
    expect(detailSource).toContain('打印')
    expect(detailSource).toContain(':disabled="pdfDisabled || !!projectOps[row.project]"')
  })

  it('打印先预开新标签页，失败由拦截器提示（catch 不重复弹 error）', () => {
    expect(detailSource).toContain('openPrintWindow')
    expect(detailSource).toContain('showPdfInWindow')
    expect(detailSource).toContain('Ctrl+P')
    expect(detailSource).not.toContain('ElMessage.error(')
  })
})

describe('finance-invoice 模块注册', () => {
  it('manifest 基本字段（name/title/menu）', () => {
    expect(manifest.name).toBe('finance-invoice')
    expect(manifest.title).toBe('发票处理')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      title: '发票处理',
      icon: 'Tickets',
      path: '/finance/invoice',
      order: 20,
    })
  })

  it('getModuleRoutes 返回批次列表页与详情页路由（懒加载组件）', () => {
    const routes = getModuleRoutes()
    const list = routes.find((r) => r.path === '/finance/invoice')
    expect(list).toBeTruthy()
    expect(list.name).toBe('finance-invoice')
    expect(typeof list.component).toBe('function')

    const detail = routes.find((r) => r.path === '/finance/invoice/batches/:id')
    expect(detail).toBeTruthy()
    expect(detail.name).toBe('finance-invoice-detail')
    expect(typeof detail.component).toBe('function')
  })

  it('getModuleMenus 返回「财务」组发票处理菜单项（order=20）', () => {
    const menus = getModuleMenus()
    const item = menus.find((m) => m.path === '/finance/invoice')
    expect(item).toBeTruthy()
    expect(item.group).toBe('财务')
    expect(item.icon).toBe('Tickets')
    expect(item.order).toBe(20)
  })
})
