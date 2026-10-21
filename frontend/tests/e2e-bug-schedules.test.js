// schedules 区域 E2E 探测发现的 bug 回归测试。
// 对应探测脚本 dev/e2e/probes/schedules.py 的 TC-SCH-018：
// 「AI 备会」按钮与事件行 @click=openEdit 同级，缺少 @click.stop 时
// 点击 AI 备会会连带打开编辑对话框（跳转 /assistant 前闪显）。
// 同行删除按钮一直带 @click.stop，此处行为应保持一致。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function readSrc(rel) {
  return readFileSync(join(root, rel), 'utf8')
}

describe('TC-SCH-018 AI 备会按钮不得连带打开编辑对话框', () => {
  it('AI 备会按钮带 @click.stop，阻止冒泡到事件行的 openEdit', () => {
    const src = readSrc('src/views/ScheduleView.vue')
    const m = src.match(/<el-button[^>]*title="AI 备会"[^>]*>/)
    expect(m, 'ScheduleView.vue 中未找到 title="AI 备会" 的按钮').toBeTruthy()
    expect(
      m[0],
      'AI 备会按钮缺少 @click.stop：点击会连带触发事件行的 openEdit，编辑对话框闪显'
    ).toContain('@click.stop')
  })

  it('删除按钮同样带 @click.stop（两个行内按钮行为一致）', () => {
    const src = readSrc('src/views/ScheduleView.vue')
    const m = src.match(/<el-button[^>]*@click\.stop="remove\(ev\)"[^>]*>/)
    expect(m, 'ScheduleView.vue 中未找到删除按钮').toBeTruthy()
  })
})
