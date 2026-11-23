// finance-deferred 模块清单：长期待摊（仅租入资产改良支出的直线摊销）。
// 菜单挂在「财务 → 账务处理」，排在租赁核算（98）之后；菜单唯一源是本文件的 menu。
export default {
  name: 'finance-deferred',
  title: '长期待摊',
  routes: [
    {
      path: '/finance/deferred',
      name: 'finance-deferred',
      component: () => import('./views/DeferredView.vue'),
      meta: { title: '长期待摊' },
    },
  ],
  menu: {
    group: '财务',
    subgroup: '账务处理',
    title: '长期待摊',
    icon: 'Tickets',
    path: '/finance/deferred',
    order: 99,
  },
}
