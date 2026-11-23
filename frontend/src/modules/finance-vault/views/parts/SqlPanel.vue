<template>
  <div class="panel">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>只读 SQL 查询</span>
          <div class="toolbar">
            <el-button size="small" type="primary" :loading="loading" @click="run">执行</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>
      <el-input
        v-model="sql"
        type="textarea"
        :rows="6"
        placeholder="SELECT ... FROM 已注册表名（仅只读查询；结果自动限流 1000 行）"
      />
      <p class="hint">可从「数据池」页签选择表名；仅允许引用已注册数据集。</p>
    </el-card>

    <el-card v-if="result" shadow="never" class="gap-top">
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
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../../api'
import { toTableRows } from '../../tableRows'
import { saveBlob } from '../../../../utils/download.js'
import AskAiButton from '../../../../components/assistant/AskAiButton.vue'

const props = defineProps({ presetTable: { type: String, default: '' } })
const route = useRoute()
const loading = ref(false)
const sql = ref('')
const result = ref(null)

const tableRows = computed(() => toTableRows(result.value?.columns, result.value?.rows))

const aiPrompt = computed(() => '请基于数据池当前数据集做质量检查：哪些表行数为0、哪些表有空值率高的列，并给出清洗建议。')

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
  saveBlob(blob, `vault_query_${new Date().toISOString().slice(0, 10)}.csv`)
}

// URL ?table= 预填查询模板；prop 传来的预选表名同样生效（页签切换不丢）
const TABLE_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

function fillTemplate(name) {
  if (typeof name === 'string' && TABLE_NAME_RE.test(name)) {
    sql.value = `SELECT * FROM ${name} LIMIT 100`
  }
}

onMounted(() => {
  fillTemplate(props.presetTable || route.query.table)
})

watch(() => props.presetTable, (v) => { if (v) fillTemplate(v) })
</script>

<style scoped>
.hint {
  margin: 10px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.gap-top {
  margin-top: 16px;
}
</style>
