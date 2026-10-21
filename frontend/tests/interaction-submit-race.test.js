// 交互竞态与失败反馈的回归测试（由 dev/e2e/perf_probe + 交互走查发现）。
// 覆盖三类曾被确认的缺陷：
//   1) 保存对话框的「双击重复提交」——锁上得太晚（在 await validate() 之后），
//      两次点击都能通过校验，于是创建两条重复数据；
//   2) 行内操作缺少 catch——axios 拦截器会 Toast 后再 Promise.reject，调用方不接就是
//      未捕获异常（前端会把它上报到 /api/debug/client-errors）；
//   3) 月历加载失败后保留上一月份的日程——日历标题已是新月份，数据却是旧月份，属误导。
// 本仓库的前端测试没有 jsdom/挂载能力，沿用既有约定：对 .vue 源码做结构化断言，
// 行为验证由 Playwright 探针承担（见 dev/e2e/probes/tasks.py 的 TASKS-UI-028~030）。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function readSrc(rel) {
  return readFileSync(join(root, 'src/views', rel), 'utf8')
}

/** 按大括号配对取出某个函数体（含函数头），避免用「下一个函数名」这类脆弱切片。 */
function fnBody(src, headerNeedle) {
  const start = src.indexOf(headerNeedle)
  if (start < 0) throw new Error(`未找到函数：${headerNeedle}`)
  const open = src.indexOf('{', start)
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return src.slice(start, i + 1)
    }
  }
  throw new Error(`函数体大括号未闭合：${headerNeedle}`)
}

/** 去掉行注释：结构性断言只看代码，不被解释性注释里的字面量误判。 */
function stripComments(code) {
  return code.replace(/\/\/[^\n]*/g, '')
}

const VIEWS = {
  'TasksView.vue': 'TasksView',
  'ScheduleView.vue': 'ScheduleView',
  'JobsView.vue': 'JobsView',
}

describe('BUG-RACE-001 保存对话框：提交锁必须先于 await validate()', () => {
  for (const [file, name] of Object.entries(VIEWS)) {
    it(`${name}.save() 先上锁再校验，且带重入守卫`, () => {
      const body = stripComments(fnBody(readSrc(file), 'async function save()'))
      const lock = body.indexOf('dialog.saving = true')
      const validate = body.indexOf('.validate()')
      expect(lock, 'save() 未设置提交锁').toBeGreaterThanOrEqual(0)
      expect(validate, 'save() 未调用表单校验').toBeGreaterThanOrEqual(0)
      expect(lock, `${name}.save()：提交锁在 await validate() 之后，双击可在两次校验都通过时重复建数据`).toBeLessThan(validate)
      expect(body).toMatch(/if\s*\(\s*dialog\.saving\s*\)\s*return/, `${name}.save() 缺少重入守卫`)
    })

    it(`${name}.save() 失败不产生未捕获异常（有 catch，且对话框不提前关闭）`, () => {
      const body = fnBody(readSrc(file), 'async function save()')
      expect(body).toContain('catch')
      // 关闭对话框必须发生在请求成功之后：位置在最后一个 await 之后即可
      const close = body.indexOf('dialog.visible = false')
      const lastAwait = body.lastIndexOf('await ')
      expect(close).toBeGreaterThan(lastAwait)
    })
  }
})

describe('BUG-RACE-002 行内操作：await 之后必须有 catch（拦截器会 reject）', () => {
  const cases = [
    ['TasksView.vue', 'TasksView', 'async function complete(row)'],
    ['TasksView.vue', 'TasksView', 'async function remove(row)'],
    ['ScheduleView.vue', 'ScheduleView', 'async function remove(ev)'],
    ['JobsView.vue', 'JobsView', 'async function remove(row)'],
    ['JobsView.vue', 'JobsView', 'async function runNow(row)'],
  ]
  for (const [file, view, header] of cases) {
    it(`${view} 的 ${header.match(/function (\w+)/)[1]}() 捕获请求异常`, () => {
      const body = fnBody(readSrc(file), header)
      expect(body).toContain('await ')
      expect(body, `${view}：${header} 里的 await 失败后会变成未捕获异常`).toContain('catch')
    })
  }

  it('TasksView 行内操作用共用在途标记，连点「完成」不会发两次 PUT', () => {
    const src = readSrc('TasksView.vue')
    const complete = fnBody(src, 'async function complete(row)')
    expect(complete).toMatch(/if\s*\(\s*rowBusy\s*\)\s*return/)
    expect(complete).toContain('rowBusy = true')
    expect(complete).toContain('rowBusy = false')
  })

  it('JobsView 执行中不得重复触发同一个定时任务', () => {
    const run = fnBody(readSrc('JobsView.vue'), 'async function runNow(row)')
    expect(run).toMatch(/if\s*\(\s*runningId\.value\s*\)\s*return/)
  })
})

describe('BUG-RACE-003 列表加载：竞态丢弃 + 失败反馈 + 状态复位', () => {
  it('三个视图的列表加载都带 catch，并在 finally 里复位 loading', () => {
    for (const file of Object.keys(VIEWS)) {
      const src = readSrc(file)
      const header = src.match(/async function (load|loadMonth)\(/)
      expect(header, `${file} 未找到列表加载函数`).toBeTruthy()
      const body = fnBody(src, `async function ${header[1]}(`)
      expect(body, `${file}：列表加载失败会产生未捕获异常`).toContain('catch')
      expect(body).toContain('finally')
      expect(body).toContain('loading.value = false')
      expect(body).toContain('loadSeq')
    }
  })

  it('ScheduleView 月历加载失败要清空旧月份数据（标题与数据不能错位）', () => {
    const body = fnBody(readSrc('ScheduleView.vue'), 'async function loadMonth()')
    const catchPart = body.slice(body.indexOf('catch'), body.indexOf('finally'))
    expect(catchPart).toContain('monthEvents.value = []')
    // 清空同样受序号守卫保护：过期请求的失败不能抹掉更新一次的成功结果
    expect(catchPart).toContain('seq === loadSeq')
  })
})
