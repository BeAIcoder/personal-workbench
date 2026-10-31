// business-hr 模块清单：行政人事（员工 / 考勤 / 薪资）
export default {
  name: 'business-hr',
  title: '行政人事',
  routes: [
    {
      path: '/business/hr',
      name: 'business-hr',
      component: () => import('./views/HrView.vue'),
      meta: { title: '行政人事' },
    },
  ],
  menu: { group: '职能', title: '行政人事', icon: 'User', path: '/business/hr', order: 82 },
}
