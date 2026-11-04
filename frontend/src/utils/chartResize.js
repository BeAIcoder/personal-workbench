// 图表 resize 监听：目标可注入，便于在 node 环境用假 target 验证绑定/解绑。
export function bindChartResize(charts, target = typeof window !== 'undefined' ? window : null) {
  if (!target || typeof target.addEventListener !== 'function') return () => {}
  const handler = () => {
    for (const chart of charts() || []) {
      try {
        chart?.resize?.()
      } catch {
        /* 单个图表 resize 失败不影响其它图表 */
      }
    }
  }
  target.addEventListener('resize', handler)
  return () => target.removeEventListener?.('resize', handler)
}
