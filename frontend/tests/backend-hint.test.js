// 分项H：后端未启动提示文案收口为 api/index.js 的 BACKEND_HINT 常量。
// - 默认文案与改造前完全一致（配置留空 = 行为不变）
// - VITE_API_BASE 为绝对地址（http 开头）时替换括号内地址、去掉末尾 /api
// - 三处写死文案（api/index.js toMessage、DashboardView、HelloView）统一引用该常量
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { API_BASE, BACKEND_HINT, buildBackendHint } from '../src/api/index.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function readSrc(rel) {
  return readFileSync(join(root, rel), 'utf8')
}

const DEFAULT_HINT = '请确认后端服务已启动（默认 http://127.0.0.1:8000）'

describe('BACKEND_HINT 导出', () => {
  it('测试环境未设 VITE_API_BASE 时 API_BASE 为 /api 且用默认文案', () => {
    expect(API_BASE).toBe('/api')
    expect(BACKEND_HINT).toBe(DEFAULT_HINT)
  })
})

describe('buildBackendHint 替换逻辑', () => {
  it('相对路径（/api 等）保持默认文案', () => {
    expect(buildBackendHint('/api')).toBe(DEFAULT_HINT)
    expect(buildBackendHint('')).toBe(DEFAULT_HINT)
    expect(buildBackendHint(undefined)).toBe(DEFAULT_HINT)
  })

  it('绝对 http(s) 地址替换括号内地址并去掉末尾 /api', () => {
    expect(buildBackendHint('http://192.168.1.5:8000/api'))
      .toBe('请确认后端服务已启动（http://192.168.1.5:8000）')
    expect(buildBackendHint('https://example.com/api/'))
      .toBe('请确认后端服务已启动（https://example.com）')
  })

  it('末尾无 /api 的绝对地址原样使用', () => {
    expect(buildBackendHint('http://10.0.0.2:9000'))
      .toBe('请确认后端服务已启动（http://10.0.0.2:9000）')
  })
})

describe('写死文案已收口为 BACKEND_HINT 引用', () => {
  it('api/index.js 的 toMessage 使用 BACKEND_HINT', () => {
    const src = readSrc('src/api/index.js')
    expect(src).toContain('网络异常，${BACKEND_HINT}')
  })

  it('DashboardView.vue 绑定 BACKEND_HINT 且不再写死地址', () => {
    const src = readSrc('src/views/DashboardView.vue')
    expect(src).toContain(':sub-title="BACKEND_HINT"')
    expect(src).toMatch(/import \{[^}]*dashboardApi[^}]*BACKEND_HINT[^}]*\} from '\.\.\/api'/)
    expect(src).not.toContain('127.0.0.1:8000')
  })

  it('HelloView.vue 绑定 BACKEND_HINT 且不再写死地址', () => {
    const src = readSrc('src/modules/finance-hello/views/HelloView.vue')
    expect(src).toContain(':sub-title="BACKEND_HINT"')
    expect(src).toContain("import api, { BACKEND_HINT } from '../../../api'")
    expect(src).not.toContain('127.0.0.1:8000')
  })
})
