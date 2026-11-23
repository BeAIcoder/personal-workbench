// business-manage 模块清单：经营管理（内控管理 / 云南周报 / 曲靖周报 / 月度工作情况）
export default {
  name: 'business-manage',
  title: '经营管理',
  routes: [
    { path: '/business/manage', redirect: '/business/manage/control' },
    {
      path: '/business/manage/control',
      name: 'business-manage-control',
      component: () => import('./views/ManageView.vue'),
      meta: { title: '内控管理' },
    },
    {
      path: '/business/manage/weekly',
      name: 'business-manage-weekly',
      component: () => import('./views/ManageView.vue'),
      meta: { title: '云南项目周报' },
    },
    {
      path: '/business/manage/weekly-qujing',
      name: 'business-manage-weekly-qujing',
      component: () => import('./views/ManageView.vue'),
      meta: { title: '曲靖项目周报' },
    },
    {
      path: '/business/manage/monthly',
      name: 'business-manage-monthly',
      component: () => import('./views/ManageView.vue'),
      meta: { title: '月度工作情况' },
    },
  ],
  menu: { group: '经营', title: '经营管理', icon: 'DataBoard', path: '/business/manage', order: 75 },
}
