// business-operations 模块清单：营运管理（合同 / 账单收缴 / 工单）
export default {
  name: 'business-operations',
  title: '营运管理',
  routes: [
    {
      path: '/business/operations',
      name: 'business-operations',
      component: () => import('./views/OperationsView.vue'),
      meta: { title: '营运管理' },
    },
  ],
  menu: { group: '经营', title: '营运管理', icon: 'DataLine', path: '/business/operations', order: 72 },
}
