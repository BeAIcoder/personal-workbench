// finance-board 模块清单：报表看板（微信报表监控管线）
export default {
  name: 'finance-board',
  title: '报表看板',
  routes: [
    {
      path: '/finance/board',
      name: 'finance-board',
      component: () => import('./views/BoardView.vue'),
      meta: { title: '报表看板' },
    },
  ],
  menu: { group: '财务', title: '报表看板', icon: 'DataBoard', path: '/finance/board', order: 70 },
}
