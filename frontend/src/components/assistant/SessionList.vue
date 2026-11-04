<template>
  <el-card class="sessions-card" shadow="never">
    <template #header>
      <div class="card-head">
        <span>会话</span>
        <el-radio-group v-model="tab" size="small">
          <el-radio-button :value="false">进行中</el-radio-button>
          <el-radio-button :value="true">已归档</el-radio-button>
        </el-radio-group>
        <el-button size="small" type="primary" plain @click="emit('create')">
          <el-icon><Plus /></el-icon>&nbsp;新建
        </el-button>
      </div>
    </template>
    <div class="session-list" role="list">
      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="session-item"
        :class="{ active: s.session_id === currentSessionId }"
        role="listitem"
      >
        <button type="button" class="session-main" :aria-label="`打开会话 ${s.title}`" @click="emit('select', s.session_id)">
          <span class="session-title">{{ s.title }}</span>
          <span class="session-meta">
            <span>{{ s.msg_count }} 条</span>
            <span class="session-time">{{ fmtDT(s.updated_at) }}</span>
          </span>
        </button>
        <div class="session-actions" @click.stop>
          <el-icon v-if="!s.archived" role="button" tabindex="0" title="归档" :aria-label="`归档会话 ${s.title}`" @click="emit('archive', s)" @keydown.enter.prevent.stop="emit('archive', s)" @keydown.space.prevent.stop="emit('archive', s)"><FolderChecked /></el-icon>
          <el-icon v-else role="button" tabindex="0" title="恢复" :aria-label="`恢复会话 ${s.title}`" @click="emit('restore', s)" @keydown.enter.prevent.stop="emit('restore', s)" @keydown.space.prevent.stop="emit('restore', s)"><FolderOpened /></el-icon>
          <el-icon role="button" tabindex="0" title="导出 Markdown" :aria-label="`导出会话 ${s.title}`" @click="exportSession(s)" @keydown.enter.prevent.stop="exportSession(s)" @keydown.space.prevent.stop="exportSession(s)"><Download /></el-icon>
          <el-icon role="button" tabindex="0" title="重命名" :aria-label="`重命名会话 ${s.title}`" @click="emit('rename', s)" @keydown.enter.prevent.stop="emit('rename', s)" @keydown.space.prevent.stop="emit('rename', s)"><Edit /></el-icon>
          <el-icon class="danger" role="button" tabindex="0" title="删除" :aria-label="`删除会话 ${s.title}`" @click="emit('delete', s)" @keydown.enter.prevent.stop="emit('delete', s)" @keydown.space.prevent.stop="emit('delete', s)"><Delete /></el-icon>
        </div>
      </div>
      <el-empty v-if="!sessions.length" :description="archived ? '暂无已归档会话' : '暂无会话'" :image-size="50" />
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { Delete, Download, Edit, FolderChecked, FolderOpened, Plus } from '@element-plus/icons-vue'
import { API_BASE } from '../../api'
import { fmtDT } from '../../utils/format'
import { sessionExportUrl } from '../../utils/assistant'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: '' },
  archived: { type: Boolean, default: false },
})

const emit = defineEmits(['create', 'select', 'rename', 'delete', 'archive', 'restore', 'update:archived'])

// 页签与父组件的 archived 状态双向绑定；父组件按 archived 参数重新拉取列表
const tab = computed({
  get: () => props.archived,
  set: (v) => emit('update:archived', v),
})

// 导出 Markdown 附件：直接 window.open 触发浏览器下载
function exportSession(s) {
  window.open(sessionExportUrl(s.session_id, API_BASE), '_blank')
}
</script>

<style scoped>
.sessions-card {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.sessions-card :deep(.el-card__body) {
  overflow-y: auto;
  flex: 1;
  padding-top: 6px;
}

.session-item {
  position: relative;
  padding: 4px 10px;
  border-radius: 8px;
  border: 1px solid transparent;
}

.session-main {
  display: block;
  width: 100%;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.session-main:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
  border-radius: 4px;
}

.session-item:hover {
  background: var(--el-fill-color-light);
}

.session-item.active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-8);
}

.session-title {
  display: block;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 104px;
}

.session-meta {
  display: flex;
  gap: 8px;
  color: var(--el-text-color-placeholder);
  font-size: 11px;
  margin-top: 2px;
}

.session-actions {
  position: absolute;
  right: 8px;
  top: 8px;
  display: flex;
  gap: 6px;
  color: var(--el-text-color-secondary);
}

.session-item:hover .session-actions,
.session-item:focus-within .session-actions {
  display: flex;
}

.session-actions .el-icon {
  cursor: pointer;
}

.session-actions .danger:hover {
  color: var(--el-color-danger);
}
</style>
