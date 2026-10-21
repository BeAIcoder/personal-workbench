import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// 拦截器（src/api/index.js）toast 之后仍会 Promise.reject，所以模板事件处理器里的
// 每个 await 都必须自己 catch：漏了 catch 就是一次未捕获 rejection。
// 本轮给 main.js 加了 window 'unhandledrejection' 采集，未捕获 rejection 会被写进
// /api/debug/client-errors —— 一次普通的 400 保存失败就会在调试中心记成「前端报错」，
// 把真实故障淹没；同时成功路径（刷新列表）也会被跳过，界面停在旧数据上。
//
// 检测口径：函数体内「恰好 2 格缩进」＝直接位于函数顶层（不在 try 块里，try 内的语句是 4 格），
// 这类直接发请求的语句必须同行带 .catch()，或其值已被外层 try/catch 接住（见 UNGUARDED 注释）。
// 扫描整个 src 而非固定清单：新页面默认纳管，不再依赖"记得把文件加进列表"。
const UNGUARDED = /^ {2}(?:const .*?= |let .*?= |return )?await [^#]*\b(?:api|[a-zA-Z]*Api)\b/
const GUARDED_ON_LINE = /\.catch\(/

const SRC = fileURLToPath(new URL('../src', import.meta.url))

function sourceFiles(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) sourceFiles(full, out)
    else if (/\.(vue|js)$/.test(entry.name)) out.push(full)
  }
  return out
}

const FILES = sourceFiles(SRC).sort()

describe('全站请求调用不得留下未捕获 rejection', () => {
  it('扫描范围覆盖 src 下全部 .vue/.js 文件', () => {
    expect(FILES.length).toBeGreaterThanOrEqual(80)
  })

  it.each(FILES)('%s 的函数顶层请求都有 catch', (full) => {
    const source = readFileSync(full, 'utf-8')
    const hits = source
      .split('\n')
      .map((line, i) => ({ line, n: i + 1 }))
      .filter(({ line }) => UNGUARDED.test(line) && !GUARDED_ON_LINE.test(line))
    const rel = path.relative(SRC, full)
    expect(hits, `未捕获的请求调用：${hits.map((h) => `${rel}:L${h.n} ${h.line.trim()}`).join(' | ')}`).toEqual([])
  })
})
