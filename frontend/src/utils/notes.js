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

// 置顶切换计划：脏状态下禁止直接调更新接口——接口只返回服务端数据，
// 用它覆盖 current 会静默丢掉尚未保存的标题/正文/标签。先提示保存。
export function planPinToggle(dirty, pinned) {
  if (dirty) {
    return { action: 'blocked', pinned, message: '请先保存笔记，再切换置顶' }
  }
  return { action: 'update', pinned: !pinned, message: '' }
}

// 切换笔记是否需要弹出“放弃修改”确认。force 用于「新建」：入口已确认过一次，
// 选中刚创建的新笔记时不得再弹第二次。
export function needsDiscardConfirm(dirty, force = false) {
  return Boolean(dirty) && !force
}

// 新建后若新笔记被筛选/分页隐藏（没有可定位的目标），而用户此前已确认「放弃修改并新建」，
// 编辑器应回滚到已保存快照：否则旧草稿仍留在编辑器里，后续保存会误改原笔记。
export function rolledBackDraft(discarded, snapshot) {
  if (!discarded || !snapshot) return null
  try {
    return JSON.parse(snapshot)
  } catch {
    return null
  }
}
