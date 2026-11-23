<template>
  <div class="panel">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>单表聚合分析</span>
          <span class="head-actions">
            <el-button size="small" @click="load" :loading="loading">刷新数据集</el-button>
          </span>
        </div>
      </template>
      <el-form label-width="70px" size="small">
        <el-form-item label="数据集">
          <el-select v-model="table" filterable placeholder="选择已入池的数据集" style="width: 320px" @change="onTableChange">
            <el-option v-for="t in tables" :key="t.table" :value="t.table" :label="`${t.table}（${t.rows} 行）`" />
          </el-select>
          <span v-if="sampledHint" class="hint">{{ sampledHint }}</span>
        </el-form-item>
        <el-form-item label="分组维度">
          <el-select v-model="group_by" multiple filterable placeholder="可多选（可空）" style="width: 100%">
            <el-option v-for="c in columns" :key="c.name" :value="c.name" :label="c.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="指标">
          <div class="rows-editor">
            <div v-for="(m, i) in metrics" :key="'m' + i" class="row-line">
              <el-select v-model="m.column" placeholder="数值列" style="width: 220px">
                <el-option v-for="c in metricColumns" :key="c.name" :value="c.name" :label="c.label" />
              </el-select>
              <el-select v-model="m.agg" style="width: 110px">
                <el-option v-for="a in AGGS" :key="a" :value="a" :label="AGG_LABELS[a]" />
              </el-select>
              <el-button size="small" text type="danger" @click="metrics.splice(i, 1)">删除</el-button>
            </div>
            <el-button size="small" @click="metrics.push({ column: '', agg: 'sum' })">+ 添加指标</el-button>
          </div>
        </el-form-item>
        <el-form-item label="过滤条件">
          <div class="rows-editor">
            <div v-for="(f, i) in filters" :key="'f' + i" class="row-line">
              <el-select v-model="f.column" placeholder="列" style="width: 180px">
                <el-option v-for="c in columns" :key="c.name" :value="c.name" :label="c.label" />
              </el-select>
              <el-select v-model="f.op" style="width: 120px">
                <el-option v-for="o in OPS" :key="o" :value="o" :label="OP_LABELS[o]" />
              </el-select>
              <el-input v-if="!['empty', 'not_empty'].includes(f.op)" v-model="f.value" placeholder="比较值" style="width: 220px" />
              <el-button size="small" text type="danger" @click="filters.splice(i, 1)">删除</el-button>
            </div>
            <el-button size="small" @click="filters.push({ column: '', op: 'eq', value: '' })">+ 添加过滤</el-button>
          </div>
        </el-form-item>
        <el-form-item label="排序">
          <el-select v-model="order_by" clearable placeholder="默认" style="width: 220px">
            <el-option v-for="c in orderCandidates" :key="c" :value="c" :label="c" />
          </el-select>
          <el-select v-model="order_dir" style="width: 100px; margin-left: 8px">
            <el-option value="asc" label="升序" />
            <el-option value="desc" label="降序" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="actions">
        <el-button type="primary" :loading="running" @click="run">执行分析</el-button>
        <el-button :disabled="!result" @click="exportXlsx" :loading="exporting">导出 xlsx</el-button>
        <el-button :disabled="!result" @click="openSaveAs">另存为数据集</el-button>
        <span v-if="result" class="hint">
          {{ result.total_groups }} 组{{ result.truncated ? '（已截断）' : '' }} · {{ result.elapsed_ms }} ms
        </span>
      </div>
      <el-dialog v-model="saveDialog" title="另存为数据集" width="520px">
        <el-form label-width="90px" size="small">
          <el-form-item label="目标表名">
            <el-input v-model="saveTarget" placeholder="小写字母/数字/下划线" />
          </el-form-item>
          <el-form-item label="同名数据集">
            <el-checkbox v-model="saveAllowOverwrite">允许覆盖已存在的数据集</el-checkbox>
            <span class="hint">不勾选时后端拒绝覆盖（409），避免误伤已有数据集</span>
          </el-form-item>
        </el-form>
        <el-alert v-if="saveSameAsSource" title="目标表名不能与源表相同：另存不能覆盖源数据，请换一个名字。" type="error" :closable="false" />
        <el-alert v-else-if="saveHint" :title="saveHint" type="warning" :closable="false" />
        <template #footer>
          <el-button @click="saveDialog = false">取消</el-button>
          <el-button type="primary" :loading="saving" :disabled="!saveTargetValid" @click="submitSaveAs">保存</el-button>
        </template>
      </el-dialog>
    </el-card>

    <el-card v-if="result" shadow="never" class="gap-top">
      <template #header>
        <div class="card-head">
          <span>聚合结果（{{ result.row_count }} 行）</span>
          <el-radio-group v-model="chartType" size="small">
            <el-radio-button value="bar">柱状</el-radio-button>
            <el-radio-button value="line">折线</el-radio-button>
            <el-radio-button value="pie" :disabled="!pieEnabled">饼图</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <BaseChart :option="chartOption" height="360px" />
      <el-table :data="resultRows" size="small" max-height="420" border class="gap-top">
        <el-table-column v-for="(c, i) in result.columns" :key="'c' + i" :prop="'c' + i" :label="c" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../../api'
import BaseChart from '../../../../components/BaseChart.vue'
import { saveBlob } from '../../../../utils/download.js'
import { toTableRows } from '../../tableRows'
import {
  buildAggregateSavePayload,
  canUsePie,
  defaultAggregateTable,
  describeAggregateSaveConflict,
  normalizeChartType,
  sameDataset,
  validateTableName,
} from '../../poolLogic'

const props = defineProps({ presetTable: { type: String, default: '' } })

const AGGS = ['sum', 'avg', 'count', 'min', 'max']
const AGG_LABELS = { sum: '求和', avg: '平均', count: '计数', min: '最小', max: '最大' }
const OPS = ['eq', 'ne', 'gt', 'ge', 'lt', 'le', 'contains', 'in', 'empty', 'not_empty']
const OP_LABELS = {
  eq: '等于', ne: '不等于', gt: '大于', ge: '大于等于', lt: '小于', le: '小于等于',
  contains: '包含', in: '属于', empty: '为空', not_empty: '非空',
}

const loading = ref(false)
const running = ref(false)
const exporting = ref(false)
const tables = ref([])
const table = ref('')
const columns = ref([]) // [{name, label, kind, numeric}]
const metricColumns = computed(() => columns.value.filter((c) => c.numeric))
const orderCandidates = computed(() => [...group_by.value, ...metricsLabels()])
const group_by = ref([])
const metrics = ref([{ column: '', agg: 'sum' }])
const filters = ref([])
const order_by = ref('')
const order_dir = ref('desc')
const result = ref(null)
const chartType = ref('bar')
// 饼图必须有分组维度：无维度的聚合结果只剩指标列，扇区没有业务含义
const pieEnabled = computed(() => canUsePie(group_by.value))
const chartKind = computed(() => normalizeChartType(chartType.value, group_by.value))

// 分组维度被清空后，已选的饼图回退柱状，避免禁用态下仍按饼图渲染
watch(group_by, () => {
  if (chartType.value === 'pie' && !pieEnabled.value) chartType.value = 'bar'
}, { deep: true })

function metricsLabels() {
  return metrics.value.filter((m) => m.column).map((m) => `${m.column}_${m.agg}`)
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/vault/tables')
    tables.value = data.items || []
    if (!table.value && props.presetTable && tables.value.some((t) => t.table === props.presetTable)) {
      table.value = props.presetTable
      await onTableChange()
    }
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

watch(() => props.presetTable, async (v) => {
  if (v && tables.value.some((t) => t.table === v) && table.value !== v) {
    table.value = v
    await onTableChange()
  }
})

async function onTableChange() {
  group_by.value = []
  metrics.value = [{ column: '', agg: 'sum' }]
  filters.value = []
  order_by.value = ''
  result.value = null
  try {
    const { data } = await api.get(`/finance/vault/tables/${encodeURIComponent(table.value)}/profile`)
    columns.value = (data.columns || []).map((c) => ({
      name: c.name,
      label: `${c.name}（${c.kind === 'numeric' ? '数值' : c.kind === 'date' ? '日期' : '文本'}${c.null_pct >= 100 ? '·全空' : ''}）`,
      kind: c.kind,
      numeric: c.duckdb_type && /^(BIGINT|INTEGER|HUGEINT|SMALLINT|TINYINT|UBIGINT|UINTEGER|USMALLINT|UTINYINT|DECIMAL|DOUBLE|FLOAT|REAL|INT)/i.test(c.duckdb_type),
    }))
  } catch {
    columns.value = []
  }
}

async function run() {
  if (!table.value) return
  running.value = true
  try {
    const { data } = await api.post(`/finance/vault/tables/${encodeURIComponent(table.value)}/aggregate`, payload())
    result.value = data
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    running.value = false
  }
}

function payload() {
  return {
    group_by: group_by.value.filter(Boolean),
    metrics: metrics.value.filter((m) => m.column),
    filters: filters.value.filter((f) => f.column && (['empty', 'not_empty'].includes(f.op) || f.value !== '')),
    order_by: order_by.value,
    order_dir: order_dir.value,
  }
}

const resultRows = computed(() => toTableRows(result.value?.columns, result.value?.rows))
const sampledHint = computed(() => {
  const t = tables.value.find((t) => t.table === table.value)
  return t && t.rows > 50000 ? '大表：聚合在内存完成，首次执行可能稍慢' : ''
})

const chartOption = computed(() => {
  if (!result.value || !result.value.rows.length) return {}
  const cols = result.value.columns
  const rows = result.value.rows
  const dimName = cols[0]
  const metricNames = cols.slice(1)
  if (chartKind.value === 'pie') {
    return {
      tooltip: { trigger: 'item' },
      legend: { orient: 'vertical', left: 'left', type: 'scroll' },
      series: [{
        type: 'pie',
        radius: '60%',
        data: rows.map((r) => ({ name: String(r[0] ?? '空'), value: r[1] })),
      }],
    }
  }
  return {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll' },
    grid: { left: 60, right: 30, bottom: 80 },
    xAxis: { type: 'category', data: rows.map((r) => String(r[0] ?? '空')), axisLabel: { rotate: 30 } },
    yAxis: { type: 'value' },
    series: metricNames.map((name, i) => ({
      name,
      type: chartKind.value === 'line' ? 'line' : 'bar',
      data: rows.map((r) => r[i + 1]),
    })),
  }
})

async function exportXlsx() {
  if (!table.value || !result.value) return
  exporting.value = true
  try {
    const resp = await api.post(
      `/finance/vault/tables/${encodeURIComponent(table.value)}/aggregate/export`,
      payload(),
      { responseType: 'blob', timeout: 180000 },
    )
    saveBlob(resp.data, `${table.value}_聚合分析.xlsx`)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    exporting.value = false
  }
}

// ---------- 另存为数据集：目标表名 + allow_overwrite（409 在弹窗内补救） ----------
const saveDialog = ref(false)
const saveTarget = ref('')
const saveAllowOverwrite = ref(false)
const saveHint = ref('')
const saving = ref(false)
const saveSameAsSource = computed(() => sameDataset(saveTarget.value, table.value))
// 目标与源表同名会替换源数据集本身：请求前拒绝，覆盖勾选只针对「其它已存在数据集」
const saveTargetValid = computed(() => validateTableName(saveTarget.value) && !saveSameAsSource.value)

function openSaveAs() {
  if (!table.value || !result.value) return
  saveTarget.value = defaultAggregateTable(table.value)
  saveAllowOverwrite.value = false
  saveHint.value = ''
  saveDialog.value = true
}

async function submitSaveAs() {
  if (!table.value || !result.value || !saveTargetValid.value) return
  saving.value = true
  saveHint.value = ''
  try {
    await api.post(
      `/finance/vault/tables/${encodeURIComponent(table.value)}/aggregate/save`,
      buildAggregateSavePayload(payload(), {
        targetTable: saveTarget.value,
        allowOverwrite: saveAllowOverwrite.value,
      }),
    )
    ElMessage.success(`已另存为数据集：${saveTarget.value}`)
    saveDialog.value = false
    await load()
  } catch (err) {
    // 目标已存在（409）：勾选覆盖或改名后重试，弹窗保持打开；同源已在请求前拒绝
    saveHint.value = describeAggregateSaveConflict(err, saveTarget.value)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.hint {
  margin-left: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.rows-editor {
  width: 100%;
}
.row-line {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  align-items: center;
}
.actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.gap-top {
  margin-top: 14px;
}
</style>
