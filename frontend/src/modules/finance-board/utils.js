// 报表看板视图共用的纯函数（组件逻辑抽离，便于单测）

// 管线失败时后端仍会归档 failed 快照；若 catch 里不重拉列表，失败记录要等
// 手动刷新才可见。仅在有 HTTP 响应时重拉：网络中断时保留现有列表，避免误清空。
export function shouldReloadAfterRun(error) {
  return Boolean(error && error.response)
}
