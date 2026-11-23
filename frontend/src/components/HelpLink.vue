<template>
  <div class="help-link-row">
    <router-link class="help-link" :to="target" :title="hint">
      <el-icon><QuestionFilled /></el-icon>
      <span>{{ label }}</span>
      <span v-if="entry" class="help-link-section">§{{ entry.section }}</span>
    </router-link>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { helpEntryByPath, helpEntryBySection, manualLocator } from '../utils/helpIndex'

// section 显式指定手册章节号（与 helpIndex 一致）；缺省时按当前路由最长前缀匹配。
const props = defineProps({
  section: { type: String, default: '' },
  label: { type: String, default: '本页帮助' },
})

const route = useRoute()
const entry = computed(() => (props.section ? helpEntryBySection(props.section) : helpEntryByPath(route.path)))
const target = computed(() => (entry.value ? { path: '/help', query: { section: entry.value.section } } : { path: '/help' }))
const hint = computed(() =>
  entry.value ? `${props.label}：${manualLocator(entry.value)}（打开使用说明导航）` : '打开使用说明导航',
)
</script>

<style scoped>
.help-link-row {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.help-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-decoration: none;
}

.help-link:hover {
  color: var(--el-color-primary);
}

.help-link-section {
  color: var(--el-text-color-placeholder);
}
</style>
