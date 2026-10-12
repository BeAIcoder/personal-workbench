// finance-tax 模块清单：企业所得税（原「企业所得税计算」项目前端 Vue 重写）
export default {
  name: 'finance-tax',
  title: '企业所得税',
  routes: [
    {
      path: '/finance/tax',
      name: 'finance-tax',
      component: () => import('./views/EnterpriseListView.vue'),
      meta: { title: '企业所得税·企业列表' },
    },
    {
      path: '/finance/tax/enterprises/new',
      name: 'finance-tax-enterprise-new',
      component: () => import('./views/EnterpriseFormView.vue'),
      meta: { title: '企业所得税·新建企业' },
    },
    {
      path: '/finance/tax/enterprises/:id/edit',
      name: 'finance-tax-enterprise-edit',
      component: () => import('./views/EnterpriseFormView.vue'),
      meta: { title: '企业所得税·编辑企业' },
    },
    {
      path: '/finance/tax/calculate/:enterpriseId',
      name: 'finance-tax-calculate',
      component: () => import('./views/TaxCalculateView.vue'),
      meta: { title: '企业所得税·税额计算' },
    },
    {
      path: '/finance/tax/returns',
      name: 'finance-tax-returns',
      component: () => import('./views/TaxReturnListView.vue'),
      meta: { title: '企业所得税·申报记录' },
    },
    {
      path: '/finance/tax/returns/:id',
      name: 'finance-tax-return-detail',
      component: () => import('./views/TaxReturnPreviewView.vue'),
      meta: { title: '企业所得税·申报预览' },
    },
    {
      path: '/finance/tax/import',
      name: 'finance-tax-import',
      component: () => import('./views/FinancialImportView.vue'),
      meta: { title: '企业所得税·财报导入' },
    },
    {
      path: '/finance/tax/statistics',
      name: 'finance-tax-statistics',
      component: () => import('./views/StatisticsView.vue'),
      meta: { title: '企业所得税·统计分析' },
    },
    {
      path: '/finance/tax/policies',
      name: 'finance-tax-policies',
      component: () => import('./views/PolicyListView.vue'),
      meta: { title: '企业所得税·政策管理' },
    },
    {
      path: '/finance/tax/textbook',
      name: 'finance-tax-textbook',
      component: () => import('./views/TextbookReferenceView.vue'),
      meta: { title: '企业所得税·教材参考' },
    },
  ],
  menu: { group: '财务', title: '企业所得税', icon: 'OfficeBuilding', path: '/finance/tax', order: 60 },
}
