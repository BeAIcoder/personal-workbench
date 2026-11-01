<template>
  <div class="page">
    <el-card shadow="never">
      <el-tabs v-model="tab">
        <el-tab-pane label="知识库检索（RAG）" name="rag">
          <div class="rag-bar">
            <el-input v-model="query" placeholder="输入问题或关键词，BM25 检索全部笔记" clearable @keyup.enter="doSearch" />
            <el-button type="primary" @click="doSearch" :loading="searching">检索</el-button>
            <el-button @click="doRebuild" :loading="rebuilding">重建索引</el-button>
            <span class="stats">索引：{{ stats.notes_indexed }} 篇笔记 / {{ stats.chunks }} 块 / {{ stats.vocabulary }} 词</span>
          </div>
          <el-empty v-if="!results.length && searched" description="没有命中，换个说法或检查笔记内容" />
          <div v-for="r in results" :key="r.note_id + r.score" class="hit">
            <div class="hit-head">
              <router-link :to="`/notes?highlight=${r.note_id}`" class="hit-title">{{ r.title }}</router-link>
              <el-tag size="small" type="info">score {{ r.score }}</el-tag>
            </div>
            <div class="hit-snippet">{{ r.snippet }}</div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="长期记忆" name="memory">
          <div class="mem-bar">
            <el-input v-model="memKeyword" placeholder="关键词过滤" style="width: 200px" clearable @input="debouncedLoadMemories" />
            <el-select v-model="memType" style="width: 140px" placeholder="全部类型" clearable @change="loadMemories">
              <el-option value="user" label="用户画像 user" />
              <el-option value="feedback" label="纠正偏好 feedback" />
              <el-option value="project" label="项目背景 project" />
              <el-option value="reference" label="参考线索 reference" />
            </el-select>
            <el-button type="primary" @click="onCreateMem">新建记忆</el-button>
            <el-button @click="exportMemories">导出 MD</el-button>
            <el-button @click="importMemories">导入 MD</el-button>
          </div>
          <el-alert v-if="memStats.total" type="info" :closable="false" style="margin-bottom: 10px"
            :title="`共 ${memStats.total} 条（来源 手动${memStats.by_source.manual || 0}/抽取${memStats.by_source.auto || 0}/导入${memStats.by_source.import || 0}，置顶 ${memStats.pinned}）`" />
          <el-table :data="memories" size="small" empty-text="暂无记忆">
            <el-table-column label="置顶" width="60" align="center">
              <template #default="{ row }">
                <el-button size="small" link :type="row.pinned ? 'warning' : 'info'" @click="togglePin(row)">★</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="content" label="内容" min-width="340" show-overflow-tooltip />
            <el-table-column label="类型" width="100">
              <template #default="{ row }">{{ typeLabel(row.type) }}</template>
            </el-table-column>
            <el-table-column prop="source" label="来源" width="80">
              <template #default="{ row }">{{ { manual: '手动', auto: '抽取', import: '导入' }[row.source] || row.source }}</template>
            </el-table-column>
            <el-table-column prop="hit_count" label="命中" width="70" align="right" />
            <el-table-column prop="created_at" label="创建时间" width="140" />
            <el-table-column label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="openEditMem(row)">编辑</el-button>
                <el-button size="small" link type="danger" @click="removeMem(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="memDialog.visible" :title="memDialog.id ? '编辑记忆' : '新建记忆'" width="560px">
      <el-input v-model="memForm.content" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="值得跨会话记住的事实/偏好/经验，每条一个主题" />
      <el-select v-model="memForm.type" style="width: 100%; margin-top: 10px" placeholder="类型（可选）" clearable>
        <el-option value="user" label="用户画像 user" />
        <el-option value="feedback" label="纠正偏好 feedback" />
        <el-option value="project" label="项目背景 project" />
        <el-option value="reference" label="参考线索 reference" />
      </el-select>
      <template #footer>
        <el-button @click="memDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="memDialog.saving" @click="saveMem">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { openCreateMem } from '../utils/knowledge'

const tab = ref('rag')

// ---- RAG ----
const query = ref('')
const results = ref([])
const searching = ref(false)
const searched = ref(false)
const rebuilding = ref(false)
const stats = reactive({ notes_indexed: 0, chunks: 0, vocabulary: 0 })

async function loadStats() {
  try {
    const { data } = await api.get('/knowledge/rag/stats')
    Object.assign(stats, data)
  } catch { /* 忽略 */ }
}

async function doSearch() {
  if (!query.value.trim()) return
  searching.value = true
  try {
    const { data } = await api.post('/knowledge/rag/search', { query: query.value, top_k: 8 })
    results.value = data.items || []
    searched.value = true
  } catch {
    results.value = []
  } finally {
    searching.value = false
  }
}

async function doRebuild() {
  if (rebuilding.value) return  // 忙态防连点
  rebuilding.value = true
  try {
    await api.post('/knowledge/rag/rebuild')
    ElMessage.success('索引已重建')
    await loadStats()
  } catch {
    /* 拦截器已提示后端 detail；重建失败不提示成功、不刷新统计 */
  } finally {
    rebuilding.value = false
  }
}

// ---- 记忆 ----
const memories = ref([])
const memKeyword = ref('')
const memType = ref('')
const memStats = reactive({ total: 0, by_source: {}, by_type: {}, pinned: 0, top_hits: [] })
const memDialog = reactive({ visible: false, id: null, saving: false })
const memForm = reactive({ content: '', pinned: false, type: '' })

const TYPE_LABELS = { user: '用户画像', feedback: '纠正偏好', project: '项目背景', reference: '参考线索' }
function typeLabel(t) {
  return TYPE_LABELS[t] || '未分类'
}

async function loadMemories() {
  try {
    const { data } = await api.get('/knowledge/memories', {
      params: { keyword: memKeyword.value, type: memType.value },
    })
    memories.value = data.items || []
  } catch {
    memories.value = []
  }
}

async function loadMemStats() {
  try {
    const { data } = await api.get('/knowledge/memories/stats')
    Object.assign(memStats, data)
  } catch { /* 忽略 */ }
}

function exportMemories() {
  window.open(`${api.defaults.baseURL}/knowledge/memories/export.md`, '_blank')
}

async function importMemories() {
  try {
    const { value } = await ElMessageBox.prompt(
      '粘贴 MEMORY.md 风格 Markdown（## 类型节 + - 条目行），近重复自动跳过',
      '导入记忆', { inputType: 'textarea', inputPlaceholder: '## project\n- 条目一\n- 条目二' })
    if (!value || !value.trim()) return
    const { data } = await api.post('/knowledge/memories/import.md', { text: value })
    ElMessage.success(`导入完成：新增 ${data.added}，近重复跳过 ${data.skipped}`)
    await Promise.all([loadMemories(), loadMemStats()])
  } catch { /* 取消或请求失败（拦截器已提示） */ }
}

// 关键词过滤防抖：避免逐键发请求
let memTimer = null
function debouncedLoadMemories() {
  clearTimeout(memTimer)
  memTimer = setTimeout(loadMemories, 300)
}

onBeforeUnmount(() => clearTimeout(memTimer))

function onCreateMem() {
  openCreateMem(memDialog, memForm)
}

function openEditMem(row) {
  memDialog.id = row.id
  memForm.content = row.content
  memForm.pinned = row.pinned
  memForm.type = row.type || ''
  memDialog.visible = true
}

async function saveMem() {
  if (!memForm.content.trim()) {
    ElMessage.warning('内容不能为空')
    return
  }
  memDialog.saving = true
  try {
    if (memDialog.id) {
      await api.put(`/knowledge/memories/${memDialog.id}`, {
        content: memForm.content.trim(), type: memForm.type || '',
      })
    } else {
      await api.post('/knowledge/memories', {
        content: memForm.content.trim(), type: memForm.type || '',
      })
    }
    ElMessage.success('已保存')
    memDialog.visible = false
    await Promise.all([loadMemories(), loadMemStats()])
  } catch {
    /* 拦截器已提示 */
  } finally {
    memDialog.saving = false
  }
}

async function togglePin(row) {
  try {
    await api.put(`/knowledge/memories/${row.id}`, { pinned: !row.pinned })
    await loadMemories()
  } catch {
    /* 拦截器已提示 */
  }
}

async function removeMem(row) {
  try {
    await ElMessageBox.confirm('删除该记忆？', '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await api.delete(`/knowledge/memories/${row.id}`)
    await loadMemories()
  } catch {
    /* 拦截器已提示 */
  }
}

onMounted(() => {
  loadStats()
  loadMemories()
  loadMemStats()
})
</script>

<style scoped>
.rag-bar,
.mem-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
}

.rag-bar .el-input {
  max-width: 420px;
}

.stats {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.hit {
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.hit-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.hit-title {
  font-weight: 600;
  color: var(--el-text-color-primary);
  text-decoration: none;
}

.hit-title:hover {
  color: var(--el-color-primary);
}

.hit-snippet {
  margin-top: 4px;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.6;
}
</style>
