// finance-lease 模块清单：租赁核算（承租人使用权资产与租赁负债的初始及正常后续计量）。
// 菜单挂在「财务 → 账务处理」，排在商户核销对账（97）之后；菜单唯一源是本文件的 menu。
export default {
  name: 'finance-lease',
  title: '租赁核算',
  routes: [
    {
      path: '/finance/lease',
      name: 'finance-lease',
      component: () => import('./views/LeaseView.vue'),
      meta: { title: '租赁核算' },
    },
  ],
  menu: {
    group: '财务',
    subgroup: '账务处理',
    title: '租赁核算',
    icon: 'Document',
    path: '/finance/lease',
    order: 98,
  },
}
