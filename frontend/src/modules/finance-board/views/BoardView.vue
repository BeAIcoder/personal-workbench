<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>经营财务报表监控看板</span>
          <div>
            <el-button size="small" :loading="running" @click="run">运行管线刷新</el-button>
            <el-select v-model="selected" placeholder="选择快照" size="small" style="width: 200px; margin-left: 8px" @change="loadHtml">
              <el-option v-for="s in items" :key="s.id" :label="`${s.stamp}（${s.status === 'ok' ? '成功' : '失败'}）`" :value="s.id" :disabled="s.status !== 'ok'" />
            </el-select>
          </div>
        </div>
      </template>
      <el-empty v-if="!items.length" description="暂无快照，点右上角「运行管线刷新」生成（首次运行需数秒）" />
      <iframe v-else-if="htmlUrl" :src="htmlUrl" class="board-frame" frameborder="0" sandbox="allow-scripts" />
      <el-alert v-else type="info" :closable="false" title="请选择一个成功快照查看" />
    </el-card>

    <el-card v-if="items.length" shadow="never" style="margin-top: 16px">
      <template #header><span>快照记录</span></template>
      <el-table :data="items" size="small">
        <el-table-column prop="stamp" label="时间戳" width="180" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">
              {{ row.status === 'ok' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="elapsed_sec" label="耗时(秒)" width="100" align="right" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" :disabled="row.status !== 'ok'" @click="selected = row.id; loadHtml()">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const loading = ref(false)
const running = ref(false)
const items = ref([])
const selected = ref(null)

const htmlUrl = computed(() => (selected.value ? `/api/finance/board/snapshots/${selected.value}/html` : ''))

function loadHtml() { /* htmlUrl 计算属性自动刷新 iframe */ }

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/board/snapshots')
    items.value = data.items || []
    const latestOk = items.value.find((s) => s.status === 'ok')
    if (latestOk && !selected.value) selected.value = latestOk.id
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function run() {
  running.value = true
  try {
    const { data } = await api.post('/finance/board/run')
    ElMessage.success(`运行完成（${data.elapsed_sec}s），已归档快照 ${data.stamp}`)
    await load()
    selected.value = data.id
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    running.value = false
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

.board-frame {
  width: 100%;
  height: 1200px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
</style>
