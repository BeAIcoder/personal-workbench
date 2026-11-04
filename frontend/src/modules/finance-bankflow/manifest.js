// finance-bankflow 模块清单：银行流水 → BIP 导入模板转换（上传/预览/日期区间/直接下载 xlsx）
export default {
  name: 'finance-bankflow',
  title: '银行流水模板',
  routes: [
    {
      path: '/finance/bankflow',
      name: 'finance-bankflow',
      component: () => import('./views/BankflowView.vue'),
      meta: { title: '银行流水模板' },
    },
  ],
  menu: { group: '财务', subgroup: '账务处理', title: '银行流水模板', icon: 'CreditCard', path: '/finance/bankflow', order: 95 },
}
