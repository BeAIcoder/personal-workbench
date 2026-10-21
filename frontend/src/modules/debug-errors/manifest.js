// debug-errors 模块清单：调试中心·前端报错
export default {
  name: 'debug-errors',
  title: '前端报错',
  routes: [
    {
      path: '/debug/errors',
      name: 'debug-errors',
      component: () => import('./views/ErrorsView.vue'),
      meta: { title: '前端报错' },
    },
  ],
  menu: { group: '调试', title: '前端报错', icon: 'Monitor', path: '/debug/errors', order: 82 },
}
