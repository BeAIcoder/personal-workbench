<template>
  <el-drawer
    :model-value="modelValue"
    title="文档详情"
    size="640px"
    @update:model-value="(value) => emit('update:modelValue', value)"
  >
    <div v-loading="loading" class="detail">
      <template v-if="detail">
        <div class="detail-head">
          <div>
            <div class="detail-title">{{ detail.title }}</div>
            <div class="detail-sub">
              <el-tag size="small" :type="statusMeta(detail.status).type">{{ statusMeta(detail.status).label }}</el-tag>
              <span>{{ detail.version }}</span>
              <span v-if="detail.doc_no">编号 {{ detail.doc_no }}</span>
            </div>
          </div>
          <div class="detail-actions">
            <el-button size="small" :disabled="!canDownload(detail)" @click="download">下载原件</el-button>
            <el-button size="small" @click="startEdit">编辑</el-button>
          </div>
        </div>

        <el-form v-if="editing.active" label-width="88px" class="edit-form">
          <el-form-item label="标题"><el-input v-model="editing.form.title" maxlength="200" /></el-form-item>
          <el-form-item label="分类">
            <el-select v-model="editing.form.category" allow-create filterable style="width: 100%">
              <el-option v-for="c in DOC_CATEGORIES" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item label="部门"><el-input v-model="editing.form.department" maxlength="100" /></el-form-item>
          <el-form-item label="制度编号"><el-input v-model="editing.form.doc_no" maxlength="100" /></el-form-item>
          <el-form-item label="版本"><el-input v-model="editing.form.version" maxlength="50" /></el-form-item>
          <el-form-item label="状态">
            <el-select v-model="editing.form.status" style="width: 100%">
              <el-option v-for="(meta, value) in DOC_STATUS" :key="value" :label="meta.label" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="生效日期">
            <el-date-picker v-model="editing.form.effective_from" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="废止日期">
            <el-date-picker v-model="editing.form.effective_to" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="标签">
            <el-input v-model="editing.form.tags" placeholder="逗号分隔" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" size="small" :loading="editing.saving" @click="saveEdit">保存</el-button>
            <el-button size="small" @click="editing.active = false">取消</el-button>
          </el-form-item>
        </el-form>

        <el-descriptions v-else :column="2" size="small" border class="meta">
          <el-descriptions-item label="分类">{{ detail.category || '—' }}</el-descriptions-item>
          <el-descriptions-item label="部门">{{ detail.department || '—' }}</el-descriptions-item>
          <el-descriptions-item label="生效日期">{{ detail.effective_from || '—' }}</el-descriptions-item>
          <el-descriptions-item label="废止日期">{{ detail.effective_to || '—' }}</el-descriptions-item>
          <el-descriptions-item label="责任人">{{ detail.owner || '—' }}</el-descriptions-item>
          <el-descriptions-item label="索引">{{ indexStatusMeta(detail.index_status).label }} · {{ docIndexSummary(detail) }}</el-descriptions-item>
          <el-descriptions-item label="原件" :span="2">{{ detail.source_name || '粘贴创建（无原件）' }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.ocr_pages" label="OCR" :span="2">
            含 OCR {{ detail.ocr_pages }} 页，扫描识别结果建议核对
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.index_error" label="索引提示" :span="2">
            {{ detail.index_error }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="context"
          type="success"
          :closable="true"
          class="context-alert"
          :title="`命中位置：${context.heading || '正文'}${context.page_no ? ' · 第 ' + context.page_no + ' 页' : ''}`"
          @close="context = null"
        />
        <div v-if="context" class="context">
          <div v-for="item in context.items" :key="item.chunk_id" class="context-block" :class="{ hit: item.is_hit }">
            <div class="context-heading">
              {{ item.heading || '正文' }}
              <span v-if="item.page_no"> · 第 {{ item.page_no }} 页</span>
            </div>
            <div class="context-text">{{ item.text }}</div>
          </div>
        </div>

        <div class="section-title">章节大纲（{{ detail.outline?.length || 0 }} 块）</div>
        <div class="outline">
          <div
            v-for="item in detail.outline"
            :key="item.chunk_id"
            class="outline-item"
            :class="{ active: context && context.items?.some((row) => row.chunk_id === item.chunk_id) }"
            @click="openChunk(item)"
          >
            <span class="outline-heading">{{ item.heading || '（无标题段）' }}</span>
            <span v-if="item.page_no" class="outline-page">P{{ item.page_no }}</span>
            <span class="outline-preview">{{ item.preview }}</span>
          </div>
        </div>

        <div v-if="detail.versions?.length > 1" class="section-title">版本链</div>
        <div v-if="detail.versions?.length > 1" class="versions">
          <div v-for="item in detail.versions" :key="item.id" class="version-row" :class="{ current: item.is_current }">
            <el-tag size="small" :type="statusMeta(item.status).type">{{ statusMeta(item.status).label }}</el-tag>
            <span class="version-name">{{ item.version }}</span>
            <span class="doc-sub">{{ item.effective_from || '未填生效日' }} → {{ item.effective_to || '至今' }}</span>
          </div>
        </div>
      </template>
      <el-empty v-else-if="!loading" description="未选择文档" />
    </div>
  </el-drawer>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { knowledgeApi } from '../../api'
import { saveBlob } from '../../utils/download'
import {
  DOC_CATEGORIES,
  DOC_STATUS,
  canDownload,
  docIndexSummary,
  indexStatusMeta,
  parseTags,
  statusMeta,
} from '../../utils/knowledge'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  docId: { type: Number, default: null },
  chunkId: { type: Number, default: null },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const detail = ref(null)
const context = ref(null)
const loading = ref(false)
const editing = reactive({
  active: false,
  saving: false,
  form: {},
})

// 请求序号：切换文档/章节时丢弃过期响应
let detailSeq = 0
let chunkSeq = 0

async function load() {
  if (!props.docId) {
    // 无文档：作废在途 chunk，避免关闭/清空后旧响应写回上下文
    chunkSeq += 1
    return
  }
  const seq = ++detailSeq
  // 切换文档即作废所有在途 chunk 请求；chunkGate 记录本次 load 开始时的章节序号，
  // 若用户在此期间手点了大纲 chunk（chunkSeq 已被推进），返回时不得用旧 prop 覆盖
  const chunkGate = ++chunkSeq
  loading.value = true
  context.value = null
  try {
    const { data } = await knowledgeApi.doc(props.docId)
    if (seq !== detailSeq) return
    detail.value = data
    if (props.chunkId && chunkSeq === chunkGate) {
      await loadChunk(props.chunkId)
    }
  } catch {
    if (seq === detailSeq) detail.value = null
  } finally {
    if (seq === detailSeq) loading.value = false
  }
}

async function loadChunk(chunkId) {
  const seq = ++chunkSeq
  try {
    const { data } = await knowledgeApi.context(props.docId, chunkId, { before: 1, after: 1 })
    if (seq !== chunkSeq) return
    context.value = data
  } catch {
    if (seq === chunkSeq) context.value = null
  }
}

function openChunk(item) {
  loadChunk(item.chunk_id)
}

function startEdit() {
  editing.form = {
    title: detail.value.title,
    category: detail.value.category,
    department: detail.value.department,
    doc_no: detail.value.doc_no,
    version: detail.value.version,
    status: detail.value.status,
    effective_from: detail.value.effective_from,
    effective_to: detail.value.effective_to,
    tags: (detail.value.tags || []).join(','),
  }
  editing.active = true
}

async function saveEdit() {
  editing.saving = true
  try {
    await knowledgeApi.update(props.docId, {
      title: editing.form.title,
      category: editing.form.category,
      department: editing.form.department,
      doc_no: editing.form.doc_no,
      version: editing.form.version,
      status: editing.form.status,
      effective_from: editing.form.effective_from || '',
      effective_to: editing.form.effective_to || '',
      tags: parseTags(editing.form.tags),
    })
    ElMessage.success('已保存')
    editing.active = false
    emit('changed')
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    editing.saving = false
  }
}

async function download() {
  try {
    const { data } = await knowledgeApi.file(props.docId)
    saveBlob(data, detail.value?.source_name || `${detail.value?.title || 'document'}.bin`)
  } catch {
    /* 拦截器已提示 */
  }
}

watch(
  () => [props.modelValue, props.docId, props.chunkId],
  ([visible, docId]) => {
    if (visible && docId) {
      editing.active = false
      load()
      return
    }
    // 关闭抽屉 / 清空文档：作废在途 chunk，避免关闭后仍把上下文写回
    chunkSeq += 1
    context.value = null
  },
  { immediate: true },
)
</script>

<style scoped>
.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.detail-title {
  font-size: 18px;
  font-weight: 600;
}

.detail-sub {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.detail-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.meta,
.edit-form {
  margin-top: 14px;
}

.context-alert {
  margin-top: 14px;
}

.context {
  margin-top: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  overflow: hidden;
}

.context-block {
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.context-block:last-child {
  border-bottom: none;
}

.context-block.hit {
  background: var(--el-color-primary-light-9);
}

.context-heading {
  color: var(--el-color-primary);
  font-size: 12px;
  margin-bottom: 4px;
}

.context-text {
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 13px;
}

.section-title {
  margin: 18px 0 8px;
  font-weight: 600;
}

.outline {
  max-height: 280px;
  overflow: auto;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.outline-item {
  padding: 8px 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 8px;
}

.outline-item:hover,
.outline-item.active {
  background: var(--el-color-primary-light-9);
}

.outline-heading {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.outline-page {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.outline-preview {
  grid-column: 1 / -1;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.versions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.version-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.version-row.current {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}

.version-name {
  font-weight: 600;
}

.doc-sub {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
