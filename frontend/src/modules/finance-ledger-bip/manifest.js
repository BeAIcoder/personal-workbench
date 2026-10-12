// finance-ledger-bip 模块清单：企业记账·用友 BIP 导入（向导 / 凭证 / 汇总）
export default {
  name: 'finance-ledger-bip',
  title: '企业记账',
  routes: [
    {
      path: '/finance/ledger-bip',
      name: 'finance-ledger-bip',
      component: () => import('./views/ImportWizardView.vue'),
      meta: { title: '企业记账·导入' },
    },
    {
      path: '/finance/ledger-bip/vouchers',
      name: 'finance-ledger-bip-vouchers',
      component: () => import('./views/VoucherListView.vue'),
      meta: { title: '企业记账·凭证查询' },
    },
    {
      path: '/finance/ledger-bip/summary',
      name: 'finance-ledger-bip-summary',
      component: () => import('./views/BalanceSummaryView.vue'),
      meta: { title: '企业记账·科目汇总' },
    },
  ],
  menu: { group: '财务', title: '企业记账（BIP）', icon: 'OfficeBuilding', path: '/finance/ledger-bip', order: 50 },
}
