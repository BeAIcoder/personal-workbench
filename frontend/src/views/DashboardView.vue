<template>
  <div class="page" v-loading="loading">
    <div class="greeting">
      <div class="greeting-row">
        <h2 class="hello">{{ greeting }}！</h2>
        <el-tooltip content="AI 汇总逾期/今日到期/今日日程，输出简报并存为笔记" placement="top">
          <el-button type="primary" plain size="small" @click="aiBrief">
            <el-icon><MagicStick /></el-icon>&nbsp;AI 今日简报
          </el-button>
        </el-tooltip>
      </div>
      <p class="today-sub">{{ todayText }}，数据保存在本机，重启不丢失。</p>
    </div>

    <el-row :gutter="16" class="cards">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <el-card shadow="hover" class="stat-card" @click="$router.push(card.to)">
          <div class="stat-inner">
            <div class="stat-icon" :style="{ background: card.bg }">{{ card.icon }}</div>
            <div>
              <div class="stat-num">{{ card.value }}</div>
              <div class="stat-label">{{ card.label }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="charts">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>任务状态分布</span>
              <span class="rate">完成率 {{ data?.task?.completion_rate ?? 0 }}%</span>
            </div>
          </template>
          <BaseChart :option="pieOption" height="260px" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header><span>近 7 天完成任务趋势</span></template>
          <BaseChart :option="barOption" height="260px" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="lists">
      <el-col :span="10">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-head">
              <span>⏰ 即将到期任务</span>
              <el-link type="primary" :underline="false" @click="$router.push('/tasks?sort=due_asc')">查看全部</el-link>
            </div>
          </template>
          <el-empty v-if="!data?.upcoming_tasks?.length" description="暂无临近截止的任务" :image-size="60" />
          <div
            v-for="t in data?.upcoming_tasks || []"
            :key="t.id"
            class="mini-item clickable"
            @click="$router.push('/tasks?sort=due_asc')"
          >
            <el-tag :type="priorityTag[t.priority]" size="small" effect="plain">{{ priorityLabel[t.priority] }}</el-tag>
            <span class="mini-title" :class="{ 'text-danger': isOverdue(t) }">{{ t.title }}</span>
            <span class="mini-time" :class="{ 'text-danger': isOverdue(t) }">
              {{ isOverdue(t) ? '已逾期 ' : '截止 ' }}{{ fmtDT(t.due_date) }}
            </span>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-head">
              <span>📅 今日日程</span>
              <el-link type="primary" :underline="false" @click="$router.push('/schedules')">查看全部</el-link>
            </div>
          </template>
          <el-empty v-if="!data?.today_schedules?.length" description="今天暂无日程安排" :image-size="60" />
          <div
            v-for="s in data?.today_schedules || []"
            :key="s.id"
            class="mini-item clickable"
            @click="$router.push('/schedules')"
          >
            <span class="bar" :style="{ background: s.color }"></span>
            <span class="mini-title">{{ s.title }}</span>
            <span class="mini-time">{{ s.all_day ? '全天' : fmtTime(s.start_time) }}</span>
          </div>
        </el-card>
      </el-col>

      <el-col :span="6">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-head">
              <span>📝 最近笔记</span>
              <el-link type="primary" :underline="false" @click="$router.push('/notes')">查看全部</el-link>
            </div>
          </template>
          <el-empty v-if="!data?.recent_notes?.length" description="还没有笔记" :image-size="60" />
          <div
            v-for="n in data?.recent_notes || []"
            :key="n.id"
            class="mini-item clickable"
            @click="$router.push('/notes')"
          >
            <span class="mini-title">{{ n.pinned ? '⭐ ' : '' }}{{ n.title }}</span>
            <span class="mini-time">{{ fmtDT(n.updated_at) }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import BaseChart from '../components/BaseChart.vue'
import { dashboardApi } from '../api'
import { fmtDT, isOverdue, priorityLabel, priorityTag } from '../utils/format'

const router = useRouter()

const data = ref(null)
const loading = ref(false)
const todayText = dayjs().format('YYYY年MM月DD日 dddd')

const greeting = computed(() => {
  const h = dayjs().hour()
  if (h < 6) return '夜深了，注意休息'
  if (h < 9) return '早上好'
  if (h < 12) return '上午好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const cards = computed(() => {
  const t = data.value || {}
  return [
    { label: '待办任务', value: t.task?.todo ?? '-', icon: '📌', bg: '#ecf5ff', to: '/tasks?status=todo' },
    { label: '进行中任务', value: t.task?.in_progress ?? '-', icon: '🚀', bg: '#fdf6ec', to: '/tasks?status=in_progress' },
    { label: '已逾期任务', value: t.task?.overdue ?? '-', icon: '⚠️', bg: '#fef0f0', to: '/tasks?overdue=true' },
    { label: '今日日程', value: t.schedule?.today_count ?? '-', icon: '📅', bg: '#f0f9eb', to: '/schedules' },
  ]
})

const pieOption = computed(() => {
  const t = data.value?.task
  if (!t) return {}
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    color: ['#909399', '#e6a23c', '#67c23a'],
    series: [
      {
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '45%'],
        itemStyle: { borderRadius: 6 },
        label: { show: false },
        data: [
          { name: '待办', value: t.todo },
          { name: '进行中', value: t.in_progress },
          { name: '已完成', value: t.done },
        ],
      },
    ],
  }
})

const barOption = computed(() => {
  const trend = data.value?.trend_7d || []
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 30, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: trend.map((x) => x.date.slice(5)) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        type: 'bar',
        barWidth: '45%',
        itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] },
        data: trend.map((x) => x.count),
      },
    ],
  }
})

onMounted(async () => {
  loading.value = true
  try {
    const resp = await dashboardApi.summary()
    data.value = resp.data
  } finally {
    loading.value = false
  }
})

function fmtTime(s) {
  return s ? dayjs(s).format('HH:mm') : ''
}

function aiBrief() {
  const prompt =
    '请生成今日工作简报：先用工具查询逾期任务、今日到期任务、今日日程和任务统计，' +
    '然后输出结构化简报（今日重点、风险提醒、优先级建议），最后把简报存为一条笔记（标签用「工作简报」并置顶）。'
  router.push({ path: '/assistant', query: { auto: prompt } })
}
</script>

<style scoped>
.greeting {
  margin-bottom: 16px;
}

.greeting-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.hello {
  margin: 0 0 4px;
  font-size: 22px;
}

.today-sub {
  margin: 0;
  color: #909399;
  font-size: 13px;
}

.stat-card {
  cursor: pointer;
}

.stat-inner {
  display: flex;
  align-items: center;
  gap: 14px;
}

.stat-icon {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
}

.stat-num {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
}

.stat-label {
  color: #909399;
  font-size: 13px;
}

.charts {
  margin-top: 16px;
}

.lists {
  margin-top: 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.rate {
  color: #67c23a;
  font-size: 13px;
  font-weight: 600;
}

.list-card :deep(.el-card__body) {
  padding-top: 8px;
}

.mini-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 4px;
  border-bottom: 1px dashed #ebeef5;
}

.mini-item:last-child {
  border-bottom: none;
}

.mini-item.clickable {
  cursor: pointer;
}

.mini-item.clickable:hover {
  background: #f5f7fa;
}

.mini-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mini-time {
  color: #909399;
  font-size: 12px;
  white-space: nowrap;
}

.bar {
  width: 4px;
  height: 16px;
  border-radius: 2px;
  flex-shrink: 0;
}
</style>
