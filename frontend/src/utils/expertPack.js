// 专家包页的纯逻辑，便于 vitest 直接单测

// 「全部导入」是否需要先弹确认：任一有效包与数据库有差异（diff），
// 或对应的数据库专家是内置——导入会用文件内容覆盖这些专家的界面修改，
// 与单条导入（importOne）的保护条件对齐。文件错误的包不参与导入，不触发确认。
export function importAllNeedsConfirm(packs, experts) {
  return (packs || []).some((p) => {
    if (!p || !p.valid) return false
    const db = (experts || []).find((e) => e.name === p.name)
    return !!p.diff || !!db?.is_builtin
  })
}
