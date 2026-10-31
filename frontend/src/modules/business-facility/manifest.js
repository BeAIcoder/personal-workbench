// business-facility 模块清单：工程物业（设备 / 合规到期）
export default {
  name: 'business-facility',
  title: '工程物业',
  routes: [
    {
      path: '/business/facility',
      name: 'business-facility',
      component: () => import('./views/FacilityView.vue'),
      meta: { title: '工程物业' },
    },
  ],
  menu: { group: '经营', title: '工程物业', icon: 'Tools', path: '/business/facility', order: 74 },
}
