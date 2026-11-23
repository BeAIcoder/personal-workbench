// Task 6C：低优先级可访问性项。
// - 看板 iframe 必须有 title（读屏器需要可辨识名称）；
// - 定时任务调度类型单选用 Element Plus 2.7 的 value 语义（label 已废弃且会被当值）；
// - 目录浏览的行是 div 点击区，必须能键盘聚焦并用 Enter/Space 进入。
// node 环境无 DOM，沿用仓库既有范式：读取 .vue 源码做结构化断言。
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

function source(rel) {
  return readFileSync(new URL(`../src/${rel}`, import.meta.url), 'utf8')
}

describe('看板 iframe 可访问名称', () => {
  it('iframe 带非空 title', () => {
    const src = source('modules/finance-board/views/BoardView.vue')
    const tag = src.match(/<iframe[^>]*>/)?.[0] || ''
    expect(tag).toContain('<iframe')
    const title = tag.match(/\btitle="([^"]*)"/)?.[1] || ''
    expect(title.trim().length).toBeGreaterThan(0)
  })
})

describe('定时任务调度类型单选使用 value 语义', () => {
  const src = source('views/JobsView.vue')

  it('三个调度选项都用 value= 而不是已废弃的 label=', () => {
    for (const value of ['interval', 'daily', 'cron']) {
      expect(src).toContain(`<el-radio-button value="${value}">`)
      expect(src).not.toContain(`<el-radio-button label="${value}">`)
    }
  })
})

describe('目录浏览支持键盘操作', () => {
  const src = source('components/DirectoryPicker.vue')
  const rowTag = () => src.match(/<div\s+v-for="e in entries"[\s\S]*?>/)?.[0] || ''

  it('目录行可聚焦且声明为按钮角色', () => {
    const row = rowTag()
    expect(row).toContain('role="button"')
    expect(row).toContain('tabindex="0"')
  })

  it('Enter 与 Space 都能进入目录（Space 阻止页面滚动）', () => {
    const row = rowTag()
    expect(row).toMatch(/@keyup\.enter="enter\(e\)"/)
    expect(row).toMatch(/@keyup\.space\.prevent="enter\(e\)"/)
  })

  it('当前目录行给出可读的操作提示（aria-label）', () => {
    const row = rowTag()
    expect(row).toContain(':aria-label="`进入目录 ${e.name}`"')
  })

  it('文案按白名单根目录语义（T5 后 /fs/drives 返回根目录，不再是盘符）', () => {
    expect(src).toContain('选择根目录：')
    expect(src).not.toContain('选择盘符：')
  })
})
