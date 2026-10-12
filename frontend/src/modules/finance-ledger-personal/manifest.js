// finance-ledger-personal 模块清单：个人记账（记账 / 流水 / 统计）
export default {
  name: 'finance-ledger-personal',
  title: '个人记账',
  routes: [
    {
      path: '/finance/ledger-personal',
      name: 'finance-ledger-personal',
      component: () => import('./views/QuickAddView.vue'),
      meta: { title: '个人记账' },
    },
    {
      path: '/finance/ledger-personal/transactions',
      name: 'finance-ledger-personal-transactions',
      component: () => import('./views/TransactionListView.vue'),
      meta: { title: '个人记账·流水' },
    },
    {
      path: '/finance/ledger-personal/stats',
      name: 'finance-ledger-personal-stats',
      component: () => import('./views/StatsView.vue'),
      meta: { title: '个人记账·统计' },
    },
  ],
  menu: { group: '财务', title: '个人记账', icon: 'Notebook', path: '/finance/ledger-personal', order: 40 },
}
