<template>
  <div class="assistant-layout">
    <!-- 左列：会话 + 团队 -->
    <div class="left-col">
      <el-card class="sessions-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>会话</span>
            <el-button size="small" type="primary" plain @click="newSession">
              <el-icon><Plus /></el-icon>&nbsp;新建
            </el-button>
          </div>
        </template>
        <div class="session-list">
          <div
            v-for="s in sessions"
            :key="s.session_id"
            class="session-item"
            :class="{ active: s.session_id === currentSessionId }"
            @click="switchSession(s.session_id)"
          >
            <div class="session-title">{{ s.title }}</div>
            <div class="session-meta">
              <span>{{ s.msg_count }} 条</span>
              <span class="session-time">{{ s.updated_at }}</span>
            </div>
            <div class="session-actions" @click.stop>
              <el-icon @click="renameSession(s)"><Edit /></el-icon>
              <el-icon class="danger" @click="deleteSession(s)"><Delete /></el-icon>
            </div>
          </div>
          <el-empty v-if="!sessions.length" description="暂无会话" :image-size="50" />
        </div>
      </el-card>

      <el-card class="team-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>Agent 团队（{{ agents.length }}）</span>
            <div class="team-actions">
              <el-tag size="small" :type="llmReady ? 'success' : 'warning'">{{ llmReady ? '模型已接入' : '模型未配置' }}</el-tag>
              <el-button link type="primary" size="small" style="margin-left: 8px" @click="openSettings">
                <el-icon><Setting /></el-icon>&nbsp;设置
              </el-button>
            </div>
          </div>
        </template>

        <el-alert
          v-if="!llmReady"
          type="warning"
          :closable="false"
          class="llm-alert"
          title="尚未配置模型 API"
          description="点右上角「设置」添加供应商并填 API Key，保存后即可激活团队。"
        />

        <div
          class="agent-item"
          :class="{ active: selectedAgent === null }"
          @click="selectedAgent = null"
        >
          <span class="agent-emoji">🧭</span>
          <div class="agent-info">
            <div class="agent-label">智能路由（推荐）</div>
            <div class="agent-desc">根据问题自动匹配最合适的专家</div>
          </div>
        </div>
        <div
          v-for="a in agents"
          :key="a.name"
          class="agent-item"
          :class="{ active: selectedAgent === a.name }"
          @click="selectedAgent = selectedAgent === a.name ? null : a.name"
        >
          <span class="agent-emoji">{{ a.emoji }}</span>
          <div class="agent-info">
            <div class="agent-label">{{ a.label }}</div>
            <div class="agent-desc">{{ a.description }}</div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 右侧：对话区 -->
    <el-card class="chat-card" shadow="never">
      <div ref="chatBox" class="chat-box">
        <el-empty
          v-if="!messages.length"
          description="向团队提问，或试试下面这些例子"
          :image-size="70"
        />
        <div v-if="!messages.length" class="suggestions">
          <el-button v-for="s in suggestions" :key="s" size="small" plain @click="send(s)">{{ s }}</el-button>
        </div>

        <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
          <div v-if="m.role === 'user'" class="bubble user-bubble">{{ m.content }}</div>
          <div v-else class="bubble ai-bubble" :class="{ 'error-bubble': m.error }">
            <div class="ai-meta">
              <span class="agent-chip" :style="{ background: m.color || '#909399' }">{{ m.agentLabel }}</span>
              <span v-if="m.routedBy" class="routed-by">{{ m.routedBy }}</span>
              <span v-if="m.mode && m.mode !== 'standard'" class="mode-chip">{{ modeLabel(m.mode) }}</span>
              <span class="msg-time">{{ m.time }}</span>
            </div>

            <el-collapse v-if="m.thinking" class="think-collapse">
              <el-collapse-item name="t">
                <template #title>🧠 思考过程</template>
                <div class="thinking-text">{{ m.thinking }}</div>
              </el-collapse-item>
            </el-collapse>

            <div v-if="m.role === 'assistant' && !m.error" class="ai-text md-body" v-html="renderMd(m.content)"></div>
            <div v-else-if="m.role === 'assistant'" class="ai-text">{{ m.content }}</div>
            <div v-if="m.streaming" class="cursor">▍</div>

            <div v-if="m.toolFlow && m.toolFlow.length && m.streaming" class="tool-live">
              <span v-for="(t, j) in m.toolFlow" :key="j" class="tool-chip">🔧 {{ t }}</span>
            </div>

            <el-collapse v-if="m.trace && m.trace.length && !m.streaming" class="trace-collapse">
              <el-collapse-item :title="`🔧 工具调用（${m.trace.length}）`">
                <div v-for="(t, j) in m.trace" :key="j" class="trace-item">
                  <b>{{ t.tool }}</b>
                  <span v-if="t.args && Object.keys(t.args).length">（{{ formatArgs(t.args) }}）</span>
                  <span class="trace-result">→ {{ t.result }}</span>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
      </div>

      <div class="input-area">
        <div class="input-toolbar">
          <el-select v-model="workMode" size="small" class="mode-select">
            <el-option v-for="(v, k) in workModes" :key="k" :label="v.label" :value="k" />
          </el-select>
          <el-tooltip content="思考深度：越深越聪明但更慢更贵；部分模型不支持时自动忽略" placement="top">
            <el-select
              v-model="reasoningEffort"
              size="small"
              class="mode-select"
              @change="saveReasoning"
            >
              <el-option label="🧠 思考：跟随模型" value="default" />
              <el-option label="🧠 思考：关闭（最快）" value="off" />
              <el-option label="🧠 思考：低" value="low" />
              <el-option label="🧠 思考：中" value="medium" />
              <el-option label="🧠 思考：高" value="high" />
              <el-option label="🧠 思考：最大" value="max" />
            </el-select>
          </el-tooltip>
          <el-dropdown trigger="click" @command="onModelCommand">
            <span class="model-chip" :title="'当前模型：' + (llmInfo.model || '未配置')">
              🤖 {{ shortModel }} <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="m in models" :key="m.id" :command="m.id" :disabled="m.active">
                  {{ m.active ? '✓ ' : '' }}{{ m.label }}
                </el-dropdown-item>
                <el-dropdown-item v-if="!models.length" disabled>暂无模型，请到设置中添加</el-dropdown-item>
                <el-dropdown-item divided command="__settings">⚙ 供应商与模型管理…</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
        <div v-if="attachments.length" class="attach-row">
          <el-tag
            v-for="(a, i) in attachments"
            :key="i"
            closable
            size="small"
            :type="a.kind === 'csv_converted' ? 'success' : 'info'"
            @close="attachments.splice(i, 1)"
          >
            📄 {{ a.name }}{{ a.note ? '（' + a.note + '）' : '' }}
          </el-tag>
        </div>
        <div class="input-row">
          <el-upload
            :show-file-list="false"
            :auto-upload="false"
            :on-change="onFilePicked"
            accept=".txt,.md,.csv,.json,.log,.py,.html,.xml,.yaml,.yml,.sql,.xlsx,.xls"
          >
            <el-button circle :loading="uploading" title="添加文件分析（≤10MB）">
              <el-icon><Paperclip /></el-icon>
            </el-button>
          </el-upload>
          <el-input
            v-model="draft"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 4 }"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行；可点 📎 附加文件"
            @keydown.enter.exact.prevent="send()"
          />
          <el-button type="primary" :loading="loading" @click="send()">发送</el-button>
        </div>
      </div>
    </el-card>

    <!-- 设置抽屉：ZCode 式供应商卡片 -->
    <el-drawer v-model="drawer" title="AI 助手设置" size="620px">
      <el-divider content-position="left">供应商与模型（点模型名即切换）</el-divider>
      <div class="prov-list">
        <div v-for="p in providers" :key="p.id" class="prov-card" :class="{ off: !p.enabled }">
          <div class="prov-head">
            <b>{{ p.name }}</b>
            <el-tag size="small" :type="p.protocol === 'anthropic' ? 'warning' : 'primary'" effect="plain">{{ p.protocol }}</el-tag>
            <div class="spacer"></div>
            <el-switch v-model="p.enabled" size="small" @change="toggleProvider(p)" />
            <el-button size="small" link @click="editProvider(p)">编辑</el-button>
            <el-button size="small" link type="danger" @click="deleteProvider(p)">删除</el-button>
          </div>
          <div class="prov-line">🔗 {{ p.base_url || '（未填）' }}</div>
          <div class="prov-line">🔑 {{ p.api_key_set ? '已配置' : '未配置 Key' }}</div>
          <div class="prov-models">
            <span
              v-for="m in p.models"
              :key="m.id"
              class="model-pill"
              :class="{ active: m.active, off: !m.enabled }"
              :title="`上下文 ${m.context_size} · 输出 ${m.max_tokens}${m.multimodal ? ' · 多模态' : ''}`"
              @click="activateModel(m)"
            >
              {{ m.active ? '✓ ' : '' }}{{ m.model }}
              <el-icon class="pill-x" @click.stop="removeModel(p, m)"><Close /></el-icon>
            </span>
            <el-button size="small" link type="primary" @click="openModelForm(p)">
              <el-icon><Plus /></el-icon> 模型
            </el-button>
          </div>
          <div class="prov-ops">
            <el-button size="small" link :loading="testingProvId === p.id + ':text'" @click="testProvider(p, 'text')">连通</el-button>
            <el-button size="small" link :loading="testingProvId === p.id + ':tools'" @click="testProvider(p, 'tools')">工具</el-button>
            <el-button size="small" link :loading="testingProvId === p.id + ':image'" @click="testProvider(p, 'image')">图片</el-button>
          </div>
          <div v-if="provTestResult[p.id]" class="prov-test" :class="{ ok: provTestResult[p.id].ok }">
            <div v-for="(line, j) in provTestResult[p.id].lines" :key="j">{{ line }}</div>
          </div>
        </div>
        <el-empty v-if="!providers.length" description="还没有供应商，点下方「新增供应商」" :image-size="60" />
      </div>
      <el-button class="add-prov" @click="openProviderForm()">
        <el-icon><Plus /></el-icon>&nbsp;新增供应商
      </el-button>

      <el-divider content-position="left">Agent 工作模型</el-divider>
      <el-form label-width="110px" label-position="left">
        <el-form-item label="最大工具轮次">
          <el-input-number v-model="globalsForm.max_iters" :min="1" :max="20" style="width: 220px" />
          <div class="cfg-hint">单轮对话中 Agent 最多连续调用工具的轮数；调大更会"多步处理"，调小更省钱省时</div>
        </el-form-item>

        <el-divider content-position="left">本地技能池（SKILL.md 格式，可直接复用 QwenPaw 技能商店）</el-divider>
        <el-form-item label="技能池目录">
          <el-input v-model="globalsForm.skills_dir" placeholder="如 D:\skills\pool">
            <template #append>
              <el-button :loading="discoveringSkills" @click="discoverSkillsCount">检测</el-button>
            </template>
          </el-input>
          <div v-if="skillsFound !== null" class="cfg-hint" :class="{ ok: skillsFound > 0 }">
            {{ skillsFound > 0 ? `✅ 发现 ${skillsFound} 个技能${skillsNames.length ? '：' + skillsNames.join('、') : ''}` : '未发现技能' }}
          </div>
        </el-form-item>
        <el-form-item label="启用技能">
          <el-switch v-model="globalsForm.enable_skills" />
          <div class="cfg-hint">
            启用后 Agent 可看到技能池里的技能清单，并通过内置 Skill 工具读取技能全文。技能脚本需配合 Bash 使用。
          </div>
        </el-form-item>
        <el-form-item label="允许 Bash">
          <el-switch v-model="globalsForm.enable_bash" />
          <div class="cfg-hint">⚠ 给 Agent 增加命令行执行能力（用于运行技能脚本）。AgentScope 已内置危险文件黑名单，但请知悉风险。</div>
        </el-form-item>

        <el-divider content-position="left">QwenPaw 插件（工具型插件兼容层，实验性）</el-divider>
        <el-form-item label="插件目录">
          <el-input v-model="globalsForm.plugins_dir" placeholder="如 D:\plugins">
            <template #append>
              <el-button :loading="discoveringPlugins" @click="discoverPluginsCount">检测</el-button>
            </template>
          </el-input>
          <div v-if="pluginsFound !== null" class="cfg-hint">
            {{ pluginsFound.loadable > 0 ? `✅ 可兼容加载 ${pluginsFound.loadable} 个插件：` + pluginsFound.plugins.filter(p => p.ok).map(p => `${p.name}(${p.tools.join('/')})`).join('、') : '没有可兼容加载的工具型插件（部分插件依赖 QwenPaw 运行时）' }}
            <div v-for="(p, j) in pluginsFound.plugins.filter(x => !x.ok).slice(0, 3)" :key="j" class="cfg-hint">✗ {{ p.name }}：{{ p.error }}</div>
          </div>
        </el-form-item>
        <el-form-item label="启用插件">
          <el-switch v-model="globalsForm.enable_plugins" />
          <div class="cfg-hint">启用后工具型插件的工具会注入 Agent 工具箱（只读咨询模式下自动关闭）。</div>
        </el-form-item>

        <el-divider content-position="left">记忆管理</el-divider>
        <el-form-item label="启用会话记忆">
          <el-switch v-model="globalsForm.enable_memory" />
        </el-form-item>
        <el-form-item label="注入历史条数">
          <el-input-number v-model="globalsForm.history_inject" :min="0" :max="100" style="width: 220px" />
          <div class="cfg-hint">每轮带入的最近对话条数（用户+助手）。关闭记忆则不携带上下文</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" :loading="savingCfg" @click="saveGlobals">保存工作参数</el-button>
      </template>
    </el-drawer>

    <!-- 供应商表单 -->
    <el-dialog v-model="provDialog.visible" :title="provDialog.id ? '编辑供应商' : '新增供应商'" width="520px">
      <el-form label-width="100px" label-position="left">
        <el-form-item label="快速预设">
          <el-select v-model="provDialog.preset" style="width: 100%" placeholder="可选，自动填充" @change="applyPreset">
            <el-option v-for="p in presets" :key="p.name" :label="p.label" :value="p.name" />
          </el-select>
          <div v-if="presetNote" class="cfg-hint">{{ presetNote }}</div>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="provDialog.name" maxlength="50" placeholder="如：ModelScope 推理" />
        </el-form-item>
        <el-form-item label="协议">
          <el-radio-group v-model="provDialog.protocol">
            <el-radio value="openai">OpenAI 兼容</el-radio>
            <el-radio value="anthropic">Anthropic</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="provDialog.base_url" placeholder="https://…/v1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="provDialog.api_key" type="password" show-password placeholder="编辑时留空 = 保留现值" autocomplete="off" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="provDialog.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="provDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="provDialog.saving" @click="saveProvider">保存</el-button>
      </template>
    </el-dialog>

    <!-- 模型表单 -->
    <el-dialog v-model="modelDialog.visible" :title="modelDialog.id ? '编辑模型' : '添加模型'" width="520px">
      <el-form label-width="110px" label-position="left">
        <el-form-item label="模型名">
          <el-select
            v-if="presetModels.length"
            v-model="modelDialog.model"
            filterable
            allow-create
            default-first-option
            style="width: 100%"
          >
            <el-option v-for="m in presetModels" :key="m" :label="m" :value="m" />
          </el-select>
          <el-input v-else v-model="modelDialog.model" placeholder="如：deepseek-v4-flash" />
        </el-form-item>
        <el-form-item label="上下文长度">
          <el-input-number v-model="modelDialog.context_size" :min="4096" :step="4096" :max="2000000" style="width: 220px" />
        </el-form-item>
        <el-form-item label="输出上限">
          <el-input-number v-model="modelDialog.max_tokens" :min="64" :step="64" :max="500000" style="width: 220px" />
        </el-form-item>
        <el-form-item label="多模态">
          <el-switch v-model="modelDialog.multimodal" />
          <div class="cfg-hint">模型支持图片输入时打开（标记用途，聊天传图功能后续开放）</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="modelDialog.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="modelDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="modelDialog.saving" @click="saveModel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { assistantApi } from '../api'

const route = useRoute()
const router = useRouter()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

function renderMd(text) {
  try {
    return md.render(text || '')
  } catch {
    return text
  }
}

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

function genSessionId() {
  return 's_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

async function loadSessions() {
  try {
    const { data } = await assistantApi.sessions.list()
    sessions.value = data.sessions
  } catch {
    /* 服务未启动 */
  }
}

function ensureSession() {
  if (currentSessionId.value) return currentSessionId.value
  const sid = genSessionId()
  currentSessionId.value = sid
  sessions.value.unshift({ session_id: sid, title: '新会话', msg_count: 0, updated_at: '' })
  return sid
}

async function newSession() {
  const sid = genSessionId()
  try {
    await assistantApi.sessions.create({ session_id: sid, title: '新会话' })
  } catch {
    /* 离线也能先切 */
  }
  currentSessionId.value = sid
  messages.value = []
  await loadSessions()
}

async function switchSession(sid) {
  if (sid === currentSessionId.value) return
  currentSessionId.value = sid
  messages.value = []
  try {
    const { data } = await assistantApi.history(sid)
    messages.value = data.messages.map((m) => ({
      role: m.role,
      content: m.content,
      thinking: m.thinking,
      agentLabel: m.role === 'user' ? '' : m.agent_label || '助手',
      agentName: m.agent_name,
      color: (agents.value.find((a) => a.name === m.agent_name) || {}).color,
      trace: m.trace,
      time: (m.created_at || '').slice(11),
    }))
    scrollBottom()
  } catch {
    /* 忽略 */
  }
}

async function renameSession(s) {
  const { value } = await ElMessageBox.prompt('新的会话名称', '重命名', {
    inputValue: s.title,
    inputPattern: /\S+/,
    inputErrorMessage: '名称不能为空',
  })
  await assistantApi.sessions.rename(s.session_id, value.trim())
  s.title = value.trim()
  ElMessage.success('已重命名')
}

async function deleteSession(s) {
  await ElMessageBox.confirm(`确定删除会话「${s.title}」及其全部消息吗？`, '删除确认', { type: 'warning' })
  await assistantApi.sessions.remove(s.session_id)
  if (s.session_id === currentSessionId.value) {
    currentSessionId.value = ''
    messages.value = []
  }
  await loadSessions()
  ElMessage.success('已删除')
}

// ---------------- 对话（SSE 流式） ----------------
const messages = ref([])
const draft = ref('')
const loading = ref(false)
const chatBox = ref(null)
const workMode = ref('standard')
const reasoningEffort = ref('')
const attachments = ref([])
const uploading = ref(false)

const suggestions = [
  '帮我看看工作台现在有多少逾期任务',
  '创建明天上午10点与品牌方洽谈的日程',
  '把上月的费用明细发我帮你解读',
]

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function modeLabel(k) {
  return workModes.value[k]?.label || k
}

function scrollBottom() {
  nextTick(() => {
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
  })
}

function formatArgs(args) {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${v}`)
    .join('，')
}

async function send(text) {
  const content = (text || draft.value).trim()
  if (!content || loading.value) return
  draft.value = ''
  const sid = ensureSession()
  messages.value.push({ role: 'user', content, time: nowTime() })

  const atts = attachments.value.splice(0)  // 发送后清空附件
  if (atts.length) {
    messages.value[messages.value.length - 1].content +=
      '\n（附件：' + atts.map((a) => a.name).join('、') + '）'
  }

  const aiMsg = reactive({
    role: 'assistant',
    content: '',
    thinking: '',
    agentLabel: '…',
    color: '#909399',
    routedBy: '',
    mode: workMode.value,
    trace: [],
    toolFlow: [],
    time: nowTime(),
    streaming: true,
    error: false,
  })
  messages.value.push(aiMsg)
  loading.value = true
  scrollBottom()

  try {
    const resp = await fetch('/api/assistant/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sid,
        message: content,
        agent: selectedAgent.value || undefined,
        mode: workMode.value,
        attachments: atts.length ? atts.map((a) => ({ name: a.name, path: a.path })) : undefined,
      }),
    })
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        for (const line of raw.split('\n')) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6)
          if (payload === '[DONE]') continue
          let ev
          try {
            ev = JSON.parse(payload)
          } catch {
            continue
          }
          handleEvent(aiMsg, ev)
          scrollBottom()
        }
      }
    }
  } catch {
    aiMsg.error = true
    aiMsg.content = aiMsg.content || '请求失败，请确认后端服务正在运行。'
  } finally {
    aiMsg.streaming = false
    loading.value = false
    loadSessions()
    scrollBottom()
  }
}

function handleEvent(m, ev) {
  if (ev.type === 'meta') {
    m.agentLabel = ev.agent.label
    m.color = ev.agent.color
    m.routedBy = ev.routed_by === '指定' ? selectedAgentLabel.value : ev.routed_by
    m.mode = ev.mode
  } else if (ev.type === 'delta') {
    m.content += ev.text
  } else if (ev.type === 'thinking_delta') {
    m.thinking += ev.text
  } else if (ev.type === 'tool_start') {
    m.toolFlow.push(ev.name)
  } else if (ev.type === 'error') {
    m.error = true
    m.agentLabel = m.agentLabel === '…' ? '系统提示' : m.agentLabel
    if (ev.need_llm_config) {
      m.color = '#E6A23C'
      m.content = ev.message
    } else {
      m.content = ev.message || '执行失败，请重试'
    }
  } else if (ev.type === 'done') {
    // done.reply 是后端剥离 <think> 后的正文，以它为准
    m.content = ev.reply || m.content
    m.thinking = ev.thinking || m.thinking
    m.trace = ev.trace || []
    m.routedBy = m.routedBy || ev.routed_by
  }
}

const selectedAgentLabel = computed(() => {
  const a = agents.value.find((x) => x.name === selectedAgent.value)
  return a ? a.label : '智能路由'
})

// ---------------- 思考深度 ----------------
async function saveReasoning() {
  try {
    const v = reasoningEffort.value === 'default' ? '' : reasoningEffort.value
    const { data } = await assistantApi.config.saveGlobals({ reasoning_effort: v })
    teamConfig.value = data.config
    reasoningEffort.value = data.config.reasoning_effort || 'default'
    ElMessage.success(data.config.reasoning_effort ? '思考深度已生效' : '思考深度已恢复为跟随模型')
  } catch {
    /* 拦截器已提示 */
  }
}

// ---------------- 附件上传 ----------------
async function onFilePicked(uploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  if (raw.size > 10 * 1024 * 1024) {
    ElMessage.warning('文件超过 10MB 上限')
    return
  }
  uploading.value = true
  const sid = ensureSession()
  try {
    const { data } = await assistantApi.config.upload(sid, raw)
    attachments.value.push({ name: data.name, path: data.path, kind: data.kind, note: data.note })
    if (data.note) ElMessage.success(data.note)
  } finally {
    uploading.value = false
  }
}

// ---------------- 模型快速切换 ----------------
const shortModel = computed(() => {
  const m = llmInfo.value.model || '未配置'
  return m.length > 26 ? m.slice(0, 24) + '…' : m
})

function onModelCommand(cmd) {
  if (cmd === '__settings') {
    openSettings()
    return
  }
  activateModel(cmd)
}

async function activateModel(m) {
  try {
    const { data } = await assistantApi.providers.activate(m.id)
    applyModelState(data)
    ElMessage.success(`已切换到：${m.label}`)
  } catch {
    /* 拦截器已提示 */
  }
}

function applyModelState(data) {
  llmReady.value = data.llm.configured
  llmInfo.value = data.llm
  if (data.providers) providers.value = data.providers
  if (data.models) models.value = data.models
}

// ---------------- 设置抽屉：供应商卡片 ----------------
const drawer = ref(false)
const savingCfg = ref(false)
const testingProvId = ref(null)
const provTestResult = ref({})
const globalsForm = reactive({
  max_iters: 5,
  enable_memory: true,
  history_inject: 12,
  enable_skills: false,
  skills_dir: '',
  enable_bash: false,
  enable_plugins: false,
  plugins_dir: '',
})

const discoveringSkills = ref(false)
const skillsFound = ref(null)
const skillsNames = ref([])
const discoveringPlugins = ref(false)
const pluginsFound = ref(null)

async function discoverSkillsCount() {
  if (!globalsForm.skills_dir.trim()) {
    ElMessage.warning('请先填写技能池目录')
    return
  }
  discoveringSkills.value = true
  try {
    const { data } = await assistantApi.config.discoverSkills(globalsForm.skills_dir.trim())
    skillsFound.value = data.count
    skillsNames.value = (data.names || []).slice(0, 8)
  } finally {
    discoveringSkills.value = false
  }
}

async function discoverPluginsCount() {
  if (!globalsForm.plugins_dir.trim()) {
    ElMessage.warning('请先填写插件目录')
    return
  }
  discoveringPlugins.value = true
  try {
    const { data } = await assistantApi.config.discoverPlugins(globalsForm.plugins_dir.trim())
    pluginsFound.value = data
  } finally {
    discoveringPlugins.value = false
  }
}

const presetModels = computed(() => {
  const p = presets.value.find((x) => x.name === provDialog.preset)
  return p ? p.models : []
})

const presetNote = computed(() => {
  const p = presets.value.find((x) => x.name === provDialog.preset)
  return p ? p.note : ''
})

function openSettings() {
  Object.assign(globalsForm, {
    max_iters: teamConfig.value.max_iters ?? 5,
    enable_memory: teamConfig.value.enable_memory !== false,
    history_inject: teamConfig.value.history_inject ?? 12,
    enable_skills: !!teamConfig.value.enable_skills,
    skills_dir: teamConfig.value.skills_dir || '',
    enable_bash: !!teamConfig.value.enable_bash,
    enable_plugins: !!teamConfig.value.enable_plugins,
    plugins_dir: teamConfig.value.plugins_dir || '',
  })
  skillsFound.value = null
  pluginsFound.value = null
  drawer.value = true
}

async function saveGlobals() {
  savingCfg.value = true
  try {
    await assistantApi.config.saveGlobals({ ...globalsForm })
    ElMessage.success('工作参数已保存并生效')
    await refreshConfig()
    drawer.value = false
  } finally {
    savingCfg.value = false
  }
}

// ---- 供应商表单 ----
const provDialog = reactive({
  visible: false,
  id: null,
  preset: null,
  name: '',
  protocol: 'openai',
  base_url: '',
  api_key: '',
  enabled: true,
  saving: false,
})

function openProviderForm() {
  Object.assign(provDialog, { visible: true, id: null, preset: null, name: '', protocol: 'openai', base_url: '', api_key: '', enabled: true, saving: false })
}

function editProvider(p) {
  Object.assign(provDialog, {
    visible: true,
    id: p.id,
    preset: null,
    name: p.name,
    protocol: p.protocol,
    base_url: p.base_url,
    api_key: '',
    enabled: p.enabled,
    saving: false,
  })
}

function applyPreset(name) {
  const p = presets.value.find((x) => x.name === name)
  if (!p) return
  if (p.base_url) provDialog.base_url = p.base_url
  provDialog.protocol = p.protocol || 'openai'
  if (!provDialog.name) provDialog.name = p.label
}

async function saveProvider() {
  if (!provDialog.name.trim()) {
    ElMessage.warning('请填写供应商名称')
    return
  }
  provDialog.saving = true
  try {
    await assistantApi.providers.save({
      provider_id: provDialog.id || undefined,
      name: provDialog.name.trim(),
      protocol: provDialog.protocol,
      base_url: provDialog.base_url,
      api_key: provDialog.api_key || undefined,
      enabled: provDialog.enabled,
    })
    await refreshConfig()
    provDialog.visible = false
    ElMessage.success('供应商已保存')
  } finally {
    provDialog.saving = false
  }
}

async function toggleProvider(p) {
  await assistantApi.providers.save({ provider_id: p.id, name: p.name, protocol: p.protocol, base_url: p.base_url, enabled: p.enabled })
  await refreshConfig()
}

async function deleteProvider(p) {
  await ElMessageBox.confirm(`确定删除供应商「${p.name}」及其全部模型吗？`, '删除确认', { type: 'warning' })
  await assistantApi.providers.remove(p.id)
  await refreshConfig()
  ElMessage.success('已删除')
}

// ---- 模型表单 ----
const modelDialog = reactive({
  visible: false,
  id: null,
  providerId: null,
  model: '',
  context_size: 128000,
  max_tokens: 2048,
  multimodal: false,
  enabled: true,
  saving: false,
})

function openModelForm(p) {
  Object.assign(modelDialog, {
    visible: true,
    id: null,
    providerId: p.id,
    model: '',
    context_size: 128000,
    max_tokens: 2048,
    multimodal: false,
    enabled: true,
    saving: false,
  })
}

async function saveModel() {
  if (!modelDialog.model.trim()) {
    ElMessage.warning('请填写模型名')
    return
  }
  modelDialog.saving = true
  try {
    await assistantApi.providers.saveModel(modelDialog.providerId, {
      model_id: modelDialog.id || undefined,
      model: modelDialog.model.trim(),
      context_size: modelDialog.context_size,
      max_tokens: modelDialog.max_tokens,
      multimodal: modelDialog.multimodal,
      enabled: modelDialog.enabled,
    })
    await refreshConfig()
    modelDialog.visible = false
    ElMessage.success('模型已保存')
  } finally {
    modelDialog.saving = false
  }
}

async function removeModel(p, m) {
  await assistantApi.providers.removeModel(p.id, m.id)
  await refreshConfig()
}

async function testProvider(p, kind) {
  const key = p.id + ':' + kind
  testingProvId.value = key
  try {
    const { data } = await assistantApi.config.test({
      provider_id: p.id,
      test_tools: kind === 'tools',
      include_image: kind === 'image',
    })
    const lines = []
    if (kind === 'text') {
      const t = data.text
      lines.push(t.ok ? `连通 ✅ ${t.latency_ms}ms · 回复：${t.reply}` : `连通 ❌ ${t.error}`)
    } else if (kind === 'tools') {
      const t = data.text, tt = data.tools
      if (!t.ok) lines.push(`连通失败：${t.error}`)
      else lines.push(tt.ok ? `工具调用 ✅ ${tt.latency_ms}ms · 发起调用：${(tt.tool_calls || []).join(', ')}` : `工具调用 ❌ ${tt.error || '未发起调用'}`)
    } else {
      const t = data.text, img = data.image
      if (!t.ok) lines.push(`连通失败：${t.error}`)
      else lines.push(img.ok ? `图片识别 ✅ ${img.latency_ms}ms · 识别：${img.reply}` : `图片识别 ❌ ${img.error || '模型不支持或无法识别'}`)
    }
    provTestResult.value[p.id] = { ok: lines.every((l) => l.includes('✅')), lines }
  } finally {
    testingProvId.value = null
  }
}

async function refreshConfig() {
  const { data } = await assistantApi.config.get()
  applyModelState(data)
  teamConfig.value = data.config
}

// ---------------- 初始化 ----------------
let lastAutoPrompt = ''

async function tryAutoRun() {
  const auto = route.query.auto
  if (!auto || loading.value) return
  const prompt = String(auto)
  if (prompt === lastAutoPrompt) return  // 防同一条指令重复执行（刷新/回退）
  lastAutoPrompt = prompt
  // 清掉 URL 上的 auto 参数，避免刷新页面重复发送
  router.replace({ path: '/assistant' })
  await nextTick()
  send(prompt)
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
    reasoningEffort.value = data.config?.reasoning_effort || 'default'
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

.sessions-card {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.sessions-card :deep(.el-card__body) {
  overflow-y: auto;
  flex: 1;
  padding-top: 6px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.session-item {
  position: relative;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

.session-item:hover {
  background: #f5f7fa;
}

.session-item.active {
  background: #ecf5ff;
  border-color: #d9ecff;
}

.session-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 40px;
}

.session-meta {
  display: flex;
  gap: 8px;
  color: #c0c4cc;
  font-size: 11px;
  margin-top: 2px;
}

.session-actions {
  position: absolute;
  right: 8px;
  top: 8px;
  display: none;
  gap: 6px;
  color: #909399;
}

.session-item:hover .session-actions {
  display: flex;
}

.session-actions .el-icon {
  cursor: pointer;
}

.session-actions .danger:hover {
  color: #f56c6c;
}

.team-card {
  display: flex;
  flex-direction: column;
  flex: 1.1;
  min-height: 0;
}

.team-card :deep(.el-card__body) {
  overflow-y: auto;
  flex: 1;
  padding-top: 6px;
}

.team-actions {
  display: flex;
  align-items: center;
}

.llm-alert {
  margin-bottom: 10px;
}

.agent-item {
  display: flex;
  gap: 10px;
  padding: 7px 8px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

.agent-item:hover {
  background: #f5f7fa;
}

.agent-item.active {
  background: #ecf5ff;
  border-color: #d9ecff;
}

.agent-emoji {
  font-size: 20px;
  line-height: 1.2;
}

.agent-label {
  font-weight: 600;
}

.agent-desc {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.chat-card :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding-bottom: 12px;
}

.chat-box {
  flex: 1;
  overflow-y: auto;
  padding: 4px 6px;
}

.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-bottom: 12px;
}

.msg-row {
  display: flex;
  margin-bottom: 12px;
}

.msg-row.user {
  justify-content: flex-end;
}

.msg-row.assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 86%;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
}

.user-bubble {
  background: #409eff;
  color: #fff;
  white-space: pre-wrap;
}

.ai-bubble {
  background: #f4f4f5;
  color: #303133;
}

.error-bubble {
  background: #fef0f0;
}

.ai-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.agent-chip {
  color: #fff;
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 8px;
}

.routed-by {
  color: #c0c4cc;
  font-size: 11px;
}

.mode-chip {
  color: #e6a23c;
  font-size: 11px;
  border: 1px solid #f3d19e;
  border-radius: 8px;
  padding: 0 6px;
}

.msg-time {
  margin-left: auto;
  color: #c0c4cc;
  font-size: 11px;
}

.think-collapse {
  margin-bottom: 6px;
  border: none;
}

.think-collapse :deep(.el-collapse-item__header) {
  height: 28px;
  font-size: 12px;
  color: #909399;
  background: transparent;
  border-bottom: none;
}

.think-collapse :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: none;
}

.thinking-text {
  color: #909399;
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

.ai-text {
  word-break: break-word;
}

.md-body :deep(p) {
  margin: 4px 0;
}

.md-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
}

.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 4px 10px;
  font-size: 13px;
}

.md-body :deep(code) {
  background: #ebeef5;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 13px;
}

.md-body :deep(pre) {
  background: #282c34;
  color: #abb2bf;
  padding: 10px;
  border-radius: 8px;
  overflow-x: auto;
}

.md-body :deep(pre code) {
  background: transparent;
  color: inherit;
}

.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 20px;
  margin: 4px 0;
}

.cursor {
  display: inline-block;
  color: #409eff;
  animation: blink 1s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.tool-live {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.tool-chip {
  font-size: 12px;
  color: #409eff;
  border: 1px dashed #a0cfff;
  border-radius: 8px;
  padding: 0 8px;
}

.trace-collapse {
  margin-top: 8px;
  border: none;
}

.trace-collapse :deep(.el-collapse-item__header) {
  height: 30px;
  font-size: 12px;
  color: #909399;
  background: transparent;
}

.trace-collapse :deep(.el-collapse-item__wrap) {
  background: transparent;
}

.trace-item {
  font-size: 12px;
  color: #606266;
  padding: 3px 0;
  border-bottom: 1px dashed #ebeef5;
}

.trace-result {
  color: #909399;
}

.input-area {
  border-top: 1px solid #ebeef5;
  padding-top: 8px;
}

.input-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.attach-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.mode-select {
  width: 150px;
}

.model-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #606266;
  background: #f4f4f5;
  border-radius: 10px;
  padding: 3px 10px;
  cursor: pointer;
}

.model-chip:hover {
  background: #ecf5ff;
}

.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

/* 设置抽屉：供应商卡片 */
.prov-list {
  margin-bottom: 10px;
}

.prov-card {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.prov-card.off {
  opacity: 0.55;
}

.prov-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.prov-head .spacer {
  flex: 1;
}

.prov-line {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
  word-break: break-all;
}

.prov-models {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  align-items: center;
}

.model-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  border: 1px solid #dcdfe6;
  border-radius: 10px;
  padding: 2px 10px;
  cursor: pointer;
  background: #fff;
}

.model-pill.active {
  border-color: #67c23a;
  color: #529b2e;
  background: #f0f9eb;
}

.model-pill.off {
  opacity: 0.5;
}

.model-pill:hover {
  border-color: #409eff;
}

.pill-x {
  font-size: 11px;
  color: #c0c4cc;
}

.pill-x:hover {
  color: #f56c6c;
}

.prov-ops {
  margin-top: 4px;
}

.prov-test {
  font-size: 12px;
  margin-top: 4px;
  color: #f56c6c;
  word-break: break-all;
}

.prov-test.ok {
  color: #529b2e;
}

.add-prov {
  width: 100%;
  margin-bottom: 8px;
}

.cfg-hint {
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
  margin-top: 2px;
  width: 100%;
}
</style>
