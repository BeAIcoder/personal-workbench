<template>
  <div class="memory-panel">
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
      <el-table-column label="来源文档" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.source_doc_title || '—' }}</template>
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

    <el-dialog v-model="memDialog.visible" :title="memDialog.id ? '编辑记忆' : '新建记忆'" width="560px">
      <el-input v-model="memForm.content" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="值得跨会话记住的事实/偏好/经验，每条一个主题" />
      <el-select v-model="memForm.type" style="width: 100%; margin-top: 10px" placeholder="类型（可选）" clearable>
        <el-option value="user" label="用户画像 user" />
        <el-option value="feedback" label="纠正偏好 feedback" />
        <el-option value="project" label="项目背景 project" />
        <el-option value="reference" label="参考线索 reference" />
      </el-select>
      <el-select v-model="memForm.source_doc_id" style="width: 100%; margin-top: 10px" placeholder="来源文档（可选）" clearable filterable>
        <el-option v-for="doc in sourceDocs" :key="doc.id" :value="doc.id" :label="doc.title || `文档 ${doc.id}`" />
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
import api, { knowledgeApi, memoryApi } from '../../api'
import { openCreateMem } from '../../utils/knowledge'

const memories = ref([])
const memKeyword = ref('')
const memType = ref('')
const memStats = reactive({ total: 0, by_source: {}, by_type: {}, pinned: 0, top_hits: [] })
const memDialog = reactive({ visible: false, id: null, saving: false })
const memForm = reactive({ content: '', pinned: false, type: '', source_doc_id: '' })
// 来源文档下拉选项：弹窗打开时按需加载（默认过滤，最多拉 5 页 × 100 条）
const sourceDocs = ref([])
const SOURCE_DOC_MAX_PAGES = 5

let sourceDocsSeq = 0

async function loadSourceDocs() {
  const seq = ++sourceDocsSeq
  const collected = []
  try {
    for (let page = 1; page <= SOURCE_DOC_MAX_PAGES; page++) {
      const { data } = await knowledgeApi.docs({ page, page_size: 100 })
      if (seq !== sourceDocsSeq) return
      const items = data.items || []
      collected.push(...items)
      if (collected.length >= Number(data.total || 0) || !items.length) break
    }
  } catch {
    // 下拉来源加载失败不阻断保存（拦截器已提示）；已有选项保留
  }
  if (seq === sourceDocsSeq) sourceDocs.value = collected
}

const TYPE_LABELS = { user: '用户画像', feedback: '纠正偏好', project: '项目背景', reference: '参考线索' }
function typeLabel(t) {
  return TYPE_LABELS[t] || '未分类'
}

// 请求序号：关键词/类型快速切换时丢弃过期响应
let memSeq = 0

async function loadMemories() {
  const seq = ++memSeq
  try {
    const { data } = await memoryApi.list({
      keyword: memKeyword.value, type: memType.value,
    })
    if (seq !== memSeq) return
    memories.value = data.items || []
  } catch {
    if (seq === memSeq) memories.value = []
  }
}

let memStatsSeq = 0

async function loadMemStats() {
  const seq = ++memStatsSeq
  try {
    const { data } = await memoryApi.stats()
    if (seq !== memStatsSeq) return
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
    const { data } = await memoryApi.importMd({ text: value })
    ElMessage.success(`导入完成：新增 ${data.added}，近重复跳过 ${data.skipped}`)
    await Promise.all([loadMemories(), loadMemStats()])
  } catch { /* 取消或请求失败（拦截器已提示） */ }
}

let memTimer = null
function debouncedLoadMemories() {
  clearTimeout(memTimer)
  memTimer = setTimeout(loadMemories, 300)
}

onBeforeUnmount(() => clearTimeout(memTimer))

function onCreateMem() {
  openCreateMem(memDialog, memForm)
  // utils/knowledge 的 openCreateMem 只重置 content/pinned/type，
  // source_doc_id 由本组件自行复位，避免残留上一条的来源文档
  memForm.source_doc_id = ''
  loadSourceDocs()
}

function openEditMem(row) {
  memDialog.id = row.id
  memForm.content = row.content
  memForm.pinned = row.pinned
  memForm.type = row.type || ''
  memForm.source_doc_id = row.source_doc_id || ''
  loadSourceDocs()
  memDialog.visible = true
}

async function saveMem() {
  if (!memForm.content.trim()) {
    ElMessage.warning('内容不能为空')
    return
  }
  memDialog.saving = true
  const payload = {
    content: memForm.content.trim(),
    type: memForm.type || '',
    source_doc_id: memForm.source_doc_id || null,
  }
  try {
    if (memDialog.id) {
      await memoryApi.update(memDialog.id, payload)
    } else {
      await memoryApi.create(payload)
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
    await memoryApi.update(row.id, { pinned: !row.pinned })
    await loadMemories()
  } catch {
    /* 拦截器已提示 */
  }
}

async function removeMem(row) {
  try {
    await ElMessageBox.confirm('删除该记忆？', '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await memoryApi.remove(row.id)
    await loadMemories()
  } catch {
    /* 拦截器已提示 */
  }
}

onMounted(() => {
  loadMemories()
  loadMemStats()
})
</script>

<style scoped>
.mem-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
}
</style>
