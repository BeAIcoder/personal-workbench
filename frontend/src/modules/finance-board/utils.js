// 报表看板视图共用的纯函数（组件逻辑抽离，便于单测）

// 管线失败时后端仍会归档 failed 快照；若 catch 里不重拉列表，失败记录要等
// 手动刷新才可见。仅在有 HTTP 响应时重拉：网络中断时保留现有列表，避免误清空。
export function shouldReloadAfterRun(error) {
  return Boolean(error && error.response)
}

// 只有「还在跑」的状态继续轮询：ok/failed 以及未知非空状态都按终态处理，
// 避免后端新增状态名后把前端拖进无意义的长时间轮询；空状态视为尚未返回结果，继续等。
const RUNNING_STATUSES = new Set(['running', 'pending', 'queued', 'started', 'submitted', 'in_progress'])

export function isTerminalRunStatus(status) {
  const normalized = String(status ?? '').trim().toLowerCase()
  return normalized !== '' && !RUNNING_STATUSES.has(normalized)
}
