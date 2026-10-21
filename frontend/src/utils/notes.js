// 笔记页的纯逻辑：新建后选中目标、路由参数解析（便于 vitest 直接单测）

// 新建笔记后应选中的目标：只认刚创建的 id。
// 筛选/翻页状态下新笔记可能不在当前列表中，此时返回 null——由调用侧保持原选中并提示，
// 绝不退化为 rows[0]（继续编辑会覆盖别人的笔记）。
export function pickAfterCreate(rows, createdId) {
  return (rows || []).find((n) => n && n.id === createdId) || null
}

// 解析 /notes 路由参数：q=搜索词；highlight=要定位选中的笔记 id（来自知识库 RAG 命中链接）。
// id 非法/缺失时为 null，调用侧不得选中任何笔记。
export function parseNotesQuery(query) {
  const q = query && query.q !== undefined && query.q !== '' ? String(query.q) : null
  let highlight = null
  if (query && query.highlight !== undefined && query.highlight !== '') {
    const id = Number(query.highlight)
    if (Number.isInteger(id) && id > 0) highlight = id
  }
  return { q, highlight }
}
