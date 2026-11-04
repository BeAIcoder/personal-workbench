<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>技能包管理</span>
          <div>
            <el-button size="small" @click="rescan" :loading="loading">重新扫描</el-button>
            <el-button size="small" type="primary" @click="openCreate">新建技能包</el-button>
            <el-button size="small" type="warning" plain @click="openMarket">技能市场</el-button>
            <el-button size="small" type="success" plain @click="openWorkbuddy">从 WorkBuddy 导入</el-button>
          </div>
        </div>
      </template>
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px"
        title="技能包（SKILL.md 格式）存放在后端 backend/data/skills 目录，启用技能池的 Agent 对话时会自动注入启用的技能包（停用的包不会注入）。若 AI 助手设置里的「技能池目录」为空，可手动指向该目录（backend/data/skills）。" />
      <el-table :data="items" size="small" empty-text="暂无技能包，点右上角「新建技能包」或等待首次启动自动播种">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="version" label="版本" width="80">
          <template #default="{ row }">{{ row.version || '-' }}</template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" size="small" @change="toggleEnabled(row)" />
          </template>
        </el-table-column>
        <el-table-column label="文件数" width="80" align="right">
          <template #default="{ row }">{{ (row.files || []).length + 1 }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openFiles(row)">查看文件</el-button>
            <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-alert v-for="row in items.filter((i) => !i.valid)" :key="row.dir"
        :title="`${row.name}：${row.error}`" type="error" :closable="false" style="margin-top: 8px" />
    </el-card>

    <el-dialog v-model="createDialog.visible" title="新建技能包" width="600px">
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" placeholder="小写字母/数字/连字符，如 tax-check（即目录名）" />
        </el-form-item>
        <el-form-item label="描述" required>
          <el-input v-model="createForm.description" type="textarea" :rows="2"
            placeholder="什么时候让 AI 启用这个技能，如：月末结账检查时使用" />
        </el-form-item>
        <el-form-item label="正文">
          <el-input v-model="createForm.body" type="textarea" :rows="8"
            placeholder="SKILL.md 正文（Markdown），留空则使用内置模板" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="createDialog.saving" @click="createPack">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="fileDialog.visible" :title="`查看文件 · ${fileDialog.pack}`" width="720px">
      <el-select v-model="fileDialog.current" style="width: 100%; margin-bottom: 12px" @change="loadFileContent">
        <el-option v-for="f in fileDialog.files" :key="f" :label="f" :value="f" />
      </el-select>
      <pre v-loading="fileDialog.loading" class="file-content">{{ fileDialog.content }}</pre>
    </el-dialog>

    <el-dialog v-model="wbDialog.visible" title="从 WorkBuddy 导入" width="640px">
      <el-form label-width="150px">
        <el-form-item label="WorkBuddy 数据目录">
          <el-input v-model="wbDialog.home" placeholder="默认 C:/Users/Administrator/.workbuddy" />
        </el-form-item>
        <el-form-item label="导入内容">
          <el-checkbox-group v-model="wbDialog.kinds">
            <el-checkbox value="skills">技能包（默认停用导入）</el-checkbox>
            <el-checkbox value="memories">记忆（MEMORY.md 分节）</el-checkbox>
            <el-checkbox value="identity">身份文件（SOUL/IDENTITY/USER/BOOTSTRAP）</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-alert v-if="wbDialog.scan" :closable="false" type="info"
          :title="`扫描：技能包 ${wbDialog.scan.skills.length} 个，记忆 ${wbDialog.scan.memories.length} 节，身份文件 ${wbDialog.scan.identity_files.length} 个`" />
        <el-alert v-if="wbDialog.result" :closable="false" type="success" :title="wbDialog.resultTitle" />
      </el-form>
      <template #footer>
        <el-button @click="wbDialog.visible = false">关闭</el-button>
        <el-button :loading="wbDialog.scanning" @click="wbScan">扫描预览</el-button>
        <el-button type="primary" :loading="wbDialog.importing" @click="wbImport">执行导入</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="marketDialog.visible" title="技能市场（本机技能源聚合）" width="760px">
      <div style="display: flex; gap: 8px; margin-bottom: 10px">
        <el-input v-model="marketDialog.keyword" placeholder="搜索名称/描述" clearable
                  @input="marketSearch" style="flex: 1" />
        <el-button :loading="marketDialog.loading" @click="marketSearch">刷新</el-button>
      </div>
      <el-alert v-if="marketDialog.sources" type="info" :closable="false" style="margin-bottom: 10px"
        :title="`来源：QwenPaw 技能池（${marketDialog.sources.qwenpaw?.available ? '可用' : '未找到'}）、WorkBuddy（${marketDialog.sources.workbuddy?.available ? '可用' : '未找到'}）、平台种子`" />
      <el-table :data="marketDialog.items" size="small" height="380" v-loading="marketDialog.loading"
                empty-text="没有匹配的技能包">
        <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="description" label="描述" min-width="260" show-overflow-tooltip />
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="{ qwenpaw: 'warning', workbuddy: 'success', builtin: 'info' }[row.source]">
              {{ { qwenpaw: 'QwenPaw', workbuddy: 'WorkBuddy', builtin: '内置' }[row.source] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.installed ? 'success' : 'info'">{{ row.installed ? '已安装' : '未安装' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" :disabled="row.installed && !row._dirty"
                       :loading="marketDialog.installing === row.name" @click="marketInstall(row)">
              {{ row.installed ? '更新' : '安装' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { encodePackFilePath, encodePackName } from '../utils/skillPack'

const loading = ref(false)
const items = ref([])

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/skills/packs')
    items.value = data.items || []
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function rescan() {
  loading.value = true
  try {
    const { data } = await api.post('/skills/rescan')
    items.value = data.items || []
    ElMessage.success(`扫描完成，共 ${items.value.length} 个技能包`)
  } catch {
    /* 响应拦截器已提示错误 */
  } finally {
    loading.value = false
  }
}

async function toggleEnabled(row) {
  try {
    await api.put(`/skills/packs/${encodePackName(row.dir)}/enabled`, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '已启用' : '已停用')
  } catch {
    row.enabled = !row.enabled
  }
}

const createDialog = reactive({ visible: false, saving: false })
const wbDialog = reactive({
  visible: false, scanning: false, importing: false,
  home: '', kinds: ['skills', 'memories', 'identity'], scan: null, result: null, resultTitle: '',
})

function openWorkbuddy() {
  wbDialog.scan = null
  wbDialog.result = null
  wbDialog.visible = true
  api.get('/assistant/workbuddy/settings').then(({ data }) => {
    if (data && data.home) wbDialog.home = data.home
  }).catch(() => {})
}

async function wbScan() {
  wbDialog.scanning = true
  try {
    const { data } = await api.get('/assistant/workbuddy/scan', { params: { home: wbDialog.home } })
    wbDialog.scan = data
    if (!data.available) ElMessage.warning(data.message || '未找到 WorkBuddy 数据目录')
  } catch { /* 拦截器已提示 */ } finally {
    wbDialog.scanning = false
  }
}

async function wbImport() {
  if (!wbDialog.kinds.length) {
    ElMessage.warning('请至少勾选一类导入内容')
    return
  }
  wbDialog.importing = true
  try {
    const { data } = await api.post('/assistant/workbuddy/import', {
      home: wbDialog.home,
      skills: wbDialog.kinds.includes('skills'),
      memories: wbDialog.kinds.includes('memories'),
      identity: wbDialog.kinds.includes('identity'),
    })
    wbDialog.result = data
    wbDialog.resultTitle = `导入完成：技能 新增${data.skills.added.length}/更新${data.skills.updated.length}；`
      + `记忆 新增${data.memories.added}/跳过${data.memories.skipped}；`
      + `身份 新增${data.identity.added.length}/更新${data.identity.updated.length}`
    ElMessage.success('WorkBuddy 导入完成')
    await load()
  } catch { /* 拦截器已提示 */ } finally {
    wbDialog.importing = false
  }
}

const marketDialog = reactive({
  visible: false, loading: false, installing: '',
  keyword: '', items: [], sources: null,
})
let marketTimer = null

function openMarket() {
  marketDialog.visible = true
  marketSearch()
}

function marketSearch() {
  clearTimeout(marketTimer)
  marketTimer = setTimeout(doMarketSearch, 250)
}

onBeforeUnmount(() => {
  clearTimeout(marketTimer)
  marketTimer = null
})

async function doMarketSearch() {
  marketDialog.loading = true
  try {
    const { data } = await api.get('/skills/market/scan', { params: { keyword: marketDialog.keyword } })
    marketDialog.items = data.items || []
    marketDialog.sources = data.sources || null
  } catch { /* 拦截器已提示 */ } finally {
    marketDialog.loading = false
  }
}

async function marketInstall(row) {
  marketDialog.installing = row.name
  try {
    const { data } = await api.post('/skills/market/install', { name: row.name })
    ElMessage.success(`${data.name} 已${data.updated ? '更新' : '安装'}（默认停用，请在列表中启用）`)
    row.installed = true
    await load()
  } catch { /* 拦截器已提示 */ } finally {
    marketDialog.installing = ''
  }
}
const createForm = reactive({ name: '', description: '', body: '' })

function openCreate() {
  Object.assign(createForm, { name: '', description: '', body: '' })
  createDialog.visible = true
}

async function createPack() {
  const name = createForm.name.trim()
  if (!name) {
    ElMessage.warning('请填写名称')
    return
  }
  if (!createForm.description.trim()) {
    ElMessage.warning('请填写描述（AI 助手靠描述决定何时启用技能）')
    return
  }
  createDialog.saving = true
  try {
    await api.post('/skills/packs', {
      name,
      description: createForm.description.trim(),
      body: createForm.body,
    })
    ElMessage.success('已创建')
    createDialog.visible = false
    await load()
  } catch {
    /* 响应拦截器已提示错误 */
  } finally {
    createDialog.saving = false
  }
}

const fileDialog = reactive({ visible: false, pack: '', dir: '', files: [], current: '', content: '', loading: false })

function openFiles(row) {
  fileDialog.pack = row.name
  fileDialog.dir = row.dir
  fileDialog.files = ['SKILL.md', ...(row.files || [])]
  fileDialog.current = 'SKILL.md'
  fileDialog.content = ''
  fileDialog.visible = true
  loadFileContent()
}

async function loadFileContent() {
  if (!fileDialog.current) return
  fileDialog.loading = true
  try {
    const { data } = await api.get(`/skills/packs/${encodePackName(fileDialog.dir)}/files/${encodePackFilePath(fileDialog.current)}`)
    fileDialog.content = data.content || ''
  } catch {
    fileDialog.content = '（读取失败）'
  } finally {
    fileDialog.loading = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除技能包「${row.name}」？目录内所有文件将一并删除。`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await api.delete(`/skills/packs/${encodePackName(row.dir)}`)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 响应拦截器已提示错误 */
  }
}

onMounted(load)
</script>

<style scoped>
.file-content {
  max-height: 480px;
  overflow: auto;
  background: var(--el-fill-color-light);
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
