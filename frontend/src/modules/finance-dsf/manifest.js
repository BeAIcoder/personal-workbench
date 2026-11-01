// finance-dsf 模块清单：残保金填报
export default {
  name: 'finance-dsf',
  title: '残保金填报',
  routes: [
    {
      path: '/finance/dsf',
      name: 'finance-dsf',
      component: () => import('./views/DsfView.vue'),
      meta: { title: '残保金填报' },
    },
  ],
  menu: { group: '财务', subgroup: '税务填报', title: '残保金填报', icon: 'Coin', path: '/finance/dsf', order: 84 },
}
