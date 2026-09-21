import dayjs from 'dayjs'

export const statusLabel = { todo: '待办', in_progress: '进行中', done: '已完成' }
export const statusTag = { todo: 'info', in_progress: 'warning', done: 'success' }

export const priorityLabel = { low: '低', medium: '中', high: '高', urgent: '紧急' }
export const priorityTag = { low: 'info', medium: 'primary', high: 'warning', urgent: 'danger' }

export function fmtDT(value) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'
}

export function fmtDate(value) {
  return value ? dayjs(value).format('YYYY-MM-DD') : '-'
}

export function isOverdue(task) {
  return task.status !== 'done' && !!task.due_date && dayjs(task.due_date).isBefore(dayjs())
}

export function snippet(text, limit = 50) {
  const t = (text || '').replace(/\s+/g, ' ').trim()
  if (t.length <= limit) return t
  return t.slice(0, limit) + '…'
}
