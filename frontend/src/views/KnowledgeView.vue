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
            <el-input v-model="memKeyword" placeholder="关键词过滤" style="width: 220px" clearable @input="debouncedLoadMemories" />
            <el-button type="primary" @click="onCreateMem">新建记忆</el-button>
          </div>
          <el-table :data="memories" size="small" empty-text="暂无记忆">
            <el-table-column label="置顶" width="60" align="center">
              <template #default="{ row }">
                <el-button size="small" link :type="row.pinned ? 'warning' : 'info'" @click="togglePin(row)">★</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="content" label="内容" min-width="380" show-overflow-tooltip />
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
  rebuilding.value = true
  try {
    await api.post('/knowledge/rag/rebuild')
    ElMessage.success('索引已重建')
    await loadStats()
  } finally {
    rebuilding.value = false
  }
}

// ---- 记忆 ----
const memories = ref([])
const memKeyword = ref('')
const memDialog = reactive({ visible: false, id: null, saving: false })
const memForm = reactive({ content: '', pinned: false })

async function loadMemories() {
  try {
    const { data } = await api.get('/knowledge/memories', { params: { keyword: memKeyword.value } })
    memories.value = data.items || []
  } catch {
    memories.value = []
  }
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
      await api.put(`/knowledge/memories/${memDialog.id}`, { content: memForm.content.trim() })
    } else {
      await api.post('/knowledge/memories', { content: memForm.content.trim() })
    }
    ElMessage.success('已保存')
    memDialog.visible = false
    await loadMemories()
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
  await api.delete(`/knowledge/memories/${row.id}`)
  await loadMemories()
}

onMounted(() => {
  loadStats()
  loadMemories()
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
  color: #909399;
  font-size: 12px;
}

.hit {
  padding: 10px 12px;
  border-bottom: 1px solid #ebeef5;
}

.hit-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.hit-title {
  font-weight: 600;
  color: #303133;
  text-decoration: none;
}

.hit-title:hover {
  color: #409eff;
}

.hit-snippet {
  margin-top: 4px;
  color: #606266;
  font-size: 13px;
  line-height: 1.6;
}
</style>
