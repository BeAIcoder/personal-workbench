// debug-runs 模块清单：调试中心·测试运行
export default {
  name: 'debug-runs',
  title: '测试运行',
  routes: [
    {
      path: '/debug/runs',
      name: 'debug-runs',
      component: () => import('./views/RunsView.vue'),
      meta: { title: '测试运行' },
    },
  ],
  menu: { group: '调试', title: '测试运行', icon: 'Aim', path: '/debug/runs', order: 81 },
}
