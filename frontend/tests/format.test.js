import { describe, expect, it } from 'vitest'
import { fmtDT, isOverdue, priorityLabel, snippet, statusLabel, statusTag } from '../src/utils/format'

describe('format 工具', () => {
  it('状态与优先级中文映射', () => {
    expect(statusLabel.todo).toBe('待办')
    expect(statusLabel.in_progress).toBe('进行中')
    expect(statusLabel.done).toBe('已完成')
    expect(statusTag.done).toBe('success')
    expect(priorityLabel.urgent).toBe('紧急')
    expect(priorityLabel.low).toBe('低')
  })

  it('格式化日期时间', () => {
    expect(fmtDT('2026-09-08T09:30:00')).toBe('2026-09-08 09:30')
    expect(fmtDT(null)).toBe('-')
    expect(fmtDT(undefined)).toBe('-')
  })

  it('逾期判断：未完成且截止时间已过才算逾期', () => {
    const past = { status: 'todo', due_date: '2020-01-01T00:00:00' }
    const done = { status: 'done', due_date: '2020-01-01T00:00:00' }
    const future = { status: 'in_progress', due_date: '2099-01-01T00:00:00' }
    const noDue = { status: 'todo', due_date: null }
    expect(isOverdue(past)).toBe(true)
    expect(isOverdue(done)).toBe(false)
    expect(isOverdue(future)).toBe(false)
    expect(isOverdue(noDue)).toBe(false)
  })

  it('摘要截断', () => {
    expect(snippet('短文本')).toBe('短文本')
    expect(snippet('换行\t和空格  会合并')).toBe('换行 和空格 会合并')
    expect(snippet('a'.repeat(60), 50)).toBe('a'.repeat(50) + '…')
  })
})
