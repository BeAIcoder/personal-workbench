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
    <div class="session-list">
      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="session-item"
        :class="{ active: s.session_id === currentSessionId }"
        @click="emit('select', s.session_id)"
      >
        <div class="session-title">{{ s.title }}</div>
        <div class="session-meta">
          <span>{{ s.msg_count }} 条</span>
          <span class="session-time">{{ fmtDT(s.updated_at) }}</span>
        </div>
        <div class="session-actions" @click.stop>
          <el-icon v-if="!s.archived" title="归档" @click="emit('archive', s)"><FolderChecked /></el-icon>
          <el-icon v-else title="恢复" @click="emit('restore', s)"><FolderOpened /></el-icon>
          <el-icon title="导出 Markdown" @click="exportSession(s)"><Download /></el-icon>
          <el-icon title="重命名" @click="emit('rename', s)"><Edit /></el-icon>
          <el-icon class="danger" title="删除" @click="emit('delete', s)"><Delete /></el-icon>
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

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-item {
  position: relative;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

.session-item:hover {
  background: #f5f7fa;
}

.session-item.active {
  background: #ecf5ff;
  border-color: #d9ecff;
}

.session-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 104px;
}

.session-meta {
  display: flex;
  gap: 8px;
  color: #c0c4cc;
  font-size: 11px;
  margin-top: 2px;
}

.session-actions {
  position: absolute;
  right: 8px;
  top: 8px;
  display: none;
  gap: 6px;
  color: #909399;
}

.session-item:hover .session-actions {
  display: flex;
}

.session-actions .el-icon {
  cursor: pointer;
}

.session-actions .danger:hover {
  color: #f56c6c;
}
</style>
