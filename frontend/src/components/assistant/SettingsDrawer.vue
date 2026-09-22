<template>
  <el-drawer :model-value="visible" title="AI 助手设置" size="640px" @update:model-value="emit('update:visible', $event)">
    <el-tabs v-model="tab">
      <el-tab-pane label="供应商与模型" name="providers">
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
      </el-tab-pane>

      <el-tab-pane label="专家团队" name="agents">
        <AgentTeamPanel @team-changed="emit('team-changed')" />
      </el-tab-pane>

      <el-tab-pane label="工作参数" name="globals">
        <el-form label-width="110px" label-position="left">
          <el-divider content-position="left">Agent 工作模型</el-divider>
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
        <el-button type="primary" :loading="savingCfg" style="width: 100%" @click="saveGlobals">保存工作参数</el-button>
      </el-tab-pane>
    </el-tabs>

    <!-- 供应商表单 -->
    <el-dialog v-model="provDialog.visible" :title="provDialog.id ? '编辑供应商' : '新增供应商'" width="520px" append-to-body>
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
    <el-dialog v-model="modelDialog.visible" :title="modelDialog.id ? '编辑模型' : '添加模型'" width="520px" append-to-body>
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
  </el-drawer>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Close, Plus } from '@element-plus/icons-vue'
import { assistantApi } from '../../api'
import AgentTeamPanel from './AgentTeamPanel.vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  providers: { type: Array, default: () => [] },
  presets: { type: Array, default: () => [] },
  teamConfig: { type: Object, default: () => ({}) },
  initialTab: { type: String, default: 'providers' },
})

const emit = defineEmits(['update:visible', 'refresh', 'team-changed'])

const tab = ref(props.initialTab)

watch(
  () => props.visible,
  (v) => {
    if (!v) return
    tab.value = props.initialTab
    // 打开抽屉时用最新 teamConfig 回填工作参数表单
    Object.assign(globalsForm, {
      max_iters: props.teamConfig.max_iters ?? 5,
      enable_memory: props.teamConfig.enable_memory !== false,
      history_inject: props.teamConfig.history_inject ?? 12,
      enable_skills: !!props.teamConfig.enable_skills,
      skills_dir: props.teamConfig.skills_dir || '',
      enable_bash: !!props.teamConfig.enable_bash,
      enable_plugins: !!props.teamConfig.enable_plugins,
      plugins_dir: props.teamConfig.plugins_dir || '',
    })
    skillsFound.value = null
    pluginsFound.value = null
  },
)

// ---------------- 工作参数 ----------------
const savingCfg = ref(false)
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

async function saveGlobals() {
  savingCfg.value = true
  try {
    await assistantApi.config.saveGlobals({ ...globalsForm })
    ElMessage.success('工作参数已保存并生效')
    emit('refresh')
    emit('update:visible', false)
  } finally {
    savingCfg.value = false
  }
}

// ---------------- 供应商卡片 ----------------
const testingProvId = ref(null)
const provTestResult = ref({})

const presetModels = computed(() => {
  const p = props.presets.find((x) => x.name === provDialog.preset)
  return p ? p.models : []
})

const presetNote = computed(() => {
  const p = props.presets.find((x) => x.name === provDialog.preset)
  return p ? p.note : ''
})

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
  const p = props.presets.find((x) => x.name === name)
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
    emit('refresh')
    provDialog.visible = false
    ElMessage.success('供应商已保存')
  } finally {
    provDialog.saving = false
  }
}

async function toggleProvider(p) {
  await assistantApi.providers.save({ provider_id: p.id, name: p.name, protocol: p.protocol, base_url: p.base_url, enabled: p.enabled })
  emit('refresh')
}

async function deleteProvider(p) {
  try {
    await ElMessageBox.confirm(`确定删除供应商「${p.name}」及其全部模型吗？`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  await assistantApi.providers.remove(p.id)
  emit('refresh')
  ElMessage.success('已删除')
}

async function activateModel(m) {
  try {
    await assistantApi.providers.activate(m.id)
    emit('refresh')
    ElMessage.success(`已切换到：${m.label || m.model}`)
  } catch {
    /* 拦截器已提示 */
  }
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
    emit('refresh')
    modelDialog.visible = false
    ElMessage.success('模型已保存')
  } finally {
    modelDialog.saving = false
  }
}

async function removeModel(p, m) {
  await assistantApi.providers.removeModel(p.id, m.id)
  emit('refresh')
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
</script>

<style scoped>
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

.cfg-hint.ok {
  color: #529b2e;
}
</style>
