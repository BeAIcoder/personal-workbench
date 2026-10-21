import { describe, expect, it } from 'vitest'
import {
  buildServerUpdatePayload,
  describeApiError,
  formatArgs,
  parseArgsText,
  parseEnvText,
  parseJsonObject,
} from '../src/utils/mcp'

// 以下用例对应 dev/e2e/probes/mcp.py 深度探测发现的真实缺陷（RED 先行）：
// 1) env 传数组：前端 parseEnv 只校验 typeof object，数组被放行 → 后端 422 英文报错
// 2) 测试调用参数传数组：JSON.parse 通过 → 后端 422 → 结果面板渲染出 "[object Object]"
// 3) args 含空格：编辑保存时 join(' ')/split(/\s+/) 往返，含空格参数被静默拆坏

describe('env JSON 校验', () => {
  it('合法对象解析通过', () => {
    expect(parseEnvText('{"API_KEY":"xxx"}')).toEqual({ API_KEY: 'xxx' })
  })

  it('空文本视为空对象', () => {
    expect(parseEnvText('')).toEqual({})
    expect(parseEnvText('   ')).toEqual({})
  })

  it('非法 JSON 抛中文提示', () => {
    expect(() => parseEnvText('{"A": oops')).toThrow('环境变量不是合法 JSON')
  })

  it('数组被拒（曾静默透传到后端 422）', () => {
    expect(() => parseEnvText('["A","B"]')).toThrow('环境变量')
    expect(() => parseEnvText('["A","B"]')).toThrow('JSON 对象')
  })

  it('标量与 null 被拒', () => {
    expect(() => parseEnvText('123')).toThrow('JSON 对象')
    expect(() => parseEnvText('"abc"')).toThrow('JSON 对象')
    expect(() => parseEnvText('null')).toThrow('JSON 对象')
  })
})

describe('测试调用参数校验', () => {
  it('合法对象通过', () => {
    expect(parseJsonObject('{"path":"D:\\\\"}', '参数')).toEqual({ path: 'D:\\' })
  })

  it('空文本按空对象（保持默认 {} 行为）', () => {
    expect(parseJsonObject('', '参数')).toEqual({})
  })

  it('数组被拒（曾渲染出 [object Object]）', () => {
    expect(() => parseJsonObject('[1,2]', '参数')).toThrow('参数')
    expect(() => parseJsonObject('[1,2]', '参数')).toThrow('JSON 对象')
  })
})

describe('接口错误文案提取', () => {
  it('detail 为字符串时原样返回', () => {
    expect(describeApiError({ response: { data: { detail: '服务器不存在' } } })).toBe('服务器不存在')
  })

  it('detail 为 pydantic 422 校验数组时提取字段与消息', () => {
    const err = {
      response: { data: { detail: [{ loc: ['body', 'arguments'], msg: 'Input should be a valid dictionary' }] } },
    }
    expect(describeApiError(err)).toBe('arguments：Input should be a valid dictionary')
  })

  it('detail 为其他对象时序列化而非 [object Object]', () => {
    const out = describeApiError({ response: { data: { detail: { a: 1 } } } })
    expect(out).not.toContain('[object Object]')
  })

  it('无 response 时回退 message', () => {
    expect(describeApiError({ message: 'Network Error' })).toBe('Network Error')
    expect(describeApiError(new Error('boom'))).toBe('boom')
  })
})

describe('args 空格往返', () => {
  it('解析尊重双引号（含空格参数不再被拆坏）', () => {
    expect(parseArgsText('-y @scope/pkg "D:\\my docs\\root"')).toEqual([
      '-y', '@scope/pkg', 'D:\\my docs\\root',
    ])
  })

  it('空文本返回空数组', () => {
    expect(parseArgsText('')).toEqual([])
    expect(parseArgsText('   ')).toEqual([])
  })

  it('无引号时按空白拆分（兼容既有输入习惯）', () => {
    expect(parseArgsText('-y pkg')).toEqual(['-y', 'pkg'])
  })

  it('formatArgs 给含空白/引号的参数加引号并转义', () => {
    expect(formatArgs(['-y', '@scope/pkg', 'D:\\my docs\\root'])).toBe('-y @scope/pkg "D:\\\\my docs\\\\root"')
    expect(formatArgs([])).toBe('')
  })

  it('formatArgs → parseArgsText 往返无损', () => {
    const args = ['-y', '@scope/pkg', 'D:\\my docs\\root', 'a"b', 'plain']
    expect(parseArgsText(formatArgs(args))).toEqual(args)
  })
})

describe('buildServerUpdatePayload（上轮 env 保留修复守护）', () => {
  it('未动 env 时不带 env 键（后端 exclude_unset 保留原值）', () => {
    const p = buildServerUpdatePayload(
      { command: 'c', args: [], env: {}, enabled: true }, { envTouched: false },
    )
    expect(p).toEqual({ command: 'c', args: [], enabled: true })
    expect('env' in p).toBe(false)
  })

  it('动过 env（含清空）才带上 env', () => {
    const p = buildServerUpdatePayload(
      { command: 'c', args: [], env: { A: '1' }, enabled: true }, { envTouched: true },
    )
    expect(p.env).toEqual({ A: '1' })
  })
})
