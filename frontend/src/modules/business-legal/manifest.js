// business-legal 模块清单：法务管理（合同审查 / 案件 / 用印）
export default {
  name: 'business-legal',
  title: '法务管理',
  routes: [
    {
      path: '/business/legal',
      name: 'business-legal',
      component: () => import('./views/LegalView.vue'),
      meta: { title: '法务管理' },
    },
  ],
  menu: { group: '职能', title: '法务管理', icon: 'Document', path: '/business/legal', order: 81 },
}
