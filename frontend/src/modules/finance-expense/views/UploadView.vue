<template>
  <div class="page">
    <el-card shadow="never" v-loading="running" element-loading-text="正在执行费用分类，请稍候...">
      <template #header>
        <div class="card-head">
          <span>费用分类 · 上传执行</span>
          <el-button size="small" @click="loadRules">刷新规则状态</el-button>
        </div>
      </template>

      <el-alert
        v-if="engineError"
        type="error"
        show-icon
        :closable="false"
        :title="engineError"
        class="block"
      />
      <template v-else-if="rules">
        <el-descriptions :column="2" border size="small" class="block">
          <el-descriptions-item label="规则分类数">{{ rules.total_categories }}</el-descriptions-item>
          <el-descriptions-item label="关键词总数">{{ rules.total_keywords }}</el-descriptions-item>
          <el-descriptions-item label="规则来源" :span="2">{{ rules.rules_file }}</el-descriptions-item>
        </el-descriptions>
        <div class="block">
          <el-tag v-for="c in rules.categories" :key="c" size="small" class="cat-tag">{{ c }}</el-tag>
        </div>
      </template>

      <el-upload
        v-model:file-list="fileList"
        class="block"
        drag
        :auto-upload="false"
        :limit="1"
        accept=".xlsx,.xls"
        :on-change="onFileChange"
        :on-remove="onFileRemove"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">拖拽银行存款辅助明细账到此处，或 <em>点击选择文件</em></div>
        <template #tip>
          <div class="el-upload__tip">仅支持 .xlsx/.xls，大小不超过 20MB；点击「执行分类」后立即处理</div>
        </template>
      </el-upload>

      <el-button type="primary" :disabled="!selectedFile || running || !!engineError" @click="runClassify">
        {{ running ? '分类执行中...' : '执行分类' }}
      </el-button>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>最近运行</span>
          <el-button size="small" @click="loadRuns">刷新</el-button>
        </div>
      </template>
      <el-table :data="runs" size="small" v-loading="loadingRuns">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="original_filename" label="原文件名" min-width="200" show-overflow-tooltip />
        <el-table-column prop="total_rows" label="行数" width="70" />
        <el-table-column prop="matched_rows" label="命中" width="70" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="duration_sec" label="耗时(秒)" width="90" />
        <el-table-column label="时间" width="165">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="goResult(row.id)">查看结果</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        class="pager"
        layout="prev, pager, next, total"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="onPage"
      />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const router = useRouter()

const running = ref(false)
const fileList = ref([])
const selectedFile = ref(null)

const rules = ref(null)
const engineError = ref('')

const runs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const loadingRuns = ref(false)

function onFileChange(uploadFile) {
  selectedFile.value = uploadFile.raw
}

function onFileRemove() {
  selectedFile.value = null
}

async function loadRules() {
  try {
    const { data } = await api.get('/finance/expense/rules/summary')
    rules.value = data
    engineError.value = ''
  } catch (err) {
    rules.value = null
    engineError.value = err?.response?.data?.detail || '费用分类引擎不可用'
  }
}

async function loadRuns() {
  loadingRuns.value = true
  try {
    const { data } = await api.get('/finance/expense/runs', { params: { page: page.value, page_size: pageSize } })
    runs.value = data.items
    total.value = data.total
  } catch {
    runs.value = []
  } finally {
    loadingRuns.value = false
  }
}

function onPage(p) {
  page.value = p
  loadRuns()
}

async function runClassify() {
  if (!selectedFile.value) return
  running.value = true
  try {
    const fd = new FormData()
    fd.append('file', selectedFile.value)
    const { data } = await api.post('/finance/expense/classify', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    ElMessage.success(`分类完成：共 ${data.total_rows} 行，命中 ${data.matched_rows} 行`)
    router.push({ name: 'finance-expense-run', params: { id: data.id } })
  } catch {
    // 拦截器已统一提示错误
  } finally {
    running.value = false
  }
}

function goResult(id) {
  router.push({ name: 'finance-expense-run', params: { id } })
}

function statusType(s) {
  return s === 'success' ? 'success' : s === 'failed' ? 'danger' : 'info'
}

function statusText(s) {
  return { success: '成功', failed: '失败', running: '执行中' }[s] || s
}

function formatTime(t) {
  return t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : ''
}

onMounted(() => {
  loadRules()
  loadRuns()
})
</script>

<style scoped>
.page {
  max-width: 960px;
}

.block {
  margin-bottom: 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.cat-tag {
  margin: 0 6px 6px 0;
}

.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
