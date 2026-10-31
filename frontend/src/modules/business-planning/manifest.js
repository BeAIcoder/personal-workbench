// business-planning 模块清单：企划管理（活动 / 预算 / 复盘 / 供应商）
export default {
  name: 'business-planning',
  title: '企划管理',
  routes: [
    {
      path: '/business/planning',
      name: 'business-planning',
      component: () => import('./views/PlanningView.vue'),
      meta: { title: '企划管理' },
    },
  ],
  menu: { group: '经营', title: '企划管理', icon: 'Promotion', path: '/business/planning', order: 73 },
}
