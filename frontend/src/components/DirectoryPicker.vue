<template>
  <el-dialog :model-value="modelValue" :title="title || '选择目录'" width="640px" append-to-body
    @update:model-value="v => emit('update:modelValue', v)" @open="openRoot">
    <div class="picker-bar">
      <el-button size="small" :disabled="!currentPath" @click="goUp">上一级</el-button>
      <el-input v-model="pathInput" size="small" placeholder="输入或粘贴绝对路径后回车" @keyup.enter="goPathInput" clearable />
      <el-button size="small" type="primary" :disabled="!currentPath" @click="pick">选择当前目录</el-button>
    </div>
    <div class="picker-list" v-loading="loading">
      <div v-if="!currentPath && entries.length" class="picker-hint">选择根目录：</div>
      <div
        v-for="e in entries"
        :key="e.path"
        class="picker-row"
        role="button"
        tabindex="0"
        :aria-label="`进入目录 ${e.name}`"
        @click="enter(e)"
        @keyup.enter="enter(e)"
        @keyup.space.prevent="enter(e)"
      >
        <el-icon class="row-icon"><Folder /></el-icon>
        <span class="row-name">{{ e.name }}</span>
      </div>
      <el-empty v-if="!loading && !entries.length" description="该目录下没有子目录" :image-size="60" />
    </div>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Folder } from '@element-plus/icons-vue'
import api from '../api'

const props = defineProps({ modelValue: Boolean, title: String })
const emit = defineEmits(['update:modelValue', 'select'])

const loading = ref(false)
const currentPath = ref('')
const parentPath = ref('')
const pathInput = ref('')
const entries = ref([])

// 请求序号：快速进出不同目录时丢弃过期响应
let loadSeq = 0

async function load(path) {
  const seq = ++loadSeq
  loading.value = true
  try {
    const { data } = await api.get('/fs/browse', { params: { path: path || '' } })
    if (seq !== loadSeq) return
    currentPath.value = data.path || ''
    parentPath.value = data.parent || ''
    pathInput.value = currentPath.value
    entries.value = data.entries || []
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function openRoot() {
  currentPath.value = ''
  pathInput.value = ''
  entries.value = []
  load('')
}

function enter(e) {
  load(e.path)
}

function goUp() {
  if (parentPath.value || currentPath.value) load(parentPath.value)
}

function goPathInput() {
  if (pathInput.value.trim()) load(pathInput.value.trim())
}

function pick() {
  if (!currentPath.value) return
  emit('select', currentPath.value)
  emit('update:modelValue', false)
}
</script>

<style scoped>
.picker-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.picker-list {
  max-height: 380px;
  overflow: auto;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  padding: 4px 0;
}

.picker-hint {
  padding: 6px 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.picker-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  cursor: pointer;
}

.picker-row:hover {
  background: var(--el-fill-color-light);
}

.row-icon {
  color: var(--el-color-warning);
}
</style>
