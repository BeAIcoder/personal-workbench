export const NARROW_MEDIA = '(max-width: 1100px)'

// 窄屏抽屉关闭态：用于 inert / aria-hidden / CSS visibility 的统一判定。
export function drawerClosed(narrow, open) {
  return !!narrow && !open
}

// 全局 Esc：只有窄屏打开且焦点确实在抽屉内才关闭；焦点在聊天区时不误关。
export function escClosesDrawer({ narrow, open, drawer, active }) {
  if (!narrow || !open || !drawer || !active) return false
  return typeof drawer.contains === 'function' && drawer.contains(active)
}
