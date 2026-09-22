<template>
  <div class="assistant-layout">
    <!-- 左列：会话 + 团队 -->
    <div class="left-col">
      <SessionList
        :sessions="sessions"
        :current-session-id="currentSessionId"
        @create="newSession"
        @select="switchSession"
        @rename="renameSession"
        @delete="deleteSession"
      />
      <AgentQuickList
        v-model:selected-agent="selectedAgent"
        :agents="agents"
        :llm-ready="llmReady"
        @open-settings="openSettings()"
      />
    </div>

    <!-- 右侧：对话区 -->
    <ChatPanel
      ref="chatRef"
      v-model:session-id="currentSessionId"
      :agents="agents"
      :selected-agent="selectedAgent"
      :llm-info="llmInfo"
      :models="models"
      :work-modes="workModes"
      :reasoning="teamConfig.reasoning_effort || 'default'"
      @session-created="onSessionCreated"
      @session-activity="loadSessions"
      @open-settings="openSettings()"
      @model-activated="applyModelState"
      @reasoning-saved="(cfg) => (teamConfig = cfg)"
    />

    <!-- 设置抽屉 -->
    <SettingsDrawer
      v-model:visible="drawer"
      :providers="providers"
      :presets="presets"
      :team-config="teamConfig"
      :initial-tab="drawerTab"
      @refresh="refreshConfig"
      @team-changed="reloadTeam"
    />
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assistantApi } from '../api'
import SessionList from '../components/assistant/SessionList.vue'
import AgentQuickList from '../components/assistant/AgentQuickList.vue'
import ChatPanel from '../components/assistant/ChatPanel.vue'
import SettingsDrawer from '../components/assistant/SettingsDrawer.vue'

const route = useRoute()
const router = useRouter()

// ---------------- 团队 / 模型状态 ----------------
const agents = ref([])
const llmReady = ref(false)
const llmInfo = ref({})
const providers = ref([])
const models = ref([])
const workModes = ref({})
const presets = ref([])
const teamConfig = ref({})
const selectedAgent = ref(null)

// ---------------- 会话 ----------------
const sessions = ref([])
const currentSessionId = ref('')
const chatRef = ref(null)

async function loadSessions() {
  try {
    const { data } = await assistantApi.sessions.list()
    sessions.value = data.sessions
  } catch {
    /* 服务未启动 */
  }
}

function onSessionCreated(placeholder) {
  sessions.value.unshift(placeholder)
}

async function newSession() {
  chatRef.value?.abortStream()  // 打断正在进行的流式回复
  const sid = 's_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
  try {
    await assistantApi.sessions.create({ session_id: sid, title: '新会话' })
  } catch {
    /* 离线也能先切 */
  }
  currentSessionId.value = sid
  chatRef.value?.clearMessages()
  await loadSessions()
}

async function switchSession(sid) {
  if (sid === currentSessionId.value) return
  chatRef.value?.abortStream()  // 切换会话前打断旧流
  currentSessionId.value = sid
  await chatRef.value?.loadHistory(sid)
}

async function renameSession(s) {
  let value
  try {
    const res = await ElMessageBox.prompt('新的会话名称', '重命名', {
      inputValue: s.title,
      inputPattern: /\S+/,
      inputErrorMessage: '名称不能为空',
    })
    value = res.value
  } catch {
    return  // 用户取消
  }
  if (!value || !value.trim()) return
  await assistantApi.sessions.rename(s.session_id, value.trim())
  s.title = value.trim()
  ElMessage.success('已重命名')
}

async function deleteSession(s) {
  try {
    await ElMessageBox.confirm(`确定删除会话「${s.title}」及其全部消息吗？`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  if (s.session_id === currentSessionId.value) {
    chatRef.value?.abortStream()  // 删除正在流式回复的会话前先打断流
    currentSessionId.value = ''
    chatRef.value?.clearMessages()
  }
  await assistantApi.sessions.remove(s.session_id)
  await loadSessions()
  ElMessage.success('已删除')
}

// ---------------- 模型状态 ----------------
function applyModelState(data) {
  llmReady.value = data.llm.configured
  llmInfo.value = data.llm
  if (data.providers) providers.value = data.providers
  if (data.models) models.value = data.models
}

async function refreshConfig() {
  const { data } = await assistantApi.config.get()
  applyModelState(data)
  teamConfig.value = data.config
}

// 专家团队变化后刷新左侧团队列表（路由行为后端即时生效）
async function reloadTeam() {
  try {
    const { data } = await assistantApi.team()
    agents.value = data.agents
    if (selectedAgent.value && !agents.value.some((a) => a.name === selectedAgent.value)) {
      selectedAgent.value = null  // 被选中的专家已删除/停用，回落到智能路由
    }
  } catch {
    /* 拦截器已提示 */
  }
}

// ---------------- 设置抽屉 ----------------
const drawer = ref(false)
const drawerTab = ref('providers')

function openSettings(tab = 'providers') {
  drawerTab.value = tab
  drawer.value = true
}

// ---------------- 初始化 ----------------
let lastAutoPrompt = ''

async function tryAutoRun() {
  const auto = route.query.auto
  if (!auto || chatRef.value?.loading) return
  const prompt = String(auto)
  if (prompt === lastAutoPrompt) return  // 防同一条指令重复执行（刷新/回退）
  lastAutoPrompt = prompt
  // 清掉 URL 上的 auto 参数，避免刷新页面重复发送
  router.replace({ path: '/assistant' })
  await nextTick()
  chatRef.value?.send(prompt)
}

watch(() => route.query.auto, () => tryAutoRun())

onMounted(async () => {
  try {
    const { data } = await assistantApi.team()
    agents.value = data.agents
    llmReady.value = data.llm.configured
    llmInfo.value = data.llm
    presets.value = data.presets || []
    providers.value = data.providers || []
    models.value = data.models || []
    workModes.value = data.work_modes || {}
    teamConfig.value = data.config || {}
  } catch {
    ElMessage.error('无法获取团队信息，请确认后端服务已启动')
  }
  await loadSessions()
  if (sessions.value.length) await switchSession(sessions.value[0].session_id)
  await tryAutoRun()
})
</script>

<style scoped>
.assistant-layout {
  display: flex;
  gap: 16px;
  height: calc(100vh - 130px);
}

.left-col {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}
</style>
