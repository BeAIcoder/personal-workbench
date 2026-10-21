<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>只读 SQL 查询</span>
          <el-button size="small" type="primary" :loading="loading" @click="run">执行</el-button>
        </div>
      </template>
      <el-input
        v-model="sql"
        type="textarea"
        :rows="6"
        placeholder="SELECT ... FROM 已注册表名（仅只读查询；结果自动限流 1000 行）"
      />
      <p class="hint">可从 <router-link to="/finance/vault">表清单</router-link> 选择表名；仅允许引用已注册数据集。</p>
    </el-card>

    <el-card v-if="result" shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="card-head">
          <span>结果：{{ result.row_count }} 行{{ result.truncated ? '（已截断）' : '' }} · {{ result.elapsed_ms }} ms</span>
          <el-button size="small" @click="exportCsv">导出 CSV</el-button>
        </div>
      </template>
      <el-table :data="tableRows" size="small" max-height="480" border>
        <el-table-column v-for="(c, i) in result.columns" :key="'c' + i" :prop="'c' + i" :label="c" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { toTableRows } from '../tableRows'

const route = useRoute()
const loading = ref(false)
const sql = ref('')
const result = ref(null)

const tableRows = computed(() => toTableRows(result.value?.columns, result.value?.rows))

async function run() {
  if (!sql.value.trim()) return
  loading.value = true
  try {
    const { data } = await api.post('/finance/vault/query', { sql: sql.value })
    result.value = data
  } catch {
    result.value = null
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  if (!result.value) return
  const esc = (v) => `"${String(v ?? '').replaceAll('"', '""')}"`
  const lines = [result.value.columns.map(esc).join(',')]
  for (const row of result.value.rows) lines.push(row.map(esc).join(','))
  const blob = new Blob(['\uFEFF' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `vault_query_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

// URL 参数拼接 SQL 前的表名白名单校验：只允许安全的标识符
const TABLE_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

onMounted(() => {
  const table = route.query.table
  if (typeof table === 'string' && TABLE_NAME_RE.test(table)) {
    sql.value = `SELECT * FROM ${table} LIMIT 100`
    return
  }
  sql.value = 'SELECT * FROM '
  if (table) ElMessage.warning(`表名「${table}」不合法，已填入默认模板`)
})
</script>

<style scoped>
.hint {
  margin: 10px 0 0;
  color: #909399;
  font-size: 13px;
}
</style>
