<template>
  <div class="page" v-loading="loading">
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px"
      title="专家团文件化：每位专家是一个 .md 文件（YAML frontmatter + 正文角色提示词），存放在 backend/data/expert-packs。导入 = 写入数据库专家（agent_specs）并立即生效；导出 = 把数据库专家保存为文件，便于备份与跨机器迁移。" />
    <el-row :gutter="12">
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>文件专家包</span>
              <div>
                <el-button size="small" type="primary" @click="importAll">全部导入</el-button>
                <el-button size="small" @click="resetFromSeed">从种子重置</el-button>
              </div>
            </div>
          </template>
          <el-table :data="packs" size="small" empty-text="目录中暂无 .md 专家包（可从种子重置生成）">
            <el-table-column prop="name" label="名称" width="130" />
            <el-table-column label="描述" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="row.valid">{{ row.description || '—' }}</span>
                <span v-else class="text-danger">{{ row.error }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="86">
              <template #default="{ row }">
                <el-tooltip v-if="row.valid && row.imported && row.diff" :content="diffTip(row)" placement="top">
                  <el-tag type="warning" size="small">有差异</el-tag>
                </el-tooltip>
                <el-tag v-else-if="row.valid && row.imported" type="success" size="small">已导入</el-tag>
                <el-tag v-else-if="row.valid" type="info" size="small">未导入</el-tag>
                <el-tooltip v-else :content="row.error" placement="top">
                  <el-tag type="danger" size="small">文件错误</el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <el-button size="small" link type="primary" :disabled="!row.valid" @click="importOne(row)">导入</el-button>
                <el-button size="small" link type="primary" @click="viewPack(row)">查看</el-button>
                <el-button size="small" link type="danger" @click="removePack(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header><span>数据库专家</span></template>
          <el-table :data="experts" size="small" empty-text="数据库暂无专家">
            <el-table-column prop="name" label="名称" width="130" />
            <el-table-column prop="display_name" label="显示名" min-width="100" />
            <el-table-column label="是否内置" width="86" align="center">
              <template #default="{ row }">
                <el-tag :type="row.is_builtin ? 'warning' : 'info'" size="small">
                  {{ row.is_builtin ? '内置' : '自定义' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="110">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="exportExpert(row)">导出为文件</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="viewDialog.visible" :title="`${viewDialog.name}.md`" width="720px">
      <pre class="pack-source">{{ viewDialog.content }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { importAllNeedsConfirm } from '../utils/expertPack'

const loading = ref(false)
const packs = ref([])
const experts = ref([])
const viewDialog = reactive({ visible: false, name: '', content: '' })

const ACTION_TEXT = { created: '新建', updated: '更新', unchanged: '无变化' }

// 「有差异」徽标悬浮提示：列出具体差异字段名（diff_fields），附完整差异简述
function diffTip(row) {
  const fields = row.diff_fields || []
  const head = fields.length ? `差异字段：${fields.join('、')}` : '文件与数据库存在差异'
  return row.diff ? `${head}（${row.diff}）` : head
}

async function loadAll() {
  loading.value = true
  try {
    const [packsResp, expertsResp] = await Promise.all([
      api.get('/experts/packs'),
      api.get('/assistant/agents'),
    ])
    packs.value = packsResp.data.items || []
    experts.value = expertsResp.data.agents || []
  } catch {
    packs.value = []
    experts.value = []
  } finally {
    loading.value = false
  }
}

async function importOne(row) {
  // 有差异或右侧数据库专家为内置时，导入会用文件内容覆盖界面修改，先确认
  const dbExpert = experts.value.find((e) => e.name === row.name)
  if (row.diff || dbExpert?.is_builtin) {
    try {
      await ElMessageBox.confirm(
        `将用文件「${row.file}」的内容覆盖数据库专家「${row.name}」的显示名/提示词/描述（工具为并集只增不减；启用状态与绑定模型保留），确定导入？`,
        '导入确认',
        { type: 'warning', confirmButtonText: '覆盖导入', cancelButtonText: '取消' },
      )
    } catch {
      return  // 用户取消
    }
  }
  try {
    const { data } = await api.post(`/experts/packs/import/${encodeURIComponent(row.name)}`)
    ElMessage.success(`已导入「${data.name}」（${ACTION_TEXT[data.action] || data.action}）`)
    await loadAll()
  } catch {
    /* 拦截器已提示；不 catch 会留下未捕获 rejection，被全局采集记成前端报错 */
  }
}

async function importAll() {
  // 存在「有差异或内置」专家时先确认：全部导入会用文件内容覆盖界面修改（与单条导入的保护对齐）
  if (importAllNeedsConfirm(packs.value, experts.value)) {
    try {
      await ElMessageBox.confirm(
        '存在与数据库有差异或为内置的专家：全部导入将用文件内容覆盖这些专家的显示名/提示词/描述（工具为并集只增不减；启用状态与绑定模型保留），确定继续？',
        '全部导入确认',
        { type: 'warning', confirmButtonText: '覆盖导入', cancelButtonText: '取消' },
      )
    } catch {
      return  // 用户取消
    }
  }
  try {
    const { data } = await api.post('/experts/packs/import-all')
    ElMessage.success(`导入 ${data.imported.length} 位，跳过 ${data.skipped.length} 位，失败 ${data.errors.length} 位`)
    if (data.errors.length) {
      ElMessage.error(data.errors.map((e) => `${e.file}：${e.error}`).join('；'))
    }
    await loadAll()
  } catch {
    /* 拦截器已提示 */
  }
}

async function resetFromSeed() {
  try {
    await ElMessageBox.confirm(
      '将用内置种子（backend/seed_experts）覆盖专家包目录中的同名文件。注意：仅重置文件，需再点「全部导入」才会生效到数据库。确定继续？',
      '从种子重置',
      { type: 'warning' },
    )
  } catch {
    return  // 用户取消
  }
  try {
    const { data } = await api.post('/experts/packs/reset')
    ElMessage.success(`已重置 ${data.reset.length} 个种子文件；文件已重置，需再点「全部导入」才会生效到数据库`)
    await loadAll()
  } catch {
    /* 拦截器已提示 */
  }
}

async function viewPack(row) {
  let data
  try {
    data = (await api.get(`/experts/packs/${encodeURIComponent(row.name)}/content`)).data
  } catch {
    return  // 拦截器已提示；读不到内容就不打开空弹窗
  }
  viewDialog.name = data.name
  viewDialog.content = data.content
  viewDialog.visible = true
}

async function removePack(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除专家包文件 ${row.file}？（不影响数据库中的专家）`,
      '删除专家包',
      { type: 'warning' },
    )
  } catch {
    return  // 用户取消
  }
  try {
    const { data } = await api.delete(`/experts/packs/${encodeURIComponent(row.name)}`)
    ElMessage.success(`已删除 ${data.deleted}`)
    await loadAll()
  } catch {
    /* 拦截器已提示 */
  }
}

async function exportExpert(row) {
  // 左侧存在同名且「有差异」的文件时，导出会用数据库内容覆盖它，先确认
  const samePack = packs.value.find((p) => p.name === row.name)
  if (samePack?.diff) {
    try {
      await ElMessageBox.confirm(
        `将覆盖已修改的文件 ${samePack.file}（用数据库专家「${row.display_name || row.name}」的当前内容写回该文件），确定导出？`,
        '覆盖确认',
        { type: 'warning', confirmButtonText: '覆盖导出', cancelButtonText: '取消' },
      )
    } catch {
      return  // 用户取消
    }
  }
  try {
    const { data } = await api.post(`/experts/packs/export/${encodeURIComponent(row.name)}`)
    ElMessage.success(`已导出为 ${data.file}`)
    await loadAll()
  } catch {
    /* 拦截器已提示 */
  }
}

onMounted(loadAll)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pack-source {
  max-height: 60vh;
  overflow: auto;
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
