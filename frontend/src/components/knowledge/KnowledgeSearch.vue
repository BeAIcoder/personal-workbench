<template>
  <div class="knowledge-search">
    <div class="search-bar">
      <el-input
        v-model="query"
        placeholder="问制度、查操作步骤，如「一线城市住宿标准是多少」"
        clearable
        @keyup.enter="doSearch"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="scope" class="scope-select">
        <el-option label="全部" value="all" />
        <el-option label="制度 / 手册" value="docs" />
        <el-option label="笔记" value="notes" />
      </el-select>
      <el-select v-model="category" clearable placeholder="全部分类" class="scope-select">
        <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
      </el-select>
      <el-button type="primary" :loading="searching" @click="doSearch">检索</el-button>
      <AskAiButton v-if="query.trim() && askPrompt" :prompt="askPrompt" />
    </div>

    <div class="stats">
      文档 {{ stats.documents?.docs || 0 }} 篇 / 已生效 {{ stats.documents?.effective_docs || 0 }} 篇 ·
      文档块 {{ stats.documents?.chunks || 0 }} · 笔记 {{ stats.notes_indexed || 0 }} 篇 ·
      向量 {{ stats.documents?.vector ? `已启用（${stats.documents?.vector_dim || 0} 维）` : '未启用' }}
      <el-button size="small" link type="primary" @click="doRebuild" :loading="rebuilding">重建索引</el-button>
    </div>

    <el-empty v-if="searched && !results.length" description="没有命中，换个说法或先上传对应制度" />
    <div v-for="item in results" :key="`${item.source}-${item.id}-${item.citation}`" class="hit">
      <div class="hit-head">
        <div class="hit-title" @click="openItem(item)">
          <el-tag size="small" :type="item.source === 'doc' ? 'primary' : 'info'">
            {{ item.source === 'doc' ? '制度' : '笔记' }}
          </el-tag>
          <span class="title-text">{{ item.title }}</span>
        </div>
        <span class="hit-score">相关度 {{ Math.round((item.score || 0) * 100) }}%</span>
      </div>
      <div class="hit-citation">{{ formatCitation(item) }}</div>
      <div class="hit-snippet">{{ item.snippet }}</div>
    </div>
    <el-alert
      v-if="vectorNote"
      type="warning"
      :closable="false"
      :title="`向量检索未生效：${vectorNote}（已回退关键词检索）`"
      style="margin-top: 12px"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import AskAiButton from '../assistant/AskAiButton.vue'
import { knowledgeApi } from '../../api'
import { DOC_CATEGORIES, buildSearchParams, formatCitation } from '../../utils/knowledge'

const emit = defineEmits(['open-doc'])

const query = ref('')
const scope = ref('all')
const category = ref('')
const categories = ref([...DOC_CATEGORIES])
const results = ref([])
const searching = ref(false)
const searched = ref(false)
const rebuilding = ref(false)
const vectorNote = ref('')
const stats = ref({})

const askPrompt = computed(() => {
  const text = query.value.trim()
  if (!text) return ''
  return (
    `请先调用 search_knowledge 检索知识库，再回答：${text}。` +
    '要求：只依据检索结果作答；每个结论给出来源（文档名 + 版本 + 章节/页码）；' +
    '检索结果没有依据时，直接回答「知识库未收录」，不要猜测。'
  )
})

// 请求序号：连点检索时丢弃过期响应
let searchSeq = 0

async function doSearch() {
  const payload = buildSearchParams({ query: query.value, scope: scope.value, category: category.value, top_k: 8 })
  if (!payload.query) return
  const seq = ++searchSeq
  searching.value = true
  vectorNote.value = ''
  try {
    const { data } = await knowledgeApi.search(payload)
    if (seq !== searchSeq) return
    results.value = data.items || []
    searched.value = true
    const note = results.value.find((item) => item.vector_note)?.vector_note
    vectorNote.value = note || ''
  } catch {
    if (seq === searchSeq) results.value = []
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}

function openItem(item) {
  if (item.source !== 'doc') return
  emit('open-doc', { docId: item.id, chunkId: item.doc?.chunk_id || null })
}

let statsSeq = 0

async function loadStats() {
  const seq = ++statsSeq
  try {
    const { data } = await knowledgeApi.stats()
    if (seq !== statsSeq) return
    stats.value = data
  } catch {
    /* 统计失败不影响检索 */
  }
}

async function doRebuild() {
  if (rebuilding.value) return
  rebuilding.value = true
  try {
    await knowledgeApi.rebuild()
    ElMessage.success('索引已重建')
    await loadStats()
  } catch {
    /* 拦截器已提示 */
  } finally {
    rebuilding.value = false
  }
}

onMounted(() => {
  loadStats()
  knowledgeApi
    .facets()
    .then(({ data }) => {
      const values = (data.categories || []).map((item) => item.value)
      categories.value = Array.from(new Set([...DOC_CATEGORIES, ...values]))
    })
    .catch(() => {})
})
</script>

<style scoped>
.search-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.search-bar .el-input {
  flex: 1;
  min-width: 260px;
}

.scope-select {
  width: 140px;
}

.stats {
  margin: 10px 0 14px;
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
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.hit-title .title-text {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.hit-title:hover .title-text {
  color: var(--el-color-primary);
}

.hit-score {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.hit-citation {
  margin-top: 4px;
  color: var(--el-color-primary);
  font-size: 12px;
}

.hit-snippet {
  margin-top: 4px;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.6;
}
</style>
