<template>
  <el-dialog
    v-model="visible"
    title="导入经营管理报表"
    width="720px"
    :close-on-click-modal="false"
    @closed="resetForm"
  >
    <el-upload
      ref="uploadRef"
      v-model:file-list="fileList"
      drag
      :auto-upload="false"
      accept=".xlsx"
      :limit="1"
      :on-change="onFileChange"
      :on-remove="onFileRemove"
      :on-exceed="onExceed"
    >
      <div class="el-upload__text">将 .xlsx 文件拖到此处，或 <em>点击选择</em></div>
      <template #tip>
        <div class="el-upload__tip">仅支持 .xlsx；单文件 ≤ 10MB；同项目同期间重复导入会整批覆盖。</div>
      </template>
    </el-upload>

    <div class="actions">
      <el-button size="small" type="primary" :loading="previewing" :disabled="!selectedFile" @click="previewFile">
        预览
      </el-button>
      <span class="hint">{{ selectedFile ? selectedFile.name : '请选择文件后预览' }}</span>
    </div>

    <template v-if="preview">
      <el-divider />
      <el-descriptions :column="2" size="small" border>
        <el-descriptions-item label="报表类型">{{ reportTypeLabel }}</el-descriptions-item>
        <el-descriptions-item label="识别项目">
          <el-select v-model="targetProject" size="small" filterable allow-create clearable placeholder="项目" style="width: 100%">
            <el-option v-for="item in projectOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-descriptions-item>
        <el-descriptions-item label="期间">
          <el-date-picker
            v-model="targetPeriod"
            type="month"
            value-format="YYYY-MM"
            size="small"
            placeholder="YYYY-MM"
            style="width: 100%"
          />
        </el-descriptions-item>
        <el-descriptions-item label="来源文件">{{ preview.source_file || selectedFile?.name || '—' }}</el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="preview.replaces_report_id"
        class="mt8"
        type="warning"
        :closable="false"
        show-icon
        :title="`将覆盖已有批次 #${preview.replaces_report_id}，原数据整批替换`"
      />
      <el-alert
        v-if="preview.warnings && preview.warnings.length"
        class="mt8"
        type="warning"
        :closable="false"
        :title="`识别提示（${preview.warnings.length}）`"
      >
        <div v-for="(warning, index) in preview.warnings" :key="index">{{ warning }}</div>
      </el-alert>

      <el-table :data="preview.sections || []" size="small" border class="mt8" empty-text="未识别到小节">
        <el-table-column prop="label" label="小节" min-width="180" show-overflow-tooltip />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ kindLabel(row.kind) }}</template>
        </el-table-column>
        <el-table-column prop="count" label="识别条数" width="100" align="right" />
      </el-table>
    </template>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!preview?.token" :loading="confirming" @click="confirmImport">
        确认导入
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  reportType: { type: String, default: 'control' },
  project: { type: String, default: '' },
  meta: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'imported'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const uploadRef = ref(null)
const fileList = ref([])
const preview = ref(null)
const previewing = ref(false)
const confirming = ref(false)
const targetProject = ref('')
const targetPeriod = ref('')

const reportTypeLabel = computed(() => {
  const found = (props.meta?.report_types || []).find((item) => item.value === props.reportType)
  return found?.label || props.reportType
})
const projectOptions = computed(() => {
  const found = (props.meta?.report_types || []).find((item) => item.value === props.reportType)
  const declared = Array.isArray(found?.projects) ? found.projects : []
  return [...new Set([...declared, props.project].filter(Boolean))]
})
const selectedFile = computed(() => fileList.value[0]?.raw || null)

function kindLabel(kind) {
  return { metric: '指标', record: '记录', text: '长文本' }[kind] || kind || '—'
}

function resetForm() {
  preview.value = null
  previewing.value = false
  confirming.value = false
  targetProject.value = props.project || ''
  targetPeriod.value = ''
  fileList.value = []
  uploadRef.value?.clearFiles?.()
}

function validateFile(file) {
  if (!file) return false
  if (!/\.xlsx$/i.test(file.name || '')) {
    ElMessage.warning('仅支持 .xlsx 文件')
    return false
  }
  if (Number(file.size || 0) > 10 * 1024 * 1024) {
    ElMessage.warning('单个文件不能超过 10MB')
    return false
  }
  return true
}

function onFileChange(file, files) {
  preview.value = null
  const current = files?.[0] || file
  if (!validateFile(current)) {
    fileList.value = []
    return
  }
  fileList.value = current ? [current] : []
}

function onFileRemove() {
  preview.value = null
}

function onExceed(files) {
  const raw = files?.[0]
  if (!raw) return
  uploadRef.value?.clearFiles?.()
  preview.value = null
  if (!validateFile(raw)) {
    fileList.value = []
    return
  }
  fileList.value = [{
    name: raw.name,
    size: raw.size,
    raw,
    uid: `${Date.now()}`,
    status: 'ready',
  }]
}

async function previewFile() {
  if (!selectedFile.value || previewing.value) return
  previewing.value = true
  try {
    const form = new FormData()
    form.append('file', selectedFile.value)
    form.append('report_type', props.reportType)
    const { data } = await api.post('/business/manage/imports/preview', form, { timeout: 120000 })
    preview.value = data || null
    targetProject.value = props.project || data?.project || targetProject.value || ''
    targetPeriod.value = data?.period || targetPeriod.value || ''
  } catch {
    preview.value = null
  } finally {
    previewing.value = false
  }
}

async function confirmImport() {
  if (!preview.value?.token || confirming.value) return
  if (!targetProject.value || !targetPeriod.value) {
    ElMessage.warning('请确认项目和期间')
    return
  }
  confirming.value = true
  try {
    const { data } = await api.post('/business/manage/imports/confirm', {
      token: preview.value.token,
      project: targetProject.value,
      period: targetPeriod.value,
    })
    ElMessage.success(data?.created === false ? '已覆盖同项目同期间批次' : '导入完成')
    emit('imported', data)
    visible.value = false
  } catch {
    /* 拦截器已提示 */
  } finally {
    confirming.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) resetForm()
  },
)
</script>

<style scoped>
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.mt8 {
  margin-top: 8px;
}
</style>
