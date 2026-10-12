// finance-expense 模块清单：费用分类汇总（上传银行明细账 → 引擎分类 → 结果页）
export default {
  name: 'finance-expense',
  title: '费用分类',
  routes: [
    {
      path: '/finance/expense',
      name: 'finance-expense',
      component: () => import('./views/UploadView.vue'),
      meta: { title: '费用分类' },
    },
    {
      path: '/finance/expense/runs/:id',
      name: 'finance-expense-run',
      component: () => import('./views/RunResultView.vue'),
      meta: { title: '费用分类结果' },
    },
  ],
  menu: { group: '财务', title: '费用分类', icon: 'Histogram', path: '/finance/expense', order: 10 },
}
