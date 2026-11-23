<template>
  <div class="page help-page">
    <el-card shadow="never" class="help-intro-card">
      <div class="help-head">
        <div>
          <div class="help-title">使用说明</div>
          <div class="help-sub">全部菜单的手册章节导航 · 手册文件：{{ manualFile }}</div>
        </div>
        <el-tag size="small" type="info">{{ entryCount }} 个条目 / {{ visibleCount }} 个可见入口</el-tag>
      </div>
      <div class="md-body help-intro" v-html="introHtml"></div>
    </el-card>

    <el-card shadow="never" class="help-filter-card">
      <el-input
        v-model="keyword"
        clearable
        placeholder="搜索模块名、路径或章节号，如 POS / 6.4 / 数据池"
        aria-label="搜索帮助条目"
      />
    </el-card>

    <el-card v-for="group in filteredGroups" :key="group.name" shadow="never" class="help-group">
      <template #header>
        <div class="group-head">
          <span class="group-name">{{ group.name }}</span>
          <span class="group-count">{{ group.count }} 项</span>
        </div>
      </template>
      <template v-for="row in group.rows" :key="row.kind === 'subgroup' ? `sg-${group.name}-${row.name}` : row.entry.path">
        <div v-if="row.kind === 'subgroup'" class="subgroup-name">{{ row.name }}</div>
        <div
          v-else
          class="help-row"
          :class="{ active: row.entry.section === activeSection }"
          :data-section="row.entry.section"
        >
          <div class="help-row-main">
            <router-link class="help-name" :to="row.entry.path">{{ row.entry.title }}</router-link>
            <code class="help-path">{{ row.entry.path }}</code>
            <el-tag v-if="row.entry.hidden" size="small" type="info">隐藏示例</el-tag>
          </div>
          <div class="help-row-desc">{{ row.entry.summary }}</div>
          <button
            type="button"
            class="help-locator"
            :title="`复制手册定位：${manualLocator(row.entry)}`"
            @click="locate(row.entry)"
          >
            §{{ row.entry.section }} {{ row.entry.sectionTitle }}
          </button>
        </div>
      </template>
    </el-card>

    <el-empty v-if="!filteredGroups.length" description="没有匹配的模块，试试章节号（如 6.4）或路径关键词" />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { countHelpEntries, HELP_ENTRIES, HELP_GROUPS, MANUAL_FILE, manualLocator } from '../utils/helpIndex'

const manualFile = MANUAL_FILE
const entryCount = countHelpEntries()
const visibleCount = HELP_ENTRIES.filter((entry) => !entry.hidden).length

const INTRO_MD = [
  '**这份帮助做什么**：按左侧菜单分组列出全部可见功能，并指向《使用说明》里的对应章节。完整的步骤、产物、限制与常见错误都在手册正文中。',
  '',
  `- **手册文件**：工作台目录根部的 \`${MANUAL_FILE}\`。在文件管理器中打开该目录，用记事本、VSCode 或任意 Markdown 阅读器打开即可。`,
  '- **定位章节**：点条目右侧的 `§章节号` 复制定位串，再到手册里按 `Ctrl+F` 粘贴搜索（例如 `§6.1 数据池清洗`）。',
  '- **基础操作**：安装启动见 §1.3，停止/重启见 §1.5，备份恢复见 §9.4，按症状排查见 §11 FAQ。',
  '',
  `> 本页只做导航，不复制手册正文；两者不一致时以 \`${MANUAL_FILE}\` 为准。`,
].join('\n')

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const introHtml = md.render(INTRO_MD)

const route = useRoute()
const keyword = ref('')
const activeSection = ref(String(route.query.section || ''))

function matchEntry(entry) {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return true
  return [entry.title, entry.path, entry.section, entry.sectionTitle, entry.summary, entry.group, entry.subgroup]
    .some((value) => String(value || '').toLowerCase().includes(q))
}

const filteredGroups = computed(() =>
  HELP_GROUPS.map((group) => {
    const rows = []
    let count = 0
    for (const item of group.items || []) {
      if (!matchEntry(item)) continue
      rows.push({ kind: 'entry', entry: item })
      count += 1
    }
    for (const sub of group.subgroups || []) {
      const items = sub.items.filter(matchEntry)
      if (!items.length) continue
      rows.push({ kind: 'subgroup', name: sub.name })
      for (const item of items) rows.push({ kind: 'entry', entry: item })
      count += items.length
    }
    return { name: group.name, rows, count }
  }).filter((group) => group.rows.length),
)

function scrollToActive() {
  nextTick(() => {
    if (typeof document === 'undefined') return
    document.querySelector('.help-row.active')?.scrollIntoView?.({ block: 'center' })
  })
}

async function locate(entry) {
  const text = manualLocator(entry)
  activeSection.value = entry.section
  try {
    if (!navigator?.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(text)
    ElMessage.success(`已复制：${text}（在 ${MANUAL_FILE} 中按 Ctrl+F 粘贴搜索）`)
  } catch {
    ElMessage.info(`请到 ${MANUAL_FILE} 中搜索：${text}`)
  }
}

watch(
  () => route.query.section,
  (value) => {
    activeSection.value = String(value || '')
    scrollToActive()
  },
)

onMounted(scrollToActive)
</script>

<style scoped>
.help-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.help-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.help-title {
  font-size: 18px;
  font-weight: 600;
}

.help-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.help-intro {
  margin-top: 12px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}

.help-intro :deep(p) {
  margin: 0 0 8px;
}

.help-intro :deep(ul) {
  margin: 0 0 8px;
  padding-left: 20px;
}

.help-intro :deep(blockquote) {
  margin: 8px 0 0;
  padding: 6px 12px;
  border-left: 3px solid var(--el-color-primary-light-5);
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-secondary);
}

.help-group :deep(.el-card__header) {
  padding: 10px 16px;
}

.group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.group-name {
  font-weight: 600;
}

.group-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.subgroup-name {
  margin: 12px 0 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.help-row {
  display: grid;
  grid-template-columns: minmax(210px, 280px) minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 9px 10px;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  border-radius: 6px;
}

.help-row:last-child {
  border-bottom: none;
}

.help-row.active {
  background: var(--el-color-primary-light-9);
  outline: 1px solid var(--el-color-primary-light-7);
}

.help-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.help-name {
  font-weight: 500;
  color: var(--el-color-primary);
  text-decoration: none;
}

.help-name:hover {
  text-decoration: underline;
}

.help-path {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.help-row-desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.5;
}

.help-locator {
  border: none;
  background: transparent;
  padding: 2px 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  white-space: nowrap;
}

.help-locator:hover {
  color: var(--el-color-primary);
  text-decoration: underline;
}

@media (max-width: 1024px) {
  .help-row {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .help-locator {
    justify-self: start;
    padding-left: 0;
  }
}
</style>
