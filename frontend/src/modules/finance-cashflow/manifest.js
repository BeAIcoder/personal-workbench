// finance-cashflow 模块清单：现金流滚动预测自动填报（L1 薄壳，模块代码已入库）
export default {
  name: 'finance-cashflow',
  title: '现金流填报',
  routes: [
    {
      path: '/finance/cashflow',
      name: 'finance-cashflow',
      component: () => import('./views/CashflowRunView.vue'),
      meta: { title: '现金流填报' },
    },
  ],
  menu: { group: '财务', title: '现金流填报', icon: 'TrendCharts', path: '/finance/cashflow', order: 80 },
}
