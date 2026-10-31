// business-leasing 模块清单：招商管理（铺位 / 线索 / 跟进 / 漏斗）
export default {
  name: 'business-leasing',
  title: '招商管理',
  routes: [
    {
      path: '/business/leasing',
      name: 'business-leasing',
      component: () => import('./views/LeasingView.vue'),
      meta: { title: '招商管理' },
    },
  ],
  menu: { group: '经营', title: '招商管理', icon: 'Shop', path: '/business/leasing', order: 71 },
}
