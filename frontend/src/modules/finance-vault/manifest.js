// finance-vault 模块清单：数据池清洗（画像 → 清洗 → 整合分析 → SQL 查询）
export default {
  name: 'finance-vault',
  title: '数据池清洗',
  routes: [
    {
      path: '/finance/vault',
      name: 'finance-vault',
      component: () => import('./views/PoolView.vue'),
    },
    {
      path: '/finance/vault/query',
      name: 'finance-vault-query',
      // 直达查询页时默认落在 SQL 页签（URL ?tab= 仍优先，切回数据池不受影响）
      meta: { tab: 'sql' },
      component: () => import('./views/PoolView.vue'),
    },
  ],
  menu: { group: '财务', title: '数据池清洗', icon: 'Coin', path: '/finance/vault', order: 30 },
}
