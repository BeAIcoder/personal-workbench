import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { employeeDeleteConfirmText, emptyEmployeeForm } from '../src/modules/business-hr/employeeForm.js'

const src = readFileSync(
  new URL('../src/modules/business-hr/views/HrView.vue', import.meta.url),
  'utf8',
)

describe('行政人事薪资生成警告', () => {
  it('生成后逐条展示后端返回的缺考勤 warnings', () => {
    const generateBody = src.match(/async function generatePayroll\(\) \{[\s\S]*?\n\}/)?.[0] || ''
    expect(generateBody).toContain('data.warnings')
    expect(generateBody).toMatch(/ElMessage\.warning\(/)
  })
})

describe('人事员工表单纯函数契约', () => {
  it('新建员工 social_base 为空（留空=按基本工资），不是 0', () => {
    const form = emptyEmployeeForm()
    expect(form.social_base).toBeNull()
    expect(form.base_salary).toBe(0)
  })

  it('每次返回全新对象，避免跨弹窗串值', () => {
    const first = emptyEmployeeForm()
    first.name = '张三'
    expect(emptyEmployeeForm().name).toBe('')
  })

  it('删除确认文案指向可执行动作，不承诺删除已确认薪资', () => {
    const text = employeeDeleteConfirmText('李四')
    expect(text).toContain('李四')
    expect(text).toContain('离职')
    expect(text).not.toContain('薪资页处理')
    expect(text).not.toContain('同时删除其考勤与薪资')
  })

  it('社保基数输入框提示「留空=按基本工资」', () => {
    expect(src).toContain('placeholder="留空=按基本工资"')
  })
})
