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
            <el-button size="small" link type="primary" :loading="previewing" @click="preview(row)">预览</el-button>
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
            <el-radio value="append">追加</el-radio>
            <el-radio value="replace">整表重建</el-radio>
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
      <el-table :data="previewRows" size="small" max-height="420" border>
        <el-table-column v-for="(c, i) in previewData.columns" :key="'c' + i" :prop="'c' + i" :label="c" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'
import { takeFile } from '../../_shared/fileInput'
import { toTableRows } from '../tableRows'

const router = useRouter()
const loading = ref(false)
const items = ref([])

const uploadDialog = ref(false)
const submitting = ref(false)
// 默认追加：整表重建会清空仓里已有的同名表，必须是用户的显式选择
const form = reactive({ table: '', mode: 'append', file: null })

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
  form.file = takeFile(e)
}

async function submitUpload() {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  try {
    // 整表重建且仓里已有同名表时必须明确确认：取消则直接返回，不发上传请求
    if (form.mode === 'replace' && items.value.some((t) => t.table === form.table.trim())) {
      await ElMessageBox.confirm(
        `表「${form.table.trim()}」已存在，整表重建将覆盖现有数据，确定继续吗？`,
        '覆盖确认',
        { type: 'warning' },
      )
    }
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
    // 模式随表单一并复位：否则下次打开弹窗仍预选破坏性 replace，
    // 「整表重建必须是显式选择」的默认值只在组件生命周期内第一次生效
    form.mode = 'append'
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    submitting.value = false
  }
}

const previewDialog = ref(false)
const previewData = reactive({ table: '', columns: [], rows: [] })
const previewing = ref(false)
// 预览请求序号：连点两行时丢弃慢的旧响应，避免对话框展示用户最后没有选择的数据集
let previewSeq = 0

// 预览行按列序扁平化为安全键：列名含点号时 el-table 的 prop 会被当作深层路径
const previewRows = computed(() => toTableRows(previewData.columns, previewData.rows))

async function preview(row) {
  const seq = ++previewSeq
  previewing.value = true
  try {
    const { data } = await api.get(`/finance/vault/tables/${encodeURIComponent(row.table)}/preview`)
    if (seq !== previewSeq) return
    previewData.table = data.table
    previewData.columns = data.columns
    previewData.rows = data.rows
    previewDialog.value = true
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    if (seq === previewSeq) previewing.value = false
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
