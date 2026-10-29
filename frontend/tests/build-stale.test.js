import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { checkNeedsBuild } from '../scripts/needs-build.js'

// stale-build 任务：前端陈旧构建检测（needs-build.js + 启动工作台.bat 分支）。
// 所有用例只使用临时目录，不触碰真实 dist/业务数据。

afterEach(() => {
  while (tmpDirs.length) {
    const dir = tmpDirs.pop()
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

const BASE = new Date('2026-01-01T00:00:00Z')
const OLDER = new Date(BASE.getTime() - 60_000)
const NEWER = new Date(BASE.getTime() + 60_000)

const tmpDirs = []

function makeTmpRoot() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'build-stale-'))
  tmpDirs.push(dir)
  return dir
}

function write(file, content, mtime) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, content)
  if (mtime) fs.utimesSync(file, mtime, mtime)
}

// 生成一个完整的最小前端工程；dist 产物默认与 BASE 同刻，源码默认早于产物。
function makeProject(root, { withDist = true, distMtime = BASE } = {}) {
  write(path.join(root, 'src', 'main.js'), '// main', OLDER)
  write(path.join(root, 'src', 'utils', 'fmt.js'), '// fmt', OLDER)
  write(path.join(root, 'index.html'), '<!DOCTYPE html><html></html>', OLDER)
  write(path.join(root, 'vite.config.js'), 'export default {}', OLDER)
  write(path.join(root, 'package.json'), '{"name":"x"}', OLDER)
  write(path.join(root, 'package-lock.json'), '{"name":"x"}', OLDER)
  if (withDist) {
    write(path.join(root, 'dist', 'index.html'), '<!DOCTYPE html><html></html>', distMtime)
    write(path.join(root, 'dist', 'assets', 'app.js'), '// built', distMtime)
  }
}

describe('needs-build：dist 缺失', () => {
  it('dist 目录整个缺失时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root, { withDist: false })
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(true)
    expect(result.reasons.join(' ')).toContain('dist/index.html')
  })

  it('dist 目录存在但 index.html 缺失时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root, { withDist: false })
    fs.mkdirSync(path.join(root, 'dist', 'assets'), { recursive: true })
    fs.writeFileSync(path.join(root, 'dist', 'assets', 'app.js'), '// built')
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(true)
  })
})

describe('needs-build：源较新', () => {
  it('src 顶层文件较新时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    write(path.join(root, 'src', 'App.vue'), '<template></template>', NEWER)
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(true)
    expect(result.reasons.join(' ')).toContain('App.vue')
  })

  it('src 嵌套子目录文件较新时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    write(path.join(root, 'src', 'views', 'deep', 'Page.vue'), '<template></template>', NEWER)
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(true)
    expect(result.reasons.join(' ')).toContain('Page.vue')
  })

  it('index.html 较新时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    write(path.join(root, 'index.html'), '<!DOCTYPE html><html></html>', NEWER)
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })

  it('vite.config.js 较新时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    write(path.join(root, 'vite.config.js'), 'export default {}', NEWER)
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })

  it('package.json 较新时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    write(path.join(root, 'package.json'), '{"name":"x"}', NEWER)
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })

  it('package-lock.json 较新时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    write(path.join(root, 'package-lock.json'), '{"name":"x"}', NEWER)
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })
})

describe('needs-build：产物较新', () => {
  it('所有源均旧于 dist/index.html 时判定为最新', () => {
    const root = makeTmpRoot()
    makeProject(root)
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(false)
    expect(result.reasons).toEqual([])
  })

  it('源与产物时间戳完全相同时判定为最新（严格大于才需构建）', () => {
    const root = makeTmpRoot()
    makeProject(root, { distMtime: BASE })
    write(path.join(root, 'src', 'main.js'), '// main', BASE)
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(false)
  })
})

describe('needs-build：命令行接口（无参运行，检查真实仓库）', () => {
  const script = path.resolve(import.meta.dirname, '..', 'scripts', 'needs-build.js')

  it('无参运行退出码为 0/1，输出 UP_TO_DATE 或 NEEDS_BUILD（真实仓库即中文含空格路径）', () => {
    let status = 0
    let stdout = ''
    try {
      stdout = execFileSync(process.execPath, [script], { encoding: 'utf8' })
    } catch (e) {
      status = e.status
      stdout = e.stdout || ''
    }
    expect([0, 1]).toContain(status)
    expect(stdout).toMatch(/UP_TO_DATE|NEEDS_BUILD/)
  })
})

describe('启动工作台.bat：GBK 编码与构建分支', () => {
  const batPath = path.resolve(import.meta.dirname, '..', '..', '启动工作台.bat')
  const raw = fs.readFileSync(batPath)

  it('文件仍是合法 GBK（严格解码成功）且不是 UTF-8', () => {
    const text = new TextDecoder('gbk', { fatal: true }).decode(raw)
    expect(text).toContain('个人工作台')
    expect(text).toContain('chcp 936')
    // GBK 中文字节不是合法 UTF-8：严格 UTF-8 解码必须失败，证明未被写成 UTF-8
    expect(() => new TextDecoder('utf-8', { fatal: true }).decode(raw)).toThrow()
  })

  it('调用 needs-build.js 并按退出码分支：需要构建时装依赖并 build，最新时直接启动', () => {
    const text = new TextDecoder('gbk', { fatal: true }).decode(raw)
    expect(text).toContain('call node scripts\\needs-build.js')
    expect(text).toMatch(/if errorlevel 1 goto build_frontend/)
    expect(text).toContain(':build_frontend')
    expect(text).toContain('call npm install --no-audit --no-fund')
    expect(text).toContain('call npm run build')
    expect(text).toContain('if exist "node_modules"')
    expect(text).toMatch(/goto start_server/)
    // 原有启动流程保持不变
    expect(text).toContain(':start_server')
    expect(text).toContain('uvicorn app.main:app')
  })
})
