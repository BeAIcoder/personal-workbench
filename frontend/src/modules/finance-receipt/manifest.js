// finance-receipt 模块清单：银行回单整理（扫描、逐笔复核、按账户类别生成 A4 PDF）
export default {
  name: 'finance-receipt',
  title: '银行回单整理',
  routes: [
    {
      path: '/finance/receipt',
      name: 'finance-receipt',
      component: () => import('./views/ReceiptView.vue'),
      meta: { title: '银行回单整理' },
    },
  ],
  menu: { group: '财务', subgroup: '账务处理', title: '银行回单整理', icon: 'Document', path: '/finance/receipt', order: 96 },
}
