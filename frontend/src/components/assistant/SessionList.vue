<template>
  <el-card class="sessions-card" shadow="never">
    <template #header>
      <div class="card-head">
        <span>会话</span>
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
          <el-icon @click="emit('rename', s)"><Edit /></el-icon>
          <el-icon class="danger" @click="emit('delete', s)"><Delete /></el-icon>
        </div>
      </div>
      <el-empty v-if="!sessions.length" description="暂无会话" :image-size="50" />
    </div>
  </el-card>
</template>

<script setup>
import { Delete, Edit, Plus } from '@element-plus/icons-vue'
import { fmtDT } from '../../utils/format'

defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: '' },
})

const emit = defineEmits(['create', 'select', 'rename', 'delete'])
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
  padding-right: 40px;
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
