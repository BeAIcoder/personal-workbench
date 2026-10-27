// 模型能力展示与思考档位的纯函数（Task 6A 前端）。
// 口径与后端 config_store / model_catalog / llm 对齐：
//  - 三态：1=支持 / 0=不支持 / 缺省或 -1=未知（未知绝不显示成红叉，也不猜）
//  - canonical 思考档位：none/minimal/low/medium/high/xhigh/max（Task 6B 补后端保存白名单）
//  - 视觉（supports_vision，图片理解）与多模态（输入模态含 text 之外）是两个概念，不互相代替
//  - 档位门控：模型已声明 thinking_levels 时，声明集之外的档位视为不支持（回退「跟随模型」）

export const CANONICAL_LEVELS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']

const LEVEL_LABEL = {
  none: '关闭',
  minimal: '极低',
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '极高',
  max: '最大',
}

const LEVEL_RANK = {
  none: 0,
  minimal: 1,
  low: 2,
  medium: 3,
  high: 4,
  xhigh: 5,
  max: 6,
}

// 后端 capability_meta.sources 的取值（manual / probe / provider_api / official_catalog）
const SOURCE_LABEL = {
  manual: '手动',
  probe: '实测',
  provider_api: '同步',
  official_catalog: '同步',
}

export function formatK(n) {
  const v = Number(n)
  if (!Number.isFinite(v) || v <= 0) return ''
  const k = Math.round(v / 1024)
  if (k < 1024) return `${k}k`
  return `${Math.round((v / 1048576) * 10) / 10}m`
}

export function parseLevels(v) {
  if (Array.isArray(v)) return v.filter((x) => typeof x === 'string' && x.trim())
  if (typeof v !== 'string' || !v.trim()) return []
  try {
    const parsed = JSON.parse(v)
    if (Array.isArray(parsed)) return parsed.filter((x) => typeof x === 'string' && x.trim())
    return []
  } catch {
    if (!v.includes(',')) return []
    return v.split(',').map((s) => s.trim()).filter(Boolean)
  }
}

export function probeAtShort(v) {
  if (!v) return ''
  const m = String(v).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : String(v)
}

// 三态归一：1 → supported；0 → unsupported；缺省/-1/其它 → unknown
export function triState(v) {
  if (v === 1 || v === '1' || v === true) return 'supported'
  if (v === 0 || v === '0' || v === false) return 'unsupported'
  return 'unknown'
}

// 视觉（图片理解）：supports_vision 优先，缺省回退旧 multimodal 布尔字段
export function visionState(m) {
  if (m == null) return 'unknown'
  if (m.supports_vision === true || m.supports_vision === 1) return 'supported'
  if (m.supports_vision === false || m.supports_vision === 0) return 'unsupported'
  if (m.multimodal === true || m.multimodal === 1) return 'supported'
  if (m.multimodal === false || m.multimodal === 0) return 'unsupported'
  return 'unknown'
}

// 多模态：输入模态含 text 之外的形态（image/video/audio/pdf…）
export function isMultimodal(m) {
  const mods = Array.isArray(m?.input_modalities) ? m.input_modalities : []
  return mods.some((x) => String(x).trim().toLowerCase() !== 'text')
}

function inputModalitiesOf(m) {
  return Array.isArray(m?.input_modalities) ? m.input_modalities.map((x) => String(x)) : []
}

function positive(n) {
  const v = Number(n)
  return Number.isFinite(v) && v > 0 ? v : null
}

// 思考能力：supports_thinking 三态；未声明 supports_thinking 但声明了档位 → 视为支持
export function thinkingSupportOf(m) {
  const state = triState(m?.supports_thinking)
  if (state === 'unknown' && parseLevels(m?.thinking_levels).length) return 'supported'
  return state
}

// 已声明且合法的思考档位（按 canonical 顺序排列，非法值剔除）
export function declaredLevelsOf(m) {
  const levels = parseLevels(m?.thinking_levels)
    .map((x) => String(x).trim().toLowerCase())
    .filter((x) => CANONICAL_LEVELS.includes(x))
  return [...new Set(levels)].sort((a, b) => LEVEL_RANK[a] - LEVEL_RANK[b])
}

// 思考是否可关闭：false=强制思考；true=可关闭；null=未知
export function disableStateOf(m) {
  if (m?.thinking_can_disable === false) return false
  if (m?.thinking_can_disable === true) return true
  return null
}

function styleOf(m) {
  return String(m?.reasoning_style || m?.capability?.reasoning_style || '').trim()
}

// 档位是否允许提交：声明集之内才允许；未声明（未知）时保留兼容全量。
// 「关闭（none）」例外：与后端 _resolve_thinking_level 同口径——off/none 只受
// thinking_can_disable 约束、不受声明集门控，因此模型未强制思考时始终允许关闭
export function levelAllowed(model, value) {
  const v = String(value || '').trim().toLowerCase()
  if (v === 'default' || v === '') return true
  if (!CANONICAL_LEVELS.includes(v)) return false
  if (v === 'none') return canCloseThinking(model)
  const declared = declaredLevelsOf(model)
  if (!declared.length) return true
  return declared.includes(v)
}

// 模型是否允许关闭思考：未强制思考（thinking_can_disable !== false）即允许。
// 与后端 `_resolve_thinking_level` 的 can_disable 计算基本一致，但多一条保守条件：
// 「明确不支持思考（supports_thinking == 0）」时也不允许关闭——后端此时同样不发任何
// 思考参数（fix round 1 兜底），前端不外露「关闭」项只是表达层更保守，不影响请求结果
export function canCloseThinking(model) {
  return thinkingSupportOf(model) !== 'unsupported' && disableStateOf(model) !== false
}

function sourceBadgeOf(m) {
  const cap = m?.capability && typeof m.capability === 'object' ? m.capability : {}
  const manual = Array.isArray(cap.manual_fields) ? cap.manual_fields.map(String) : []
  if (manual.length) {
    return {
      key: 'source',
      text: '来源：手动',
      type: 'warning',
      effect: 'plain',
      dashed: false,
      title: `手动覆盖字段：${manual.join('、')}（同步默认不覆盖，可「恢复同步值」）`,
    }
  }
  const sources = cap.sources && typeof cap.sources === 'object' ? cap.sources : {}
  const values = Object.values(sources).map(String)
  if (!values.length) return null
  if (values.includes('manual')) {
    return { key: 'source', text: '来源：手动', type: 'warning', effect: 'plain', dashed: false, title: '该能力字段为手动填写' }
  }
  if (values.includes('probe')) {
    return { key: 'source', text: '来源：实测', type: 'success', effect: 'plain', dashed: false, title: '该能力字段来自本地实测探测' }
  }
  if (values.some((v) => v === 'provider_api' || v === 'official_catalog')) {
    return { key: 'source', text: '来源：同步', type: 'primary', effect: 'plain', dashed: false, title: '该能力字段来自在线列表或官方能力目录' }
  }
  return null
}

function syncedBadgeOf(m) {
  const at = String(m?.synced_at || '').trim()
  if (!at) return null
  return {
    key: 'synced',
    text: `同步于 ${probeAtShort(at)}`,
    type: 'info',
    effect: 'plain',
    dashed: false,
    title: `最近同步时间：${at}`,
  }
}

// 能力徽章：思考（含精确档位）/ 工具 / ctx / input / out / 视觉 / 多模态 / 来源 / 同步时间
export function capabilityBadges(model) {
  const badges = []
  const support = thinkingSupportOf(model)

  if (support === 'supported') {
    badges.push({ key: 'thinking', text: '思考✓', type: 'success', effect: 'plain', dashed: false, title: '支持思考' })
  } else if (support === 'unsupported') {
    badges.push({ key: 'thinking', text: '思考✗', type: 'info', effect: 'plain', dashed: false, title: '模型不支持思考' })
  } else {
    // 未知：虚线「未确认」，不显示红叉也不猜测
    badges.push({ key: 'thinking', text: '思考未确认', type: 'info', effect: 'plain', dashed: true, title: '思考能力尚未探测或声明' })
  }

  const levels = declaredLevelsOf(model)
  if (support === 'supported' && levels.length) {
    badges.push({
      key: 'levels',
      text: levels.join('/'),
      type: 'success',
      effect: 'plain',
      dashed: false,
      title: `思考档位：${levels.map((l) => `${l}（${LEVEL_LABEL[l]}）`).join('、')}`,
    })
  }

  const tools = triState(model?.supports_tools)
  badges.push({
    key: 'tools',
    text: tools === 'supported' ? '工具✓' : tools === 'unsupported' ? '工具✗' : '工具未确认',
    type: tools === 'supported' ? 'success' : 'info',
    effect: 'plain',
    dashed: tools === 'unknown',
    title: tools === 'supported' ? '支持工具调用' : tools === 'unsupported' ? '不支持工具调用' : '工具调用能力尚未实测',
  })

  const ctx = positive(model?.probed_context) ?? positive(model?.context_size)
  if (ctx) {
    badges.push({
      key: 'ctx',
      text: `ctx ${formatK(ctx)}`,
      type: 'info',
      effect: 'plain',
      dashed: false,
      title: `上下文窗口 ${ctx} tokens${model?.probed_context ? '（实测）' : '（声明值）'}`,
    })
  }

  const input = positive(model?.max_input_tokens)
  if (input) {
    badges.push({ key: 'input', text: `输入 ${formatK(input)}`, type: 'info', effect: 'plain', dashed: false, title: `最大输入 ${input} tokens` })
  }

  const out = positive(model?.probed_max_output) ?? positive(model?.max_tokens)
  if (out) {
    badges.push({
      key: 'out',
      text: `out ${formatK(out)}`,
      type: 'info',
      effect: 'plain',
      dashed: false,
      title: `最大输出 ${out} tokens${model?.probed_max_output ? '（实测）' : '（声明值）'}`,
    })
  }

  const vision = visionState(model)
  if (vision === 'supported') {
    badges.push({ key: 'vision', text: '视觉✓', type: 'success', effect: 'plain', dashed: false, title: '支持图片理解（聊天可传图）' })
  } else if (vision === 'unsupported') {
    badges.push({ key: 'vision', text: '视觉✗', type: 'info', effect: 'plain', dashed: false, title: '不支持图片理解' })
  }

  if (isMultimodal(model)) {
    badges.push({
      key: 'multimodal',
      text: '多模态',
      type: 'primary',
      effect: 'plain',
      dashed: false,
      title: `输入模态：${inputModalitiesOf(model).join('、')}`,
    })
  }

  const source = sourceBadgeOf(model)
  if (source) badges.push(source)
  const synced = syncedBadgeOf(model)
  if (synced) badges.push(synced)

  return badges
}

// ChatPanel 动态思考下拉：由（激活/专家生效）模型生成选项
export function reasoningOptions(model) {
  const support = thinkingSupportOf(model)
  const declared = declaredLevelsOf(model)
  const canDisable = disableStateOf(model)
  const style = styleOf(model)
  const defaultLevel = String(model?.default_thinking_level || '').trim()

  let levels = []
  let unconfirmed = false
  let note = ''

  if (support === 'unsupported') {
    levels = []
    note = '该模型不支持思考，思考深度不可用'
  } else if (declared.length) {
    levels = declared
    if (canDisable === false && levels.includes('none')) levels = levels.filter((l) => l !== 'none')
  } else {
    // 未声明档位：保留兼容全量，但明确标注未确认（后端会按模型实际情况忽略）
    levels = [...CANONICAL_LEVELS]
    unconfirmed = true
    note = '该模型思考档位未确认，以下为兼容选项（不支持的档位发送时会被忽略）'
  }

  if (canDisable === false && support !== 'unsupported') {
    note = note || '该模型为强制思考，无法关闭'
  }

  // 「关闭」项：与后端同口径只受 can_disable 约束；声明集里没有 none 但允许关闭时也提供
  // （否则旧版 off 偏好迁移成 none 后无可选项，用户无法再次表达「关闭思考」）
  const canClose = canCloseThinking(model)
  const optionLevels = levels.includes('none') || !canClose ? levels : ['none', ...levels]

  const options = [
    {
      value: 'default',
      label: '🧠 思考：跟随模型',
      title: defaultLevel && CANONICAL_LEVELS.includes(defaultLevel)
        ? `不指定档位，按模型默认（${defaultLevel}）`
        : '不指定档位，按模型默认',
    },
    ...optionLevels.map((l) => ({
      value: l,
      label: `🧠 思考：${LEVEL_LABEL[l]}`,
      unconfirmed,
    })),
  ]

  return {
    support,
    declared: declared.length > 0,
    canDisable,
    canClose,
    style,
    defaultLevel,
    levels,
    options,
    disabled: support === 'unsupported',
    unconfirmed,
    note,
  }
}

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
}

// 同步结果摘要：把 POST /providers/{id}/sync-models 的响应转成界面文案与分级
export function syncSummary(result) {
  const r = result && typeof result === 'object' ? result : {}
  const added = num(r.added)
  const updated = num(r.updated)
  const unchanged = num(r.unchanged)
  const skipped = Array.isArray(r.skipped) ? r.skipped.length : num(r.skipped)
  const partial = !!r.partial
  const online = r.online !== false
  const truncated = !!r.truncated
  const errors = Array.isArray(r.errors) ? r.errors.map(String) : []
  const warnings = Array.isArray(r.warnings) ? r.warnings.map(String) : []
  const counts = `新增 ${added} · 更新 ${updated} · 未变 ${unchanged} · 跳过 ${skipped}`

  let level = 'success'
  let text = `同步完成：${counts}`
  if (r.ok !== true || errors.length) {
    // 缺 ok 字段同样视为未确认成功：宁可报错，不假成功
    level = 'error'
    text = `同步未完成：${errors[0] || '后端未返回原因'}`
  } else if (truncated) {
    level = 'warning'
    text = `列表已达分页上限，结果可能不完整：${counts}`
  } else if (partial) {
    level = 'warning'
    text = `在线列表不可达，已按能力目录同步：${counts}`
  }

  return {
    ok: r.ok === true,
    level,
    text,
    partial,
    online,
    truncated,
    added,
    updated,
    unchanged,
    skipped,
    skippedItems: Array.isArray(r.skipped) ? r.skipped : [],
    warnings,
    errors,
    source: String(r.source || ''),
    syncedAt: String(r.synced_at || ''),
  }
}
