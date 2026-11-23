// 帮助入口契约：31 个可见模块菜单 + 隐藏示例 finance-hello 必须有帮助条目，
// 章节标题与《使用说明》的 ### 标题逐字一致；/help 路由、顶栏入口与页内 HelpLink 均需真实装配。
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import { STATIC_MENUS, STATIC_MENU_GROUPS, TAIL_MENUS } from '../src/components/AppLayout.vue'
import {
  countHelpEntries,
  HELP_ENTRIES,
  HELP_GROUPS,
  MANUAL_FILE,
  helpEntryByPath,
  helpEntryBySection,
  manualLocator,
} from '../src/utils/helpIndex'

const read = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8')

describe('帮助索引覆盖 31 个可见模块', () => {
  const menus = getModuleMenus()

  it('侧栏模块菜单正好 31 项（财务 19 + 经营 6 + 职能 3 + 调试 3）', () => {
    expect(menus).toHaveLength(31)
    expect(menus.filter((m) => m.group === '财务')).toHaveLength(19)
    expect(menus.filter((m) => m.group === '经营')).toHaveLength(6)
    expect(menus.filter((m) => m.group === '职能')).toHaveLength(3)
    expect(menus.filter((m) => m.group === '调试')).toHaveLength(3)
  })

  it('每个可见模块都有帮助条目，模块名与菜单路径和 manifest 一致', () => {
    for (const menu of menus) {
      const entry = helpEntryByPath(menu.path)
      expect(entry, `缺少帮助条目：${menu.title}（${menu.path}）`).toBeTruthy()
      expect(entry.title).toBe(menu.title)
      expect(entry.path).toBe(menu.path)
      expect(entry.section).toBeTruthy()
      expect(entry.summary).toBeTruthy()
    }
  })

  it('静态页面与定时任务同样有条目', () => {
    const staticPaths = [
      ...STATIC_MENUS,
      ...STATIC_MENU_GROUPS.flatMap((group) => group.items),
      ...TAIL_MENUS,
    ].map((item) => item.path)
    for (const path of staticPaths) {
      expect(helpEntryByPath(path), `缺少静态页帮助条目：${path}`).toBeTruthy()
    }
  })

  it('隐藏示例 finance-hello 有条目并标记 hidden，且仍不进侧栏', () => {
    expect(getModuleRoutes().find((route) => route.path === '/finance/hello')).toBeTruthy()
    const entry = helpEntryByPath('/finance/hello')
    expect(entry).toBeTruthy()
    expect(entry.hidden).toBe(true)
    expect(menus.find((menu) => menu.path === '/finance/hello')).toBeUndefined()
  })
})

describe('帮助索引数据完整性', () => {
  it('菜单路径不重复，条目数与分组合计一致', () => {
    const paths = HELP_ENTRIES.map((entry) => entry.path)
    expect(new Set(paths).size).toBe(paths.length)
    expect(helpEntryBySection('6.1')).toBeTruthy()
    expect(countHelpEntries()).toBe(HELP_ENTRIES.length)
    const grouped = HELP_GROUPS.flatMap((group) => [
      ...(group.items || []),
      ...(group.subgroups || []).flatMap((sub) => sub.items),
    ])
    expect(grouped).toHaveLength(HELP_ENTRIES.length)
  })

  it('每条都有章节号、手册标题、路径与一句话说明', () => {
    for (const entry of HELP_ENTRIES) {
      expect(entry.section, entry.path).toMatch(/^\d+\.\d+(\.\d+)?$/)
      expect(entry.sectionTitle, entry.path).toBeTruthy()
      expect(entry.heading).toBe(`${entry.section} ${entry.sectionTitle}`)
      expect(entry.path, entry.title).toMatch(/^\//)
      expect(entry.summary.length, entry.title).toBeGreaterThan(5)
      expect(entry.group).toBeTruthy()
    }
  })

  it('章节号唯一，避免两个模块指向同一节', () => {
    const sections = HELP_ENTRIES.map((entry) => entry.section)
    expect(new Set(sections).size).toBe(sections.length)
  })

  it('详情子路由按最长前缀命中模块条目', () => {
    expect(helpEntryByPath('/finance/expense/runs/42').section).toBe('6.2')
    expect(helpEntryByPath('/finance/vault/query').section).toBe('6.1')
    expect(helpEntryByPath('/business/manage/weekly').section).toBe('7.6')
    expect(helpEntryByPath('/finance/tax/returns/7').section).toBe('6.8')
    expect(helpEntryByPath('/')).toBe(helpEntryBySection('2.1'))
    expect(helpEntryByPath('/not-a-page')).toBeNull()
  })

  it('manualLocator 生成可粘贴到手册搜索框的定位串', () => {
    expect(manualLocator(helpEntryBySection('6.1'))).toBe('§6.1 数据池清洗')
    expect(manualLocator(null)).toBe('')
  })
})

describe('手册章节标题逐字一致', () => {
  const manual = read('../../使用说明.md')
  const headings = new Set(
    manual
      .split(/\r?\n/)
      .filter((line) => line.startsWith('### '))
      .map((line) => line.slice(4).trim()),
  )

  it('手册文件名与索引常量一致', () => {
    expect(MANUAL_FILE).toBe('使用说明.md')
    expect(manual.startsWith('# 个人工作台 · 使用手册')).toBe(true)
  })

  it('每个条目的 heading 都能在手册 ### 标题中原样找到', () => {
    for (const entry of HELP_ENTRIES) {
      expect(headings.has(entry.heading), `手册缺少标题：### ${entry.heading}`).toBe(true)
    }
  })
})

describe('帮助页与全局入口注册', () => {
  const routerSource = read('../src/router/index.js')

  it('/help 路由注册为 HelpView（懒加载，标题“使用说明”）', () => {
    expect(routerSource).toMatch(/path: '\/help'[\s\S]{0,180}name: 'help'[\s\S]{0,180}HelpView\.vue/)
    expect(routerSource).toMatch(/path: '\/help'[\s\S]{0,260}meta: \{ title: '使用说明' \}/)
  })

  it('AppLayout 顶栏有“使用说明”入口并指向 /help', () => {
    const layout = read('../src/components/AppLayout.vue')
    expect(layout).toContain('to="/help"')
    expect(layout).toContain('使用说明')
  })

  it('HelpView 用已有 markdown-it 渲染说明，并提供手册文件定位文案', () => {
    const view = read('../src/views/HelpView.vue')
    expect(view).toMatch(/import\s+MarkdownIt\s+from\s+'markdown-it'/)
    expect(view).toContain('HELP_GROUPS')
    expect(view).toContain('HELP_ENTRIES')
    expect(view).toContain('MANUAL_FILE')
    expect(view).toContain('文件管理器')
    expect(view).toContain('Ctrl+F')
    expect(view).toContain('route.query.section')
  })

  it('markdown-it 是既有依赖，未为本功能新增依赖', () => {
    const pkg = JSON.parse(read('../package.json'))
    expect(pkg.dependencies['markdown-it']).toBeTruthy()
  })
})

// 页内“本页帮助”：任务要求至少覆盖 11 类页面；企业所得税也纳入 5 个税务页全量。
const HELP_LINK_TARGETS = [
  ['src/modules/finance-vault/views/PoolView.vue', '6.1'],
  ['src/modules/finance-expense/views/UploadView.vue', '6.2'],
  ['src/modules/finance-invoice/views/BatchListView.vue', '6.3'],
  ['src/modules/finance-pos/views/PosRunView.vue', '6.4'],
  ['src/modules/finance-bankflow/views/BankflowView.vue', '6.5'],
  ['src/modules/finance-tax/views/EnterpriseListView.vue', '6.8'],
  ['src/modules/finance-stamp-duty/views/StampDutyView.vue', '6.9'],
  ['src/modules/finance-vat/views/VatView.vue', '6.10'],
  ['src/modules/finance-iit/views/IitView.vue', '6.11'],
  ['src/modules/finance-dsf/views/DsfView.vue', '6.12'],
  ['src/modules/finance-board/views/BoardView.vue', '6.13'],
  ['src/modules/finance-cashflow/views/CashflowRunView.vue', '6.14'],
  ['src/modules/finance-recon/views/ReconView.vue', '6.17'],
  ['src/views/KnowledgeView.vue', '4.1'],
  ['src/views/JobsView.vue', '5.1'],
  ['src/views/AssistantView.vue', '3.1'],
]

describe('页内“本页帮助”覆盖关键模块', () => {
  for (const [file, section] of HELP_LINK_TARGETS) {
    it(`${file} 使用 HelpLink 指向 §${section}`, () => {
      const source = read(`../${file}`)
      expect(source).toMatch(/import\s+HelpLink\s+from\s+['"][^'"]*components\/HelpLink\.vue['"]/)
      expect(source).toContain(`<HelpLink section="${section}" />`)
      expect(helpEntryBySection(section), `未知章节 ${section}`).toBeTruthy()
    })
  }

  it('HelpLink 接受 section 参数并跳转到 /help?section=', () => {
    const link = read('../src/components/HelpLink.vue')
    expect(link).toMatch(/section:\s*\{ type: String/)
    expect(link).toContain("path: '/help'")
    expect(link).toContain('helpEntryBySection')
    expect(link).toContain('helpEntryByPath')
  })
})
