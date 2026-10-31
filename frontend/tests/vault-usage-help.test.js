import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// 数据仓「使用说明」静态断言：两视图必须引用公共说明组件，组件文案必须覆盖
// 面向非技术用户的关键要点（与后端 finance_vault/service.py 的实际口径一致：
// 只读 SELECT/WITH、结果上限 1000 行、单条 5 秒超时、预览前 100 行）。
// 静态断言而非组件渲染：本改动是纯展示文案，不引入请求，渲染测试无增量价值。

const VAULT_DIR = fileURLToPath(new URL('../src/modules/finance-vault', import.meta.url))
const read = (name) => readFileSync(`${VAULT_DIR}/${name}`, 'utf-8')

const help = read('UsageHelp.vue')
const listView = read('views/TableListView.vue')
const queryView = read('views/QueryView.vue')

describe('数据仓使用说明组件接入', () => {
  it('表清单页引用 UsageHelp 并传 page="list"', () => {
    expect(listView).toMatch(/import\s+UsageHelp\s+from\s+['"]\.\.\/UsageHelp\.vue['"]/)
    expect(listView).toContain('<UsageHelp page="list"')
  })

  it('SQL 查询页引用 UsageHelp 并传 page="query"', () => {
    expect(queryView).toMatch(/import\s+UsageHelp\s+from\s+['"]\.\.\/UsageHelp\.vue['"]/)
    expect(queryView).toContain('<UsageHelp page="query"')
  })
})

describe('UsageHelp 文案关键要点', () => {
  it('公共部分：数据仓是什么（DuckDB / 上传入仓 / 其它模块数据入仓）', () => {
    expect(help).toContain('DuckDB')
    expect(help).toMatch(/xlsx\s*\/\s*csv|xlsx\/csv/)
    expect(help).toContain('BIP')
    expect(help).toContain('入仓')
  })

  it('表清单页部分：表名规则 / 追加与整表重建 / 预览 100 行 / 跳转查询', () => {
    expect(help).toContain('小写字母')
    expect(help).toContain('下划线')
    expect(help).toContain('追加')
    expect(help).toContain('整表重建')
    expect(help).toMatch(/前\s*100\s*行/)
    expect(help).toContain('查询')
  })

  it('查询页部分：SELECT/WITH 只读 / 已注册表名 / 1000 行上限 / 5 秒超时 / CSV 带 BOM', () => {
    expect(help).toContain('SELECT')
    expect(help).toContain('WITH')
    expect(help).toContain('只读')
    expect(help).toContain('已注册')
    expect(help).toContain('1000')
    expect(help).toMatch(/5\s*秒/)
    expect(help).toContain('CSV')
    expect(help).toContain('BOM')
  })

  it('可折叠且默认收起（el-collapse + 空激活数组）', () => {
    expect(help).toContain('<el-collapse')
    expect(help).toMatch(/active\s*=\s*ref\(\[\]\)/)
  })
})
