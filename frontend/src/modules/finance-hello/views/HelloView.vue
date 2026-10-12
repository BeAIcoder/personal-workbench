<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>财务模块示例（finance_hello）</span>
          <el-button size="small" @click="ping">重新自检</el-button>
        </div>
      </template>
      <el-result v-if="failed" icon="error" title="模块接口不可达" sub-title="请确认后端服务已启动（默认 http://127.0.0.1:8000）" />
      <template v-else-if="pong">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="模块名">{{ pong.module }}</el-descriptions-item>
          <el-descriptions-item label="接口">GET /api/finance/hello/ping</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="pong.ok ? 'success' : 'danger'">{{ pong.ok ? 'ok' : '异常' }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <p class="hint">本页由模块注册机制驱动：后端 app/modules/finance_hello + 前端 src/modules/finance-hello。</p>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import api from '../../../api'

const loading = ref(false)
const failed = ref(false)
const pong = ref(null)

async function ping() {
  loading.value = true
  failed.value = false
  try {
    const { data } = await api.get('/finance/hello/ping')
    pong.value = data
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(ping)
</script>

<style scoped>
.page {
  max-width: 720px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint {
  margin: 16px 0 0;
  color: #909399;
  font-size: 13px;
}
</style>
