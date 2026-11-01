// 财务菜单层级重组契约：直挂项 + 二级折叠子分组（manifest menu.subgroup）
import { describe, expect, it } from 'vitest'
import { getModuleMenuGroups, getModuleMenus } from '../src/modules'

describe('财务菜单分组聚合', () => {
  const groups = getModuleMenuGroups()
  const finance = groups.find((g) => g.name === '财务')
  const names = (list) => list.map((x) => x.title)

  it('财务组：直挂数据仓 + 四个子分组', () => {
    expect(finance).toBeTruthy()
    expect(names(finance.items)).toEqual(['数据仓'])
    expect(finance.subgroups.map((s) => s.name)).toEqual(['账务处理', '记账', '税务填报', '报表分析'])
  })

  it('子分组内容与顺序', () => {
    const byName = Object.fromEntries(finance.subgroups.map((s) => [s.name, names(s.items)]))
    expect(byName['账务处理']).toEqual(['费用分类', '发票处理', 'POS 对账'])
    expect(byName['记账']).toEqual(['个人记账', '企业记账（BIP）'])
    expect(byName['税务填报']).toEqual(['企业所得税', '印花税填报', '增值税填报', '个人所得税填报', '残保金填报'])
    expect(byName['报表分析']).toEqual(['报表看板', '现金流填报'])
  })

  it('模块示例不进菜单；其余组无子分组、财务组无重复项', () => {
    const menus = getModuleMenus()
    expect(menus.find((m) => m.path === '/finance/hello')).toBeUndefined()
    for (const name of ['经营', '职能', '调试']) {
      const g = groups.find((x) => x.name === name)
      expect(g).toBeTruthy()
      expect(g.subgroups).toHaveLength(0)
      expect(g.items.length).toBeGreaterThan(0)
    }
    const financeAll = menus.filter((m) => m.group === '财务')
    const grouped = [...finance.items, ...finance.subgroups.flatMap((s) => s.items)]
    expect(grouped).toHaveLength(financeAll.length)
    expect(new Set(grouped.map((m) => m.path)).size).toBe(grouped.length)
  })
})
