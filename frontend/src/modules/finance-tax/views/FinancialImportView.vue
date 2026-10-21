<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span>财务报表导入（Excel）</span></template>
      <div class="actions">
        <el-button size="small" @click="downloadTemplate">下载标准模板</el-button>
        <el-button size="small" @click="downloadTemplate('balance-sheet')">资产负债表模板</el-button>
        <el-button size="small" @click="downloadTemplate('income-statement')">利润表模板</el-button>
      </div>
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".xlsx,.xls"
        :on-change="onChange"
        :on-remove="onRemove"
        :file-list="fileList"
      >
        <div class="upload-hint">将财务报表 Excel 拖到此处，或<em>点击选择</em></div>
      </el-upload>
      <el-button type="primary" :loading="importing" :disabled="!file" style="margin-top: 12px" @click="doImport">
        开始解析导入
      </el-button>
      <p class="hint">支持：标准导入模板（下载后填写）/ 财务系统导出的 Excel。解析成功会自动创建或更新企业与其财务数据。</p>
    </el-card>

    <el-card v-if="result" shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="card-head">
          <span>导入结果</span>
          <el-tag :type="result.success ? 'success' : 'danger'" size="small">{{ result.success ? '成功' : '失败' }}</el-tag>
        </div>
      </template>
      <p>{{ result.summary }}</p>
      <template v-if="result.errors?.length">
        <h4>错误（{{ result.errors.length }}）</h4>
        <el-alert v-for="(e, i) in result.errors.slice(0, 10)" :key="i" :title="JSON.stringify(e)" type="error" :closable="false" style="margin-bottom: 6px" />
      </template>
      <template v-if="result.warnings?.length">
        <h4>警告（{{ result.warnings.length }}）</h4>
        <el-alert v-for="(w, i) in result.warnings.slice(0, 10)" :key="i" :title="JSON.stringify(w)" type="warning" :closable="false" style="margin-bottom: 6px" />
      </template>
      <template v-if="result.taxable_income_estimate">
        <h4>应纳税所得额估算</h4>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item v-for="(v, k) in result.taxable_income_estimate" :key="k" :label="k">{{ v }}</el-descriptions-item>
        </el-descriptions>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const file = ref(null)
const fileList = ref([])
const importing = ref(false)
const result = ref(null)

function onChange(uploadFile) {
  file.value = uploadFile.raw
  fileList.value = [uploadFile]
}

function onRemove() {
  file.value = null
  fileList.value = []
}

function downloadTemplate(kind = '') {
  window.open(`/api/finance/tax/financial-import/template${kind ? '/' + kind : ''}`, '_blank')
}

async function doImport() {
  if (!file.value) return
  importing.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    const { data } = await api.post('/finance/tax/financial-import/import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    result.value = data
    ElMessage.success(data.success ? '导入成功' : '导入完成，但存在问题，见下方明细')
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.actions {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.upload-hint {
  padding: 20px;
  color: #606266;
}

.hint {
  color: #909399;
  font-size: 13px;
}
</style>
