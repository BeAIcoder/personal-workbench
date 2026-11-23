<template>
  <div class="doc-library">
    <div class="toolbar">
      <el-input
        v-model="filters.q"
        placeholder="搜索标题 / 制度编号 / 摘要"
        clearable
        class="search-input"
        @input="debouncedLoad"
        @clear="load(1)"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="filters.category" clearable placeholder="全部分类" class="filter-select" @change="load(1)">
        <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
      </el-select>
      <el-select v-model="filters.status" clearable placeholder="默认：现行有效" class="filter-select" @change="load(1)">
        <el-option v-for="(meta, value) in DOC_STATUS" :key="value" :label="meta.label" :value="value" />
      </el-select>
      <el-checkbox v-model="filters.include_inactive" @change="load(1)">含历史版本</el-checkbox>
      <div class="spacer"></div>
      <el-button size="small" @click="openSettings">
        <el-icon><Setting /></el-icon>&nbsp;知识库设置
      </el-button>
      <el-button size="small" type="primary" @click="openUpload">
        <el-icon><Upload /></el-icon>&nbsp;上传文档
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" size="small" empty-text="暂无文档，先上传一份制度或手册">
      <el-table-column label="标题" min-width="240">
        <template #default="{ row }">
          <div class="doc-title">{{ row.title }}</div>
          <div class="doc-sub">{{ row.doc_no || row.source_name || '粘贴创建' }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="category" label="分类" width="100" />
      <el-table-column prop="department" label="部门" width="100" show-overflow-tooltip />
      <el-table-column prop="version" label="版本" width="80" />
      <el-table-column label="状态" width="96">
        <template #default="{ row }">
          <el-tag size="small" :type="statusMeta(row.status).type">{{ statusMeta(row.status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="effective_from" label="生效日期" width="100">
        <template #default="{ row }">{{ row.effective_from || '—' }}</template>
      </el-table-column>
      <el-table-column label="索引" width="164">
        <template #default="{ row }">
          <el-tag size="small" :type="indexStatusMeta(row.index_status).type">
            {{ indexStatusMeta(row.index_status).label }}
          </el-tag>
          <span class="doc-sub">&nbsp;{{ docIndexSummary(row) }}</span>
          <span v-if="row.ocr_pages" class="doc-sub ocr-note">&nbsp;· OCR {{ row.ocr_pages }} 页</span>
        </template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="150" />
      <el-table-column label="操作" width="210" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="openDoc(row)">查看</el-button>
          <el-button v-if="canDownload(row)" size="small" link type="primary" @click="download(row)">下载</el-button>
          <el-button size="small" link type="warning" @click="reindex(row)">重解析</el-button>
          <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        layout="total, prev, pager, next"
        small
        :total="total"
        v-model:current-page="page"
        :page-size="pageSize"
        @change="load()"
      />
    </div>

    <el-dialog v-model="upload.visible" title="上传制度 / 手册" width="640px">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        :file-list="upload.fileList"
        accept=".pdf,.docx,.doc,.pptx,.ppt,.md,.txt"
        :on-change="onFileChange"
        :on-remove="onFileRemove"
        :on-exceed="onFileExceed"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到这里，或点击选择</div>
        <template #tip>
          <div class="el-upload__tip">
            支持 PDF / Word / PPT / Markdown / 文本，单文件 ≤ 20MB。
          </div>
          <div class="ocr-limit-tip">
            扫描件需先在「知识库设置」开启 OCR；单文档最多识别 50 页，超出会截断，请拆分后上传。
          </div>
        </template>
      </el-upload>

      <el-form label-width="92px" class="meta-form">
        <el-form-item label="标题">
          <el-input v-model="upload.form.title" maxlength="200" placeholder="留空则用文件名" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="upload.form.category" allow-create filterable style="width: 100%">
            <el-option v-for="c in DOC_CATEGORIES" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-input v-model="upload.form.department" maxlength="100" placeholder="如：财务部 / 行政部" />
        </el-form-item>
        <el-form-item label="制度编号">
          <el-input v-model="upload.form.doc_no" maxlength="100" placeholder="可选；同编号视为同一制度" />
        </el-form-item>
        <el-form-item label="版本">
          <el-input v-model="upload.form.version" maxlength="50" placeholder="如 v1.0 / 2026版" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="upload.form.status" style="width: 100%">
            <el-option v-for="(meta, value) in DOC_STATUS" :key="value" :label="meta.label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="生效日期">
          <el-date-picker
            v-model="upload.form.effective_from"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="upload.form.tags" placeholder="逗号分隔，如：差旅,报销" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="upload.visible = false">取消</el-button>
        <el-button type="primary" :loading="upload.saving" @click="submitUpload">上传并建索引</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="settings.visible" title="知识库设置（OCR / 向量）" width="640px">
      <el-form label-width="132px">
        <el-form-item label="扫描件 OCR">
          <el-switch v-model="settings.form.ocr_enabled" :disabled="!settings.ocrAvailable" />
          <span class="doc-sub">
            &nbsp;{{ settings.ocrAvailable ? '仅对无文本层的 PDF 页识别' : '未安装 rapidocr，OCR 不可用' }}
          </span>
        </el-form-item>
      </el-form>
      <div v-if="settings.ocrAvailable" class="ocr-limit-tip ocr-limit-indent">
        单文档最多 OCR 50 页；超出会截断，请拆分后上传。
      </div>
      <el-divider />
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="向量检索是可选增强：未启用时只用关键词（FTS5），不影响制度检索与 AI 引用。"
        style="margin-bottom: 12px"
      />
      <el-form label-width="132px">
        <el-form-item label="启用向量">
          <el-switch v-model="settings.form.embedding_enabled" />
        </el-form-item>
        <el-form-item label="Embedding 模型">
          <el-input v-model="settings.form.embedding_model" placeholder="如 text-embedding-3-small / bge-m3" />
        </el-form-item>
        <el-form-item label="接口地址">
          <el-input v-model="settings.form.embedding_base_url" placeholder="留空 = 跟随当前激活模型供应商" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="settings.form.embedding_api_key"
            type="password"
            show-password
            :placeholder="keyPlaceholder"
          />
          <div class="doc-sub key-hint">
            自定义向量接口地址必须单独填写该地址的 API Key（不会使用聊天模型 Key）。
          </div>
        </el-form-item>
        <el-form-item label="向量维度">
          <el-input-number v-model="settings.form.embedding_dim" :min="0" :max="4096" />
          <span class="doc-sub">&nbsp;0 = 首次调用自动探测</span>
        </el-form-item>
      </el-form>
      <el-alert
        v-if="settings.resolvedDetail"
        type="warning"
        :closable="false"
        show-icon
        title="向量检索当前不可用"
        :description="settings.resolvedDetail"
        style="margin-bottom: 12px"
      />
      <div class="settings-actions">
        <el-button :loading="settings.testing" @click="testEmbedding">测试连接</el-button>
        <el-button :loading="settings.rebuildingVectors" @click="rebuildVectors">重建向量索引</el-button>
        <span class="doc-sub">{{ settings.hint }}</span>
      </div>
      <template #footer>
        <el-button @click="settings.visible = false">取消</el-button>
        <el-button type="primary" :loading="settings.saving" @click="saveSettings">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Setting, Upload, UploadFilled } from '@element-plus/icons-vue'
import { knowledgeApi, toMessage } from '../../api'
import { saveBlob } from '../../utils/download'
import {
  DOC_CATEGORIES,
  DOC_STATUS,
  buildUploadForm,
  canDownload,
  docIndexSummary,
  indexStatusMeta,
  statusMeta,
} from '../../utils/knowledge'

const emit = defineEmits(['open-doc'])

const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const categoryOptions = ref([...DOC_CATEGORIES])
const filters = reactive({ q: '', category: '', status: '', include_inactive: false })

const upload = reactive({
  visible: false,
  saving: false,
  file: null,
  fileList: [],
  form: {
    title: '',
    category: '制度规范',
    department: '',
    doc_no: '',
    version: 'v1.0',
    status: 'effective',
    effective_from: '',
    tags: '',
  },
})

const settings = reactive({
  visible: false,
  saving: false,
  testing: false,
  rebuildingVectors: false,
  ocrAvailable: true,
  hasKey: false,
  hint: '',
  resolved: true,
  resolvedDetail: '',
  form: {
    ocr_enabled: false,
    embedding_enabled: false,
    embedding_model: '',
    embedding_base_url: '',
    embedding_api_key: '',
    embedding_dim: 0,
  },
})

// 自定义向量接口地址必须填该地址的专用 Key（后端 embedding.py::CUSTOM_URL_KEY_REQUIRED）：
// 留空只在沿用当前激活供应商地址时才成立，换自定义地址还会清空旧 Key。
const keyPlaceholder = computed(() => {
  if (settings.form.embedding_base_url.trim()) {
    return '自定义地址必须填写专用 Key（不会使用聊天模型 Key）'
  }
  return settings.hasKey ? '已保存，留空保持不变' : '留空 = 跟随当前激活模型供应商'
})

// 请求序号：筛选/翻页连点时丢弃过期响应（A 慢 B 快只显示 B）
let loadSeq = 0

async function load(nextPage = page.value) {
  const seq = ++loadSeq
  page.value = nextPage
  loading.value = true
  try {
    const { data } = await knowledgeApi.docs({
      q: filters.q.trim(),
      category: filters.category,
      status: filters.status,
      include_inactive: filters.include_inactive,
      page: page.value,
      page_size: pageSize.value,
    })
    if (seq !== loadSeq) return
    rows.value = data.items || []
    total.value = data.total || 0
  } catch {
    if (seq === loadSeq) rows.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

let facetsSeq = 0

async function loadFacets() {
  const seq = ++facetsSeq
  try {
    const { data } = await knowledgeApi.facets()
    if (seq !== facetsSeq) return
    const values = (data.categories || []).map((item) => item.value)
    categoryOptions.value = Array.from(new Set([...DOC_CATEGORIES, ...values]))
  } catch {
    /* 筛选器加载失败不阻断列表 */
  }
}

let timer = null
function debouncedLoad() {
  clearTimeout(timer)
  timer = setTimeout(() => load(1), 300)
}
onBeforeUnmount(() => clearTimeout(timer))

function openDoc(row) {
  emit('open-doc', { docId: row.id, chunkId: null })
}

async function download(row) {
  try {
    const { data } = await knowledgeApi.file(row.id)
    saveBlob(data, row.source_name || `${row.title}.bin`)
  } catch {
    /* 拦截器已提示 */
  }
}

async function reindex(row) {
  try {
    await ElMessageBox.confirm(`重新解析《${row.title}》并重建索引？`, '重解析确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await knowledgeApi.reindex(row.id)
    ElMessage.success('已重解析并重建索引')
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除《${row.title}》？块、索引与原件会一并删除。`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await knowledgeApi.remove(row.id)
    ElMessage.success('已删除')
    await Promise.all([load(), loadFacets()])
  } catch {
    /* 拦截器已提示 */
  }
}

function openUpload() {
  upload.file = null
  upload.fileList = []
  upload.form.title = ''
  upload.form.category = '制度规范'
  upload.form.department = ''
  upload.form.doc_no = ''
  upload.form.version = 'v1.0'
  upload.form.status = 'effective'
  upload.form.effective_from = ''
  upload.form.tags = ''
  upload.visible = true
}

function onFileChange(file) {
  upload.file = file.raw || null
  upload.fileList = [file]
  if (!upload.form.title && file.name) {
    upload.form.title = file.name.replace(/\.[^.]+$/, '')
  }
}

function onFileRemove() {
  upload.file = null
  upload.fileList = []
}

function onFileExceed() {
  ElMessage.warning('一次只能上传一个文件，请先移除已选文件')
}

async function submitUpload() {
  if (!upload.file) {
    ElMessage.warning('请先选择文件')
    return
  }
  upload.saving = true
  try {
    const { data } = await knowledgeApi.upload(buildUploadForm(upload.file, upload.form))
    ElMessage.success('已入库并建立索引')
    if (data.index_status === 'embedding') {
      ElMessage.info('向量化在后台进行，稍后刷新可看到结果')
    }
    upload.visible = false
    await Promise.all([load(1), loadFacets()])
  } catch {
    /* 拦截器已提示；重复文件会返回 409 */
  } finally {
    upload.saving = false
  }
}

async function openSettings() {
  settings.visible = true
  settings.hint = ''
  try {
    const { data } = await knowledgeApi.settings()
    settings.form.ocr_enabled = Boolean(data.ocr_enabled)
    settings.ocrAvailable = Boolean(data.ocr_available)
    settings.form.embedding_enabled = Boolean(data.embedding_enabled)
    settings.form.embedding_model = data.embedding_model || ''
    settings.form.embedding_base_url = data.embedding_base_url || ''
    settings.form.embedding_api_key = ''
    settings.form.embedding_dim = Number(data.embedding_dim || 0)
    settings.hasKey = Boolean(data.has_api_key)
    // 后端 resolve_config_with_reason 的原因：缺专用 Key / 地址非法 / 未启用等
    settings.resolved = data.resolved !== false
    settings.resolvedDetail = data.resolved === false ? (data.resolved_detail || '') : ''
    settings.hint = data.vector_ready ? 'sqlite-vec 已就绪' : '当前环境未加载 sqlite-vec，向量检索不可用'
  } catch {
    /* 拦截器已提示 */
  }
}

async function saveSettings() {
  settings.saving = true
  try {
    const payload = {
      ocr_enabled: settings.form.ocr_enabled,
      embedding_enabled: settings.form.embedding_enabled,
      embedding_model: settings.form.embedding_model.trim(),
      embedding_base_url: settings.form.embedding_base_url.trim(),
      embedding_dim: Number(settings.form.embedding_dim || 0),
    }
    if (settings.form.embedding_api_key.trim()) {
      payload.embedding_api_key = settings.form.embedding_api_key.trim()
    }
    const { data } = await knowledgeApi.saveSettings(payload)
    settings.hasKey = Boolean(data.has_api_key)
    settings.resolved = data.resolved !== false
    settings.resolvedDetail = data.resolved === false ? (data.resolved_detail || '') : ''
    // 已提交的明文不再留在输入框/组件状态里（占位提示改由 hasKey 表达）
    settings.form.embedding_api_key = ''
    ElMessage.success('设置已保存')
  } catch {
    /* 拦截器已提示 */
  } finally {
    settings.saving = false
  }
}

async function testEmbedding() {
  settings.testing = true
  settings.hint = ''
  try {
    // 用弹窗当前值测试：用户经常先粘贴 Key 就点测试，尚未保存
    const payload = {
      text: '公司差旅报销制度',
      embedding_model: settings.form.embedding_model.trim(),
      embedding_base_url: settings.form.embedding_base_url.trim(),
      embedding_dim: Number(settings.form.embedding_dim || 0),
    }
    if (settings.form.embedding_api_key.trim()) {
      payload.embedding_api_key = settings.form.embedding_api_key.trim()
    }
    const { data } = await knowledgeApi.testEmbedding(payload)
    settings.hint = `连接成功：维度 ${data.dim}；请打开「启用向量」并点「保存」`
  } catch (error) {
    // 把后端真实原因显示在弹窗里（401/404/模型名错误等），不再只提示“详见上方”
    settings.hint = await toMessage(error)
  } finally {
    settings.testing = false
  }
}

async function rebuildVectors() {
  try {
    await ElMessageBox.confirm(
      '重算全部文档向量？换 embedding 模型或维度后需要执行；文档较多时可能耗时数分钟。',
      '重建向量索引',
      { type: 'warning' },
    )
  } catch {
    return
  }
  settings.rebuildingVectors = true
  try {
    const { data } = await knowledgeApi.rebuild({ with_vectors: true })
    const result = data.vectors || {}
    const processed = Number(result.processed || 0)
    const done = Number(result.done || 0)
    const failed = Number(result.failed || 0)
    const remaining = Number(result.remaining || 0)
    const summary = `处理 ${processed} 篇，成功 ${done} 篇，失败 ${failed} 篇`
    if (remaining > 0) {
      // 后端显式分批/超上限时只处理一部分；不提示会让人误以为剩余文档也有向量
      ElMessage.warning(
        `向量重建未全部完成：${summary}，剩余 ${remaining} 篇未处理；可再次点击「重建向量索引」续跑`,
      )
    } else {
      ElMessage.success(`向量重建完成：${summary}`)
    }
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    settings.rebuildingVectors = false
  }
}

onMounted(() => {
  load(1)
  loadFacets()
})

defineExpose({ reload: () => load() })
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.search-input {
  width: 260px;
}

.filter-select {
  width: 140px;
}

.spacer {
  flex: 1;
}

.doc-title {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.doc-sub {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.key-hint {
  margin-top: 4px;
  line-height: 1.5;
  width: 100%;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.meta-form {
  margin-top: 12px;
}

.settings-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 50 页上限是硬边界：红字加粗，避免用户扫描几百页后才发现被截断 */
.ocr-limit-tip {
  color: var(--el-color-danger);
  font-weight: 700;
  font-size: 12px;
  line-height: 1.6;
  margin-top: 4px;
}

.ocr-limit-indent {
  margin: 0 0 12px 132px;
}
</style>
