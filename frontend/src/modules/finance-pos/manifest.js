// finance-pos 模块清单：建行 POS 收款对账（L1 薄壳）
export default {
  name: 'finance-pos',
  title: 'POS 对账',
  routes: [
    {
      path: '/finance/pos',
      name: 'finance-pos',
      component: () => import('./views/PosRunView.vue'),
      meta: { title: 'POS 对账' },
    },
  ],
  menu: { group: '财务', title: 'POS 对账', icon: 'CreditCard', path: '/finance/pos', order: 90 },
}
