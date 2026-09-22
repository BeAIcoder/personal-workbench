<template>
  <div class="notes-layout">
    <el-card class="note-list-card" shadow="never">
      <template #header>
        <div class="list-header">
          <span>笔记（{{ total }}）</span>
          <el-button type="primary" size="small" @click="createNew">
            <el-icon><Plus /></el-icon>&nbsp;新建
          </el-button>
        </div>
      </template>

      <el-input v-model="filters.q" placeholder="搜索标题 / 内容" clearable @input="debouncedLoad" @clear="load(1)">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="filters.tag" clearable placeholder="全部标签" style="width: 100%; margin-top: 8px" @change="load(1)">
        <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
      </el-select>

      <div v-loading="loading" class="note-items">
        <el-empty v-if="!rows.length && !loading" description="暂无笔记" :image-size="70" />
        <div
          v-for="n in rows"
          :key="n.id"
          class="note-item"
          :class="{ active: current && current.id === n.id }"
          @click="select(n)"
        >
          <div class="note-item-title">
            <el-icon v-if="n.pinned" color="#e6a23c"><StarFilled /></el-icon>
            <span>{{ n.title }}</span>
          </div>
          <div class="note-item-snip">{{ snippet(n.content) }}</div>
          <div class="note-item-meta">
            <el-tag v-for="t in n.tags.slice(0, 3)" :key="t" size="small" effect="plain">{{ t }}</el-tag>
            <span class="time">{{ fmtDT(n.updated_at) }}</span>
          </div>
        </div>
      </div>

      <div class="pager">
        <el-pagination
          layout="prev, pager, next"
          small
          :total="total"
          v-model:current-page="page"
          :page-size="pageSize"
          @change="load()"
        />
      </div>
    </el-card>

    <el-card v-if="current" class="note-editor-card" shadow="never">
      <template #header>
        <div class="editor-header">
          <el-button size="small" :type="current.pinned ? 'warning' : 'default'" @click="togglePin">
            {{ current.pinned ? '★ 取消置顶' : '☆ 置顶' }}
          </el-button>
          <el-tooltip content="让 AI 总结要点并提取行动项为任务" placement="top">
            <el-button size="small" type="primary" plain @click="aiDigest"><el-icon><MagicStick /></el-icon>&nbsp;AI 总结</el-button>
          </el-tooltip>
          <div class="spacer"></div>
          <el-button size="small" type="danger" plain @click="remove">删除</el-button>
          <el-button size="small" type="primary" :disabled="!dirty" :loading="saving" @click="save">
            {{ dirty ? '保存' : '已保存' }}
          </el-button>
        </div>
      </template>

      <el-input v-model="current.title" class="title-input" placeholder="笔记标题" maxlength="200" show-word-limit />
      <el-select
        v-model="current.tags"
        multiple
        filterable
        allow-create
        default-first-option
        placeholder="添加标签，回车确认"
        class="tags-select"
      >
        <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
      </el-select>
      <el-input
        v-model="current.content"
        type="textarea"
        :autosize="{ minRows: 16 }"
        placeholder="记录内容，支持多行文本…"
        class="content-input"
      />
      <div class="editor-meta">最后更新：{{ fmtDT(current.updated_at) }}</div>
    </el-card>

    <el-card v-else class="note-editor-card" shadow="never">
      <el-empty description="选择左侧笔记，或点击「新建」开始记录" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { noteApi } from '../api'
import { fmtDT, snippet } from '../utils/format'

const route = useRoute()
const router = useRouter()
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ q: '', tag: null })
const tagOptions = ref([])

const current = ref(null)
const snapshot = ref('')
const saving = ref(false)

const dirty = computed(() => current.value && JSON.stringify(current.value) !== snapshot.value)

// 请求序号：快速翻页/搜索时丢弃过期响应
let loadSeq = 0

async function load(targetPage) {
  if (targetPage) page.value = targetPage
  const seq = ++loadSeq
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.q) params.q = filters.q
    if (filters.tag) params.tag = filters.tag
    const { data } = await noteApi.list(params)
    if (seq !== loadSeq) return  // 已有更新的请求发出，丢弃旧结果
    rows.value = data.items
    total.value = data.total
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadTags() {
  const { data } = await noteApi.tags()
  tagOptions.value = data.tags
}

function select(note) {
  if (dirty.value && !window.confirm('当前笔记有未保存的修改，放弃修改并切换？')) return
  current.value = JSON.parse(JSON.stringify(note))
  snapshot.value = JSON.stringify(current.value)
}

async function createNew() {
  if (dirty.value && !window.confirm('当前笔记有未保存的修改，放弃修改并新建？')) return
  const { data } = await noteApi.create({ title: '未命名笔记', content: '', tags: [] })
  ElMessage.success('已创建，请编辑内容后保存')
  await load()
  loadTags()
  select(rows.value.find((n) => n.id === data.id) || rows.value[0])
}

async function save() {
  if (!current.value) return
  saving.value = true
  try {
    const { data } = await noteApi.update(current.value.id, {
      title: current.value.title,
      content: current.value.content,
      tags: current.value.tags,
      pinned: current.value.pinned,
    })
    snapshot.value = JSON.stringify(data)
    current.value = data
    ElMessage.success('已保存')
    load()
    loadTags()
  } finally {
    saving.value = false
  }
}

function aiDigest() {
  if (!current.value) return
  if (dirty.value) {
    ElMessage.warning('请先保存笔记，再让 AI 总结')
    return
  }
  const content = (current.value.content || '').slice(0, 800)
  const prompt =
    `请总结这条笔记的要点，并提取其中的可执行行动项（每条行动项用 create_task 创建为任务，截止时间合理估计）。笔记：「${current.value.title}」${current.value.tags.length ? '（标签：' + current.value.tags.join('、') + '）' : ''}。内容：${content}`
  router.push({ path: '/assistant', query: { auto: prompt } })
}

async function togglePin() {
  const { data } = await noteApi.update(current.value.id, { pinned: !current.value.pinned })
  snapshot.value = JSON.stringify(data)
  current.value = data
  ElMessage.success(data.pinned ? '已置顶' : '已取消置顶')
  load()
}

async function remove() {
  try {
    await ElMessageBox.confirm(`确定删除笔记「${current.value.title}」吗？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  await noteApi.remove(current.value.id)
  ElMessage.success('已删除')
  current.value = null
  load()
  loadTags()
}

let timer = null
function debouncedLoad() {
  clearTimeout(timer)
  timer = setTimeout(() => load(1), 300)
}

function applyRoute() {
  if (route.path !== '/notes') return
  if (route.query.q !== undefined && route.query.q !== filters.q) {
    filters.q = String(route.query.q || '')
    load(1)
  }
}

onMounted(() => {
  load()
  loadTags()
  applyRoute()
})
watch(() => route.query, applyRoute)
</script>

<style scoped>
.notes-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.note-list-card {
  width: 380px;
  flex-shrink: 0;
}

.note-editor-card {
  flex: 1;
  min-width: 0;
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.note-items {
  margin-top: 10px;
  min-height: 200px;
}

.note-item {
  padding: 10px 8px;
  border-bottom: 1px dashed #ebeef5;
  cursor: pointer;
  border-radius: 6px;
}

.note-item:hover {
  background: #f5f7fa;
}

.note-item.active {
  background: #ecf5ff;
}

.note-item-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}

.note-item-snip {
  color: #909399;
  font-size: 12px;
  margin: 4px 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.note-item-meta {
  display: flex;
  align-items: center;
  gap: 4px;
}

.note-item-meta .time {
  margin-left: auto;
  color: #c0c4cc;
  font-size: 11px;
}

.pager {
  display: flex;
  justify-content: center;
  margin-top: 10px;
}

.editor-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.spacer {
  flex: 1;
}

.title-input {
  margin-bottom: 12px;
}

.title-input :deep(.el-input__inner) {
  font-size: 17px;
  font-weight: 600;
}

.tags-select {
  width: 100%;
  margin-bottom: 12px;
}

.editor-meta {
  margin-top: 8px;
  color: #c0c4cc;
  font-size: 12px;
  text-align: right;
}
</style>
