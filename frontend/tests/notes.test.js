import { describe, expect, it } from 'vitest'
import { needsDiscardConfirm, planPinToggle, rolledBackDraft } from '../src/utils/notes'

describe('notes / 切换确认（needsDiscardConfirm）', () => {
  it('脏状态普通切换 → 需要确认', () => {
    expect(needsDiscardConfirm(true)).toBe(true)
  })

  it('新建入口已确认过（force）→ 选中新笔记不再二次确认', () => {
    expect(needsDiscardConfirm(true, true)).toBe(false)
  })

  it('非脏状态切换不需要确认', () => {
    expect(needsDiscardConfirm(false)).toBe(false)
    expect(needsDiscardConfirm(false, true)).toBe(false)
  })
})

// 回归：置顶切换不得用服务端返回值覆盖未保存的标题/正文/标签。
// 组件在脏状态下必须被 planPinToggle 拦截，先提示保存。
describe('notes / 置顶切换计划（planPinToggle）', () => {
  it('有未保存编辑 → 阻止直接更新，保持原置顶状态并提示先保存', () => {
    expect(planPinToggle(true, false)).toEqual({
      action: 'blocked',
      pinned: false,
      message: '请先保存笔记，再切换置顶',
    })
    expect(planPinToggle(true, true)).toEqual({
      action: 'blocked',
      pinned: true,
      message: '请先保存笔记，再切换置顶',
    })
  })

  it('无未保存编辑 → 允许切换并翻转置顶状态', () => {
    expect(planPinToggle(false, false)).toEqual({ action: 'update', pinned: true, message: '' })
    expect(planPinToggle(false, true)).toEqual({ action: 'update', pinned: false, message: '' })
  })
})

// 回归：筛选/翻页下新建后新笔记不可见，若用户已确认「放弃修改并新建」，
// 旧草稿必须回滚到服务端快照，不能继续留在编辑器里被误保存。
describe('notes / 新建落空后的草稿回滚（rolledBackDraft）', () => {
  const snapshot = JSON.stringify({ id: 7, title: '原笔记', content: '服务端原文', tags: [] })

  it('已确认放弃 + 有快照 → 返回快照对象（编辑器回滚）', () => {
    expect(rolledBackDraft(true, snapshot)).toEqual({
      id: 7, title: '原笔记', content: '服务端原文', tags: [],
    })
  })

  it('未确认放弃 → 不擅自丢弃用户编辑', () => {
    expect(rolledBackDraft(false, snapshot)).toBeNull()
  })

  it('无快照 / 快照损坏 → 安全返回 null', () => {
    expect(rolledBackDraft(true, '')).toBeNull()
    expect(rolledBackDraft(true, '{bad json')).toBeNull()
  })
})
