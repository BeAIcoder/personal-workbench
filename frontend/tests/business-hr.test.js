import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

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
