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
  // 模块示例仅作新模块开发样板：不进侧栏（menu 置 null，注册中心按无菜单过滤），路由保留可直达 /finance/hello
  menu: null,
}
