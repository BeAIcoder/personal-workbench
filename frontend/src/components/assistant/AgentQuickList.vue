<template>
  <el-card class="team-card" shadow="never">
    <template #header>
      <div class="card-head">
        <span>Agent 团队（{{ agents.length }}）</span>
        <div class="team-actions">
          <el-tag size="small" :type="llmReady ? 'success' : 'warning'">{{ llmReady ? '模型已接入' : '模型未配置' }}</el-tag>
          <el-button link type="primary" size="small" style="margin-left: 8px" @click="emit('open-settings')">
            <el-icon><Setting /></el-icon>&nbsp;设置
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      v-if="!llmReady"
      type="warning"
      :closable="false"
      class="llm-alert"
      title="尚未配置模型 API"
      description="点右上角「设置」添加供应商并填 API Key，保存后即可激活团队。"
    />

    <div
      class="agent-item"
      :class="{ active: selectedAgent === null }"
      @click="emit('update:selectedAgent', null)"
    >
      <span class="agent-emoji">🧭</span>
      <div class="agent-info">
        <div class="agent-label">智能路由（推荐）</div>
        <div class="agent-desc">根据问题自动匹配最合适的专家</div>
      </div>
    </div>
    <div
      v-for="a in agents"
      :key="a.name"
      class="agent-item"
      :class="{ active: selectedAgent === a.name }"
      @click="emit('update:selectedAgent', selectedAgent === a.name ? null : a.name)"
    >
      <span class="agent-emoji">{{ a.emoji }}</span>
      <div class="agent-info">
        <div class="agent-label">{{ a.label }}</div>
        <div class="agent-desc">{{ a.description }}</div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { Setting } from '@element-plus/icons-vue'

defineProps({
  agents: { type: Array, default: () => [] },
  llmReady: { type: Boolean, default: false },
  selectedAgent: { type: String, default: null },
})

const emit = defineEmits(['update:selectedAgent', 'open-settings'])
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.team-card {
  display: flex;
  flex-direction: column;
  flex: 1.1;
  min-height: 0;
}

.team-card :deep(.el-card__body) {
  overflow-y: auto;
  flex: 1;
  padding-top: 6px;
}

.team-actions {
  display: flex;
  align-items: center;
}

.llm-alert {
  margin-bottom: 10px;
}

.agent-item {
  display: flex;
  gap: 10px;
  padding: 7px 8px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

.agent-item:hover {
  background: #f5f7fa;
}

.agent-item.active {
  background: #ecf5ff;
  border-color: #d9ecff;
}

.agent-emoji {
  font-size: 20px;
  line-height: 1.2;
}

.agent-label {
  font-weight: 600;
}

.agent-desc {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
