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
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>工作台概览</span>
        </el-menu-item>
        <el-menu-item index="/tasks">
          <el-icon><Finished /></el-icon>
          <span>任务管理</span>
        </el-menu-item>
        <el-menu-item index="/schedules">
          <el-icon><Calendar /></el-icon>
          <span>日程管理</span>
        </el-menu-item>
        <el-menu-item index="/notes">
          <el-icon><Notebook /></el-icon>
          <span>笔记管理</span>
        </el-menu-item>
        <el-menu-item index="/assistant">
          <el-icon><ChatDotRound /></el-icon>
          <span>AI 助手</span>
        </el-menu-item>
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

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import api from '../api'
import GlobalSearch from './GlobalSearch.vue'

const route = useRoute()
const searchRef = ref(null)
const activeMenu = computed(() => route.path)
const todayText = dayjs().format('YYYY年MM月DD日 dddd')
const version = ref('')

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
