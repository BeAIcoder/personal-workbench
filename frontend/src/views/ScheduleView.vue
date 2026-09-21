<template>
  <div class="schedule-layout">
    <el-card class="cal-card" shadow="never">
      <el-calendar v-model="currentDate">
        <template #date-cell="{ data }">
          <div class="cal-cell">
            <div class="cal-day" :class="{ today: data.isSelected }">{{ Number(data.day.split('-')[2]) }}</div>
            <div class="cal-dots">
              <span
                v-for="(s, i) in (eventsByDay[data.day] || []).slice(0, 3)"
                :key="i"
                class="dot"
                :style="{ background: s.color }"
              ></span>
              <span v-if="(eventsByDay[data.day] || []).length > 3" class="more">
                +{{ eventsByDay[data.day].length - 3 }}
              </span>
            </div>
          </div>
        </template>
      </el-calendar>
    </el-card>

    <el-card class="day-card" shadow="never">
      <template #header>
        <div class="day-header">
          <span class="day-title">{{ selectedDayText }} · {{ dayEvents.length }} 项日程</span>
          <el-button type="primary" size="small" @click="openCreate">
            <el-icon><Plus /></el-icon>&nbsp;新建日程
          </el-button>
        </div>
      </template>

      <el-empty v-if="!dayEvents.length" description="这一天暂无日程，点击右上角新建" :image-size="80" />
      <div v-else class="event-list">
        <div v-for="ev in dayEvents" :key="ev.id" class="event-item" @click="openEdit(ev)">
          <span class="event-bar" :style="{ background: ev.color }"></span>
          <div class="event-main">
            <div class="event-title">
              {{ ev.title }}
              <el-tag v-if="ev.all_day" size="small" type="info">全天</el-tag>
            </div>
            <div class="event-meta">
              {{ ev.all_day ? '全天' : fmtDT(ev.start_time) + ' - ' + timeOnly(ev.end_time) }}
              <span v-if="ev.location"> · 📍{{ ev.location }}</span>
            </div>
          </div>
          <el-button size="small" link type="primary" title="AI 备会" @click="aiPrepare(ev)"><el-icon><MagicStick /></el-icon></el-button>
          <el-button size="small" type="danger" plain @click.stop="remove(ev)">删除</el-button>
        </div>
      </div>
    </el-card>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑日程' : '新建日程'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" maxlength="200" show-word-limit placeholder="例如：月度经营分析会" />
        </el-form-item>
        <el-form-item label="全天">
          <el-switch v-model="form.all_day" />
        </el-form-item>
        <el-form-item label="日期" prop="start_time">
          <el-date-picker
            v-model="form.start_time"
            :type="form.all_day ? 'date' : 'datetime'"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择开始"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item v-if="!form.all_day" label="结束" prop="end_time">
          <el-date-picker
            v-model="form.end_time"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择结束"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="form.location" maxlength="200" placeholder="可选" />
        </el-form-item>
        <el-form-item label="颜色">
          <el-color-picker v-model="form.color" :predefine="predefine" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="5000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { scheduleApi } from '../api'
import { fmtDT } from '../utils/format'
import { monthRange } from '../utils/daterange'

const currentDate = ref(new Date())
const monthEvents = ref([])
const loading = ref(false)

const formRef = ref(null)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({
  title: '',
  description: '',
  location: '',
  start_time: null,
  end_time: null,
  all_day: false,
  color: '#409EFF',
})
const rules = {
  title: [{ required: true, message: '请输入日程标题', trigger: 'blur' }],
  start_time: [{ required: true, message: '请选择时间', trigger: 'change' }],
  end_time: [{ required: true, message: '请选择时间', trigger: 'change' }],
}
const predefine = ['#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#9C27B0', '#00BFFF']

const selectedDayKey = computed(() => dayjs(currentDate.value).format('YYYY-MM-DD'))
const selectedDayText = computed(() => dayjs(currentDate.value).format('YYYY年MM月DD日 dddd'))

const eventsByDay = computed(() => {
  const map = {}
  for (const ev of monthEvents.value) {
    const key = dayjs(ev.start_time).format('YYYY-MM-DD')
    if (!map[key]) map[key] = []
    map[key].push(ev)
  }
  return map
})

const dayEvents = computed(() => eventsByDay.value[selectedDayKey.value] || [])

async function loadMonth() {
  loading.value = true
  try {
    const range = monthRange(currentDate.value)
    const { data } = await scheduleApi.list({ ...range, page_size: 200 })
    monthEvents.value = data.items
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    title: '',
    description: '',
    location: '',
    start_time: dayjs(currentDate.value).format('YYYY-MM-DD') + 'T09:00:00',
    end_time: dayjs(currentDate.value).format('YYYY-MM-DD') + 'T10:00:00',
    all_day: false,
    color: '#409EFF',
  })
}

function openCreate() {
  dialog.id = null
  resetForm()
  dialog.visible = true
}

function openEdit(ev) {
  dialog.id = ev.id
  Object.assign(form, {
    title: ev.title,
    description: ev.description,
    location: ev.location,
    start_time: ev.start_time,
    end_time: ev.end_time,
    all_day: ev.all_day,
    color: ev.color,
  })
  dialog.visible = true
}

async function save() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  const payload = { ...form }
  if (form.all_day) {
    const d = (form.start_time || '').slice(0, 10)
    if (!d) {
      ElMessage.warning('请选择日期')
      return
    }
    payload.start_time = `${d}T00:00:00`
    payload.end_time = `${d}T23:59:59`
  }
  dialog.saving = true
  try {
    if (dialog.id) {
      await scheduleApi.update(dialog.id, payload)
      ElMessage.success('日程已保存')
    } else {
      await scheduleApi.create(payload)
      ElMessage.success('日程创建成功')
    }
    dialog.visible = false
    loadMonth()
  } finally {
    dialog.saving = false
  }
}

async function remove(ev) {
  await ElMessageBox.confirm(`确定删除日程「${ev.title}」吗？`, '删除确认', { type: 'warning' })
  await scheduleApi.remove(ev.id)
  ElMessage.success('已删除')
  loadMonth()
}

function aiPrepare(ev) {
  const time = ev.all_day ? '全天' : fmtDT(ev.start_time) + ' - ' + ev.end_time.slice(11, 16)
  const prompt =
    `请为这个日程做会前准备：给出议程建议、需要准备的物料/资料清单，并整理成一条笔记（标签用「会前准备」）。` +
    `日程：「${ev.title}」，时间 ${time}${ev.location ? '，地点 ' + ev.location : ''}。` +
    (ev.description ? `备注：${ev.description}` : '')
  useRouter().push({ path: '/assistant', query: { auto: prompt } })
}

function timeOnly(value) {
  return value ? dayjs(value).format('HH:mm') : ''
}

onMounted(loadMonth)
watch(currentDate, loadMonth)
</script>

<style scoped>
.schedule-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.cal-card {
  flex: 1.4;
  min-width: 0;
}

.day-card {
  flex: 1;
  min-width: 340px;
}

.cal-cell {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: 2px;
}

.cal-day {
  font-size: 13px;
}

.cal-day.today {
  color: #409eff;
  font-weight: 700;
}

.cal-dots {
  display: flex;
  gap: 3px;
  margin-top: 3px;
  min-height: 8px;
  align-items: center;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.more {
  font-size: 10px;
  color: #909399;
}

.day-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.day-title {
  font-weight: 600;
}

.event-list {
  display: flex;
  flex-direction: column;
}

.event-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 6px;
  border-bottom: 1px dashed #ebeef5;
  cursor: pointer;
  border-radius: 6px;
}

.event-item:hover {
  background: #f5f7fa;
}

.event-item:last-child {
  border-bottom: none;
}

.event-bar {
  width: 4px;
  height: 30px;
  border-radius: 2px;
  flex-shrink: 0;
}

.event-main {
  flex: 1;
  min-width: 0;
}

.event-title {
  font-weight: 500;
}

.event-meta {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
}
</style>
