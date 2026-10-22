// 2026-10-22 Swarm 第三轮：五个 Important 列表/预览竞态的静态回归。
// 根因同 frontend-independent-review I-1~I-5：用户触发的异步加载无请求序号守卫，
// 慢的旧响应会后到并覆盖新页面/预览。行为验证由 Playwright 探针继续覆盖；
// 本仓库无 jsdom/挂载能力，沿用既有约定对 .vue 源码做结构化断言。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function readSrc(rel) {
  return readFileSync(join(root, rel), 'utf8')
}

/** 按大括号配对取函数体，避免用「下一个函数名」做脆弱切片。 */
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

const CASES = [
  {
    name: '数据集预览',
    file: 'src/modules/finance-vault/views/TableListView.vue',
    header: 'async function preview(row)',
    seq: 'previewSeq',
  },
  {
    name: 'BIP 导入向导文件预览',
    file: 'src/modules/finance-ledger-bip/views/ImportWizardView.vue',
    header: 'async function onFile(e)',
    seq: 'previewSeq',
  },
  {
    name: 'BIP 凭证列表',
    file: 'src/modules/finance-ledger-bip/views/VoucherListView.vue',
    header: 'async function reload()',
    seq: 'loadSeq',
  },
  {
    name: '费用分类最近运行',
    file: 'src/modules/finance-expense/views/UploadView.vue',
    header: 'async function loadRuns()',
    seq: 'loadSeq',
  },
  {
    name: '现金流运行列表',
    file: 'src/modules/finance-cashflow/views/CashflowRunView.vue',
    header: 'async function load()',
    seq: 'loadSeq',
  },
]

describe('I-1~I-5：慢旧响应不得覆盖新请求结果', () => {
  for (const c of CASES) {
    it(`${c.name}使用最新请求序号并丢弃过期响应`, () => {
      const body = fnBody(readSrc(c.file), c.header)
      expect(body, `${c.name}未生成请求序号`).toMatch(new RegExp(`const seq = \\+\\+${c.seq}`))
      expect(body, `${c.name}未丢弃过期响应`).toContain(`if (seq !== ${c.seq}) return`)
    })

    it(`${c.name}的 loading/finally 复位只对最新请求生效`, () => {
      const body = fnBody(readSrc(c.file), c.header)
      const finallyStart = body.lastIndexOf('finally')
      expect(finallyStart, `${c.name}缺少 finally`).toBeGreaterThan(-1)
      const finallyBody = body.slice(finallyStart)
      expect(
        finallyBody,
        `${c.name}过期请求会把新请求的 loading 提前复位`,
      ).toContain(`seq === ${c.seq}`)
    })
  }

  it('BIP 凭证列表的过期失败不得清空新页数据', () => {
    const body = fnBody(readSrc(CASES[2].file), CASES[2].header)
    const catchStart = body.indexOf('catch')
    expect(catchStart).toBeGreaterThan(-1)
    expect(body.slice(catchStart), '过期 catch 会清空新请求结果').toContain('seq === loadSeq')
  })
})
