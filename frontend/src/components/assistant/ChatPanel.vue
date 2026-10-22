<template>
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
        <div v-if="m.role === 'user'" class="bubble user-bubble">
          <img v-for="(src, j) in m.images || []" :key="j" class="msg-img" :src="src" alt="聊天图片" />
          {{ m.content }}
        </div>
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
          <span class="model-chip" :title="modelChipTitle">
            🤖 {{ shortModel }} <el-icon><ArrowDown /></el-icon>
            <span v-if="isEnvFallback" class="env-fallback-badge">env 兜底</span>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="isEnvFallback" disabled class="env-fallback-tip">
                当前供应商均已停用，正在使用 .env 兜底模型（不在下方列表中）
              </el-dropdown-item>
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
        <template v-for="(a, i) in attachments" :key="i">
          <div v-if="isImageAttachment(a)" class="attach-img-wrap" :title="a.name">
            <img class="attach-img" :src="attachmentImageSrc(a, API_BASE)" :alt="a.name" />
            <el-icon class="attach-img-x" title="移除" @click="removeAttachment(i)"><Close /></el-icon>
          </div>
          <el-tag
            v-else
            closable
            size="small"
            :type="a.kind === 'csv_converted' ? 'success' : 'info'"
            @close="removeAttachment(i)"
          >
            📄 {{ a.name }}{{ a.note ? '（' + a.note + '）' : '' }}
          </el-tag>
        </template>
      </div>
      <div class="input-row">
        <el-upload
          :show-file-list="false"
          :auto-upload="false"
          :on-change="onFilePicked"
          :accept="ASSISTANT_ACCEPT"
        >
          <el-button circle :loading="uploading" title="添加文件或图片分析（≤10MB）">
            <el-icon><Paperclip /></el-icon>
          </el-button>
        </el-upload>
        <el-input
          v-model="draft"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          placeholder="输入问题，Enter 发送，Shift+Enter 换行；可点 📎 附加文件或图片"
          @keydown.enter.exact.prevent="send()"
        />
        <el-button type="primary" :loading="loading" @click="send()">发送</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { ArrowDown, Close, Paperclip } from '@element-plus/icons-vue'
import { assistantApi, API_BASE } from '../../api'
import { ASSISTANT_ACCEPT, attachmentImageSrc, canSend, isImageAttachment, isImageFile, sendBlockHint, splitPreviewUrls } from '../../utils/assistant'

const props = defineProps({
  sessionId: { type: String, default: '' },
  agents: { type: Array, default: () => [] },
  selectedAgent: { type: String, default: null },
  llmInfo: { type: Object, default: () => ({}) },
  models: { type: Array, default: () => [] },
  workModes: { type: Object, default: () => ({}) },
  reasoning: { type: String, default: 'default' },
})

const emit = defineEmits([
  'update:sessionId',
  'session-created',   // 本地生成新会话 id，父组件把占位会话插进列表
  'session-activity',  // 一轮对话结束，父组件刷新会话列表
  'open-settings',
  'model-activated',   // 快速切换模型成功，父组件应用新的模型状态
  'reasoning-saved',   // 思考深度已保存，父组件更新 teamConfig
])

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 链接一律新窗口打开，并防 noopener 安全风险
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

function renderMd(text) {
  try {
    return md.render(text || '')
  } catch {
    // 渲染失败时转义后再进 v-html，避免把未转义原文当 HTML 注入
    return md.utils.escapeHtml(text || '')
  }
}

// ---------------- 对话（SSE 流式） ----------------
const messages = ref([])
const draft = ref('')
const loading = ref(false)
const chatBox = ref(null)
const workMode = ref('standard')
const reasoningEffort = ref(props.reasoning || 'default')

// team 接口异步返回后，父组件回填已保存的思考深度
watch(() => props.reasoning, (v) => {
  reasoningEffort.value = v || 'default'
})
const attachments = ref([])
const uploading = ref(false)

// 当前 SSE 流的控制器；切换/删除会话、发送新消息、组件卸载时 abort
let streamCtrl = null

function abortStream() {
  if (streamCtrl) {
    streamCtrl.abort()
    streamCtrl = null
  }
}

// 图片缩略图用的 objectURL：移除附件或组件卸载时统一 revoke，避免内存泄漏
const objectUrls = new Set()

// objectURL 延迟回收：同一地址可能仍被气泡里的 <img> 引用，同步 revoke 会打断加载中的图片（破图）
const REVOKE_DELAY_MS = 5000
const revokeTimers = new Set()

function createPreviewUrl(file) {
  const url = URL.createObjectURL(file)
  objectUrls.add(url)
  return url
}

function scheduleRevokeUrl(url) {
  const timer = setTimeout(() => {
    revokeTimers.delete(timer)
    if (!objectUrls.has(url)) return
    URL.revokeObjectURL(url)
    objectUrls.delete(url)
  }, REVOKE_DELAY_MS)
  revokeTimers.add(timer)
}

function revokePreviewUrl(url) {
  if (!url || !objectUrls.has(url)) return
  scheduleRevokeUrl(url)
}

// 回收缩略图 objectURL；exceptUrls 中的地址保留（待发送附件的 previewUrl 是唯一显示源，不能连带回收）
function releaseAllPreviewUrls(exceptUrls = []) {
  const { revoke } = splitPreviewUrls([...objectUrls], exceptUrls)
  for (const url of revoke) scheduleRevokeUrl(url)
}

onBeforeUnmount(() => {
  abortStream()
  releaseAllPreviewUrls()
})

const suggestions = [
  '帮我看看工作台现在有多少逾期任务',
  '创建明天上午10点与品牌方洽谈的日程',
  '把上月的费用明细发我帮你解读',
]

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function modeLabel(k) {
  return props.workModes[k]?.label || k
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

function genSessionId() {
  return 's_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

function ensureSession() {
  if (props.sessionId) return props.sessionId
  const sid = genSessionId()
  emit('update:sessionId', sid)
  emit('session-created', { session_id: sid, title: '新会话', msg_count: 0, updated_at: '' })
  return sid
}

// 父组件切换会话时调用：拉历史并渲染
async function loadHistory(sid) {
  // 旧会话气泡里的本地缩略图 objectURL 一并回收；待发送附件的预览地址保留
  releaseAllPreviewUrls(attachments.value.map((a) => a.previewUrl))
  messages.value = []
  try {
    const { data } = await assistantApi.history(sid)
    messages.value = data.messages.map((m) => ({
      role: m.role,
      content: m.content,
      thinking: m.thinking,
      agentLabel: m.role === 'user' ? '' : m.agent_label || '助手',
      agentName: m.agent_name,
      color: (props.agents.find((a) => a.name === m.agent_name) || {}).color,
      trace: m.trace,
      time: (m.created_at || '').slice(11),
    }))
    scrollBottom()
  } catch {
    // 历史加载失败：给明确反馈，不再静默忽略（否则切换会话后只剩空白）
    ElMessage.error('历史消息加载失败，请检查后端服务后重试')
  }
}

function clearMessages() {
  // 清空气泡：只回收已入气泡的缩略图，待发送附件的预览地址保留
  releaseAllPreviewUrls(attachments.value.map((a) => a.previewUrl))
  messages.value = []
}

async function send(text) {
  const content = (text || draft.value).trim()
  if (loading.value) {
    // 生成中再按 Enter / 点发送：明确提示，不静默吞掉
    ElMessage.warning('正在生成回复，请等待当前回复结束后再发送')
    return
  }
  if (!canSend(content, attachments.value)) {
    // 空文本+附件：后端不支持只发附件，给明确提示而不是静默吞掉
    const hint = sendBlockHint(content, attachments.value)
    if (hint) ElMessage.warning(hint)
    return
  }
  // 只有发送草稿本身才清空输入框；建议/auto 等显式文本发送不冲掉用户正在输入的内容
  if (!text) draft.value = ''
  const sid = ensureSession()
  messages.value.push({ role: 'user', content, time: nowTime() })

  const atts = attachments.value.splice(0)  // 发送后清空附件
  const userMsg = messages.value[messages.value.length - 1]
  // 图片附件：气泡里显示缩略图（优先上传响应 url，其次本地 objectURL），不再拼文件名文本
  const images = atts
    .filter((a) => isImageAttachment(a))
    .map((a) => attachmentImageSrc(a, API_BASE))
    .filter(Boolean)
  if (images.length) userMsg.images = images
  // 非图片附件：保持原有「（附件：…）」文本行为
  const files = atts.filter((a) => !isImageAttachment(a))
  if (files.length) {
    userMsg.content += '\n（附件：' + files.map((a) => a.name).join('、') + '）'
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

  let ctrl = null
  try {
    abortStream()  // 发送新消息前先打断旧流
    ctrl = new AbortController()
    streamCtrl = ctrl
    const resp = await fetch(`${API_BASE}/assistant/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: ctrl.signal,
      body: JSON.stringify({
        session_id: sid,
        message: content,
        agent: props.selectedAgent || undefined,
        mode: workMode.value,
        attachments: atts.length ? atts.map((a) => ({ name: a.name, path: a.path })) : undefined,
      }),
    })
    if (!resp.ok) {
      // 非 2xx：读取错误体给友好提示，不当 SSE 解析
      let msg = `请求失败（HTTP ${resp.status}）`
      try {
        const text = await resp.text()
        const j = JSON.parse(text)
        if (typeof j.detail === 'string') msg = j.detail
        else if (typeof j.message === 'string') msg = j.message
      } catch {
        /* 保留默认提示 */
      }
      throw new Error(msg)
    }
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
          try {
            handleEvent(aiMsg, ev)
          } catch (e) {
            // 单个畸形事件只跳过，不中断整条流
            console.warn('[assistant] 跳过畸形 SSE 事件：', e, ev)
          }
          scrollBottom()
        }
      }
    }
  } catch (err) {
    if (err?.name === 'AbortError') {
      // 主动打断（切换/删除会话、发新消息、卸载），不算错误
      aiMsg.content = aiMsg.content || '（已停止生成）'
    } else {
      // 真失败：恢复草稿与待发附件（沿用原 previewUrl，不重复创建 objectURL），
      // 并回滚这对乐观气泡，让输入区回到发送前状态可直接重试；失败原因走 toast。
      // 请求期间用户新输入的草稿不能丢：与失败原文合并保留（原文在前），新附件同理保留
      draft.value = draft.value ? content + '\n' + draft.value : content
      if (atts.length) attachments.value.unshift(...atts)
      messages.value = messages.value.filter((m) => m !== userMsg && m !== aiMsg)
      ElMessage.error(err?.message || '请求失败，请确认后端服务正在运行。')
    }
  } finally {
    if (streamCtrl === ctrl) streamCtrl = null
    aiMsg.streaming = false
    loading.value = false
    emit('session-activity')
    scrollBottom()
  }
}

function handleEvent(m, ev) {
  if (!ev || typeof ev !== 'object') return
  if (ev.type === 'meta') {
    m.agentLabel = ev.agent?.label ?? '助手'
    m.color = ev.agent?.color ?? '#909399'
    m.routedBy = ev.routed_by === '指定' ? selectedAgentLabel.value : (ev.routed_by || '')
    if (ev.mode) m.mode = ev.mode
  } else if (ev.type === 'delta') {
    m.content += ev.text ?? ''
  } else if (ev.type === 'thinking_delta') {
    m.thinking += ev.text ?? ''
  } else if (ev.type === 'tool_start') {
    m.toolFlow.push(ev.name ?? '工具')
  } else if (ev.type === 'error') {
    m.error = true
    m.agentLabel = m.agentLabel === '…' ? '系统提示' : m.agentLabel
    if (ev.need_llm_config) {
      m.color = '#E6A23C'
      m.content = ev.message || '请先在设置中配置模型'
    } else {
      m.content = ev.message || '执行失败，请重试'
    }
  } else if (ev.type === 'done') {
    // done.reply 是后端剥离 <think> 后的正文，以它为准
    m.content = ev.reply || m.content
    m.thinking = ev.thinking || m.thinking
    m.trace = Array.isArray(ev.trace) ? ev.trace : []
    m.routedBy = m.routedBy || ev.routed_by || ''
  }
}

const selectedAgentLabel = computed(() => {
  const a = props.agents.find((x) => x.name === props.selectedAgent)
  return a ? a.label : '智能路由'
})

// ---------------- 思考深度 ----------------
async function saveReasoning() {
  try {
    const v = reasoningEffort.value === 'default' ? '' : reasoningEffort.value
    const { data } = await assistantApi.config.saveGlobals({ reasoning_effort: v })
    reasoningEffort.value = data.config.reasoning_effort || 'default'
    emit('reasoning-saved', data.config)
    ElMessage.success(data.config.reasoning_effort ? '思考深度已生效' : '思考深度已恢复为跟随模型')
  } catch {
    /* 拦截器已提示 */
  }
}

// ---------------- 附件上传 ----------------
function removeAttachment(i) {
  const a = attachments.value[i]
  revokePreviewUrl(a?.previewUrl)  // 本地预览地址随移除一起回收
  attachments.value.splice(i, 1)
}

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
    const att = { name: data.name, path: data.path, kind: data.kind, note: data.note }
    // 图片：本地 objectURL 供缩略图预览（后端若返回可访问 url 则优先用它）
    if (isImageFile(raw) || isImageAttachment(att)) att.previewUrl = createPreviewUrl(raw)
    attachments.value.push(att)
    if (data.note) ElMessage.success(data.note)
  } catch {
    /* 拦截器已提示；吞掉拒绝，避免异步事件处理器产生未捕获异常 */
  } finally {
    uploading.value = false
  }
}

// ---------------- 模型快速切换 ----------------
// .env 兜底：供应商全关/未配置时后端回退 .env 模型（不在 models 下拉里），chip 与下拉均明示
const isEnvFallback = computed(() => props.llmInfo?.source === 'env' && !!props.llmInfo?.configured)

const shortModel = computed(() => {
  const m = props.llmInfo.model || '未配置'
  return m.length > 26 ? m.slice(0, 24) + '…' : m
})

const modelChipTitle = computed(() => {
  const base = '当前模型：' + (props.llmInfo.model || '未配置')
  return isEnvFallback.value ? base + '（.env 兜底，供应商均已停用）' : base
})

function onModelCommand(cmd) {
  if (cmd === '__settings') {
    emit('open-settings')
    return
  }
  activateModel(cmd)
}

async function activateModel(m) {
  try {
    const { data } = await assistantApi.providers.activate(m.id ?? m)
    const label = m.label ?? props.models.find((x) => x.id === m)?.label ?? ''
    emit('model-activated', data)
    ElMessage.success(`已切换到：${label}`)
  } catch {
    /* 拦截器已提示 */
  }
}

defineExpose({ abortStream, loadHistory, clearMessages, send, loading })
</script>

<style scoped>
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

.attach-img-wrap {
  position: relative;
  width: 48px;
  height: 48px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid #ebeef5;
  background: #fff;
}

.attach-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.attach-img-x {
  position: absolute;
  top: 0;
  right: 0;
  cursor: pointer;
  padding: 2px;
  font-size: 12px;
  color: #fff;
  background: rgba(0, 0, 0, 0.45);
  border-radius: 0 0 0 6px;
}

.attach-img-x:hover {
  background: rgba(0, 0, 0, 0.65);
}

.msg-img {
  display: block;
  max-width: 220px;
  max-height: 220px;
  border-radius: 6px;
  margin-bottom: 6px;
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

.env-fallback-badge {
  font-size: 10px;
  line-height: 15px;
  color: #e6a23c;
  border: 1px solid #f3d19e;
  border-radius: 8px;
  padding: 0 5px;
}

/* 下拉顶部的 env 兜底说明行：禁用态样式 + 自动换行 */
.env-fallback-tip {
  max-width: 280px;
  white-space: normal;
  line-height: 1.5;
  color: #e6a23c;
  cursor: default;
}

.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
</style>
