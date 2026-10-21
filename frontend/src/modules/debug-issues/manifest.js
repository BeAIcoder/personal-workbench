// debug-issues 模块清单：调试中心·问题记录
export default {
  name: 'debug-issues',
  title: '问题记录',
  routes: [
    {
      path: '/debug/issues',
      name: 'debug-issues',
      component: () => import('./views/IssuesView.vue'),
      meta: { title: '问题记录' },
    },
  ],
  menu: { group: '调试', title: '问题记录', icon: 'Warning', path: '/debug/issues', order: 80 },
}
