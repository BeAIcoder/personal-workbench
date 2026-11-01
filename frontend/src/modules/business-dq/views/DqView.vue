<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>数据质量巡检</span>
          <div>
            <el-button size="small" :loading="loading" @click="load">刷新</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>

      <el-alert
        type="info"
        :closable="false"
        title="只读巡检：不修改任何数据。覆盖空值/重复/孤儿引用/日期有效性/金额异常五维 × 七域。"
      />
      <el-row :gutter="12" class="kpi">
        <el-col :span="8">
          <div class="kpi-box">
            <div class="kpi-label">总体分数</div>
            <div class="kpi-value">{{ overall ?? '—' }}</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="kpi-box">
            <div class="kpi-label">巡检域</div>
            <div class="kpi-value">{{ availableCount }}/{{ totalDomains }}</div>
        </div>
        </el-col>
        <el-col :span="8">
          <div class="kpi-box">
            <div class="kpi-label">问题总数</div>
            <div class="kpi-value">{{ issueTotal }}</div>
          </div>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="kpi">
        <el-col v-for="d in domainList" :key="d.key" :span="6">
          <div class="kpi-box">
            <div class="kpi-label">{{ d.label }}</div>
            <div class="kpi-value">{{ d.score ?? '—' }}</div>
            <div class="kpi-sub">{{ d.summary }}</div>
          </div>
        </el-col>
      </el-row>

      <el-table :data="issueRows" size="small" border class="issues">
        <el-table-column prop="level" label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="row.level === 'error' ? 'danger' : row.level === 'warning' ? 'warning' : 'info'" size="small">
              {{ row.level === 'error' ? '错误' : row.level === 'warning' ? '警告' : '提示' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="domain_label" label="域" width="90" />
        <el-table-column prop="check" label="检查" width="110" />
        <el-table-column prop="count" label="数量" width="80" />
        <el-table-column prop="suggestion" label="建议" min-width="280" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../../../api'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'

const loading = ref(false)
const data = ref(null)

const DOMAIN_LABELS = {
  leasing: '招商',
  operations: '营运',
  hr: '人事',
  legal: '法务',
  planning: '企划',
  facility: '工程',
  finance: '财务',
}

const domainList = computed(() =>
  Object.entries(DOMAIN_LABELS).map(([key, label]) => {
    const r = data.value?.domains?.[key]
    return {
      key,
      label,
      score: r?.available ? r.score : null,
      summary: r?.available
        ? `E${r.error ?? 0}/W${r.warning ?? 0}/I${r.info ?? 0}`
        : '未就绪',
    }
  })
)

const issueRows = computed(() => (data.value?.top_issues || []).map(i => ({ ...i, domain_label: DOMAIN_LABELS[i.domain] || i.domain })))

const overall = computed(() => data.value?.overall_score ?? null)
const availableCount = computed(() => Object.values(data.value?.domains || {}).filter(d => d.available).length)
const totalDomains = computed(() => Object.keys(DOMAIN_LABELS).length)
const issueTotal = computed(() => (data.value?.top_issues || []).reduce((s, i) => s + (i.count || 0), 0))

const aiPrompt = computed(() => {
  const s = data.value?.overall_score
  const top = (data.value?.top_issues || []).slice(0, 5)
    .map(i => `${i.domain}/${i.check}/${i.level}×${i.count}`)
    .join('；')
  return `请解读数据质量巡检结果：总分 ${s ?? '未知'}，主要问题：${top || '暂无'}。给出清洗与整改建议。`
})

async function load() {
  loading.value = true
  try {
    const resp = await api.get('/business/dq/summary')
    data.value = resp.data
  } catch (e) {
    data.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.kpi { margin-top: 12px; }
.kpi-box { background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 6px; padding: 10px 12px; }
.kpi-label { font-size: 12px; color: #64748b; }
.kpi-value { font-size: 20px; font-weight: 600; margin-top: 2px; }
.kpi-sub { font-size: 12px; color: #94a3b8; margin-top: 2px; }
.issues { margin-top: 12px; }
</style>
