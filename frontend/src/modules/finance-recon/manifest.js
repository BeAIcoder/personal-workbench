// finance-recon 模块清单：商户核销对账（批次、四源解析、勾稽匹配与报告）
export default {
  name: 'finance-recon',
  title: '商户核销对账',
  routes: [
    {
      path: '/finance/recon',
      name: 'finance-recon',
      component: () => import('./views/ReconView.vue'),
      meta: { title: '商户核销对账' },
    },
  ],
  menu: { group: '财务', subgroup: '账务处理', title: '商户核销对账', icon: 'Document', path: '/finance/recon', order: 97 },
}
