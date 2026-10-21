// 发票明细展示辅助：冲红发票方向判定。
// 引擎与服务端口径为「冲红发票」（invoice_processor._determine_invoice_direction 的
// 返回值），历史上也出现过「红字」写法；旧模板只认「红字」，导致红字标签永不渲染。
const RED_DIRECTIONS = ['冲红发票', '红字']

export function isRedInvoice(row) {
  return RED_DIRECTIONS.includes(row?.invoice_direction)
}
