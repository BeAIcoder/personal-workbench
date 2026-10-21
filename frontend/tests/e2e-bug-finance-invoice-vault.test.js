import { describe, expect, it } from 'vitest'
import { isRedInvoice } from '../src/modules/finance-invoice/itemDisplay'
import { columnKeys, toTableRows } from '../src/modules/finance-vault/tableRows'

// 本文件覆盖 finance-invoice-vault 区域 E2E 探测发现、并已修复的前端缺陷：
// B1 档案库预览弹窗把「数组行 + 列名 prop」直接绑给 el-table，lodash get 在数组上
//    按列名取值全部落空 → 预览全空白；
// B2 查询结果/预览的列名含点号时被当作深层路径 → 该列单元格空白；
// B3 冲红发票「红」标签只认 '红字'，而引擎/服务端返回 '冲红发票' → 标签永不渲染。

describe('B1/B2：查询与预览结果行扁平化（toTableRows）', () => {
  const columns = ['月份', '分类', '金额']

  it('数组行按列序映射到安全键（预览接口行形态）', () => {
    const rows = [
      ['2026-09', '办公费', 100],
      ['2026-10', '差旅费', 150],
    ]
    expect(toTableRows(columns, rows)).toEqual([
      { c0: '2026-09', c1: '办公费', c2: 100 },
      { c0: '2026-10', c1: '差旅费', c2: 150 },
    ])
  })

  it('点号列名不再被当作深层路径：值落在扁平键上', () => {
    const dotted = ['a.b', '普通列']
    const rows = [
      ['点值1', 'x'],
      ['点值2', 'y'],
    ]
    const mapped = toTableRows(dotted, rows)
    expect(mapped).toEqual([
      { c0: '点值1', c1: 'x' },
      { c0: '点值2', c1: 'y' },
    ])
    // 回归点：键必须是扁平键，而不是把 'a.b' 当对象键（el-table prop 会按 . 分层）
    expect(Object.keys(mapped[0])).toEqual(['c0', 'c1'])
    expect('a.b' in mapped[0]).toBe(false)
  })

  it('columnKeys 与列序一一对应且数量一致', () => {
    expect(columnKeys(columns)).toEqual(['c0', 'c1', 'c2'])
    expect(columnKeys([])).toEqual([])
    expect(columnKeys(undefined)).toEqual([])
  })

  it('空行/空列不抛错', () => {
    expect(toTableRows([], [])).toEqual([])
    expect(toTableRows(columns, undefined)).toEqual([])
    expect(toTableRows(undefined, [['only']])).toEqual([])
  })
})

describe('B3：冲红发票方向判定（isRedInvoice）', () => {
  it('引擎/服务端口径「冲红发票」必须判为冲红', () => {
    expect(isRedInvoice({ invoice_direction: '冲红发票' })).toBe(true)
  })

  it('历史写法「红字」同样判为冲红', () => {
    expect(isRedInvoice({ invoice_direction: '红字' })).toBe(true)
  })

  it('正数发票/空值/缺列不误判', () => {
    expect(isRedInvoice({ invoice_direction: '正数发票' })).toBe(false)
    expect(isRedInvoice({ invoice_direction: '' })).toBe(false)
    expect(isRedInvoice({})).toBe(false)
    expect(isRedInvoice(undefined)).toBe(false)
    expect(isRedInvoice(null)).toBe(false)
  })
})
