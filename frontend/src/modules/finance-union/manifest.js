// finance-union 模块清单：工会经费计提（手工工资基数版本）。
// 菜单挂在「财务 → 账务处理」，排在租赁核算（98）之后；菜单唯一源是本文件的 menu。
export default {
  name: 'finance-union',
  title: '工会经费计提',
  routes: [
    {
      path: '/finance/union',
      name: 'finance-union',
      component: () => import('./views/UnionView.vue'),
      meta: { title: '工会经费计提' },
    },
  ],
  menu: {
    group: '财务',
    subgroup: '账务处理',
    title: '工会经费计提',
    icon: 'Coin',
    path: '/finance/union',
    order: 100,
  },
}
