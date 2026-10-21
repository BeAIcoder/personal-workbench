// 知识库页（长期记忆）的纯逻辑，便于 vitest 直接单测

// 打开「新建记忆」弹窗：必须重置 id 与表单——否则编辑过上一条后再点新建，
// 保存会带着旧 id 走 PUT，覆盖掉上一条记忆。
export function openCreateMem(memDialog, memForm) {
  memDialog.id = null
  memDialog.visible = true
  memForm.content = ''
  memForm.pinned = false
}
