// 模块视图 → AI 助手自动提问的统一入口。
// 与 Dashboard/Schedule/Notes/Tasks 的既有 ?auto= 用法同构：对象形式 router.push
// 保留 query（tasks.js:74 先例），AssistantView.tryAutoRun 读取后自动发送并清理 URL。
export function buildAssistantAutoRoute(prompt) {
  return { path: '/assistant', query: { auto: String(prompt || '') } }
}
