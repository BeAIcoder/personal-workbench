<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>测试运行（{{ total }}）</span>
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>
      <el-empty v-if="!items.length" description="暂无测试运行记录（运行 dev/e2e 脚本后自动归档）" />
      <el-table v-else :data="items" size="small">
        <el-table-column prop="finished_at" label="时间" width="170" />
        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="页面 OK / BAD" width="130" align="center">
          <template #default="{ row }">
            <span class="ok-text">{{ row.totals.pages - row.totals.bad_pages }}</span>
            <span class="sep">/</span>
            <span :class="row.totals.bad_pages ? 'bad-text' : ''">{{ row.totals.bad_pages }}</span>
          </template>
        </el-table-column>
        <el-table-column label="失败用例" width="90" align="center">
          <template #default="{ row }">
            <span :class="row.totals.failed_tests ? 'bad-text' : ''">{{ row.totals.failed_tests }}</span>
          </template>
        </el-table-column>
        <el-table-column label="页面/用例" width="110" align="center">
          <template #default="{ row }">{{ row.totals.pages }} 页 / {{ row.totals.tests }} 例</template>
        </el-table-column>
        <el-table-column prop="id" label="运行 ID" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button size="small" link type="danger" @click="removeRun(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 运行详情抽屉 -->
    <el-drawer v-model="drawerVisible" :title="`运行详情 · ${detail?.id || ''}`" size="720px">
      <div v-if="detail" v-loading="detailLoading" class="detail-body">
        <el-descriptions :column="2" border size="small" style="margin-bottom: 16px">
          <el-descriptions-item label="类型">{{ kindLabel(detail.kind) }}</el-descriptions-item>
          <el-descriptions-item label="目标">{{ detail.base }}</el-descriptions-item>
          <el-descriptions-item label="开始">{{ detail.started_at }}</el-descriptions-item>
          <el-descriptions-item label="结束">{{ detail.finished_at }}</el-descriptions-item>
        </el-descriptions>

        <template v-if="detail.pages?.length">
          <h4 class="sec-title">页面检查（{{ detail.pages.length }}）</h4>
          <el-table :data="detail.pages" size="small">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="err-lists">
                  <ErrList title="console 错误" :items="row.console_errors" />
                  <ErrList title="页面异常" :items="row.page_errors" />
                  <ErrList title="失败请求" :items="row.failed_reqs" />
                  <p v-if="!hasErrors(row)" class="no-err">无错误</p>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="route" label="路由" width="180" />
            <el-table-column prop="status" label="HTTP" width="80" align="center" />
            <el-table-column label="结果" width="90">
              <template #default="{ row }">
                <el-tag :type="hasErrors(row) ? 'danger' : 'success'" size="small">
                  {{ hasErrors(row) ? 'BAD' : 'OK' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="错误数" width="90" align="center">
              <template #default="{ row }">{{ errCount(row) }}</template>
            </el-table-column>
          </el-table>
        </template>

        <template v-if="detail.tests?.length">
          <h4 class="sec-title">测试用例（{{ detail.tests.length }}）</h4>
          <el-table :data="detail.tests" size="small">
            <el-table-column type="expand">
              <template #default="{ row }">
                <pre v-if="row.detail" class="err-pre">{{ row.detail }}</pre>
                <p v-else class="no-err">无失败详情</p>
              </template>
            </el-table-column>
            <el-table-column prop="id" label="用例 ID" width="190" />
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column label="结果" width="90">
              <template #default="{ row }">
                <el-tag :type="row.passed ? 'success' : 'danger'" size="small">
                  {{ row.passed ? 'PASS' : 'FAIL' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </template>

        <template v-if="detail.shots?.length">
          <h4 class="sec-title">截图（{{ detail.shots.length }}，点击放大）</h4>
          <div class="shots-grid">
            <div v-for="name in detail.shots" :key="name" class="shot-item">
              <el-image :src="shotUrl(detail.id, name)" :preview-src-list="[shotUrl(detail.id, name)]"
                        fit="cover" class="shot-img" preview-teleported lazy />
              <div class="shot-name">{{ name }}</div>
            </div>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { debugApi } from '../../../api'

// 错误清单小组件：标题 + 可滚动列表（空则隐藏）
function ErrList(props) {
  if (!props.items?.length) return null
  return h('div', { class: 'err-block' }, [
    h('div', { class: 'err-title' }, `${props.title}（${props.items.length}）`),
    h('pre', { class: 'err-pre' }, props.items.join('\n')),
  ])
}
ErrList.props = ['title', 'items']

const loading = ref(false)
const items = ref([])
const total = ref(0)
const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

function kindLabel(kind) {
  return { recon: '页面侦察', interactions: '交互测试', edge: '边界测试' }[kind] || kind
}

function hasErrors(row) {
  return (row.console_errors?.length || 0) + (row.page_errors?.length || 0) + (row.failed_reqs?.length || 0) > 0
}

function errCount(row) {
  return (row.console_errors?.length || 0) + (row.page_errors?.length || 0) + (row.failed_reqs?.length || 0)
}

function shotUrl(runId, filename) {
  return debugApi.runs.shotUrl(runId, filename)
}

async function load() {
  loading.value = true
  try {
    const { data } = await debugApi.runs.list()
    items.value = data.items || []
    total.value = data.total ?? items.value.length
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  drawerVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const { data } = await debugApi.runs.get(row.id)
    detail.value = data
  } catch {
    drawerVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function removeRun(row) {
  try {
    await ElMessageBox.confirm(`确定删除运行「${row.id}」及其全部截图？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await debugApi.runs.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 拦截器已提示 */
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

.ok-text {
  color: #67c23a;
  font-weight: 600;
}

.bad-text {
  color: #f56c6c;
  font-weight: 600;
}

.sep {
  color: #c0c4cc;
  margin: 0 2px;
}

.sec-title {
  margin: 16px 0 8px;
  font-size: 14px;
}

.err-lists {
  padding: 4px 16px;
}

.err-block {
  margin-bottom: 8px;
}

.err-title {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.err-pre {
  background: #fef0f0;
  color: #f56c6c;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow: auto;
  margin: 0;
}

.no-err {
  color: #67c23a;
  font-size: 12px;
  margin: 0;
}

.shots-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.shot-img {
  width: 100%;
  height: 140px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  cursor: zoom-in;
}

.shot-name {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  text-align: center;
}
</style>
