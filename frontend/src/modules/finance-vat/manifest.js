// finance-vat 模块清单：增值税填报
export default {
  name: 'finance-vat',
  title: '增值税填报',
  routes: [
    {
      path: '/finance/vat',
      name: 'finance-vat',
      component: () => import('./views/VatView.vue'),
      meta: { title: '增值税填报' },
    },
  ],
  menu: { group: '财务', title: '增值税填报', icon: 'Coin', path: '/finance/vat', order: 82 },
}
