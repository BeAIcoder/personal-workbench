<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">📋 个人工作台</div>
      <el-menu
        :default-active="activeMenu"
        router
        class="menu"
        background-color="#1f2d3d"
        text-color="#bfc9d4"
        active-text-color="#ffffff"
      >
        <el-menu-item v-for="item in staticMenus" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
        <el-sub-menu v-for="group in menuGroups" :key="group.name" :index="`group-${group.name}`">
          <template #title>
            <el-icon><component :is="group.icon" /></el-icon>
            <span>{{ group.name }}</span>
          </template>
          <el-menu-item v-for="m in group.items" :key="m.path" :index="m.path">
            <span>{{ m.title }}</span>
          </el-menu-item>
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
// 静态菜单：原有 6 项，文案/图标/顺序保持不变（导出供测试断言不丢失）
export const STATIC_MENUS = [
  { path: '/', title: '工作台概览', icon: 'Odometer' },
  { path: '/tasks', title: '任务管理', icon: 'Finished' },
  { path: '/schedules', title: '日程管理', icon: 'Calendar' },
  { path: '/notes', title: '笔记管理', icon: 'Notebook' },
  { path: '/assistant', title: 'AI 助手', icon: 'ChatDotRound' },
  { path: '/knowledge', title: '知识与记忆', icon: 'Reading' },
  { path: '/mcp', title: 'MCP 工具', icon: 'Connection' },
  { path: '/skill-packs', title: '技能包', icon: 'Collection' },
  { path: '/expert-packs', title: '专家团文件', icon: 'UserFilled' },
  { path: '/jobs', title: '定时任务', icon: 'Timer' },
]
</script>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import api from '../api'
import { getModuleMenus } from '../modules'
import GlobalSearch from './GlobalSearch.vue'

const route = useRoute()
const searchRef = ref(null)
const activeMenu = computed(() => route.path)
const todayText = dayjs().format('YYYY年MM月DD日 dddd')
const version = ref('')
const staticMenus = STATIC_MENUS

// 模块菜单按 group 分组渲染（如「财务」组）；组图标取组内首项图标
const menuGroups = computed(() => {
  const groups = new Map()
  for (const m of getModuleMenus()) {
    const name = m.group || '模块'
    if (!groups.has(name)) {
      groups.set(name, { name, icon: m.icon || 'Folder', items: [] })
    }
    groups.get(name).items.push(m)
  }
  return [...groups.values()]
})

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
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
  background: #1f2d3d;
  display: flex;
  flex-direction: column;
}

.logo {
  color: #fff;
  font-size: 18px;
  font-weight: 600;
  padding: 18px 20px;
  letter-spacing: 1px;
}

.menu {
  border-right: none;
  flex: 1;
}

.menu :deep(.el-menu-item.is-active) {
  background: #409eff !important;
}

.aside-footer {
  color: #6b7a8a;
  font-size: 12px;
  padding: 14px 20px;
}

.header {
  display: flex;
  align-items: center;
  gap: 16px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}

.page-title {
  font-size: 17px;
  font-weight: 600;
  flex: 1;
  white-space: nowrap;
}

.today {
  color: #909399;
  font-size: 13px;
  white-space: nowrap;
}

.main {
  background: #f5f7fa;
  overflow: auto;
  padding: 20px;
}
</style>
