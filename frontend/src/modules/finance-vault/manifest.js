// finance-vault 模块清单：DuckDB 数据仓（表清单 + 只读 SQL 查询）
export default {
  name: 'finance-vault',
  title: '数据仓',
  routes: [
    {
      path: '/finance/vault',
      name: 'finance-vault',
      component: () => import('./views/TableListView.vue'),
      meta: { title: '数据仓·表清单' },
    },
    {
      path: '/finance/vault/query',
      name: 'finance-vault-query',
      component: () => import('./views/QueryView.vue'),
      meta: { title: '数据仓·SQL 查询' },
    },
  ],
  menu: { group: '财务', title: '数据仓', icon: 'Coin', path: '/finance/vault', order: 30 },
}
