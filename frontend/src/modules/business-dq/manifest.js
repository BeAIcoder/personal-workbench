// business-dq 模块清单：跨模块数据质量巡检（只读）
export default {
  name: 'business-dq',
  title: '数据质量巡检',
  routes: [
    {
      path: '/business/dq',
      name: 'business-dq',
      component: () => import('./views/DqView.vue'),
      meta: { title: '数据质量巡检' },
    },
  ],
  menu: { group: '职能', title: '数据质量', icon: 'DataAnalysis', path: '/business/dq', order: 83 },
}
