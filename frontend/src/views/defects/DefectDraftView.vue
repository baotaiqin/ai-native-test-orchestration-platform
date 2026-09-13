<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  exportDefectDraft,
  generateDefectDraft,
  getDefectDrafts,
  updateDefectDraft,
} from '@/api/defect-drafts'
import { getApiErrorMessage } from '@/api/http'
import { getProjects } from '@/api/projects'
import { getPrompts } from '@/api/prompt-center'
import type {
  DefectDraft,
  DefectDraftUpdateRequest,
} from '@/types/defect-draft'
import type { Project } from '@/types/project'
import type { PromptDefinition } from '@/types/prompt-center'
import { formatReportDate } from '@/utils/report-display'

const route = useRoute()
const router = useRouter()
const projects = ref<Project[]>([])
const prompts = ref<PromptDefinition[]>([])
const projectId = ref<number>()
const runId = ref('')
const caseRunId = ref<number>()
const promptId = ref<number>()
const instructions = ref('')
const items = ref<DefectDraft[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const generating = ref(false)
const saving = ref(false)
const exportingId = ref<number>()
const error = ref<string | null>(null)
const editorVisible = ref(false)
const editingId = ref<number>()
const editForm = ref<DefectDraftUpdateRequest | null>(null)
let listSequence = 0

const currentProject = computed(
  () => projects.value.find((item) => item.id === projectId.value) ?? null,
)
const canMutate = computed(() => currentProject.value?.status === 'ACTIVE')
const availablePrompts = computed(() => prompts.value.filter(
  (item) => item.enabled && item.current_version?.output_schema_id,
))

function positiveQuery(name: string): number | undefined {
  const raw = route.query[name]
  const value = Array.isArray(raw) ? raw[0] : raw
  if (typeof value !== 'string' || !/^[1-9]\d*$/.test(value)) return undefined
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) ? parsed : undefined
}

function stringQuery(name: string): string {
  const raw = route.query[name]
  const value = Array.isArray(raw) ? raw[0] : raw
  return typeof value === 'string' ? value.trim() : ''
}

function lines(value: string[]): string {
  return value.join('\n')
}

function splitLines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function draftError(value: unknown, fallback: string): string {
  const status = (value as { response?: { status?: number } }).response?.status
  if (status === 403 || status === 404) return '缺陷草稿不存在，或当前用户无权访问。'
  if (status === 409) return getApiErrorMessage(value, '草稿状态已变化，请刷新后重试。')
  return getApiErrorMessage(value, fallback)
}

async function syncQuery(): Promise<void> {
  await router.replace({
    name: route.meta.projectScoped ? 'project-defect-drafts' : 'defect-drafts',
    params: route.meta.projectScoped ? { projectId: projectId.value } : {},
    query: {
      project_id: projectId.value ? String(projectId.value) : undefined,
      run_id: runId.value.trim() || undefined,
    },
  })
}

async function loadDrafts(reset = false): Promise<void> {
  if (!projectId.value) return
  if (reset) page.value = 1
  const sequence = ++listSequence
  const selectedProject = projectId.value
  loading.value = true
  error.value = null
  try {
    const response = await getDefectDrafts(selectedProject, {
      runId: runId.value.trim() || undefined,
      page: page.value,
      pageSize: pageSize.value,
    })
    if (sequence !== listSequence || projectId.value !== selectedProject) return
    items.value = response.items
    total.value = response.total
  } catch (loadError) {
    if (sequence !== listSequence) return
    items.value = []
    total.value = 0
    error.value = draftError(loadError, '缺陷草稿加载失败，请稍后重试。')
  } finally {
    if (sequence === listSequence) loading.value = false
  }
}

async function applyFilter(): Promise<void> {
  await syncQuery()
  await loadDrafts(true)
}

async function selectProject(value: number): Promise<void> {
  projectId.value = value
  await applyFilter()
}

async function createDraft(): Promise<void> {
  const selectedRun = runId.value.trim()
  if (!selectedRun || !promptId.value) {
    ElMessage.warning('请先填写失败运行 ID 并选择缺陷草稿 Prompt')
    return
  }
  generating.value = true
  try {
    await generateDefectDraft(selectedRun, {
      prompt_id: promptId.value,
      case_run_id: caseRunId.value,
      additional_instructions: instructions.value.trim() || undefined,
    })
    ElMessage.success('缺陷草稿已生成；它不会自动提交到外部系统')
    await loadDrafts(true)
  } catch (generateError) {
    ElMessage.error(draftError(generateError, '缺陷草稿生成失败，请核对运行状态和 AI 配置。'))
  } finally {
    generating.value = false
  }
}

function openEditor(draft: DefectDraft): void {
  editingId.value = draft.id
  editForm.value = {
    revision: draft.revision,
    title: draft.title,
    module: draft.module,
    environment: draft.environment,
    preconditions: [...draft.preconditions],
    reproduction_steps: [...draft.reproduction_steps],
    expected_result: draft.expected_result,
    actual_result: draft.actual_result,
    evidence: [...draft.evidence],
    ai_analysis: draft.ai_analysis,
  }
  editorVisible.value = true
}

async function saveDraft(): Promise<void> {
  if (!editingId.value || !editForm.value) return
  if (!editForm.value.title.trim() || !editForm.value.reproduction_steps.length) {
    ElMessage.warning('标题和复现步骤不能为空')
    return
  }
  saving.value = true
  try {
    const updated = await updateDefectDraft(editingId.value, editForm.value)
    const index = items.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) items.value[index] = updated
    editorVisible.value = false
    ElMessage.success('缺陷草稿已保存')
  } catch (saveError) {
    ElMessage.error(draftError(saveError, '缺陷草稿保存失败，请稍后重试。'))
  } finally {
    saving.value = false
  }
}

function exportedFileName(disposition: string, draft: DefectDraft): string {
  const match = disposition.match(/filename="([A-Za-z0-9._-]+)"/i)
  return match?.[1] ?? `defect-draft-${draft.id}.md`
}

async function withExport(draft: DefectDraft, mode: 'copy' | 'download'): Promise<void> {
  if (exportingId.value) return
  exportingId.value = draft.id
  try {
    const exported = await exportDefectDraft(draft.id)
    if (mode === 'copy') {
      await navigator.clipboard.writeText(await exported.blob.text())
      ElMessage.success('Markdown 缺陷草稿已复制')
      return
    }
    const objectUrl = URL.createObjectURL(exported.blob)
    const anchor = document.createElement('a')
    try {
      anchor.href = objectUrl
      anchor.download = exportedFileName(exported.contentDisposition, draft)
      anchor.rel = 'noopener'
      document.body.appendChild(anchor)
      anchor.click()
    } finally {
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
    }
  } catch (exportError) {
    ElMessage.error(draftError(exportError, mode === 'copy' ? '复制失败。' : '导出失败。'))
  } finally {
    exportingId.value = undefined
  }
}

async function loadInitial(): Promise<void> {
  try {
    const [projectResponse, promptResponse] = await Promise.all([
      getProjects(true),
      getPrompts(false, 'DEFECT_DRAFT'),
    ])
    projects.value = projectResponse.items
    prompts.value = promptResponse
    const requestedProject = positiveQuery('project_id')
    projectId.value = requestedProject && projects.value.some((item) => item.id === requestedProject)
      ? requestedProject
      : projects.value.find((item) => item.status === 'ACTIVE')?.id ?? projects.value[0]?.id
    runId.value = stringQuery('run_id')
    promptId.value = availablePrompts.value[0]?.id
    await loadDrafts(true)
  } catch (loadError) {
    error.value = draftError(loadError, '缺陷草稿初始化失败，请稍后重试。')
  }
}

onMounted(loadInitial)
</script>

<template>
  <div class="defect-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow dark">内部缺陷草稿</span>
        <h1>缺陷草稿</h1>
        <p>从失败运行生成可编辑草稿；复制与导出均为 Markdown，不会自动提交 Jira、禅道或其他外部系统。</p>
      </div>
      <el-button :loading="loading" :disabled="!projectId" @click="loadDrafts()">刷新</el-button>
    </header>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <el-alert v-if="currentProject?.status === 'ARCHIVED'" title="归档项目中的缺陷草稿只读" type="info" show-icon :closable="false" />

    <el-card class="filter-card" shadow="never">
      <div class="filter-grid">
        <label><span>项目</span><el-select :model-value="projectId" filterable @update:model-value="selectProject"><el-option v-for="project in projects" :key="project.id" :label="`${project.name}（${project.code}）`" :value="project.id" /></el-select></label>
        <label class="run-field"><span>运行 ID</span><el-input v-model="runId" clearable maxlength="128" placeholder="失败或超时运行 ID；留空查看项目全部草稿" /></label>
        <div class="filter-actions"><el-button type="primary" :disabled="!projectId" @click="applyFilter">查询</el-button></div>
      </div>
    </el-card>

    <el-card class="generate-card" shadow="never">
      <template #header><div><strong>AI 生成新草稿</strong><small>来源为服务端脱敏的失败报告快照，必须人工检查和编辑。</small></div></template>
      <div class="generate-grid">
        <label><span>缺陷草稿 Prompt</span><el-select v-model="promptId" placeholder="选择已绑定输出 Schema 的 Prompt"><el-option v-for="prompt in availablePrompts" :key="prompt.id" :label="`${prompt.name}（${prompt.code}）`" :value="prompt.id" /></el-select></label>
        <label><span>用例运行 ID（可选）</span><el-input-number v-model="caseRunId" :min="1" :max="2147483647" controls-position="right" /></label>
        <label class="instruction-field"><span>补充说明（可选）</span><el-input v-model="instructions" type="textarea" :rows="2" maxlength="5000" show-word-limit /></label>
        <el-button type="primary" :loading="generating" :disabled="!canMutate || !runId.trim() || !promptId" @click="createDraft">生成草稿</el-button>
      </div>
      <el-empty v-if="!availablePrompts.length" description="没有可用的缺陷草稿 Prompt，请先在 Prompt 中心配置输出 Schema，并在项目设置绑定模型" :image-size="52" />
    </el-card>

    <el-card class="list-card" shadow="never">
      <el-table v-loading="loading" :data="items" row-key="id" table-layout="fixed">
        <el-table-column label="草稿" min-width="260"><template #default="{ row }"><div class="primary-cell">{{ row.title }}</div><code>第 {{ row.revision }} 版</code></template></el-table-column>
        <el-table-column prop="module" label="模块" min-width="140" />
        <el-table-column label="关联执行" min-width="230"><template #default="{ row }"><code>{{ row.run_id }}</code><small class="secondary-cell">用例运行 {{ row.case_run_id ?? '未指定' }}</small></template></el-table-column>
        <el-table-column label="AI 审计" min-width="190"><template #default="{ row }"><span>{{ row.actual_model }}</span><small class="secondary-cell">{{ row.needs_human_review ? '需人工复核' : '建议人工检查' }}</small></template></el-table-column>
        <el-table-column label="更新时间" width="175"><template #default="{ row }">{{ formatReportDate(row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="250" fixed="right"><template #default="{ row }"><el-button link type="primary" :disabled="!canMutate" @click="openEditor(row)">编辑</el-button><el-button link :loading="exportingId === row.id" @click="withExport(row, 'copy')">复制</el-button><el-button link :loading="exportingId === row.id" @click="withExport(row, 'download')">Markdown 导出</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!loading && !items.length" description="当前筛选暂无缺陷草稿" :image-size="72" />
      <div v-if="total" class="pagination-row"><el-pagination :current-page="page" :page-size="pageSize" :page-sizes="[10, 20, 50, 100]" :total="total" layout="total, sizes, prev, pager, next" @current-change="(value: number) => { page = value; loadDrafts() }" @size-change="(value: number) => { pageSize = value; page = 1; loadDrafts() }" /></div>
    </el-card>

    <el-dialog v-model="editorVisible" title="编辑缺陷草稿" width="min(860px, 92vw)" destroy-on-close>
      <el-form v-if="editForm" label-position="top" class="editor-grid">
        <el-form-item label="标题"><el-input v-model="editForm.title" maxlength="300" show-word-limit /></el-form-item>
        <div class="two-columns"><el-form-item label="模块"><el-input v-model="editForm.module" maxlength="200" /></el-form-item><el-form-item label="环境"><el-input v-model="editForm.environment" maxlength="500" /></el-form-item></div>
        <el-form-item label="前置条件（每行一项）"><el-input :model-value="lines(editForm.preconditions)" type="textarea" :rows="3" @update:model-value="editForm.preconditions = splitLines($event)" /></el-form-item>
        <el-form-item label="复现步骤（每行一步）"><el-input :model-value="lines(editForm.reproduction_steps)" type="textarea" :rows="5" @update:model-value="editForm.reproduction_steps = splitLines($event)" /></el-form-item>
        <div class="two-columns"><el-form-item label="预期结果"><el-input v-model="editForm.expected_result" type="textarea" :rows="4" /></el-form-item><el-form-item label="实际结果"><el-input v-model="editForm.actual_result" type="textarea" :rows="4" /></el-form-item></div>
        <el-form-item label="证据（每行一项）"><el-input :model-value="lines(editForm.evidence)" type="textarea" :rows="3" @update:model-value="editForm.evidence = splitLines($event)" /></el-form-item>
        <el-form-item label="AI 分析"><el-input v-model="editForm.ai_analysis" type="textarea" :rows="4" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="editorVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveDraft">保存草稿</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-card,.generate-card,.list-card{margin-bottom:18px;border:1px solid #e3e9f2;border-radius:16px}.filter-grid{display:grid;grid-template-columns:minmax(220px,1fr) minmax(320px,2fr) auto;gap:14px;align-items:end}.filter-grid label,.generate-grid label{display:flex;flex-direction:column;gap:7px;color:#586579;font-size:12px}.filter-actions{display:flex}.generate-card :deep(.el-card__header)>div{display:flex;flex-direction:column;gap:4px}.generate-card small,.secondary-cell{display:block;color:#7c8799;font-size:11px}.generate-grid{display:grid;grid-template-columns:minmax(240px,1.3fr) minmax(170px,.7fr) auto;gap:14px;align-items:end}.instruction-field{grid-column:1/3}.primary-cell{font-weight:700;color:#243047}.pagination-row{display:flex;align-items:center;justify-content:space-between;padding:18px 4px 2px;color:#7c8799;font-size:12px}.two-columns{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:900px){.filter-grid,.generate-grid,.two-columns{grid-template-columns:1fr}.instruction-field{grid-column:auto}}
</style>
