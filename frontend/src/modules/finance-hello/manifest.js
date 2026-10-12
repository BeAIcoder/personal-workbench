// finance-hello 模块清单：前端模块机制实证样例
export default {
  name: 'finance-hello',
  title: '模块示例',
  routes: [
    {
      path: '/finance/hello',
      name: 'finance-hello',
      component: () => import('./views/HelloView.vue'),
      meta: { title: '模块示例' },
    },
  ],
  menu: { group: '财务', title: '模块示例', icon: 'Coin', path: '/finance/hello', order: 99 },
}
