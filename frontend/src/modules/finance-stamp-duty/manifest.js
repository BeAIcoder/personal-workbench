// finance-stamp-duty 模块清单：印花税填报
export default {
  name: 'finance-stamp-duty',
  title: '印花税填报',
  routes: [
    {
      path: '/finance/stamp-duty',
      name: 'finance-stamp-duty',
      component: () => import('./views/StampDutyView.vue'),
      meta: { title: '印花税填报' },
    },
  ],
  menu: { group: '财务', subgroup: '税务填报', title: '印花税填报', icon: 'Stamp', path: '/finance/stamp-duty', order: 81 },
}
