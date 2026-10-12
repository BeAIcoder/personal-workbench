<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>数据集清单</span>
          <el-button size="small" type="primary" @click="uploadDialog = true">上传入仓</el-button>
        </div>
      </template>
      <el-table :data="items" size="small" empty-text="暂无数据集，点右上角上传 xlsx/csv">
        <el-table-column prop="table" label="表名" min-width="140" />
        <el-table-column prop="source" label="来源" min-width="160" show-overflow-tooltip />
        <el-table-column prop="rows" label="行数" width="90" align="right" />
        <el-table-column label="列数" width="80" align="right">
          <template #default="{ row }">{{ (row.columns || []).length }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="170" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="preview(row)">预览</el-button>
            <el-button size="small" link type="primary" @click="useInQuery(row.table)">查询</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="uploadDialog" title="上传文件入仓" width="480px">
      <el-form label-width="80px">
        <el-form-item label="表名">
          <el-input v-model="form.table" placeholder="小写字母/数字/下划线，如 t_expense" />
        </el-form-item>
        <el-form-item label="模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="replace">整表重建</el-radio>
            <el-radio value="append">追加</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文件">
          <input type="file" accept=".xlsx,.csv" @change="onFile" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!canSubmit" @click="submitUpload">入仓</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="previewDialog" :title="`预览：${previewData.table}`" width="760px">
      <el-table :data="previewData.rows" size="small" max-height="420" border>
        <el-table-column v-for="c in previewData.columns" :key="c" :prop="c" :label="c" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const router = useRouter()
const loading = ref(false)
const items = ref([])

const uploadDialog = ref(false)
const submitting = ref(false)
const form = reactive({ table: '', mode: 'replace', file: null })

const canSubmit = computed(() => form.table.trim() && form.file)

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/vault/tables')
    items.value = data.items || []
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function onFile(e) {
  form.file = e.target.files?.[0] || null
}

async function submitUpload() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('file', form.file)
    const { data } = await api.post(
      `/finance/vault/ingest/file?table=${encodeURIComponent(form.table.trim())}&mode=${form.mode}`,
      fd,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    ElMessage.success(`入仓成功：${data.rows} 行`)
    uploadDialog.value = false
    form.table = ''
    form.file = null
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '入仓失败')
  } finally {
    submitting.value = false
  }
}

const previewDialog = ref(false)
const previewData = reactive({ table: '', columns: [], rows: [] })

async function preview(row) {
  try {
    const { data } = await api.get(`/finance/vault/tables/${encodeURIComponent(row.table)}/preview`)
    previewData.table = data.table
    previewData.columns = data.columns
    previewData.rows = data.rows
    previewDialog.value = true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '预览失败')
  }
}

function useInQuery(table) {
  router.push({ name: 'finance-vault-query', query: { table } })
}

onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
