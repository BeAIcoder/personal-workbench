<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span>财务报表导入（Excel）</span></template>
      <el-form inline>
        <el-form-item label="落库企业">
          <el-select v-model="selectedEnterpriseId" clearable filterable placeholder="不选则按表头企业身份自动匹配" style="width: 320px">
            <el-option
              v-for="e in enterprises"
              :key="e.id"
              :label="e.tax_number ? `${e.name}（${e.tax_number}）` : e.name"
              :value="e.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
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
        {{ selectedEnterpriseId ? '导入并落库到所选企业' : '开始解析导入' }}
      </el-button>
      <p class="hint">
        支持：标准导入模板（下载后填写）/ 财务系统导出的 Excel。
        落库前提：选择上方「落库企业」，或文件表头含企业名称与税号（编制单位 / 纳税人识别号）；
        缺企业身份时仅返回解析结果、不写库，页面会标记「仅解析未落库」。
      </p>
    </el-card>

    <el-card v-if="result" shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="card-head">
          <span>导入结果</span>
          <el-tag :type="outcome.tagType" size="small">{{ outcome.tagText }}</el-tag>
        </div>
      </template>
      <p>{{ result.summary }}</p>
      <p v-if="result.persisted" class="hint">已写入企业 #{{ result.enterprise_id }} 的财务数据，可在统计/申报中取数。</p>
      <p v-else-if="result.success" class="hint warn">
        本次仅解析、未写入财务数据。请选择「落库企业」重新导入，或在 Excel 表头补齐编制单位与纳税人识别号。
      </p>
      <template v-if="result.warnings?.length">
        <h4>警告（{{ result.warnings.length }}）</h4>
        <el-alert v-for="(w, i) in result.warnings.slice(0, 10)" :key="i" :title="w.message || JSON.stringify(w)" type="warning" :closable="false" style="margin-bottom: 6px" />
      </template>
      <template v-if="result.errors?.length">
        <h4>错误（{{ result.errors.length }}）</h4>
        <el-alert v-for="(e, i) in result.errors.slice(0, 10)" :key="i" :title="e.message || JSON.stringify(e)" type="error" :closable="false" style="margin-bottom: 6px" />
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
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { importOutcome, importToastType } from '../importOutcome'

const file = ref(null)
const fileList = ref([])
const importing = ref(false)
const result = ref(null)
const enterprises = ref([])
const selectedEnterpriseId = ref(null)

const outcome = computed(() => importOutcome(result.value))

async function loadEnterprises() {
  try {
    const { data } = await api.get('/finance/tax/enterprises/')
    enterprises.value = data || []
  } catch {
    // 拦截器已提示；企业列表拉不到时仍可按文件表头身份导入
  }
}

function onChange(uploadFile) {
  file.value = uploadFile.raw
  fileList.value = [uploadFile]
}

function onRemove() {
  file.value = null
  fileList.value = []
}

function downloadTemplate(kind = '') {
  window.open(`${api.defaults.baseURL}/finance/tax/financial-import/template${kind ? '/' + kind : ''}`, '_blank')
}

async function doImport() {
  if (!file.value) return
  importing.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    // 选了企业就落库到该企业；否则由后端按表头身份自动匹配（匹配不到仅解析）
    const url = selectedEnterpriseId.value
      ? `/finance/tax/financial-import/import/${selectedEnterpriseId.value}`
      : '/finance/tax/financial-import/import'
    const { data } = await api.post(url, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    result.value = data
    // toast 类型由纯函数统一决定（success 仅代表已落库），避免页面与测试口径分叉
    const toast = importToastType(result.value)
    if (toast === 'success') {
      ElMessage.success('导入成功，财务数据已落库')
    } else if (data.success) {
      ElMessage.warning('解析完成，但未落库：请选择落库企业或补齐表头企业身份后重新导入')
    } else {
      ElMessage.warning('解析失败，见下方明细')
    }
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    importing.value = false
  }
}

onMounted(loadEnterprises)
</script>

<style scoped>
.actions {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.upload-hint {
  padding: 20px;
  color: var(--el-text-color-regular);
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.warn {
  color: var(--el-color-warning);
}
</style>
