// finance-invoice 模块清单：发票处理（批次列表 + 详情）
export default {
  name: 'finance-invoice',
  title: '发票处理',
  routes: [
    {
      path: '/finance/invoice',
      name: 'finance-invoice',
      component: () => import('./views/BatchListView.vue'),
      meta: { title: '发票处理' },
    },
    {
      path: '/finance/invoice/batches/:id',
      name: 'finance-invoice-detail',
      component: () => import('./views/BatchDetailView.vue'),
      meta: { title: '发票批次详情' },
    },
  ],
  menu: { group: '财务', title: '发票处理', icon: 'Tickets', path: '/finance/invoice', order: 20 },
}
