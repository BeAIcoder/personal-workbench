<template>
  <el-drawer :model-value="visible" title="AI 助手设置" size="640px" @update:model-value="emit('update:visible', $event)">
    <el-tabs v-model="tab">
      <el-tab-pane label="供应商与模型" name="providers">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px"
          :title="effectiveModelText(teamConfig)" />
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
            <div class="prov-line">
              🔑
              <el-tag size="small" :type="providerKeyBadge(p).type" effect="plain">{{ providerKeyBadge(p).text }}</el-tag>
            </div>
            <div class="model-filter-row">
              <el-input
                :model-value="modelQueries[p.id] || ''"
                size="small"
                class="model-search"
                placeholder="搜索模型名"
                aria-label="搜索模型名"
                clearable
                @update:model-value="(v) => setModelQuery(p, v)"
              />
              <el-button
                v-if="hiddenModelCount(p) > 0"
                size="small"
                link
                type="primary"
                @click="expandModels(p)"
              >展开剩余 {{ hiddenModelCount(p) }} 个</el-button>
              <span v-else-if="filteredModelCount(p) > MODEL_PAGE_SIZE" class="models-count">共 {{ filteredModelCount(p) }} 个</span>
            </div>
            <div class="prov-models">
              <div
                v-for="(m, mi) in visibleModels(p)"
                :key="m.id"
                class="model-row"
                :class="{ active: m.active, off: !m.enabled, 'model-row--deferred': mi >= DEFERRED_ROW_FROM }"
              >
                <div class="model-row-main">
                  <span class="model-name" :title="modelTip(m)" @click="activateModel(m)">
                    {{ m.active ? '✓ ' : '' }}{{ m.model }}
                  </span>
                  <el-tag
                    v-for="b in capabilityBadges(m)"
                    :key="b.key"
                    size="small"
                    :type="b.type"
                    :effect="b.effect"
                    :class="{ 'badge-dashed': b.dashed }"
                    :title="b.title"
                  >{{ b.text }}</el-tag>
                  <span v-if="m.probed_at" class="probe-at" :title="`探测时间：${m.probed_at}`">探测于 {{ probeAtShort(m.probed_at) }}</span>
                  <div class="spacer"></div>
                  <el-button size="small" link type="primary" @click="openModelForm(p, m)">编辑</el-button>
                  <el-icon v-if="removingModelId === m.id" class="pill-x is-loading" title="删除中"><Loading /></el-icon>
                  <el-icon v-else class="pill-x" title="删除模型" @click="removeModel(p, m)"><Close /></el-icon>
                </div>
                <div class="model-row-ops">
                  <span class="ops-label">能力探测</span>
                  <el-button size="small" link :loading="probingId === m.id + ':reasoning'" @click="runProbe(m, 'reasoning')">思考级别</el-button>
                  <el-button size="small" link :loading="probingId === m.id + ':context'" @click="runProbe(m, 'context')">上下文</el-button>
                  <el-button size="small" link :loading="probingId === m.id + ':output'" @click="runProbe(m, 'output')">输出</el-button>
                  <el-button size="small" link :loading="probingId === m.id + ':all'" @click="runProbeAll(m)">全部</el-button>
                  <span class="ops-label">实测</span>
                  <el-button size="small" link :loading="testingId === m.id + ':tools'" @click="testProvider(p, 'tools', m)">工具</el-button>
                  <el-button size="small" link :loading="testingId === m.id + ':image'" @click="testProvider(p, 'image', m)">图片</el-button>
                  <el-button
                    v-if="hasManualOverrides(m)"
                    size="small"
                    link
                    :loading="resettingModelId === m.id"
                    @click="resetModelOverrides(m)"
                  >恢复同步值</el-button>
                </div>
              </div>
              <el-button size="small" link type="primary" @click="openModelForm(p)">
                <el-icon><Plus /></el-icon> 模型
              </el-button>
            </div>
            <div class="prov-ops">
              <el-button size="small" link :loading="testingId === p.id + ':text'" @click="testProvider(p, 'text')">连通</el-button>
              <el-button size="small" link :loading="probingProviderId === p.id" @click="runProbeAllModels(p)">探测全部模型</el-button>
              <el-button
                size="small"
                link
                type="primary"
                :loading="syncingProviderId === p.id"
                :disabled="syncingProviderId !== null && syncingProviderId !== p.id"
                @click="syncProviderModels(p)"
              >同步模型</el-button>
              <el-checkbox v-model="syncOverrideManual[p.id]" class="sync-override-cb">覆盖手动/实测</el-checkbox>
            </div>
            <div v-if="syncOverrideManual[p.id]" class="sync-override-warn">
              ⚠ 覆盖开启：本次同步将用同步值覆盖该供应商下模型的手动修改与实测（探测）能力，执行前需确认
            </div>
            <div v-if="syncResults[p.id]" class="sync-summary" :class="syncResults[p.id].level" role="status" aria-live="polite">
              <div>{{ syncResults[p.id].text }}</div>
              <div v-if="syncResults[p.id].warnings.length" class="sync-warn">⚠ {{ syncResults[p.id].warnings.join('；') }}</div>
              <div v-if="syncResults[p.id].skippedItems.length" class="sync-skip">跳过：{{ syncResults[p.id].skippedItems.map((s) => s.model || s).join('、') }}</div>
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
          <div class="base-url-row">
            <el-input v-model="provDialog.base_url" placeholder="https://…/v1" />
            <el-select v-model="provDialog.presetFill" placeholder="从预设填充" clearable style="width: 150px" @change="applyPresetFill">
              <el-option v-for="p in presetOptions" :key="p.name" :label="p.label" :value="p.name" />
            </el-select>
            <el-button size="small" link title="重新读取后端预设文件" @click="reloadPresets">重载</el-button>
          </div>
          <div v-if="presetFillInfo" class="cfg-hint">
            <span v-if="presetFillInfo.note">{{ presetFillInfo.note }} </span>
            <a v-if="presetFillInfo.docs_url" :href="presetFillInfo.docs_url" target="_blank" rel="noopener noreferrer">查看文档</a>
          </div>
        </el-form-item>
        <el-form-item label="预设标识">
          <el-select v-model="provDialog.preset_key" clearable filterable style="width: 100%" placeholder="适配器只认 preset_key（如 volcengine）">
            <el-option v-for="p in presetKeyOptions" :key="p.preset_key" :label="`${p.label}（${p.preset_key}）`" :value="p.preset_key" />
          </el-select>
          <div class="cfg-hint">决定在线同步适配器与能力目录；custom = 无官方列表，仅目录/手动</div>
        </el-form-item>
        <el-form-item label="模型列表地址">
          <el-input v-model="provDialog.models_url" placeholder="https://…/models（可留空）" />
          <div class="cfg-hint">仅允许 http(s)；留空 = 无官方列表，同步时按能力目录兜底并如实标注</div>
        </el-form-item>
        <template v-if="needsAkSk">
          <el-form-item label="管控 Access Key">
            <el-input v-model="provDialog.sync_access_key" type="password" show-password placeholder="可选；编辑时留空 = 保留现值" autocomplete="off" />
          </el-form-item>
          <el-form-item label="管控 Secret Key">
            <el-input v-model="provDialog.sync_secret_key" type="password" show-password placeholder="可选；编辑时留空 = 保留现值" autocomplete="off" />
            <div class="cfg-hint">管控面凭证（如火山 AK/SK）：密封存储、任何界面不回传；仅用于拉取官方基础模型列表，绝不发推理请求</div>
          </el-form-item>
        </template>
        <div v-if="presetPlanHint" class="cfg-hint plan-hint">
          {{ presetPlanHint.text }}
          <a v-if="presetPlanHint.docs_url" :href="presetPlanHint.docs_url" target="_blank" rel="noopener noreferrer">查看文档</a>
        </div>
        <el-form-item v-if="presetCandidateModels.length" label="候选模型">
          <el-select
            v-model="provDialog.presetModels"
            multiple
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            placeholder="保存新供应商后自动添加所选模型（可增删）"
          >
            <el-option v-for="m in presetCandidateModels" :key="m" :label="m" :value="m" />
          </el-select>
          <div class="cfg-hint">来自预设的候选模型；取消勾选则不添加，也可直接输入新模型名</div>
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
        <el-form-item label="最大输入 tokens">
          <el-input-number v-model="modelDialog.max_input_tokens" :min="0" :step="4096" :max="20000000" style="width: 220px" />
          <div class="cfg-hint">0 = 未提供；通常由同步/探测回填，可手动覆盖</div>
        </el-form-item>
        <el-form-item label="工具调用">
          <el-select v-model="modelDialog.supports_tools" style="width: 220px">
            <el-option label="自动（未实测）" :value="-1" />
            <el-option label="支持" :value="1" />
            <el-option label="不支持" :value="0" />
          </el-select>
          <div class="cfg-hint">由「实测」按钮按本模型回写；未知不显示成红叉</div>
        </el-form-item>
        <el-form-item label="温度">
          <el-input-number v-model="modelDialog.temperature" :min="0" :max="2" :step="0.1" :value-on-clear="null" style="width: 220px" />
          <div class="cfg-hint">范围 0-2；留空 = 跟随后端默认</div>
        </el-form-item>
        <el-form-item label="思考支持">
          <el-select v-model="modelDialog.supports_thinking" style="width: 220px">
            <el-option label="自动（未探测）" :value="-1" />
            <el-option label="支持" :value="1" />
            <el-option label="不支持" :value="0" />
          </el-select>
          <div class="cfg-hint">通常由「能力探测」自动回填，此处可手动覆盖</div>
        </el-form-item>
        <el-form-item label="思考级别">
          <el-select
            v-model="modelDialog.thinking_levels"
            multiple
            collapse-tags
            collapse-tags-tooltip
            style="width: 100%"
            placeholder="手动覆盖思考支持后可多选"
            :disabled="modelDialog.supports_thinking === -1"
          >
            <el-option v-for="lv in THINKING_LEVEL_OPTIONS" :key="lv" :label="lv" :value="lv" />
          </el-select>
        </el-form-item>
        <el-form-item label="默认思考档">
          <el-select v-model="modelDialog.default_thinking_level" clearable style="width: 220px" placeholder="跟随模型（不指定）">
            <el-option v-for="lv in THINKING_LEVEL_OPTIONS" :key="lv" :label="lv" :value="lv" />
          </el-select>
        </el-form-item>
        <el-form-item label="思考可关闭">
          <el-select v-model="modelDialog.thinking_can_disable" style="width: 220px">
            <el-option label="未知" :value="null" />
            <el-option label="可关闭" :value="true" />
            <el-option label="强制思考（不可关）" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="思考参数风格">
          <el-select v-model="modelDialog.reasoning_style" clearable style="width: 220px" placeholder="跟随协议默认">
            <el-option label="effort（档位字符串）" value="effort" />
            <el-option label="budget（token 预算）" value="budget" />
            <el-option label="toggle（开关）" value="toggle" />
          </el-select>
          <div class="cfg-hint">由同步写入；手动填写后记入手动覆盖，同步默认不覆盖</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="modelDialog.note" maxlength="200" placeholder="模型用途、计费方式等备注" />
        </el-form-item>
        <el-form-item label="支持视觉/图片理解">
          <el-switch v-model="modelDialog.supports_vision" />
          <div class="cfg-hint">开启后可在聊天中直接发送图片，由该模型做视觉理解；模型不支持时发送图片会返回错误提示</div>
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

<script>
// 模型能力探测的纯逻辑：徽章计算 / k 格式化 / 级别解析 / 结论文案。
// 格式化和档位解析等通用纯函数已下沉到 utils/modelCapabilities.js（聊天区动态思考下拉同源），
// 此处重新导出保持既有导入路径不变；放在普通 <script> 块中导出，便于 vitest 直接单测
// （当前测试环境为 node，无法渲染组件）
import {
  CANONICAL_LEVELS,
  capabilityBadges,
  formatK,
  parseLevels,
  probeAtShort,
  syncSummary,
} from '../../utils/modelCapabilities'

export { CANONICAL_LEVELS, capabilityBadges, formatK, parseLevels, probeAtShort, syncSummary }

// 手动编辑模型的思考档位候选：与 canonical 七档一致（Task 6B 起后端保存同白名单）
export const THINKING_LEVEL_OPTIONS = CANONICAL_LEVELS

// 当前生效模型文案（供应商页顶部展示）：来源=界面供应商激活模型 / .env 兜底
// teamConfig 为脱敏后的 config（含 model/provider_name/api_key_set）；未配置时引导去下方添加
// 缺 Key 的生效模型（env 兜底未填 Key、供应商 Key 被清空）不得宣称可用——
// 与对话区「尚未配置模型 API」口径对齐，明示对话不可用（browser-use 走查发现）
export function effectiveModelText(cfg) {
  const model = String((cfg && cfg.model) || '').trim()
  const provider = String((cfg && cfg.provider_name) || '').trim()
  if (!model) return '当前生效模型：未配置（可在下方添加供应商并激活模型）'
  const source = provider === 'env兜底' ? '.env 兜底' : `供应商「${provider}」`
  if (cfg && cfg.api_key_set === false) {
    return `当前生效模型：${model}（来源：${source}，未配置 Key，对话不可用）`
  }
  return `当前生效模型：${model}（来源：${source}）`
}

// 供应商 Key 状态徽标：停用时不显示「已配置」，改显「已停用」（与聊天页状态口径一致）
export function providerKeyBadge(p) {
  if (!p || !p.enabled) return { text: '已停用', type: 'info' }
  return p.api_key_set ? { text: '已配置', type: 'success' } : { text: '未配置 Key', type: 'info' }
}

// 探测响应解包：后端实际返回 {model_id, result, probe}（result=分步详情，probe=权威状态）；
// 同时兼容契约定平的字段形态（字段直接在响应顶层）
export function probeResultOf(data) {
  if (!data || typeof data !== 'object') return {}
  return data.result && typeof data.result === 'object' ? data.result : data
}

export function probeStateOf(data) {
  if (!data || typeof data !== 'object') return {}
  return data.probe && typeof data.probe === 'object' ? data.probe : data
}

// 结论文案：{ level: 'success' | 'warning' | 'error', text }
export function reasoningResultMessage(data) {
  const r = probeResultOf(data)
  const s = probeStateOf(data)
  if (r.skipped) return { level: 'warning', text: String(r.skipped) }
  const err = r.error || data?.error
  if (err) return { level: 'error', text: String(err) }
  if (s.supports_thinking === 1) {
    const levels = r.thinking_levels || s.thinking_levels || []
    return { level: 'success', text: `支持思考：${levels.join('/') || '未给出级别'}` }
  }
  if (s.supports_thinking === 0) return { level: 'success', text: '模型不支持思考' }
  return { level: 'warning', text: '未获得探测结论' }
}

export function contextResultMessage(data) {
  const r = probeResultOf(data)
  const s = probeStateOf(data)
  if (r.skipped) return { level: 'warning', text: String(r.skipped) }
  const err = r.error || data?.error
  if (err) return { level: 'error', text: String(err) }
  return { level: 'success', text: `上下文有效 ${r.effective_context ?? s.probed_context ?? 0}` }
}

export function outputResultMessage(data) {
  const r = probeResultOf(data)
  const s = probeStateOf(data)
  if (r.skipped) return { level: 'warning', text: String(r.skipped) }
  const err = r.error || data?.error
  if (err) return { level: 'error', text: String(err) }
  return { level: 'success', text: `最大输出 ${r.effective_max_output ?? s.probed_max_output ?? 0}` }
}

export function probeAllSummary(data) {
  const s = probeStateOf(data)
  const think =
    s.supports_thinking === 1
      ? `思考 ${(s.thinking_levels || []).join('/') || '支持'}`
      : s.supports_thinking === 0
        ? '思考 不支持'
        : '思考 未探测'
  return `${think} · ctx ${s.probed_context || '-'} · out ${s.probed_max_output || '-'}`
}
</script>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Close, Loading, Plus } from '@element-plus/icons-vue'
import { assistantApi, modelProbeApi, providerPresetsApi } from '../../api'
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
    if (!presetsLoaded.value) loadPresets()
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

// 目录检测前置：后端只探测已保存目录。输入框路径与已保存配置不一致时，
// 弹确认「先保存再检测」；用户取消则放弃检测（不发任何请求），
// 保存成功才继续检测；保存失败（拦截器已提示）同样不再检测。
async function ensureDirSaved(dir, savedDir, label) {
  if (dir === String(savedDir || '').trim()) return true
  try {
    await ElMessageBox.confirm(
      `${label}尚未保存，检测仅对已保存的目录生效。是否先保存工作参数再检测？`,
      '需要先保存',
      { type: 'warning', confirmButtonText: '保存并检测', cancelButtonText: '取消' },
    )
  } catch {
    return false  // 用户取消：不保存、不检测
  }
  savingCfg.value = true
  try {
    await assistantApi.config.saveGlobals({ ...globalsForm })
    ElMessage.success('工作参数已保存并生效')
    emit('refresh')
    return true
  } catch {
    return false
  } finally {
    savingCfg.value = false
  }
}

async function discoverSkillsCount() {
  const dir = globalsForm.skills_dir.trim()
  if (!dir) {
    ElMessage.warning('请先填写技能池目录')
    return
  }
  if (discoveringSkills.value) return  // 忙态防连点
  discoveringSkills.value = true
  try {
    if (!(await ensureDirSaved(dir, props.teamConfig?.skills_dir, '技能池目录'))) return
    const { data } = await assistantApi.config.discoverSkills(dir)
    skillsFound.value = data.count
    skillsNames.value = (data.names || []).slice(0, 8)
  } catch {
    /* 拦截器已提示后端 detail；失败不清空上次检测结果 */
  } finally {
    discoveringSkills.value = false
  }
}

async function discoverPluginsCount() {
  const dir = globalsForm.plugins_dir.trim()
  if (!dir) {
    ElMessage.warning('请先填写插件目录')
    return
  }
  if (discoveringPlugins.value) return  // 忙态防连点
  discoveringPlugins.value = true
  try {
    if (!(await ensureDirSaved(dir, props.teamConfig?.plugins_dir, '插件目录'))) return
    const { data } = await assistantApi.config.discoverPlugins(dir)
    pluginsFound.value = data
  } catch {
    /* 拦截器已提示后端 detail；失败不清空上次检测结果 */
  } finally {
    discoveringPlugins.value = false
  }
}

async function saveGlobals() {
  if (savingCfg.value) return  // 忙态防连点
  savingCfg.value = true
  try {
    await assistantApi.config.saveGlobals({ ...globalsForm })
    ElMessage.success('工作参数已保存并生效')
    emit('refresh')
    emit('update:visible', false)
  } catch {
    /* 拦截器已提示后端 detail；保存失败不关闭抽屉、不伪成功 */
  } finally {
    savingCfg.value = false
  }
}

// ---------------- 供应商卡片 ----------------
const testingId = ref(null)
const provTestResult = ref({})

// ---------------- 在线模型列表同步 ----------------
// 每供应商独立 loading 键（syncingProviderId）；partial/truncated 以 warning 如实标注，
// ok=false（整批回滚）只报错不刷新——失败保留原列表，不假成功
const syncingProviderId = ref(null)
const syncResults = reactive({})
// override_manual 勾选（每供应商独立，默认 false）：勾选后同步前弹确认，
// 明确警告同步值会覆盖手动修改与实测（探测）能力；不同步下线模型的 include_disabled 不暴露
const syncOverrideManual = reactive({})

async function syncProviderModels(p) {
  if (syncingProviderId.value) return  // 忙态防连点（任一供应商同步中即锁）
  const overrideManual = !!syncOverrideManual[p.id]
  if (overrideManual) {
    try {
      await ElMessageBox.confirm(
        '覆盖开启：本次同步将用同步值覆盖该供应商下模型的手动修改与实测（探测）能力，原手动值不会保留。确定仍要覆盖？',
        '确认覆盖手动与实测能力',
        { type: 'warning', confirmButtonText: '仍然覆盖', cancelButtonText: '取消' },
      )
    } catch {
      return  // 用户取消：不发请求、不置忙态
    }
  }
  syncingProviderId.value = p.id
  try {
    const { data } = await assistantApi.providers.syncModels(p.id, { override_manual: overrideManual })
    const summary = syncSummary(data)
    syncResults[p.id] = summary
    if (summary.level === 'error') ElMessage.error(summary.text)
    else if (summary.level === 'warning') ElMessage.warning(summary.text)
    else ElMessage.success(summary.text)
    if (summary.ok) emit('refresh')  // 回滚/失败不刷新：本地列表保持原状
  } catch {
    /* 拦截器已提示后端 detail；失败不写摘要、不刷新 */
  } finally {
    syncingProviderId.value = null
  }
}

// ---------------- 模型列表：搜索与分批渲染 ----------------
const MODEL_PAGE_SIZE = 20
// 首屏外的复杂模型卡延迟渲染（content-visibility: auto + contain-intrinsic-size）；
// 前 3 条在首屏内保持常规渲染（指南：不要对首屏内元素用 content-visibility）
const DEFERRED_ROW_FROM = 3
const modelQueries = reactive({})
const modelLimits = reactive({})

function filteredModels(p) {
  const q = String(modelQueries[p.id] || '').trim().toLowerCase()
  const list = Array.isArray(p?.models) ? p.models : []
  if (!q) return list
  return list.filter((m) => String(m.model || '').toLowerCase().includes(q))
}

function visibleModels(p) {
  const limit = modelLimits[p.id] || MODEL_PAGE_SIZE
  return filteredModels(p).slice(0, limit)
}

function filteredModelCount(p) {
  return filteredModels(p).length
}

function hiddenModelCount(p) {
  return Math.max(0, filteredModelCount(p) - visibleModels(p).length)
}

function setModelQuery(p, q) {
  modelQueries[p.id] = String(q || '')
}

function expandModels(p) {
  modelLimits[p.id] = (modelLimits[p.id] || MODEL_PAGE_SIZE) + MODEL_PAGE_SIZE
}

function hasManualOverrides(m) {
  const cap = m?.capability
  return Array.isArray(cap?.manual_fields) && cap.manual_fields.length > 0
}

async function resetModelOverrides(m) {
  if (resettingModelId.value) return  // 忙态防连点
  if (!hasManualOverrides(m)) {
    ElMessage.warning('该模型没有手动覆盖字段，无需恢复')
    return
  }
  resettingModelId.value = m.id
  try {
    const { data } = await assistantApi.models.resetOverrides(m.id)
    const cleared = Array.isArray(data?.cleared) ? data.cleared : []
    ElMessage.success(`已恢复同步值${cleared.length ? '：' + cleared.join('、') : '（手动覆盖已清除）'}`)
    emit('refresh')
  } catch {
    /* 拦截器已提示后端 detail */
  } finally {
    resettingModelId.value = null
  }
}

// ---------------- 供应商预设（providerPresetsApi） ----------------
const presetOptions = ref([])
const presetsLoaded = ref(false)
const presetCandidateModels = ref([])
const presetFillInfo = ref(null)

async function loadPresets() {
  try {
    const { data } = await providerPresetsApi.list()
    presetOptions.value = data.presets || data.items || []
    presetsLoaded.value = true
  } catch {
    /* 拦截器已提示 */
  }
}

async function reloadPresets() {
  try {
    const { data } = await providerPresetsApi.reload()
    await loadPresets()
    ElMessage.success(`预设已重载（${data.count ?? presetOptions.value.length} 条）`)
  } catch {
    /* 拦截器已提示 */
  }
}

// ---------------- 模型能力探测 ----------------
const probingId = ref(null)

const PROBE_FN = {
  reasoning: modelProbeApi.probeReasoning,
  context: modelProbeApi.probeContext,
  output: modelProbeApi.probeOutput,
}

function modelTip(m) {
  const caps = capabilityBadges(m).map((b) => b.text).join(' · ')
  return `上下文 ${m.context_size} · 输出 ${m.max_tokens}${caps ? ' · ' + caps : ''}${m.note ? ' · 备注：' + m.note : ''}`
}

function applyProbeData(m, data) {
  const s = probeStateOf(data)
  if (s.supports_thinking !== undefined) m.supports_thinking = s.supports_thinking
  if (s.thinking_levels !== undefined) m.thinking_levels = s.thinking_levels
  if (s.probed_context !== undefined) m.probed_context = s.probed_context
  if (s.probed_max_output !== undefined) m.probed_max_output = s.probed_max_output
  if (s.probed_at !== undefined) m.probed_at = s.probed_at
}

function showProbeMessage(msg) {
  if (msg.level === 'error') ElMessage.error(msg.text)
  else if (msg.level === 'warning') ElMessage.warning(msg.text)
  else ElMessage.success(msg.text)
}

async function runProbe(m, kind) {
  probingId.value = m.id + ':' + kind
  try {
    const { data } = await PROBE_FN[kind](m.id)
    applyProbeData(m, data)
    emit('refresh')
    if (kind === 'reasoning') showProbeMessage(reasoningResultMessage(data))
    else if (kind === 'context') showProbeMessage(contextResultMessage(data))
    else showProbeMessage(outputResultMessage(data))
  } catch {
    /* 拦截器已提示后端 detail */
  } finally {
    probingId.value = null
  }
}

async function runProbeAll(m) {
  try {
    await ElMessageBox.confirm(
      '将发起约 15 次小型 API 请求（每次 max_tokens≤64 或上下文探测），消耗少量 token，约 1-2 分钟。确定开始？',
      '全量能力探测',
      { type: 'warning', confirmButtonText: '开始探测', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  probingId.value = m.id + ':all'
  try {
    const { data } = await modelProbeApi.probeAll(m.id)
    applyProbeData(m, data)
    emit('refresh')
    ElMessage.success(`探测完成：${probeAllSummary(data)}`)
  } catch {
    /* 拦截器已提示 */
  } finally {
    probingId.value = null
  }
}

// 批量入口：对某供应商下全部启用中的模型依次全量探测（只确认一次，失败容错不中断）
const probingProviderId = ref(null)

async function runProbeAllModels(p) {
  if (probingProviderId.value) return  // 忙态防连点
  const targets = (p.models || []).filter((m) => m.enabled !== false)
  if (!targets.length) {
    ElMessage.warning(`供应商「${p.name}」下没有启用中的模型`)
    return
  }
  try {
    await ElMessageBox.confirm(
      `将对「${p.name}」下 ${targets.length} 个启用中的模型依次执行全量能力探测（每个模型约 15 次小型 API 请求，消耗少量 token）。确定开始？`,
      '批量全量能力探测',
      { type: 'warning', confirmButtonText: '开始探测', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  probingProviderId.value = p.id
  const failed = []
  try {
    for (const m of targets) {
      probingId.value = m.id + ':all'
      try {
        const { data } = await modelProbeApi.probeAll(m.id)
        applyProbeData(m, data)
      } catch {
        failed.push(m.model)  // 单个模型失败不中断其余（拦截器已提示后端 detail）
      }
    }
  } catch {
    // 兜底：循环体意外异常（如探测响应结构异常）不伪成功，锁由 finally 复位
    ElMessage.error('批量探测意外中断，请重试')
    return
  } finally {
    probingId.value = null
    probingProviderId.value = null
  }
  emit('refresh')
  if (failed.length) {
    ElMessage.warning(`批量探测完成：成功 ${targets.length - failed.length} 个，失败 ${failed.length} 个（${failed.join('、')}）`)
  } else {
    ElMessage.success(`批量探测完成：${targets.length} 个模型均已更新`)
  }
}

const presetModels = computed(() => {
  const name = provDialog.presetFill || provDialog.preset
  if (!name) return []
  const p = props.presets.find((x) => x.name === name) || presetOptions.value.find((x) => x.name === name)
  return p ? p.models || [] : []
})

const presetNote = computed(() => {
  const p = props.presets.find((x) => x.name === provDialog.preset)
  return p ? p.note : ''
})

// 预设标识候选（props.presets 与后端加载的 presetOptions 合并去重）
const presetKeyOptions = computed(() => {
  const map = new Map()
  for (const p of [...props.presets, ...presetOptions.value]) {
    if (p?.preset_key && !map.has(p.preset_key)) map.set(p.preset_key, p)
  }
  return [...map.values()]
})

// 管控面凭证（AK/SK）：仅 ak_sk 套餐需要；密码输入可粘贴不回显，留空=保留现值
const needsAkSk = computed(() => {
  const key = provDialog.preset_key
  if (!key) return false
  const p = presetKeyOptions.value.find((x) => x.preset_key === key)
  return p?.auth_scheme === 'ak_sk'
})

// 套餐提示：独立 Key / 地域 / 交互限制 + 文档链接
const presetPlanHint = computed(() => {
  const key = provDialog.preset_key
  if (!key) return null
  const p = presetKeyOptions.value.find((x) => x.preset_key === key)
  if (!p) return null
  let text = ''
  if (p.usage_scope === 'plan_interactive_only') {
    text = '套餐限制：交互式专属，Key 与按量不通用；禁止定时任务使用'
  } else if (p.usage_scope === 'plan_api_allowed') {
    text = '套餐限制：官方允许第三方 API 调用（可用于定时任务）'
  }
  if (p.note) text = text ? `${text}；${p.note}` : p.note
  if (!text && !p.docs_url) return null
  return { text, docs_url: p.docs_url || '' }
})

// ---- 供应商表单 ----
const provDialog = reactive({
  visible: false,
  id: null,
  preset: null,
  presetFill: null,
  presetModels: [],
  name: '',
  protocol: 'openai',
  base_url: '',
  api_key: '',
  // 同步适配器定位（适配器只认 preset_key）与可选管控凭证（火山 AK/SK，空=保留现值）
  preset_key: '',
  models_url: '',
  sync_access_key: '',
  sync_secret_key: '',
  enabled: true,
  saving: false,
})

function openProviderForm() {
  Object.assign(provDialog, { visible: true, id: null, preset: null, presetFill: null, presetModels: [], name: '', protocol: 'openai', base_url: '', api_key: '', preset_key: '', models_url: '', sync_access_key: '', sync_secret_key: '', enabled: true, saving: false })
  presetCandidateModels.value = []
  presetFillInfo.value = null
}

function editProvider(p) {
  Object.assign(provDialog, {
    visible: true,
    id: p.id,
    preset: null,
    presetFill: null,
    presetModels: [],
    name: p.name,
    protocol: p.protocol,
    base_url: p.base_url,
    api_key: '',
    preset_key: p.preset_key || '',
    models_url: p.models_url || '',
    // 管控凭证任何 GET 不回传：编辑时留空 = 保留现值（后端同口径）
    sync_access_key: '',
    sync_secret_key: '',
    enabled: p.enabled,
    saving: false,
  })
  presetCandidateModels.value = []
  presetFillInfo.value = null
}

// 快速预设与「从预设填充」共用：填 base_url/协议，并带出候选模型多选
function fillProviderFromPreset(p) {
  if (p.base_url) provDialog.base_url = p.base_url
  provDialog.protocol = p.protocol || 'openai'
  if (!provDialog.name) provDialog.name = p.label
  presetCandidateModels.value = [...(p.models || [])]
  provDialog.presetModels = [...(p.models || [])]
}

function applyPreset(name) {
  const p = props.presets.find((x) => x.name === name) || presetOptions.value.find((x) => x.name === name)
  if (p) fillProviderFromPreset(p)
}

// 「从预设填充」：填 base_url/协议，并带出候选模型多选
function applyPresetFill(name) {
  const p = presetOptions.value.find((x) => x.name === name) || props.presets.find((x) => x.name === name)
  if (!p) {
    presetCandidateModels.value = []
    presetFillInfo.value = null
    provDialog.presetModels = []
    return
  }
  fillProviderFromPreset(p)
  presetFillInfo.value = { note: p.note || '', docs_url: p.docs_url || '' }
}

async function saveProvider() {
  if (provDialog.saving) return  // 忙态防连点
  if (!provDialog.name.trim()) {
    ElMessage.warning('请填写供应商名称')
    return
  }
  provDialog.saving = true
  try {
    const { data } = await assistantApi.providers.save({
      provider_id: provDialog.id || undefined,
      name: provDialog.name.trim(),
      protocol: provDialog.protocol,
      base_url: provDialog.base_url,
      // 同步适配器定位与管控凭证（空串 = 清除/无；后端对 AK/SK 空值保留现值）
      preset_key: provDialog.preset_key || '',
      models_url: provDialog.models_url || '',
      api_key: provDialog.api_key || undefined,
      sync_access_key: provDialog.sync_access_key || undefined,
      sync_secret_key: provDialog.sync_secret_key || undefined,
      enabled: provDialog.enabled,
    })
    // 新供应商：把预设里勾选的候选模型一并添加（单个失败不阻塞其余）
    if (!provDialog.id) {
      const models = provDialog.presetModels.map((x) => String(x).trim()).filter(Boolean)
      const providerId = data?.provider?.id ?? data?.id
      if (models.length && providerId) {
        for (const name of models) {
          try {
            await assistantApi.providers.saveModel(providerId, { model: name, context_size: 128000, max_tokens: 2048 })
          } catch {
            /* 拦截器已提示 */
          }
        }
      }
    }
    emit('refresh')
    provDialog.visible = false
    ElMessage.success('供应商已保存')
  } catch {
    /* 拦截器已提示后端 detail；保存失败不关对话框、不伪成功 */
  } finally {
    provDialog.saving = false
  }
}

async function toggleProvider(p) {
  try {
    await assistantApi.providers.save({ provider_id: p.id, name: p.name, protocol: p.protocol, base_url: p.base_url, enabled: p.enabled })
    emit('refresh')
  } catch {
    p.enabled = !p.enabled  // 启停失败回滚开关（拦截器已提示）
  }
}

async function deleteProvider(p) {
  try {
    await ElMessageBox.confirm(`确定删除供应商「${p.name}」及其全部模型吗？`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await assistantApi.providers.remove(p.id)
    emit('refresh')
    ElMessage.success('已删除')
  } catch {
    /* 拦截器已提示 */
  }
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
  max_input_tokens: 0,
  supports_tools: -1,
  supports_vision: false,
  enabled: true,
  temperature: null,
  note: '',
  supports_thinking: -1,
  thinking_levels: [],
  default_thinking_level: '',
  thinking_can_disable: null,
  reasoning_style: '',
  base: null,
  saving: false,
})

function openModelForm(p, m) {
  Object.assign(modelDialog, {
    visible: true,
    id: m ? m.id : null,
    providerId: p.id,
    model: m ? m.model : '',
    context_size: m ? m.context_size : 128000,
    max_tokens: m ? m.max_tokens : 2048,
    // 能力契约字段（Task 3）：缺省按未知/空处理，不猜
    max_input_tokens: m ? m.max_input_tokens ?? 0 : 0,
    supports_tools: m ? m.supports_tools ?? -1 : -1,
    // 视觉开关：supports_vision 缺省时回退读旧的 multimodal 字段
    supports_vision: m ? !!(m.supports_vision ?? m.multimodal) : false,
    enabled: m ? m.enabled !== false : true,
    temperature: m ? (m.temperature === '' || m.temperature == null ? null : Number(m.temperature)) : null,
    note: m ? m.note || '' : '',
    supports_thinking: m ? m.supports_thinking ?? -1 : -1,
    thinking_levels: m ? parseLevels(m.thinking_levels) : [],
    default_thinking_level: m ? m.default_thinking_level || '' : '',
    thinking_can_disable: m ? m.thinking_can_disable ?? null : null,
    reasoning_style: m ? m.reasoning_style || m.capability?.reasoning_style || '' : '',
    saving: false,
    // 打开表单时的能力字段快照：保存时逐项比对，未变化的能力字段不提交
    //（后端只对「提交了」的字段记 manual；仅改备注不该把能力字段重新标记成手动）
    base: modelCapabilitySnapshot(m),
  })
}

// 能力字段快照（与 modelDialog 的同名字段一一对应，供差量比对）
function modelCapabilitySnapshot(m) {
  return {
    context_size: m ? m.context_size ?? 128000 : 128000,
    max_tokens: m ? m.max_tokens ?? 2048 : 2048,
    max_input_tokens: m ? m.max_input_tokens ?? 0 : 0,
    supports_tools: m ? m.supports_tools ?? -1 : -1,
    supports_thinking: m ? m.supports_thinking ?? -1 : -1,
    thinking_levels: m ? parseLevels(m.thinking_levels) : [],
    default_thinking_level: m ? m.default_thinking_level || '' : '',
    thinking_can_disable: m ? m.thinking_can_disable ?? null : null,
    reasoning_style: m ? m.reasoning_style || m.capability?.reasoning_style || '' : '',
    supports_vision: m ? !!(m.supports_vision ?? m.multimodal) : false,
  }
}

// 编辑时只提交实际发生变化的能力字段；新模型（无快照）提交完整默认值
function changedCapabilityFields() {
  if (!modelDialog.id) {
    return {
      context_size: modelDialog.context_size,
      max_tokens: modelDialog.max_tokens,
      max_input_tokens: modelDialog.max_input_tokens || 0,
      supports_tools: modelDialog.supports_tools,
      supports_thinking: modelDialog.supports_thinking,
      thinking_levels: modelDialog.supports_thinking === 1 ? modelDialog.thinking_levels : [],
      default_thinking_level: modelDialog.default_thinking_level || '',
      thinking_can_disable: modelDialog.thinking_can_disable,
      reasoning_style: modelDialog.reasoning_style || '',
    }
  }
  const base = modelDialog.base || {}
  const eq = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
  const out = {}
  for (const k of ['context_size', 'max_tokens', 'max_input_tokens', 'supports_tools', 'supports_thinking']) {
    if (!eq(Number(modelDialog[k] ?? 0), Number(base[k] ?? 0))) out[k] = modelDialog[k]
  }
  for (const k of ['default_thinking_level', 'reasoning_style']) {
    if (!eq(String(modelDialog[k] ?? ''), String(base[k] ?? ''))) out[k] = String(modelDialog[k] ?? '')
  }
  if (!eq(modelDialog.thinking_can_disable ?? null, base.thinking_can_disable ?? null)) {
    out.thinking_can_disable = modelDialog.thinking_can_disable ?? null
  }
  const curLevels = [...(modelDialog.thinking_levels || [])].sort()
  const baseLevels = [...(base.thinking_levels || [])].sort()
  if (modelDialog.supports_thinking === 1) {
    // 标记「支持」时：级别实际变化才提交
    if (!eq(curLevels, baseLevels)) out.thinking_levels = modelDialog.thinking_levels || []
  } else if (baseLevels.length || curLevels.length) {
    // 回到自动/不支持：显式传空数组清掉旧档位（旧约定，避免残留档位继续门控）
    out.thinking_levels = []
  }
  return out
}

async function saveModel() {
  if (modelDialog.saving) return  // 忙态防连点
  if (!modelDialog.model.trim()) {
    ElMessage.warning('请填写模型名')
    return
  }
  modelDialog.saving = true
  try {
    const cap = changedCapabilityFields()
    // 视觉开关：新模型或实际变化时才提交（旧字段 multimodal 同步保持一致）
    if (!modelDialog.id || !!modelDialog.supports_vision !== !!(modelDialog.base || {}).supports_vision) {
      cap.supports_vision = !!modelDialog.supports_vision
      cap.multimodal = !!modelDialog.supports_vision
    }
    await assistantApi.providers.saveModel(modelDialog.providerId, {
      model_id: modelDialog.id || undefined,
      model: modelDialog.model.trim(),
      ...cap,
      enabled: modelDialog.enabled,
      // 温度留空传空串 = 跟随系统默认（后端约定）
      temperature: modelDialog.temperature ?? '',
      note: modelDialog.note.trim(),
    })
    emit('refresh')
    modelDialog.visible = false
    ElMessage.success('模型已保存')
  } catch {
    /* 拦截器已提示后端 detail；保存失败不关对话框、不伪成功 */
  } finally {
    modelDialog.saving = false
  }
}

// 恢复同步值：忙态在确认前生效，连点不会重复占锁
const resettingModelId = ref(null)

// 删除模型：破坏性操作，先弹可取消确认（取消 = 零请求）；
// 忙态在确认前生效，连点不会重复占锁/重复删除
const removingModelId = ref(null)

async function removeModel(p, m) {
  if (removingModelId.value) return  // 忙态防连点
  removingModelId.value = m.id
  try {
    await ElMessageBox.confirm(`确定删除模型「${m.model}」吗？删除后可在供应商「${p.name}」下重新添加。`, '删除确认', { type: 'warning' })
  } catch {
    removingModelId.value = null
    return  // 用户取消：零请求、零刷新
  }
  try {
    await assistantApi.providers.removeModel(p.id, m.id)
  } catch {
    /* 拦截器已提示后端 detail */
  } finally {
    removingModelId.value = null
  }
  emit('refresh')  // 与既有口径一致：失败也刷新，拉取后端真实模型列表
}

// 连通/工具/图片实测：工具与图片必须带该模型 model_id（不只测供应商第一个模型），
//  verdict 三态（supported/rejected/unknown）：401/403/404/408/429/网络错误 = unknown，
//  不显示成「❌ 不支持」，只如实说明未获得结论
async function testProvider(p, kind, model) {
  if (testingId.value) return  // 忙态防连点
  const key = model ? model.id + ':' + kind : p.id + ':' + kind
  testingId.value = key
  try {
    const { data } = await assistantApi.config.test({
      provider_id: p.id,
      model_id: model ? model.id : undefined,
      test_tools: kind === 'tools',
      include_image: kind === 'image',
    })
    const lines = []
    const target = model ? `（${model.model}）` : ''
    if (kind === 'text') {
      const t = data.text
      lines.push(t.ok ? `连通 ✅ ${t.latency_ms}ms · 回复：${t.reply}` : `连通 ❌ ${t.error}`)
    } else if (kind === 'tools') {
      const t = data.text, tt = data.tools
      if (!t.ok) lines.push(`连通失败：${t.error}`)
      else if (tt.ok) lines.push(`工具调用 ✅ ${tt.latency_ms}ms${target} · 发起调用：${(tt.tool_calls || []).join(', ')}`)
      else if (tt.verdict === 'unknown') lines.push(`工具调用 未确认${target}：${tt.error || '请求失败或超时，未获得结论'}`)
      else lines.push(`工具调用 ❌${target} ${tt.error || '模型未发起调用'}`)
    } else {
      const t = data.text, img = data.image
      if (!t.ok) lines.push(`连通失败：${t.error}`)
      else if (img.ok) lines.push(`图片识别 ✅ ${img.latency_ms}ms${target} · 识别：${img.reply}`)
      else if (img.verdict === 'unknown') lines.push(`图片识别 未确认${target}：${img.error || '请求失败或超时，未获得结论'}`)
      else lines.push(`图片识别 ❌${target} ${img.error || '模型不支持或无法识别'}`)
    }
    provTestResult.value[p.id] = { ok: lines.every((l) => l.includes('✅')), lines }
  } catch {
    /* 拦截器已提示后端 detail；失败不写检测结果 */
  } finally {
    testingId.value = null
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

.model-row {
  width: 100%;
  border: 1px solid #dcdfe6;
  border-radius: 10px;
  padding: 6px 10px;
  margin-bottom: 6px;
  background: #fff;
}

.model-row.active {
  border-color: #67c23a;
  background: #f0f9eb;
}

.model-row.off {
  opacity: 0.55;
}

/* 首屏外的复杂模型卡延迟渲染：跳过布局/绘制直到临近视口。
   必须配对 contain-intrinsic-size，否则离屏时高度塌缩造成滚动跳动。
   不支持的浏览器忽略该属性，自然回退为常规渲染。 */
.model-row--deferred {
  content-visibility: auto;
  contain-intrinsic-size: auto none 76px;
}

.model-filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.model-search {
  width: 200px;
}

.models-count {
  font-size: 11px;
  color: #909399;
}

.sync-summary {
  font-size: 12px;
  margin-top: 4px;
  color: #529b2e;
  word-break: break-all;
}

.sync-summary.warning {
  color: #e6a23c;
}

.sync-summary.error {
  color: #f56c6c;
}

.sync-warn,
.sync-skip {
  color: #909399;
}

.plan-hint {
  margin-bottom: 4px;
}

.model-row-main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.model-row-main .spacer {
  flex: 1;
}

.model-name {
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.model-name:hover {
  color: #409eff;
}

.badge-levels {
  font-size: 11px;
  color: #529b2e;
}

.probe-at {
  font-size: 11px;
  color: #909399;
}

.badge-dashed {
  border-style: dashed;
}

.model-row-ops {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 2px;
}

.ops-label {
  font-size: 11px;
  color: #909399;
  margin-right: 4px;
}

.pill-x {
  font-size: 11px;
  color: #c0c4cc;
}

.pill-x:hover {
  color: #f56c6c;
}

.pill-x.is-loading {
  color: #409eff;
  cursor: default;
}

.prov-ops {
  margin-top: 4px;
}

.sync-override-cb {
  margin-left: 8px;
}

.sync-override-cb :deep(.el-checkbox__label) {
  font-size: 12px;
  color: #e6a23c;
}

.sync-override-warn {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: #e6a23c;
  word-break: break-all;
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

.cfg-hint a {
  margin-left: 6px;
}

.base-url-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.base-url-row .el-input {
  flex: 1;
}
</style>
