<template>
  <div class="page">
    <div class="toolbar">
      <div class="filters">
        <el-select v-model="filters.status" placeholder="全部状态" clearable style="width: 130px" @change="load(1)">
          <el-option label="待办" value="todo" />
          <el-option label="进行中" value="in_progress" />
          <el-option label="已完成" value="done" />
        </el-select>
        <el-select v-model="filters.priority" placeholder="全部优先级" clearable style="width: 130px" @change="load(1)">
          <el-option label="紧急" value="urgent" />
          <el-option label="高" value="high" />
          <el-option label="中" value="medium" />
          <el-option label="低" value="low" />
        </el-select>
        <el-input
          v-model="filters.q"
          placeholder="搜索任务标题 / 描述，回车确认"
          clearable
          style="width: 240px"
          @keyup.enter="load(1)"
          @clear="load(1)"
        >
          <template #append>
            <el-button @click="load(1)"><el-icon><Search /></el-icon></el-button>
          </template>
        </el-input>
        <el-select v-model="filters.sort" style="width: 140px" @change="load(1)">
          <el-option label="按创建时间" value="created_desc" />
          <el-option label="按截止时间" value="due_asc" />
          <el-option label="按优先级" value="priority_desc" />
        </el-select>
      </div>
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建任务
      </el-button>
    </div>

    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="title" label="任务标题" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <span :class="{ 'done-title': row.status === 'done' }">{{ row.title }}</span>
          <el-tag v-if="row.category" size="small" type="info" effect="plain" style="margin-left: 6px">
            {{ row.category }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{ row }">
          <el-tag :type="statusTag[row.status]">{{ statusLabel[row.status] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="优先级" width="80" align="center">
        <template #default="{ row }">
          <el-tag :type="priorityTag[row.priority]" effect="plain">{{ priorityLabel[row.priority] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="截止时间" width="190">
        <template #default="{ row }">
          <span :class="{ 'text-danger': isOverdue(row) }">{{ fmtDT(row.due_date) }}</span>
          <el-tag v-if="isOverdue(row)" type="danger" size="small" style="margin-left: 4px">已逾期</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="160">
        <template #default="{ row }">{{ fmtDT(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-tooltip content="AI 分析这个任务" placement="top">
            <el-button size="small" link type="primary" @click="aiAnalyze(row)"><el-icon><MagicStick /></el-icon></el-button>
          </el-tooltip>
          <el-button v-if="row.status !== 'done'" size="small" type="success" plain @click="complete(row)">完成</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        background
        layout="total, sizes, prev, pager, next"
        :total="total"
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50]"
        @change="load()"
      />
    </div>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑任务' : '新建任务'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" maxlength="200" show-word-limit placeholder="例如：整理本月进项发票" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="5000" />
        </el-form-item>
        <el-row>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-select v-model="form.status" style="width: 100%">
                <el-option label="待办" value="todo" />
                <el-option label="进行中" value="in_progress" />
                <el-option label="已完成" value="done" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="优先级">
              <el-select v-model="form.priority" style="width: 100%">
                <el-option label="低" value="low" />
                <el-option label="中" value="medium" />
                <el-option label="高" value="high" />
                <el-option label="紧急" value="urgent" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row>
          <el-col :span="12">
            <el-form-item label="截止时间">
              <el-date-picker
                v-model="form.due_date"
                type="datetime"
                value-format="YYYY-MM-DDTHH:mm:ss"
                placeholder="可选"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="分类">
              <el-input v-model="form.category" maxlength="50" placeholder="例如：税务申报" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { taskApi } from '../api'
import { fmtDT, isOverdue, priorityLabel, priorityTag, statusLabel, statusTag } from '../utils/format'

const route = useRoute()
const router = useRouter()
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(10)
const filters = reactive({ status: null, priority: null, q: '', sort: 'created_desc' })

const formRef = ref(null)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ title: '', description: '', status: 'todo', priority: 'medium', due_date: null, category: '' })
const rules = { title: [{ required: true, message: '请输入任务标题', trigger: 'blur' }] }

function cleanFilters() {
  const out = {}
  for (const [k, v] of Object.entries(filters)) {
    if (v !== null && v !== undefined && v !== '') out[k] = v
  }
  return out
}

// 请求序号：快速翻页/筛选时丢弃过期响应
let loadSeq = 0

async function load(targetPage) {
  if (targetPage) page.value = targetPage
  const seq = ++loadSeq
  loading.value = true
  try {
    const { data } = await taskApi.list({ ...cleanFilters(), page: page.value, page_size: pageSize.value })
    if (seq !== loadSeq) return  // 已有更新的请求发出，丢弃旧结果
    rows.value = data.items
    total.value = data.total
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function openCreate() {
  dialog.id = null
  Object.assign(form, { title: '', description: '', status: 'todo', priority: 'medium', due_date: null, category: '' })
  dialog.visible = true
}

function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, {
    title: row.title,
    description: row.description,
    status: row.status,
    priority: row.priority,
    due_date: row.due_date,
    category: row.category,
  })
  dialog.visible = true
}

async function save() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  dialog.saving = true
  try {
    if (dialog.id) {
      await taskApi.update(dialog.id, { ...form })
      ElMessage.success('任务已保存')
    } else {
      await taskApi.create({ ...form })
      ElMessage.success('任务创建成功')
    }
    dialog.visible = false
    load()
  } finally {
    dialog.saving = false
  }
}

async function complete(row) {
  await taskApi.update(row.id, { status: 'done' })
  ElMessage.success('任务已完成 🎉')
  load()
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除任务「${row.title}」吗？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  await taskApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function aiAnalyze(row) {
  const due = row.due_date ? '，截止 ' + fmtDT(row.due_date) : '（无截止时间）'
  const prompt =
    `请分析这个任务并给出执行建议，把分析要点存为一条笔记（标签用「AI分析」），` +
    `如需跟进请一并创建后续任务：「${row.title}」（状态 ${statusLabel[row.status]}，优先级 ${priorityLabel[row.priority]}${due}，分类 ${row.category || '无'}）。` +
    (row.description ? `任务描述：${row.description}` : '')
  router.push({ path: '/assistant', query: { auto: prompt } })
}

function applyRoute() {
  if (route.path !== '/tasks') return
  let needLoad = false
  if (route.query.q !== undefined && route.query.q !== filters.q) {
    filters.q = String(route.query.q || '')
    needLoad = true
  }
  if (route.query.status !== undefined && route.query.status !== filters.status) {
    filters.status = route.query.status || null
    needLoad = true
  }
  if (route.query.overdue !== undefined) {
    // 逾期卡片跳转：改为按截止时间排序，用户可直观看到最前面的逾期项
    filters.sort = 'due_asc'
    needLoad = true
  }
  if (needLoad) load(1)
}

onMounted(() => {
  if (route.path === '/tasks' && Object.keys(route.query).length > 0) {
    applyRoute()
  } else {
    load(1)
  }
})
watch(() => route.query, applyRoute)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  gap: 12px;
  flex-wrap: wrap;
}

.filters {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
</style>
