// finance-iit 模块清单：个人所得税填报
export default {
  name: 'finance-iit',
  title: '个人所得税填报',
  routes: [
    {
      path: '/finance/iit',
      name: 'finance-iit',
      component: () => import('./views/IitView.vue'),
      meta: { title: '个人所得税填报' },
    },
  ],
  menu: { group: '财务', subgroup: '税务填报', title: '个人所得税填报', icon: 'User', path: '/finance/iit', order: 83 },
}
