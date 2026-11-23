import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// 数据池清洗「使用说明」静态断言：PoolView 统一引用公共说明组件（按页签传 page），
// 三个子面板不再各自渲染；组件文案覆盖面向非技术用户的关键要点（与后端实际口径一致：
// 只读 SELECT/WITH、结果上限 1000 行、单条 5 秒超时、预览前 100 行）。

const VAULT_DIR = fileURLToPath(new URL('../src/modules/finance-vault', import.meta.url))
const read = (name) => readFileSync(`${VAULT_DIR}/${name}`, 'utf-8')

const help = read('UsageHelp.vue')
const poolView = read('views/PoolView.vue')
const PANELS = ['views/parts/TablesPanel.vue', 'views/parts/AnalyzePanel.vue', 'views/parts/SqlPanel.vue']

describe('数据池清洗使用说明组件接入', () => {
  it('PoolView 引用 UsageHelp 并按页签动态传 page', () => {
    expect(poolView).toMatch(/import\s+UsageHelp\s+from\s+['"]\.\.\/UsageHelp\.vue['"]/)
    expect(poolView).toContain('<UsageHelp :page=')
  })

  it('三个子面板不再各自渲染说明（PoolView 统一提供）', () => {
    for (const p of PANELS) expect(read(p)).not.toMatch(/UsageHelp/)
  })
})

describe('UsageHelp 文案关键要点', () => {
  it('公共部分：数据池是什么（DuckDB / 上传入池 / 其它模块数据入池统一清洗）', () => {
    expect(help).toContain('DuckDB')
    expect(help).toMatch(/xlsx\s*\/\s*csv|xlsx\/csv/)
    expect(help).toContain('BIP')
    expect(help).toContain('入池')
  })

  it('数据池页签：表名规则 / 追加与整表重建 / 预览 100 行 / 画像清洗分析 / 跳转查询', () => {
    expect(help).toContain('小写字母')
    expect(help).toContain('下划线')
    expect(help).toContain('追加')
    expect(help).toContain('整表重建')
    expect(help).toMatch(/前\s*100\s*行/)
    expect(help).toContain('画像')
    expect(help).toContain('清洗')
    expect(help).toContain('分析')
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
