<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <span class="logo-icon"><el-icon><DataBoard /></el-icon></span>
        <span>个人工作台</span>
      </div>
      <el-menu :default-active="activeMenu" :default-openeds="defaultOpeneds" router class="menu">
        <el-menu-item v-for="item in staticMenus" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
        <el-sub-menu v-for="group in staticMenuGroups" :key="group.name" :index="`group-${group.name}`">
          <template #title>
            <el-icon><component :is="group.icon" /></el-icon>
            <span>{{ group.name }}</span>
          </template>
          <el-menu-item v-for="m in group.items" :key="m.path" :index="m.path">
            <span>{{ m.title }}</span>
          </el-menu-item>
        </el-sub-menu>
        <el-menu-item v-for="item in tailMenus" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
        <el-sub-menu v-for="group in menuGroups" :key="group.name" :index="`group-${group.name}`">
          <template #title>
            <el-icon><component :is="group.icon" /></el-icon>
            <span>{{ group.name }}</span>
          </template>
          <!-- 直挂项（manifest menu 无 subgroup 的模块） -->
          <el-menu-item v-for="m in group.items" :key="m.path" :index="m.path">
            <span>{{ m.title }}</span>
          </el-menu-item>
          <!-- 二级折叠子分组（manifest menu.subgroup，如「税务填报」） -->
          <el-sub-menu v-for="sg in group.subgroups" :key="`sg-${group.name}-${sg.name}`" :index="`subgroup-${group.name}-${sg.name}`">
            <template #title>
              <span>{{ sg.name }}</span>
            </template>
            <el-menu-item v-for="m in sg.items" :key="m.path" :index="m.path">
              <span>{{ m.title }}</span>
            </el-menu-item>
          </el-sub-menu>
        </el-sub-menu>
      </el-menu>
      <div class="aside-footer">v{{ version }} · 数据本地存储</div>
    </el-aside>

    <el-container>
      <el-header class="header" height="60px">
        <div class="page-title">{{ $route.meta.title }}</div>
        <GlobalSearch ref="searchRef" />
        <div class="today">{{ todayText }}</div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script>
// 静态菜单：一级菜单项（文案/图标/顺序导出供测试断言不丢失）。
// 「AI 配置」相关页面收进 STATIC_MENU_GROUPS 的子菜单（仅侧栏聚合，不改路由）；
// tailMenus（定时任务）保持一级菜单原位，渲染在静态分组之后。
export const STATIC_MENUS = [
  { path: '/', title: '工作台概览', icon: 'Odometer' },
  { path: '/tasks', title: '任务管理', icon: 'Finished' },
  { path: '/schedules', title: '日程管理', icon: 'Calendar' },
  { path: '/notes', title: '笔记管理', icon: 'Notebook' },
  { path: '/assistant', title: 'AI 助手', icon: 'ChatDotRound' },
]

// 静态菜单分组：「AI 配置」el-sub-menu（知识与记忆/MCP 工具/技能包/专家团文件）
export const STATIC_MENU_GROUPS = [
  {
    name: 'AI 配置',
    icon: 'Setting',
    items: [
      { path: '/knowledge', title: '知识与记忆', icon: 'Reading' },
      { path: '/mcp', title: 'MCP 工具', icon: 'Connection' },
      { path: '/skill-packs', title: '技能包管理', icon: 'Collection' },
      { path: '/expert-packs', title: '专家团文件', icon: 'UserFilled' },
    ],
  },
]

// 渲染在静态分组之后的尾部一级菜单（定时任务保持原位）
export const TAIL_MENUS = [{ path: '/jobs', title: '定时任务', icon: 'Timer' }]

// Ctrl/Cmd+K 唤起全局搜索的判定：e.key 在部分输入法/合成态下可能缺失，
// 不能直接 toLowerCase（旧代码遇缺 key 事件会抛 TypeError）
export function isSearchHotkey(e) {
  return !!(e && (e.ctrlKey || e.metaKey) && e.key?.toLowerCase() === 'k')
}
</script>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import api from '../api'
import { getModuleMenuGroups } from '../modules'
import GlobalSearch from './GlobalSearch.vue'

const route = useRoute()
const searchRef = ref(null)
const activeMenu = computed(() => route.path)
const todayText = dayjs().format('YYYY年MM月DD日 dddd')
const version = ref('')
const staticMenus = STATIC_MENUS
const staticMenuGroups = STATIC_MENU_GROUPS
const tailMenus = TAIL_MENUS

// 模块菜单按 group 分组渲染（如「财务」组）；组图标取组内首项图标。
// 组内支持二级折叠子分组（manifest menu.subgroup，如「税务填报」）与直挂项。
const menuGroups = computed(() => getModuleMenuGroups())

// 深链/刷新时展开激活项所在的组与子分组（仅初始一次：default-openeds 只在挂载时生效，
// 做成响应式会在用户手动折叠后又被强行展开）。静态「AI 配置」组同理纳入。
const defaultOpeneds = (() => {
  const path = route.path
  const hit = (items) => items.some((m) => m.path === path)
  const opened = []
  for (const g of menuGroups.value) {
    if (hit(g.items) || g.subgroups.some((sg) => hit(sg.items))) {
      opened.push(`group-${g.name}`)
      for (const sg of g.subgroups) {
        if (hit(sg.items)) opened.push(`subgroup-${g.name}-${sg.name}`)
      }
    }
  }
  for (const g of staticMenuGroups) {
    if (g.items.some((m) => m.path === path)) opened.push(`group-${g.name}`)
  }
  return opened
})()

function onKeydown(e) {
  if (isSearchHotkey(e)) {
    e.preventDefault()
    searchRef.value?.focus()
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  // 侧栏版本号与后端保持一致，获取失败则静默不显示
  api.get('/health').then(({ data }) => { version.value = data.version || '' }).catch(() => {})
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.layout {
  height: 100vh;
}

.aside {
  background: var(--wb-sidebar-bg);
  display: flex;
  flex-direction: column;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 18px;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.logo-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--el-color-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 18px;
}

.menu {
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--wb-sidebar-text);
  --el-menu-active-color: #fff;
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.06);
  --el-menu-base-level-padding: 16px;
  --el-menu-item-height: 42px;
  --el-menu-sub-item-height: 38px;
  border-right: none;
  flex: 1;
}

.menu :deep(.el-menu-item),
.menu :deep(.el-sub-menu__title) {
  margin: 2px 10px;
  border-radius: 8px;
}

.menu :deep(.el-menu-item.is-active) {
  background: var(--el-color-primary);
  color: #fff;
  font-weight: 500;
}

.aside-footer {
  color: rgba(255, 255, 255, 0.35);
  font-size: 12px;
  padding: 14px 20px;
}

.header {
  display: flex;
  align-items: center;
  gap: 16px;
  background: #fff;
  border-bottom: 1px solid var(--el-border-color-light);
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  flex: 1;
  white-space: nowrap;
}

.today {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  white-space: nowrap;
}

.main {
  background: var(--wb-bg-page);
  overflow: auto;
  padding: 20px;
}
</style>
