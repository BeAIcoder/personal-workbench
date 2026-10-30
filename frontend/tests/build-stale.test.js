import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { checkNeedsBuild } from '../scripts/needs-build.js'
import { sourcesHash, writeStamp } from '../scripts/write-build-stamp.js'

// stale-build 任务：前端陈旧构建检测（needs-build.js + 启动工作台.bat 分支）。
// 判定语义为「源码内容哈希 vs dist 构建印章」——不用 mtime，因为 ZIP 解包
// 不保留文件时间戳（Python zipfile 即如此），分发包解包后 mtime 语义会误判。
// 所有用例只使用临时目录，不触碰真实 dist/业务数据。

afterEach(() => {
  while (tmpDirs.length) {
    const dir = tmpDirs.pop()
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

const tmpDirs = []

function makeTmpRoot() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'build-stale-'))
  tmpDirs.push(dir)
  return dir
}

function write(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, content)
}

// 生成一个完整的最小前端工程；dist 产物存在与否由 withDist 控制。
function makeProject(root, { withDist = true } = {}) {
  write(path.join(root, 'src', 'main.js'), '// main')
  write(path.join(root, 'src', 'utils', 'fmt.js'), '// fmt')
  write(path.join(root, 'index.html'), '<!DOCTYPE html><html></html>')
  write(path.join(root, 'vite.config.js'), 'export default {}')
  write(path.join(root, 'package.json'), '{"name":"x"}')
  write(path.join(root, 'package-lock.json'), '{"name":"x"}')
  if (withDist) {
    write(path.join(root, 'dist', 'index.html'), '<!DOCTYPE html><html></html>')
    write(path.join(root, 'dist', 'assets', 'app.js'), '// built')
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

describe('needs-build：构建印章', () => {
  it('dist 存在但缺少构建印章时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(true)
    expect(result.reasons.join(' ')).toContain('stamp')
  })

  it('印章与当前源码一致时判定为最新', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(false)
    expect(result.reasons).toEqual([])
  })

  it('写入的印章 sources 等于当前源码哈希', () => {
    const root = makeTmpRoot()
    makeProject(root)
    const stamp = writeStamp(root)
    expect(stamp.sources).toBe(sourcesHash(root))
  })

  it('印章损坏（非法 JSON）时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    fs.writeFileSync(path.join(root, 'dist', '.build-stamp.json'), '{ not json')
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })
})

describe('needs-build：源码变化', () => {
  it('src 顶层文件内容变化时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    write(path.join(root, 'src', 'App.vue'), '<template></template>')
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(true)
    expect(result.reasons.join(' ')).toContain('sources changed')
  })

  it('src 嵌套子目录文件内容变化时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    write(path.join(root, 'src', 'views', 'deep', 'Page.vue'), '<template>changed</template>')
    const result = checkNeedsBuild(root)
    expect(result.needsBuild).toBe(true)
    expect(result.reasons.join(' ')).toContain('sources changed')
  })

  it('index.html 内容变化时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    write(path.join(root, 'index.html'), '<!DOCTYPE html><html><body>x</body></html>')
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })

  it('vite.config.js 内容变化时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    write(path.join(root, 'vite.config.js'), 'export default { base: "./" }')
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })

  it('package.json 内容变化时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    write(path.join(root, 'package.json'), '{"name":"y"}')
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })

  it('package-lock.json 内容变化时判定需要构建', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    write(path.join(root, 'package-lock.json'), '{"name":"y"}')
    expect(checkNeedsBuild(root).needsBuild).toBe(true)
  })
})

describe('needs-build：mtime 不影响判定（ZIP 解包回归）', () => {
  it('所有文件时间戳被改乱（内容不变）时仍判定为最新', () => {
    const root = makeTmpRoot()
    makeProject(root)
    writeStamp(root)
    // 模拟 zip 解包：所有文件时间戳趋同/乱序（源码晚于产物也不再触发重建）
    const future = new Date('2030-01-01T00:00:00Z')
    const past = new Date('2000-01-01T00:00:00Z')
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full)
        else fs.utimesSync(full, future, future)
      }
    }
    walk(root)
    fs.utimesSync(path.join(root, 'dist', 'index.html'), past, past)
    fs.utimesSync(path.join(root, 'src', 'main.js'), future, future)
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

  it('附带预构建 dist 时不强制 Node：无 node 命令也可直达启动分支', () => {
    const text = new TextDecoder('gbk', { fatal: true }).decode(raw)
    expect(text).toContain('if exist "frontend\\dist\\index.html" goto dist_present')
    expect(text).toContain(':dist_present')
    // dist 存在且本机无 node 时跳过构建直接启动
    expect(text).toMatch(/if errorlevel 1 goto start_server/)
    expect(text).toContain('Node.js 24+')
    expect(text).not.toContain('Node.js 22+')
  })
})
