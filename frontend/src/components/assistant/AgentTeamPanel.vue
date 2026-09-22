<template>
  <div class="agent-team">
    <!-- 路由测试 -->
    <div class="route-test">
      <el-input
        v-model="routeText"
        placeholder="输入一句话测试路由，如：帮我算一下增值税"
        clearable
        @keyup.enter="runRouteTest"
      >
        <template #append>
          <el-button :loading="routeTesting" @click="runRouteTest">测试路由</el-button>
        </template>
      </el-input>
      <div v-if="routeResult" class="route-result">
        <template v-if="routeResult.matches && routeResult.matches.length">
          <span
            v-for="m in routeResult.matches"
            :key="m.name"
            class="route-hit"
            :class="{ top: routeResult.routed === m.name }"
          >
            {{ m.display_name }} · 命中 {{ m.hits }} · 得分 {{ m.score }}
          </span>
          <span class="route-by">（{{ routeResult.matched_by }}）</span>
        </template>
        <span v-else class="route-none">无命中关键词，将走兜底专家 / 智能路由</span>
      </div>
    </div>

    <div class="team-toolbar">
      <el-button type="primary" plain size="small" @click="openForm()">
        <el-icon><Plus /></el-icon>&nbsp;新建专家
      </el-button>
      <el-button size="small" @click="resetBuiltin">恢复出厂</el-button>
    </div>

    <!-- 专家卡片 -->
    <div v-loading="loading" class="agent-list">
      <div
        v-for="a in agents"
        :key="a.id"
        class="agent-card"
        :class="{ off: !a.enabled, hit: hitNames.has(a.name) }"
      >
        <div class="agent-head">
          <span class="agent-emoji" :style="{ background: a.color || '#909399' }">{{ a.emoji }}</span>
          <div class="agent-title">
            <b>{{ a.display_name }}</b>
            <span class="agent-name">{{ a.name }}</span>
            <el-tag v-if="a.is_builtin" size="small" effect="plain" type="info">内置</el-tag>
          </div>
          <div class="spacer"></div>
          <el-switch
            :model-value="a.enabled"
            size="small"
            :loading="togglingId === a.id"
            @change="(v) => toggleEnabled(a, v)"
          />
        </div>
        <div class="agent-desc">{{ a.description || '（无描述）' }}</div>
        <div class="agent-meta">
          <span class="meta-item">🤖 {{ agentModelLabel(a, models) }}</span>
          <span class="meta-item">排序 {{ a.sort }}</span>
        </div>
        <div v-if="a.keywords && a.keywords.length" class="agent-kws">
          <el-tag
            v-for="k in a.keywords.slice(0, 8)"
            :key="k"
            size="small"
            effect="plain"
            class="kw-tag"
          >{{ k }}</el-tag>
          <span v-if="a.keywords.length > 8" class="kw-more">+{{ a.keywords.length - 8 }}</span>
        </div>
        <div class="agent-ops">
          <el-button size="small" link type="primary" @click="openForm(a)">编辑</el-button>
          <el-button size="small" link type="danger" @click="removeAgent(a)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !agents.length" description="还没有专家，点上方「新建专家」" :image-size="60" />
    </div>

    <!-- 新建/编辑对话框 -->
    <el-dialog v-model="form.visible" :title="form.id ? '编辑专家' : '新建专家'" width="560px" append-to-body>
      <el-form label-width="100px" label-position="left">
        <el-form-item label="名称" required :error="nameError">
          <el-input
            v-model="form.name"
            :disabled="!!form.id"
            placeholder="小写字母开头，如 tax_expert"
            maxlength="40"
            @input="nameError = ''"
          />
          <div class="cfg-hint">路由与 API 标识，规则：小写字母开头，仅小写字母/数字/下划线；创建后不可改</div>
        </el-form-item>
        <el-form-item label="显示名" required>
          <el-input v-model="form.display_name" maxlength="30" placeholder="如：税务专家" />
        </el-form-item>
        <el-form-item label="图标 / 颜色">
          <div class="emoji-color">
            <el-input v-model="form.emoji" maxlength="4" style="width: 90px" placeholder="🧾" />
            <el-color-picker v-model="form.color" />
          </div>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" maxlength="100" placeholder="一句话说明这个专家擅长什么" />
        </el-form-item>
        <el-form-item label="角色提示词">
          <el-input
            v-model="form.role_prompt"
            type="textarea"
            :rows="4"
            placeholder="系统级角色设定，如：你是一名资深税务师……"
          />
        </el-form-item>
        <el-form-item label="路由关键词">
          <el-select
            v-model="form.keywords"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            style="width: 100%"
            placeholder="输入后回车添加，命中关键词即路由给该专家"
          />
        </el-form-item>
        <el-form-item label="绑定模型">
          <el-select v-model="form.model_id" clearable style="width: 100%" placeholder="跟随全局默认">
            <el-option label="跟随全局默认" :value="null" />
            <el-option v-for="m in models" :key="m.id" :label="m.label || m.model" :value="m.id" />
          </el-select>
          <div class="cfg-hint">不选则使用全局当前激活模型</div>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort" :min="0" :max="999" style="width: 160px" />
          <div class="cfg-hint">数字越小越靠前</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="form.visible = false">取消</el-button>
        <el-button type="primary" :loading="form.saving" @click="saveAgent">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { assistantApi } from '../../api'
import { agentModelLabel, isValidAgentName, normalizeKeywords } from '../../utils/agent'

const emit = defineEmits(['team-changed'])

const agents = ref([])
const models = ref([])
const loading = ref(false)
const togglingId = ref(null)

// ---------------- 路由测试 ----------------
const routeText = ref('')
const routeTesting = ref(false)
const routeResult = ref(null)

const hitNames = computed(() => new Set((routeResult.value?.matches || []).map((m) => m.name)))

async function runRouteTest() {
  const text = routeText.value.trim()
  if (!text) {
    ElMessage.warning('请先输入一句话')
    return
  }
  routeTesting.value = true
  try {
    const { data } = await assistantApi.agents.routeTest(text)
    routeResult.value = data
  } catch {
    /* 拦截器已提示 */
  } finally {
    routeTesting.value = false
  }
}

// ---------------- 列表 ----------------
async function loadAgents() {
  loading.value = true
  try {
    const { data } = await assistantApi.agents.list()
    agents.value = data.agents ?? data
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function loadModels() {
  try {
    const { data } = await assistantApi.models.list()
    models.value = data.models ?? data
  } catch {
    /* 拦截器已提示 */
  }
}

async function toggleEnabled(a, v) {
  togglingId.value = a.id
  try {
    await assistantApi.agents.update(a.id, { enabled: v })
    a.enabled = v
    emit('team-changed')
  } catch {
    /* 拦截器已提示 */
  } finally {
    togglingId.value = null
  }
}

// ---------------- 新建 / 编辑 ----------------
const form = reactive({
  visible: false,
  id: null,
  name: '',
  display_name: '',
  emoji: '🤖',
  color: '#409eff',
  description: '',
  role_prompt: '',
  keywords: [],
  model_id: null,
  enabled: true,
  sort: 100,
  saving: false,
})
const nameError = ref('')

function openForm(a) {
  nameError.value = ''
  if (a) {
    Object.assign(form, {
      visible: true,
      id: a.id,
      name: a.name,
      display_name: a.display_name,
      emoji: a.emoji || '🤖',
      color: a.color || '#409eff',
      description: a.description || '',
      role_prompt: a.role_prompt || '',
      keywords: [...(a.keywords || [])],
      model_id: a.model_id ?? null,
      enabled: a.enabled,
      sort: a.sort ?? 100,
      saving: false,
    })
  } else {
    Object.assign(form, {
      visible: true,
      id: null,
      name: '',
      display_name: '',
      emoji: '🤖',
      color: '#409eff',
      description: '',
      role_prompt: '',
      keywords: [],
      model_id: null,
      enabled: true,
      sort: 100,
      saving: false,
    })
  }
}

async function saveAgent() {
  const keywords = normalizeKeywords(form.keywords)
  if (!form.id && !isValidAgentName(form.name)) {
    nameError.value = '名称须以小写字母开头，仅含小写字母/数字/下划线'
    return
  }
  if (!form.display_name.trim()) {
    ElMessage.warning('请填写显示名')
    return
  }
  form.saving = true
  try {
    const payload = {
      display_name: form.display_name.trim(),
      emoji: form.emoji,
      color: form.color,
      description: form.description,
      role_prompt: form.role_prompt,
      keywords,
      model_id: form.model_id ?? null,  // 显式 null = 清空绑定，跟随全局
      enabled: form.enabled,
      sort: form.sort,
    }
    if (form.id) {
      await assistantApi.agents.update(form.id, payload)
    } else {
      await assistantApi.agents.create({ ...payload, name: form.name.trim() })
    }
    form.visible = false
    ElMessage.success('专家已保存')
    await loadAgents()
    emit('team-changed')
  } catch {
    /* 拦截器已提示（含 name 重名 400） */
  } finally {
    form.saving = false
  }
}

// ---------------- 删除 / 恢复出厂 ----------------
async function removeAgent(a) {
  try {
    const tip = a.is_builtin
      ? `「${a.display_name}」是内置专家，删除后可用「恢复出厂」找回。确定删除吗？`
      : `确定删除专家「${a.display_name}」吗？删除后不可恢复。`
    await ElMessageBox.confirm(tip, '删除确认', { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' })
  } catch {
    return  // 用户取消
  }
  try {
    await assistantApi.agents.remove(a.id)
    ElMessage.success('已删除')
    await loadAgents()
    emit('team-changed')
  } catch {
    /* 拦截器已提示 */
  }
}

async function resetBuiltin() {
  try {
    await ElMessageBox.confirm(
      '恢复出厂会把内置专家还原为初始配置（覆盖你对内置专家的修改），自定义专家不受影响。确定继续吗？',
      '恢复出厂',
      { type: 'warning' },
    )
  } catch {
    return  // 用户取消
  }
  try {
    await assistantApi.agents.reset()
    ElMessage.success('内置专家已恢复出厂')
    await loadAgents()
    emit('team-changed')
  } catch {
    /* 拦截器已提示 */
  }
}

onMounted(() => {
  loadAgents()
  loadModels()
})

defineExpose({ reload: loadAgents })
</script>

<style scoped>
.route-test {
  margin-bottom: 12px;
}

.route-result {
  margin-top: 6px;
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.route-hit {
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 1px 8px;
  color: #606266;
}

.route-hit.top {
  border-color: #67c23a;
  color: #529b2e;
  background: #f0f9eb;
}

.route-by {
  color: #c0c4cc;
}

.route-none {
  color: #909399;
}

.team-toolbar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
}

.agent-list {
  min-height: 120px;
}

.agent-card {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.agent-card.off {
  opacity: 0.55;
}

.agent-card.hit {
  border-color: #67c23a;
  box-shadow: 0 0 0 1px #e1f3d8;
}

.agent-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-head .spacer {
  flex: 1;
}

.agent-emoji {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  color: #fff;
  flex-shrink: 0;
}

.agent-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.agent-name {
  color: #c0c4cc;
  font-size: 12px;
}

.agent-desc {
  color: #909399;
  font-size: 12px;
  margin-top: 6px;
}

.agent-meta {
  display: flex;
  gap: 12px;
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

.agent-kws {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
  align-items: center;
}

.kw-tag {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kw-more {
  color: #c0c4cc;
  font-size: 12px;
}

.agent-ops {
  margin-top: 4px;
  text-align: right;
}

.emoji-color {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cfg-hint {
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
  margin-top: 2px;
  width: 100%;
}
</style>
