<template>
  <div class="page" v-loading="loading">
    <el-result
      v-if="loadFailed"
      icon="error"
      title="经营聚合加载失败"
      sub-title="请确认后端服务已启动，然后重试；失败期间不展示旧指标。"
      role="alert"
    >
      <template #extra><el-button type="primary" @click="load">重试</el-button></template>
    </el-result>

    <template v-else>
      <el-card shadow="never" class="header-card">
        <div class="page-head">
          <div>
            <h2>总经理经营聚合</h2>
            <p>只读汇总各域现有 summary；不可用与缺源指标均明确标注，不以 0 顶替。</p>
          </div>
          <div class="period-control">
            <label for="gm-period">经营期间</label>
            <el-date-picker
              id="gm-period"
              v-model="period"
              type="month"
              value-format="YYYY-MM"
              :clearable="false"
              @change="load"
            />
            <el-button type="primary" :loading="loading" @click="load">刷新</el-button>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="reminder-card">
        <template #header><span>高优先经营提醒</span></template>
        <div aria-live="polite">
          <el-alert
            v-for="item in reminders"
            :key="item.code"
            :title="item.message"
            type="warning"
            :closable="false"
            show-icon
            class="reminder"
          />
          <el-empty v-if="!reminders.length" description="当前无高优先经营提醒" :image-size="56" />
        </div>
      </el-card>

      <el-row :gutter="16">
        <el-col v-for="domain in domainCards" :key="domain.key" :xs="24" :md="12">
          <el-card shadow="never" class="domain-card">
            <template #header>
              <div class="domain-head">
                <span>{{ domain.label }}</span>
                <el-tag :type="domain.available ? 'success' : 'info'" size="small">
                  {{ domain.available ? '可用' : '暂不可用' }}
                </el-tag>
              </div>
            </template>

            <el-empty
              v-if="!domain.available"
              description="该域数据暂不可用，未以 0 顶替"
              :image-size="56"
            />
            <div v-else-if="!domain.metrics.length" class="empty-metrics">
              该域已接入，当前暂无已登记指标。
            </div>
            <div v-else class="metrics">
              <div v-for="metric in domain.metrics" :key="metric.key" class="metric-item">
                <div class="metric-line">
                  <span>{{ metricLabel(metric) }}</span>
                  <strong :class="{ 'null-value': metric.value === null || metric.value === undefined }">
                    {{ formatMetric(metric) }}
                  </strong>
                </div>
                <div v-if="metric.reason" class="gap-reason">{{ metric.reason }}</div>
                <div v-else-if="metricDefinition(metric)" class="metric-method">
                  {{ metricDefinition(metric).formula }}
                  <span class="metric-source">来源：{{ metricSource(metric) }}</span>
                </div>
              </div>
            </div>
            <ul v-if="domain.gapReasons.length" class="gap-list">
              <li v-for="reason in domain.gapReasons" :key="reason">{{ reason }}</li>
            </ul>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import { gmApi } from '../../../api'

const DOMAIN_DEFINITIONS = [
  { key: 'leasing', label: '招商' },
  { key: 'operations', label: '营运与收缴' },
  { key: 'planning', label: '企划' },
  { key: 'legal', label: '法务' },
  { key: 'hr', label: '人事' },
  { key: 'facility', label: '工程物业' },
]
const RATE_KEYS = new Set([
  'leasing.vacancy_rate',
  'leasing.leasing_rate',
  'leasing.opening_rate',
  'leasing.conversion_rate',
  'operations.collection_rate',
  'operations.expiry_12m_area_ratio',
  'planning.avg_roi',
])
const MONEY_KEYS = new Set([
  'operations.receivable',
  'operations.received',
  'operations.arrears',
  'planning.budget_total',
  'planning.actual_total',
  'planning.variance',
  'hr.payroll_net',
])

const route = useRoute()
const routePeriod = typeof route.query.period === 'string'
  && /^\d{4}-(?:0[1-9]|1[0-2])$/.test(route.query.period)
  ? route.query.period
  : ''
const period = ref(routePeriod || dayjs().format('YYYY-MM'))
const summary = ref(null)
const loading = ref(false)
const loadFailed = ref(false)

const reminders = computed(() => summary.value?.reminders || [])
const domainCards = computed(() => DOMAIN_DEFINITIONS.map((definition) => {
  const state = summary.value?.domains?.[definition.key]
  return {
    ...definition,
    available: state?.available === true,
    data: state?.data || null,
    gapReasons: state?.gap_reasons || (state ? [] : ['该域未返回数据']),
    metrics: (summary.value?.metrics || []).filter((item) => item.domain === definition.key),
  }
}))

function metricDefinition(metric) {
  return summary.value?.metric_dictionary?.[metric.key] || null
}

function metricLabel(metric) {
  return metricDefinition(metric)?.label || metric.label || metric.key
}

function metricSource(metric) {
  return metricDefinition(metric)?.source || '无可用来源'
}

function formatMetric(metric) {
  const value = metric.value
  if (value === null || value === undefined) return '—'
  if (RATE_KEYS.has(metric.key)) return `${(Number(value) * 100).toFixed(2)}%`
  if (MONEY_KEYS.has(metric.key)) {
    return new Intl.NumberFormat('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(value))
  }
  return String(value)
}

async function load() {
  loading.value = true
  loadFailed.value = false
  try {
    const response = await gmApi.summary(period.value)
    summary.value = response.data
  } catch {
    summary.value = null
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.header-card,
.reminder-card,
.domain-card {
  margin-bottom: 16px;
}

.page-head,
.domain-head,
.metric-line,
.period-control {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.page-head h2 {
  margin: 0 0 6px;
  font-size: 22px;
}

.page-head p {
  margin: 0;
  color: #909399;
  font-size: 13px;
}

.period-control {
  justify-content: flex-end;
  flex-wrap: wrap;
}

.period-control label {
  color: #606266;
  font-size: 14px;
}

.reminder + .reminder {
  margin-top: 8px;
}

.metric-item {
  padding: 12px 0;
  border-bottom: 1px solid #ebeef5;
}

.metric-item:last-child {
  border-bottom: 0;
}

.metric-line strong {
  font-size: 20px;
}

.null-value {
  color: #909399;
}

.metric-method,
.gap-reason,
.empty-metrics {
  margin-top: 6px;
  color: #909399;
  font-size: 12px;
  line-height: 1.6;
}

.metric-source {
  display: block;
  overflow-wrap: anywhere;
}

.gap-reason,
.gap-list {
  color: #b88230;
}

.gap-list {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 768px) {
  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .period-control {
    justify-content: flex-start;
  }
}
</style>
