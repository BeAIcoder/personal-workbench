<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>MCP 服务器</span>
          <el-button size="small" type="primary" @click="openCreate">新增服务器</el-button>
          <el-button size="small" type="warning" plain @click="openCatalog">服务器目录</el-button>
        </div>
      </template>
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px"
        title="MCP（Model Context Protocol）stdio 服务器：连接后其工具自动注册为 mcp_<服务器>__<工具>，可在 AI 助手「专家团队」白名单中勾选使用。示例：npx -y @modelcontextprotocol/server-filesystem &lt;目录&gt;" />
      <el-table :data="items" size="small" empty-text="暂无服务器">
        <el-table-column prop="name" label="名称" width="140" />
        <el-table-column label="启动命令" min-width="220">
          <template #default="{ row }">
            <code>{{ row.command }} {{ (row.args || []).join(' ') }}</code>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'connected' ? 'success' : (row.status === 'error' ? 'danger' : 'info')" size="small">
              {{ { connected: '已连接', error: '错误', stopped: '未连接' }[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="工具" width="70" align="right">
          <template #default="{ row }">{{ (row.tools || []).length }}</template>
        </el-table-column>
        <el-table-column label="启用" width="70" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" size="small" @change="toggleEnabled(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status !== 'connected'" size="small" link type="primary" :loading="connectingId === row.id" @click="connect(row)">连接</el-button>
            <el-button v-else size="small" link type="warning" @click="disconnect(row)">断开</el-button>
            <el-button size="small" link type="primary" :disabled="row.status !== 'connected'" @click="openTest(row)">测试调用</el-button>
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-alert v-for="row in items.filter((i) => i.last_error)" :key="row.id" :title="`${row.name}：${row.last_error}`" type="error" :closable="false" style="margin-top: 8px" />
    </el-card>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑服务器' : '新增 MCP 服务器'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" :disabled="!!dialog.id" placeholder="如 filesystem（工具前缀 mcp_filesystem__*）" />
        </el-form-item>
        <el-form-item label="启动命令" required>
          <el-input v-model="form.command" placeholder="如 npx / python / uv / node" />
        </el-form-item>
        <el-form-item label="参数">
          <el-input v-model="argsText" placeholder="空格分隔，如：-y @modelcontextprotocol/server-filesystem D:\文档" />
        </el-form-item>
        <el-form-item label="环境变量">
          <el-input v-model="envText" placeholder='JSON，如：{"API_KEY":"xxx"}（仅存本机库）' type="textarea" :rows="2" @input="envTouched = true" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="testDialog.visible" title="测试调用" width="560px">
      <el-form label-width="70px">
        <el-form-item label="工具">
          <el-select v-model="testDialog.tool" style="width: 100%">
            <el-option v-for="t in testDialog.tools" :key="t.name" :label="t.name" :value="t.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="参数">
          <el-input v-model="testDialog.arguments" type="textarea" :rows="4" placeholder='JSON，如：{"path":"D:\\"}' />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="testDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="testDialog.calling" @click="runTest">调用</el-button>
      </template>
      <pre v-if="testDialog.result" class="result">{{ testDialog.result }}</pre>
    </el-dialog>

    <el-dialog v-model="catalogDialog.visible" title="MCP 服务器目录（内置常用服务器模板）" width="720px">
      <el-table :data="catalogDialog.items" size="small" height="380" v-loading="catalogDialog.loading"
                empty-text="目录为空">
        <el-table-column prop="label" label="名称" min-width="150" />
        <el-table-column prop="description" label="说明" min-width="280" show-overflow-tooltip />
        <el-table-column prop="command" label="启动命令" min-width="140" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.installed ? 'success' : 'info'">{{ row.installed ? '已添加' : '未添加' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" :disabled="row.installed"
                       :loading="catalogDialog.adding === row.id" @click="catalogAdd(row)">添加</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-alert type="info" :closable="false" style="margin-top: 10px"
        title="按目录模板一键添加（默认停用）：添加后在列表里「编辑」可改命令/参数/环境变量，「连接」注册工具。" />
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { buildServerUpdatePayload, describeApiError, formatArgs, parseArgsText, parseEnvText, parseJsonObject } from '../utils/mcp'

const loading = ref(false)
const items = ref([])
const connectingId = ref(null)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ name: '', command: '', enabled: true })
const argsText = ref('')
const envText = ref('')
// 编辑时 envText 只是脱敏占位（'{}'）：用户没动过就不把 env 发给后端，避免静默清空
const envTouched = ref(false)
const testDialog = reactive({ visible: false, server: null, tools: [], tool: '', arguments: '{}', calling: false, result: '' })

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/mcp/servers')
    items.value = data.items || []
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, { name: '', command: '', enabled: true })
  argsText.value = ''
  envText.value = ''
  envTouched.value = false
}

function openCreate() {
  dialog.id = null
  resetForm()
  dialog.visible = true
}

function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, { name: row.name, command: row.command, enabled: row.enabled })
  argsText.value = formatArgs(row.args || [])
  envText.value = '{}'
  envTouched.value = false
  dialog.visible = true
}

function parseArgs() {
  return parseArgsText(argsText.value)
}

function parseEnv() {
  return parseEnvText(envText.value)
}

async function save() {
  if (!form.name.trim() || !form.command.trim()) {
    ElMessage.warning('名称与启动命令必填')
    return
  }
  let env
  try {
    env = parseEnv()
  } catch (e) {
    ElMessage.error(e.message)
    return
  }
  dialog.saving = true
  try {
    const payload = { name: form.name.trim(), command: form.command.trim(), args: parseArgs(), env, enabled: form.enabled }
    if (dialog.id) {
      await api.put(`/mcp/servers/${dialog.id}`, buildServerUpdatePayload(payload, { envTouched: envTouched.value }))
    } else {
      await api.post('/mcp/servers', payload)
    }
    ElMessage.success('已保存')
    dialog.visible = false
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    dialog.saving = false
  }
}

async function connect(row) {
  connectingId.value = row.id
  try {
    const { data } = await api.post(`/mcp/servers/${row.id}/connect`, {}, { timeout: 30000 })
    ElMessage.success(`已连接，注册 ${(data.tools || []).length} 个工具`)
    await load()
  } catch {
    await load()  // 拦截器已提示；刷新列表拿到最新连接状态/错误
  } finally {
    connectingId.value = null
  }
}

async function disconnect(row) {
  try {
    await api.post(`/mcp/servers/${row.id}/disconnect`)
    ElMessage.success('已断开')
  } catch {
    /* 拦截器已提示 */
  }
  await load()  // 无论成败都刷新：失败时也要拿到后端记录的真实状态
}

async function toggleEnabled(row) {
  try {
    await api.put(`/mcp/servers/${row.id}`, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '已启用（下次启动/手动连接生效）' : '已停用')
  } catch {
    row.enabled = !row.enabled  // 启停失败回滚开关（拦截器已提示）
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除服务器「${row.name}」及其工具注册？`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await api.delete(`/mcp/servers/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

function openTest(row) {
  testDialog.server = row
  testDialog.tools = row.tools || []
  testDialog.tool = testDialog.tools[0]?.name || ''
  testDialog.arguments = '{}'
  testDialog.result = ''
  testDialog.visible = true
}

async function runTest() {
  let args
  try {
    args = parseJsonObject(testDialog.arguments, '参数')
  } catch (e) {
    ElMessage.error(e.message)
    return
  }
  testDialog.calling = true
  try {
    const { data } = await api.post(`/mcp/servers/${testDialog.server.id}/test-call`, { tool: testDialog.tool, arguments: args }, { timeout: 60000 })
    testDialog.result = data.text || JSON.stringify(data.raw || data, null, 2)
  } catch (e) {
    testDialog.result = `调用失败：${describeApiError(e)}`
  } finally {
    testDialog.calling = false
  }
}

onMounted(load)

const catalogDialog = reactive({ visible: false, loading: false, adding: '', items: [] })

async function openCatalog() {
  catalogDialog.visible = true
  catalogDialog.loading = true
  try {
    const { data } = await api.get('/mcp/catalog')
    catalogDialog.items = data.servers || []
  } catch { /* 拦截器已提示 */ } finally {
    catalogDialog.loading = false
  }
}

async function catalogAdd(row) {
  catalogDialog.adding = row.id
  try {
    await api.post(`/mcp/catalog/${row.id}/add`, { name: row.name })
    ElMessage.success(`${row.label} 已添加（默认停用），请在列表中编辑并连接`)
    row.installed = true
    await load()
  } catch { /* 拦截器已提示 */ } finally {
    catalogDialog.adding = ''
  }
}
</script>

<style scoped>
.result {
  max-height: 300px;
  overflow: auto;
  background: var(--el-fill-color-light);
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
}
</style>
