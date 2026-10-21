<template>
  <div class="schedule-layout">
    <el-card class="cal-card" shadow="never" v-loading="loading">
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
      <div v-if="monthLimitHit" class="month-limit-hint">本月日程已达单次加载上限 200 条，可能未完整显示</div>
    </el-card>

    <el-card class="day-card" shadow="never">
      <template #header>
        <div class="day-header">
          <div class="day-heading">
            <span class="day-title">{{ selectedDayText }} · {{ dayEvents.length }} 项日程</span>
            <el-tag
              v-if="searchKeyword"
              class="search-hint"
              type="info"
              closable
              size="small"
              @close="clearSearch"
            >
              搜索：{{ searchKeyword }}（本月命中 {{ visibleEvents.length }}）
            </el-tag>
          </div>
          <el-button type="primary" size="small" @click="openCreate">
            <el-icon><Plus /></el-icon>&nbsp;新建日程
          </el-button>
        </div>
      </template>

      <el-empty v-if="!dayEvents.length" :description="emptyDayText" :image-size="80" />
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
          <el-button size="small" link type="primary" title="AI 备会" @click.stop="aiPrepare(ev)"><el-icon><MagicStick /></el-icon></el-button>
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
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { scheduleApi } from '../api'
import { fmtDT } from '../utils/format'
import { monthRange } from '../utils/daterange'
import { matchesScheduleQuery, parseSearchDate } from '../utils/search'

const route = useRoute()
const router = useRouter()
// 全局搜索跳转（?q=关键词&date=YYYY-MM-DD）：定位到命中日程所在日期并按关键词过滤
const initialSearchDate = parseSearchDate(route.query.date)
const currentDate = ref(initialSearchDate ? dayjs(initialSearchDate).toDate() : new Date())
const searchKeyword = ref(String(route.query.q || '').trim())
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

// 搜索态下只展示命中关键词的日程（标题 / 地点 / 备注，与 /api/search 一致）
const visibleEvents = computed(() => {
  if (!searchKeyword.value) return monthEvents.value
  return monthEvents.value.filter((ev) => matchesScheduleQuery(ev, searchKeyword.value))
})

const eventsByDay = computed(() => {
  const map = {}
  for (const ev of visibleEvents.value) {
    const key = dayjs(ev.start_time).format('YYYY-MM-DD')
    if (!map[key]) map[key] = []
    map[key].push(ev)
  }
  return map
})

const dayEvents = computed(() => eventsByDay.value[selectedDayKey.value] || [])

const emptyDayText = computed(() =>
  searchKeyword.value
    ? `未找到与「${searchKeyword.value}」匹配的日程`
    : '这一天暂无日程，点击右上角新建',
)

// 单次拉取上限 200 条：达到上限说明本月日程可能未完整加载
const monthLimitHit = computed(() => monthEvents.value.length >= 200)

// 请求序号：快速切换月份时丢弃过期响应
let loadSeq = 0

async function loadMonth() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const range = monthRange(currentDate.value)
    const { data } = await scheduleApi.list({ ...range, page_size: 200 })
    if (seq !== loadSeq) return  // 已有更新的请求发出，丢弃旧结果
    monthEvents.value = data.items
  } catch {
    // 拦截器已提示；吞掉 rejection，避免 onMounted/watch 产生未捕获异常。
    // 失败要清空月份数据：日历标题已经是新月份，却还挂着上个月的日程，比空白更容易误导。
    if (seq === loadSeq) monthEvents.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
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
  // 先上锁再校验：validate() 是 async，锁放在 await 之后就给双击留了窗口，
  // 两次都通过校验会创建两条重复日程（:loading 此刻也还没禁用）
  if (dialog.saving) return
  dialog.saving = true
  try {
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
    if (dialog.id) {
      await scheduleApi.update(dialog.id, payload)
      ElMessage.success('日程已保存')
    } else {
      await scheduleApi.create(payload)
      ElMessage.success('日程创建成功')
    }
    dialog.visible = false
    loadMonth()
  } catch {
    // 拦截器已提示；吞掉 rejection 避免未捕获异常，对话框保留让用户改完重发
  } finally {
    dialog.saving = false
  }
}

async function remove(ev) {
  try {
    await ElMessageBox.confirm(`确定删除日程「${ev.title}」吗？`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await scheduleApi.remove(ev.id)
  } catch {
    return  // 删除失败：日程仍在月历上（拦截器已提示）
  }
  ElMessage.success('已删除')
  loadMonth()
}

function aiPrepare(ev) {
  const time = ev.all_day ? '全天' : fmtDT(ev.start_time) + ' - ' + ev.end_time.slice(11, 16)
  const prompt =
    `请为这个日程做会前准备：给出议程建议、需要准备的物料/资料清单，并整理成一条笔记（标签用「会前准备」）。` +
    `日程：「${ev.title}」，时间 ${time}${ev.location ? '，地点 ' + ev.location : ''}。` +
    (ev.description ? `备注：${ev.description}` : '')
  router.push({ path: '/assistant', query: { auto: prompt } })
}

function timeOnly(value) {
  return value ? dayjs(value).format('HH:mm') : ''
}

function applyRouteQuery() {
  searchKeyword.value = String(route.query.q || '').trim()
  const date = parseSearchDate(route.query.date)
  if (date) {
    const next = dayjs(date).toDate()
    // 同一天不重设，避免无意义的月份 watcher 触发
    if (!dayjs(next).isSame(currentDate.value, 'day')) currentDate.value = next
  }
}

function clearSearch() {
  router.replace({ path: '/schedules' })
}

onMounted(() => {
  applyRouteQuery()
  loadMonth()
})
// 离开 /schedules?q=… 的查询参数时（菜单跳转 / 关闭搜索标签）恢复完整月历
watch(() => route.query, applyRouteQuery)
// 只在月份变化时才整月重拉，同月内点选日期不触发请求
watch(currentDate, (nv, ov) => {
  if (dayjs(nv).isSame(ov, 'month')) return
  loadMonth()
})
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

.month-limit-hint {
  margin-top: 8px;
  color: #e6a23c;
  font-size: 12px;
  text-align: center;
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
  gap: 12px;
}

.day-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.search-hint {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
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
