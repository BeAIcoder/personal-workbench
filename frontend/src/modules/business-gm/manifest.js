// business-gm 模块清单：总经理只读经营聚合
export default {
  name: 'business-gm',
  title: '总经理经营聚合',
  routes: [
    {
      path: '/business/gm',
      name: 'business-gm',
      component: () => import('./views/GmView.vue'),
      meta: { title: '总经理经营聚合' },
    },
  ],
  menu: { group: '经营', title: '总经理经营聚合', icon: 'DataAnalysis', path: '/business/gm', order: 70 },
}
