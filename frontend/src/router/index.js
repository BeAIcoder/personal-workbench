import { createRouter, createWebHashHistory } from 'vue-router'
import { getModuleRoutes } from '../modules'

const routes = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { title: '工作台概览' },
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('../views/TasksView.vue'),
    meta: { title: '任务管理' },
  },
  {
    path: '/schedules',
    name: 'schedules',
    component: () => import('../views/ScheduleView.vue'),
    meta: { title: '日程管理' },
  },
  {
    path: '/notes',
    name: 'notes',
    component: () => import('../views/NotesView.vue'),
    meta: { title: '笔记管理' },
  },
  {
    path: '/assistant',
    name: 'assistant',
    component: () => import('../views/AssistantView.vue'),
    meta: { title: 'AI 助手' },
  },
  {
    path: '/jobs',
    name: 'jobs',
    component: () => import('../views/JobsView.vue'),
    meta: { title: '定时任务' },
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('../views/KnowledgeView.vue'),
    meta: { title: '知识与记忆' },
  },
  {
    path: '/mcp',
    name: 'mcp',
    component: () => import('../views/McpView.vue'),
    meta: { title: 'MCP 工具' },
  },
  {
    path: '/skill-packs',
    name: 'skill-packs',
    component: () => import('../views/SkillPackView.vue'),
    meta: { title: '技能包管理' },
  },
  {
    path: '/expert-packs',
    name: 'expert-packs',
    component: () => import('../views/ExpertPackView.vue'),
    meta: { title: '专家团文件' },
  },
  // 模块路由（/finance/<name>/...，组件懒加载），404 兜底保持在最后
  ...getModuleRoutes(),
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = `${to.meta.title || '个人工作台'} · 个人工作台`
})

export default router
