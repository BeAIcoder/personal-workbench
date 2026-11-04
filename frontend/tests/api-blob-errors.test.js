// Task 7 / FE L-122：responseType:'blob' 的错误体是 JSON 时，拦截器必须异步解析出后端 detail。
// 旧实现只读 error.response.data.detail（Blob 没有该字段）→ 一律落到「网络异常，请确认后端服务已启动」，
// 下载失败与后端未启动无法区分。这里用真实 axios 实例 + 自定义 adapter 驱动拦截器，而不是读源码。
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

const { ElMessage } = await import('element-plus')
const { default: api, toMessage, BACKEND_HINT } = await import('../src/api/index.js')

function httpError(data, { status = 400, code } = {}) {
  const error = new Error('Request failed')
  error.response = { status, data, headers: { 'content-type': 'application/json' } }
  if (code) error.code = code
  return error
}

function withAdapter(factory) {
  const original = api.defaults.adapter
  api.defaults.adapter = factory
  return () => { api.defaults.adapter = original }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Blob 错误体解析（下载失败显示后端中文 detail）', () => {
  it('Blob JSON detail 被解析并原样提示，不再误报后端未启动', async () => {
    const restore = withAdapter(async () => {
      throw httpError(new Blob([JSON.stringify({ detail: '批次未导出，请先导出后再下载' })], { type: 'application/json' }))
    })
    try {
      await expect(api.get('/finance/invoice/batches/1/download', { responseType: 'blob' })).rejects.toBeTruthy()
    } finally {
      restore()
    }
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith('批次未导出，请先导出后再下载')
    expect(ElMessage.error.mock.calls[0][0]).not.toContain('后端服务已启动')
  })

  it('Blob JSON 的数组 detail 按字段格式化（FastAPI 校验错误）', async () => {
    const restore = withAdapter(async () => {
      throw httpError(new Blob([JSON.stringify({ detail: [{ loc: ['body', 'account'], msg: '不能为空' }] })], { type: 'application/json' }))
    })
    try {
      await expect(api.post('/finance/bankflow/template', {}, { responseType: 'blob' })).rejects.toBeTruthy()
    } finally {
      restore()
    }
    expect(ElMessage.error).toHaveBeenCalledWith('account：不能为空')
  })

  it('非 JSON 的 Blob（例如代理返回 HTML 502）回退到网络异常提示，不抛二次异常', async () => {
    const restore = withAdapter(async () => {
      throw httpError(new Blob(['<html>bad gateway</html>'], { type: 'text/html' }))
    })
    try {
      await expect(api.get('/finance/invoice/batches/1/download', { responseType: 'blob' })).rejects.toBeTruthy()
    } finally {
      restore()
    }
    expect(ElMessage.error).toHaveBeenCalledWith(`网络异常，${BACKEND_HINT}`)
  })

  it('普通 JSON 错误的字符串 detail 保持原行为', async () => {
    const restore = withAdapter(async () => {
      throw httpError({ detail: '账户未配置 H 编码映射' })
    })
    try {
      await expect(api.post('/finance/bankflow/preview')).rejects.toBeTruthy()
    } finally {
      restore()
    }
    expect(ElMessage.error).toHaveBeenCalledWith('账户未配置 H 编码映射')
  })
})

describe('toMessage 纯行为', () => {
  it('超时提示不再断言后端未启动（长任务超时不是服务未启动）', async () => {
    const message = await toMessage(httpError(undefined, { code: 'ECONNABORTED' }))
    expect(message).toContain('请求超时')
    expect(message).not.toContain('后端服务已启动')
  })

  it('无响应（网络中断）仍给出后端未启动排查提示', async () => {
    const error = new Error('Network Error')
    const message = await toMessage(error)
    expect(message).toBe(`网络异常，${BACKEND_HINT}`)
  })

  it('Blob 读取失败时不吞掉原始错误', async () => {
    const blob = new Blob(['{}'])
    blob.text = () => Promise.reject(new Error('stream broken'))
    const message = await toMessage(httpError(blob))
    expect(message).toBe(`网络异常，${BACKEND_HINT}`)
  })
})
