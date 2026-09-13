<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Connection, FullScreen, MagicStick, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, type UploadFile } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  decideApiDesignSuggestion,
  createApiScenarioPlan,
  editApiDesignSuggestion,
  generateApiScenarioPlanItems,
  getApiDefinitions,
  getApiDesignSuggestions,
  getApiScenarioPlans,
  importOpenApi,
  previewOpenApi,
  type OpenApiImportPayload,
} from '@/api/api-definitions'
import { getPrompts } from '@/api/prompt-center'
import { getProjects } from '@/api/projects'
import { getRequirementDocumentVersions, getRequirementTree } from '@/api/requirements'
import type {
  ApiDefinition, ApiDesignSuggestion, ApiDiffStatus, ApiScenarioPlan,
  ApiScenarioPlanItem, OpenApiPreview,
} from '@/types/api-definition'
import type { PromptDefinition } from '@/types/prompt-center'
import type { Project } from '@/types/project'
import type {
  RequirementDocumentSnapshotNode, RequirementDocumentVersion, RequirementTreeNode,
} from '@/types/requirement'
import { showAiGenerationError } from '@/utils/ai-error'
import { formatApiDateTime } from '@/utils/datetime'
import { promptOptionLabel } from '@/utils/prompt-display'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const projects = ref<Project[]>([])
const route = useRoute()
const router = useRouter()
const projectId = ref<number>()
const definitions = ref<ApiDefinition[]>([])
const selected = ref<ApiDefinition | null>(null)
const preview = ref<OpenApiPreview | null>(null)
const importVisible = ref(false)
const detailVisible = ref(false)
const loading = ref(false)
const importing = ref(false)
const payload = ref<OpenApiImportPayload | null>(null)
const designVisible = ref(false)
const designFullscreen = ref(false)
const designLoading = ref(false)
const designSaving = ref(false)
const designPromptId = ref<number>()
const designPlanPromptId = ref<number>()
const designInstructions = ref('')
const designNote = ref('')
const designError = ref('')
const designImportId = ref<number>()
const designPrompts = ref<PromptDefinition[]>([])
const designPlanPrompts = ref<PromptDefinition[]>([])
const designRequirementLoading = ref(false)
const designDocumentVersions = ref<RequirementDocumentVersion[]>([])
const designDocumentVersionId = ref<number>()
const WHOLE_DOCUMENT_SCOPE = 'DOCUMENT' as const
const designRequirementId = ref<number | typeof WHOLE_DOCUMENT_SCOPE>(WHOLE_DOCUMENT_SCOPE)
const designStage = ref<1 | 2 | 3>(1)
const scenarioPlans = ref<ApiScenarioPlan[]>([])
const selectedPlan = ref<ApiScenarioPlan | null>(null)
const selectedPlanItemIds = ref<number[]>([])
const planSubmitting = ref(false)
const detailSubmitting = ref(false)
const designSuggestions = ref<ApiDesignSuggestion[]>([])
const selectedSuggestion = ref<ApiDesignSuggestion | null>(null)
const designJson = ref('')
const requirementPickerVisible = ref(false)
const requirementPickerLoading = ref(false)
const requirementOptions = ref<RequirementTreeNode[]>([])
const selectedRequirementId = ref<number>()
const {
  items: pagedDefinitions, total: definitionTotal, page: definitionPage,
  pageSize: definitionPageSize, changePage: changeDefinitionPage,
  changePageSize: changeDefinitionPageSize,
} = useClientPagination(definitions)
const {
  items: pagedDesignSuggestions, total: designSuggestionTotal, page: designSuggestionPage,
  pageSize: designSuggestionPageSize, changePage: changeDesignSuggestionPage,
  changePageSize: changeDesignSuggestionPageSize,
} = useClientPagination(designSuggestions)

const previewOperations = computed(() => [
  ...(preview.value?.operations ?? []),
  ...(preview.value?.removed ?? []),
])
const latestImportId = computed(() => {
  const ids = definitions.value
    .map((item) => item.import_id)
    .filter((item): item is number => item !== null)
  return ids.length ? Math.max(...ids) : undefined
})
const formattedRawResponse = computed(() => {
  const rawResponse = selectedSuggestion.value?.raw_response ?? ''
  try {
    return JSON.stringify(JSON.parse(rawResponse), null, 2)
  } catch {
    return rawResponse
  }
})
const designStatusLabels: Record<ApiDesignSuggestion['status'], string> = {
  DRAFT: '待审核', ACCEPTED: '已接受', REJECTED: '已拒绝',
}
const generationStatusLabels: Record<ApiDesignSuggestion['generation_status'], string> = {
  QUEUED: '等待生成', RUNNING: '正在生成', SUCCEEDED: '已生成完成', FAILED: '生成失败',
}
const planStatusLabels: Record<ApiScenarioPlan['generation_status'], string> = {
  QUEUED: '等待规划', RUNNING: '正在规划', SUCCEEDED: '方案已生成', FAILED: '规划失败',
}
const planCategoryLabels: Record<ApiScenarioPlanItem['category'], string> = {
  POSITIVE: '正向', NEGATIVE: '异常', BOUNDARY: '边界', RECOVERY: '恢复',
}
const selectedDocumentVersion = computed(() => designDocumentVersions.value.find(
  (item) => item.id === designDocumentVersionId.value,
))
const documentRequirementOptions = computed(() => (
  selectedDocumentVersion.value?.snapshot ?? []
).slice().sort((left, right) => left.order_index - right.order_index))
const selectedScopeCount = computed(() => {
  const nodes = selectedDocumentVersion.value?.snapshot ?? []
  if (designRequirementId.value === WHOLE_DOCUMENT_SCOPE) return nodes.length
  const ids = new Set<number>([designRequirementId.value])
  let changed = true
  while (changed) {
    changed = false
    for (const node of nodes) {
      if (!ids.has(node.requirement_id) && node.parent_id !== null && ids.has(node.parent_id)) {
        ids.add(node.requirement_id)
        changed = true
      }
    }
  }
  return ids.size
})
const activeDesignCount = computed(() => designSuggestions.value.filter(
  (item) => item.kind === 'SCENARIO' && ['QUEUED', 'RUNNING'].includes(item.generation_status),
).length + scenarioPlans.value.filter(
  (item) => ['QUEUED', 'RUNNING'].includes(item.generation_status),
).length)
let designPollingTimer: ReturnType<typeof setTimeout> | undefined

function suggestionResult(item: ApiDesignSuggestion): Record<string, unknown> {
  return item.human_result ?? item.structured_result ?? {}
}

function suggestionName(item: ApiDesignSuggestion): string {
  if (item.generation_status === 'QUEUED') return 'AI 场景编排任务（等待生成）'
  if (item.generation_status === 'RUNNING') return 'AI 场景编排任务（正在生成）'
  if (item.generation_status === 'FAILED') return 'AI 场景编排任务（生成失败）'
  const name = suggestionResult(item).name
  return typeof name === 'string' && name.trim() ? name.trim() : '未命名场景建议'
}

function suggestionRationale(item: ApiDesignSuggestion): string {
  if (item.generation_status === 'QUEUED') return '后台记录已创建，正在等待执行'
  if (item.generation_status === 'RUNNING') return 'AI 正在生成并校验场景编排'
  if (item.generation_status === 'FAILED') return item.error_message ?? '模型调用或输出校验失败'
  const rationale = suggestionResult(item).rationale
  return typeof rationale === 'string' && rationale.trim() ? rationale.trim() : '暂无编排说明'
}

function suggestionNodeCount(item: ApiDesignSuggestion): number {
  const dsl = suggestionResult(item).dsl
  if (!dsl || typeof dsl !== 'object' || Array.isArray(dsl)) return 0
  const nodes = (dsl as Record<string, unknown>).nodes
  return Array.isArray(nodes) ? nodes.length : 0
}

function designStatusType(item: ApiDesignSuggestion): 'success' | 'warning' | 'info' | 'danger' {
  if (item.generation_status === 'FAILED') return 'danger'
  if (item.generation_status === 'QUEUED') return 'info'
  if (item.generation_status === 'RUNNING') return 'warning'
  return item.status === 'ACCEPTED' ? 'success' : item.status === 'REJECTED' ? 'info' : 'warning'
}

function designStatusLabel(item: ApiDesignSuggestion): string {
  if (item.generation_status !== 'SUCCEEDED') return generationStatusLabels[item.generation_status]
  return item.status === 'DRAFT' ? '已生成完成 · 待审核' : designStatusLabels[item.status]
}

function formatSuggestionTime(value: string): string {
  return formatApiDateTime(value, value)
}

function planStatusType(plan: ApiScenarioPlan): 'success' | 'warning' | 'info' | 'danger' {
  return ({ QUEUED: 'info', RUNNING: 'warning', SUCCEEDED: 'success', FAILED: 'danger' } as const)[plan.generation_status]
}

function planScopeLabel(plan: ApiScenarioPlan): string {
  const source = plan.requirement_source
  return source.scope_mode === 'DOCUMENT'
    ? `完整需求 V${source.document_version_no} · 整份需求（${source.scope_count} 个节点）`
    : `完整需求 V${source.document_version_no} · ${source.code} ${source.title}（含子需求 ${source.scope_count} 个节点）`
}

function planItemCanGenerate(item: ApiScenarioPlanItem): boolean {
  return !item.latest_suggestion
    || item.latest_suggestion.status === 'REJECTED'
    || item.latest_suggestion.generation_status === 'FAILED'
}

function planItemIsNew(item: ApiScenarioPlanItem): boolean {
  return item.latest_suggestion === null
}

function defaultPlanItemIds(plan: ApiScenarioPlan): number[] {
  return plan.items.filter(planItemIsNew).map((item) => item.id)
}

const newPlanItemIds = computed(() => (
  selectedPlan.value ? defaultPlanItemIds(selectedPlan.value) : []
))

const generatedPlanItemCount = computed(() => (
  selectedPlan.value?.items.filter((item) => !planItemIsNew(item)).length ?? 0
))

function selectAllNewPlanItems(): void {
  if (!selectedPlan.value) return
  const explicitlySelectedRetryIds = selectedPlanItemIds.value.filter((itemId) => {
    const item = selectedPlan.value?.items.find((candidate) => candidate.id === itemId)
    return item && !planItemIsNew(item) && planItemCanGenerate(item)
  })
  selectedPlanItemIds.value = [
    ...new Set([...newPlanItemIds.value, ...explicitlySelectedRetryIds]),
  ]
}

function clearSelectedPlanItems(): void {
  selectedPlanItemIds.value = []
}

function planItemHistoryLabel(item: ApiScenarioPlanItem): string {
  const suggestion = item.latest_suggestion
  if (!suggestion) return '未生成'
  if (suggestion.generation_status === 'FAILED') return '生成失败 · 可手动重试'
  if (suggestion.status === 'REJECTED') return '已拒绝 · 可手动重新生成'
  return `历史已生成 · ${designStatusLabel(suggestion)}`
}

function selectScenarioPlan(plan: ApiScenarioPlan): void {
  selectedPlan.value = plan
  selectedPlanItemIds.value = defaultPlanItemIds(plan)
  designStage.value = 2
}

function showPlanSuggestion(item: ApiScenarioPlanItem): void {
  if (!item.latest_suggestion) return
  selectSuggestion(item.latest_suggestion)
  designStage.value = 3
}

const diffLabels: Record<ApiDiffStatus, string> = {
  ADDED: '新增', CHANGED: '变更', UNCHANGED: '未变化', REMOVED: '已移除',
}
const diffTypes: Record<ApiDiffStatus, 'success' | 'warning' | 'info' | 'danger'> = {
  ADDED: 'success', CHANGED: 'warning', UNCHANGED: 'info', REMOVED: 'danger',
}

function methodType(method: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  return ({ GET: 'success', POST: 'primary', PUT: 'warning', PATCH: 'warning', DELETE: 'danger' } as const)[method] ?? 'info'
}

async function loadDefinitions(): Promise<void> {
  if (!projectId.value) { definitions.value = []; return }
  loading.value = true
  try { definitions.value = await getApiDefinitions(projectId.value) }
  finally { loading.value = false }
}

function openImport(): void {
  preview.value = null
  payload.value = null
  importVisible.value = true
}

async function chooseFile(file: UploadFile): Promise<void> {
  if (!projectId.value || !file.raw) return
  if (!/\.(json|ya?ml)$/i.test(file.name)) {
    ElMessage.warning('请选择 .json、.yaml 或 .yml 文件')
    return
  }
  const nextPayload = {
    project_id: projectId.value,
    filename: file.name,
    content: await file.raw.text(),
    mark_missing_removed: true,
  }
  payload.value = nextPayload
  importing.value = true
  try { preview.value = await previewOpenApi(nextPayload) }
  catch { preview.value = null; ElMessage.error('OpenAPI 文档解析失败，请检查格式') }
  finally { importing.value = false }
}

async function confirmImport(): Promise<void> {
  if (!payload.value || !preview.value) return
  importing.value = true
  try {
    const result = await importOpenApi(payload.value)
    ElMessage.success(
      `导入 V${result.version_no} 完成：新增 ${result.created_count}，更新 ${result.updated_count}`,
    )
    importVisible.value = false
    await loadDefinitions()
  } catch { ElMessage.error('导入失败，请确认项目权限与文档内容') }
  finally { importing.value = false }
}

function showDetail(row: ApiDefinition): void {
  selected.value = row
  detailVisible.value = true
}

function pretty(value: unknown): string { return JSON.stringify(value, null, 2) }

async function loadDesignPrompts(): Promise<void> {
  const [detailPrompts, planPrompts] = await Promise.all([
    getPrompts(false, 'API_SCENARIO_GENERATE'),
    getPrompts(false, 'API_SCENARIO_PLAN'),
  ])
  designPrompts.value = detailPrompts
  designPlanPrompts.value = planPrompts
  designPromptId.value = designPrompts.value[0]?.id
  designPlanPromptId.value = designPlanPrompts.value[0]?.id
}

async function loadScenarioPlans(): Promise<void> {
  if (!designImportId.value) { scenarioPlans.value = []; return }
  scenarioPlans.value = await getApiScenarioPlans(designImportId.value)
  if (selectedPlan.value) {
    const previousStatus = selectedPlan.value.generation_status
    selectedPlan.value = scenarioPlans.value.find(
      (item) => item.id === selectedPlan.value?.id,
    ) ?? scenarioPlans.value[0] ?? null
    if (previousStatus !== 'SUCCEEDED' && selectedPlan.value?.generation_status === 'SUCCEEDED') {
      selectedPlanItemIds.value = defaultPlanItemIds(selectedPlan.value)
    }
  } else {
    selectedPlan.value = scenarioPlans.value[0] ?? null
  }
}

async function loadDesignSuggestions(): Promise<void> {
  if (!designImportId.value) { designSuggestions.value = []; return }
  designSuggestions.value = (await getApiDesignSuggestions(designImportId.value)).filter(
    (item) => item.kind === 'SCENARIO',
  )
  if (selectedSuggestion.value) {
    const refreshed = designSuggestions.value.find(
      (item) => item.id === selectedSuggestion.value?.id,
    )
    if (refreshed && ['QUEUED', 'RUNNING'].includes(selectedSuggestion.value.generation_status)) {
      selectSuggestion(refreshed)
    } else if (refreshed) {
      selectedSuggestion.value = refreshed
    }
  } else if (designSuggestions.value[0]) {
    selectSuggestion(designSuggestions.value[0])
  }
}

function selectSuggestion(item: ApiDesignSuggestion): void {
  selectedSuggestion.value = item
  designJson.value = item.human_result || item.structured_result
    ? pretty(item.human_result ?? item.structured_result)
    : ''
  designNote.value = item.decision_note ?? ''
}

function stopDesignPolling(): void {
  if (designPollingTimer) clearTimeout(designPollingTimer)
  designPollingTimer = undefined
}

function scheduleDesignPolling(): void {
  stopDesignPolling()
  if (!designImportId.value || !activeDesignCount.value) return
  designPollingTimer = setTimeout(async () => {
    try { await Promise.all([loadDesignSuggestions(), loadScenarioPlans()]) }
    catch { /* Keep the current record visible and retry while the dialog remains open. */ }
    scheduleDesignPolling()
  }, 2500)
}

async function openDesign(): Promise<void> {
  designImportId.value = selected.value?.import_id ?? latestImportId.value
  if (!designImportId.value) {
    ElMessage.warning('请先导入 OpenAPI 文档')
    return
  }
  designVisible.value = true
  designFullscreen.value = false
  designLoading.value = true
  designError.value = ''
  designRequirementId.value = WHOLE_DOCUMENT_SCOPE
  designStage.value = 1
  selectedPlan.value = null
  selectedPlanItemIds.value = []
  selectedSuggestion.value = null
  try {
    await Promise.all([
      loadDesignPrompts(), loadDesignSuggestions(), loadDesignRequirements(), loadScenarioPlans(),
    ])
  }
  catch { ElMessage.error('AI 建议配置或历史加载失败') }
  finally { designLoading.value = false; scheduleDesignPolling() }
}

async function generateDesignPlan(): Promise<void> {
  if (!designImportId.value || !designPlanPromptId.value || !designDocumentVersionId.value) {
    ElMessage.warning('请先选择需求版本和场景规划规则')
    return
  }
  planSubmitting.value = true
  designError.value = ''
  try {
    const created = await createApiScenarioPlan(designImportId.value, {
      prompt_id: designPlanPromptId.value,
      requirement_document_version_id: designDocumentVersionId.value,
      ...(typeof designRequirementId.value === 'number'
        ? { requirement_id: designRequirementId.value }
        : {}),
      additional_instructions: designInstructions.value.trim() || undefined,
    })
    scenarioPlans.value = [created, ...scenarioPlans.value.filter((item) => item.id !== created.id)]
    selectedPlan.value = created
    selectedPlanItemIds.value = []
    scheduleDesignPolling()
    designVisible.value = false
    ElMessage.success('场景方案目录正在后台生成，稍后重新打开即可查看进度')
  } catch (error) {
    designError.value = showAiGenerationError(error, '场景规划任务创建失败')
  }
  finally { planSubmitting.value = false }
}

async function generateSelectedPlanItems(): Promise<void> {
  if (!selectedPlan.value || !designPromptId.value || !selectedPlanItemIds.value.length) {
    ElMessage.warning('请至少选择一个候选场景')
    return
  }
  detailSubmitting.value = true
  designError.value = ''
  try {
    const created = await generateApiScenarioPlanItems(selectedPlan.value.id, {
      item_ids: selectedPlanItemIds.value,
      prompt_id: designPromptId.value,
    })
    designSuggestions.value = [
      ...created,
      ...designSuggestions.value.filter(
        (item) => !created.some((createdItem) => createdItem.id === item.id),
      ),
    ]
    if (created[0]) selectSuggestion(created[0])
    selectedPlanItemIds.value = []
    scheduleDesignPolling()
    designVisible.value = false
    ElMessage.success(`已在后台分别生成 ${created.length} 个完整场景编排，稍后重新打开查看`)
  } catch (error) {
    designError.value = showAiGenerationError(error, '完整场景编排任务创建失败')
  } finally {
    detailSubmitting.value = false
  }
}

function flattenRequirements(items: RequirementTreeNode[]): RequirementTreeNode[] {
  return items.flatMap((item) => [item, ...flattenRequirements(item.children)])
}

const flatRequirementOptions = computed(() => flattenRequirements(requirementOptions.value))

async function loadDesignRequirements(): Promise<void> {
  if (!projectId.value) {
    requirementOptions.value = []
    return
  }
  designRequirementLoading.value = true
  try {
    designDocumentVersions.value = await getRequirementDocumentVersions(projectId.value)
    designDocumentVersionId.value = designDocumentVersions.value[0]?.id
    designRequirementId.value = WHOLE_DOCUMENT_SCOPE
  } catch {
    designDocumentVersions.value = []
    ElMessage.error('已确认需求加载失败，请稍后重试')
  } finally {
    designRequirementLoading.value = false
  }
}

async function openRequirementPicker(): Promise<void> {
  if (!selected.value || !projectId.value) return
  requirementPickerVisible.value = true
  requirementPickerLoading.value = true
  selectedRequirementId.value = undefined
  try { requirementOptions.value = await getRequirementTree(projectId.value) }
  catch { ElMessage.error('需求列表加载失败，请稍后重试') }
  finally { requirementPickerLoading.value = false }
}

async function startApiFirstDesign(): Promise<void> {
  if (!selected.value || !projectId.value || !selectedRequirementId.value) {
    ElMessage.warning('请先选择要验证的业务需求')
    return
  }
  requirementPickerVisible.value = false
  detailVisible.value = false
  await router.push({
    name: 'project-requirements',
    params: { projectId: projectId.value },
    query: {
      project_id: String(projectId.value),
      requirement_id: String(selectedRequirementId.value),
      design_api_ids: String(selected.value.id),
      open_ai_design: '1',
    },
  })
  ElMessage.success('已带入当前 API 和需求，正在打开 AI 测试设计')
}

async function openCreatedScenario(): Promise<void> {
  const scenario = selectedSuggestion.value?.created_scenario
  if (!scenario || !projectId.value) return
  designVisible.value = false
  await router.push({
    name: 'project-scenarios',
    params: { projectId: projectId.value },
    query: {
      project_id: String(projectId.value),
      scenario_id: String(scenario.id),
    },
  })
}

function parsedDesignResult(): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(designJson.value)
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error()
    return value as Record<string, unknown>
  } catch {
    ElMessage.error('编辑结果必须是 JSON 对象')
    return null
  }
}

async function saveDesignEdit(): Promise<ApiDesignSuggestion | null> {
  if (!selectedSuggestion.value || selectedSuggestion.value.status !== 'DRAFT') return null
  const result = parsedDesignResult()
  if (!result) return null
  designSaving.value = true
  try {
    const updated = await editApiDesignSuggestion(
      selectedSuggestion.value.id, result, designNote.value.trim() || undefined,
    )
    await loadDesignSuggestions()
    selectSuggestion(updated)
    ElMessage.success('人工编辑已保存')
    return updated
  } catch { ElMessage.error('建议内容不符合固定 Swagger 或 V1 资产约束'); return null }
  finally { designSaving.value = false }
}

async function decideDesign(action: 'ACCEPT' | 'REJECT'): Promise<void> {
  if (!selectedSuggestion.value || selectedSuggestion.value.status !== 'DRAFT') return
  if (action === 'ACCEPT' && !(await saveDesignEdit())) return
  designSaving.value = true
  try {
    const updated = await decideApiDesignSuggestion(
      selectedSuggestion.value.id, action, designNote.value.trim() || undefined,
    )
    await Promise.all([loadDesignSuggestions(), loadDefinitions()])
    selectSuggestion(updated)
    ElMessage.success(action === 'ACCEPT' ? '已生成草稿资产' : '已拒绝并保留审计')
  } catch { ElMessage.error('审核失败，固定接口可能已变化') }
  finally { designSaving.value = false }
}

onMounted(async () => {
  projects.value = (await getProjects()).items
  const requestedProjectId = Number(route.query.project_id)
  projectId.value = projects.value.some((item) => item.id === requestedProjectId)
    ? requestedProjectId
    : projects.value[0]?.id
})
watch(projectId, () => {
  stopDesignPolling()
  selected.value = null
  designVisible.value = false
  designSuggestions.value = []
  void loadDefinitions()
})
watch(designVisible, (visible) => {
  if (!visible) designFullscreen.value = false
})
watch(designDocumentVersionId, () => {
  designRequirementId.value = WHOLE_DOCUMENT_SCOPE
})
onUnmounted(stopDesignPolling)
</script>

<template>
  <div class="api-definition-page">
    <header class="page-heading api-heading">
      <div><span class="eyebrow dark">接口契约中心</span><h1>API 定义</h1><p>导入 Swagger / OpenAPI，审查契约差异并沉淀接口资产。</p></div>
      <div class="requirement-actions">
        <el-select v-model="projectId" placeholder="选择项目" style="width: 220px"><el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" /></el-select>
        <el-button :icon="Refresh" :disabled="!projectId" @click="loadDefinitions">刷新</el-button>
        <el-button :icon="MagicStick" :disabled="!latestImportId" @click="openDesign">AI 场景编排{{ activeDesignCount ? `（${activeDesignCount} 正在生成）` : '' }}</el-button>
        <el-button type="primary" :icon="UploadFilled" :disabled="!projectId" @click="openImport">导入 OpenAPI</el-button>
      </div>
    </header>

    <el-alert v-if="!projects.length" title="请先在项目管理中创建项目" type="info" :closable="false" show-icon />
    <section v-else class="api-card" v-loading="loading">
      <header class="api-card-header"><div><strong>接口资产</strong><span>当前共 {{ definitions.length }} 个有效接口</span></div><el-icon><Connection /></el-icon></header>
      <el-table :data="pagedDefinitions" stripe @row-click="showDetail">
        <el-table-column label="方法" width="105"><template #default="{ row }: { row: ApiDefinition }"><el-tag :type="methodType(row.method)" effect="dark">{{ row.method }}</el-tag></template></el-table-column>
        <el-table-column prop="path" label="路径" min-width="280"><template #default="{ row }: { row: ApiDefinition }"><code class="api-path">{{ row.path }}</code></template></el-table-column>
        <el-table-column prop="name" label="名称" min-width="220" />
        <el-table-column label="标签" min-width="180"><template #default="{ row }: { row: ApiDefinition }"><el-tag v-for="tag in row.tags" :key="tag" size="small" type="info">{{ tag }}</el-tag></template></el-table-column>
        <el-table-column label="来源版本" width="150"><template #default="{ row }: { row: ApiDefinition }">{{ row.source_filename }} · V{{ row.source_version }}</template></el-table-column>
      </el-table>
      <el-empty v-if="!loading && !definitions.length" description="尚未导入 API 定义" />
      <el-pagination v-if="definitionTotal" class="records-pagination" background layout="total, sizes, prev, pager, next" :current-page="definitionPage" :page-size="definitionPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="definitionTotal" @current-change="changeDefinitionPage" @size-change="changeDefinitionPageSize" />
    </section>

    <el-dialog v-model="importVisible" title="导入 Swagger / OpenAPI" width="960px" destroy-on-close>
      <el-upload drag :auto-upload="false" accept=".json,.yaml,.yml" :limit="1" :on-change="chooseFile">
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖入 JSON / YAML 文件，或<em>点击选择</em></div>
      </el-upload>
      <div v-if="preview" class="openapi-preview">
        <header><div><strong>{{ preview.title || preview.filename }}</strong><span>OpenAPI {{ preview.spec_version }}</span></div><div class="diff-summary"><el-tag type="success">新增 {{ preview.added_count }}</el-tag><el-tag type="warning">变更 {{ preview.changed_count }}</el-tag><el-tag type="info">未变化 {{ preview.unchanged_count }}</el-tag><el-tag type="danger">移除 {{ preview.removed_count }}</el-tag></div></header>
        <el-table :data="previewOperations" max-height="360">
          <el-table-column label="差异" width="95"><template #default="{ row }"><el-tag :type="diffTypes[row.diff_status as ApiDiffStatus]" size="small">{{ diffLabels[row.diff_status as ApiDiffStatus] }}</el-tag></template></el-table-column>
          <el-table-column label="方法" width="90"><template #default="{ row }"><strong>{{ row.method }}</strong></template></el-table-column>
          <el-table-column prop="path" label="路径" min-width="280" />
          <el-table-column prop="name" label="名称" min-width="220" />
        </el-table>
      </div>
      <template #footer><el-button @click="importVisible = false">取消</el-button><el-button type="primary" :loading="importing" :disabled="!preview" @click="confirmImport">确认导入</el-button></template>
    </el-dialog>

    <el-dialog v-model="designVisible" class="api-scenario-dialog" title="OpenAPI AI 场景编排" width="1180px" top="4vh" :fullscreen="designFullscreen" destroy-on-close>
      <template #header>
        <div class="api-scenario-dialog-header">
          <strong>OpenAPI AI 场景编排</strong>
          <el-button text :icon="FullScreen" :aria-label="designFullscreen ? '退出全屏' : '全屏'" @click="designFullscreen = !designFullscreen">{{ designFullscreen ? '退出全屏' : '全屏' }}</el-button>
        </div>
      </template>
      <div v-loading="designLoading" class="swagger-ai-workbench">
        <el-steps :active="designStage - 1" finish-status="success" align-center class="api-design-steps">
          <el-step title="1. 选择需求范围" description="固定需求版本与读取范围" />
          <el-step title="2. 场景方案目录" description="先规划，再选择候选场景" />
          <el-step title="3. 完整场景编排" description="逐场景生成、审核和入库" />
        </el-steps>
        <div class="api-design-stage-nav">
          <el-button :type="designStage === 1 ? 'primary' : 'default'" @click="designStage = 1">需求范围与规划历史</el-button>
          <el-button :type="designStage === 2 ? 'primary' : 'default'" :disabled="!selectedPlan" @click="designStage = 2">场景方案目录</el-button>
          <el-button :type="designStage === 3 ? 'primary' : 'default'" :disabled="!designSuggestions.length" @click="designStage = 3">完整编排记录</el-button>
        </div>
        <el-alert v-if="designError" :title="designError" type="error" show-icon :closable="false" />

        <section v-if="designStage === 1" class="api-design-stage">
          <el-alert title="先确定 AI 读取哪一版完整需求、整份文档还是某个需求子树。所选节点会自动包含它的全部子需求。" description="提交后窗口自动关闭，场景方案目录在后台生成；重新打开即可查看进度，不会阻塞当前操作。" type="info" show-icon :closable="false" />
          <el-form label-position="top" class="api-design-form">
            <div class="config-form-grid">
              <el-form-item label="OpenAPI 来源版本"><el-input :model-value="definitions.find((item) => item.import_id === designImportId)?.source_filename + ' · V' + definitions.find((item) => item.import_id === designImportId)?.source_version" disabled /></el-form-item>
              <el-form-item label="场景规划规则">
                <el-select v-model="designPlanPromptId" placeholder="选择启用的场景规划规则">
                  <el-option v-for="prompt in designPlanPrompts" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="完整需求版本">
                <el-select v-model="designDocumentVersionId" :loading="designRequirementLoading" placeholder="选择已确认的完整需求版本">
                  <el-option v-for="version in designDocumentVersions" :key="version.id" :label="`完整需求 V${version.version_no} · ${formatSuggestionTime(version.created_at)}`" :value="version.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="需求范围">
                <el-select v-model="designRequirementId" filterable :loading="designRequirementLoading" placeholder="选择整份需求或一个需求子树">
                  <el-option label="整份需求（包含全部需求节点）" :value="WHOLE_DOCUMENT_SCOPE" />
                  <el-option v-for="item in documentRequirementOptions" :key="item.requirement_id" :label="`${item.outline_number} ${item.code} · ${item.title}`" :value="item.requirement_id" />
                </el-select>
              </el-form-item>
            </div>
            <div class="api-scope-summary">
              <strong>{{ designRequirementId === WHOLE_DOCUMENT_SCOPE ? '整份需求' : documentRequirementOptions.find((item) => item.requirement_id === designRequirementId)?.title }}</strong>
              <span>本次将固定读取完整需求 V{{ selectedDocumentVersion?.version_no ?? '—' }} 中的 {{ selectedScopeCount }} 个节点；之后的需求修改不会改变本次规划依据。</span>
            </div>
            <el-form-item label="附加规划要求"><el-input v-model="designInstructions" maxlength="5000" show-word-limit placeholder="例如：同时覆盖正常下单、认证失败、库存不足和失败后清理" /></el-form-item>
            <el-alert v-if="!designPlanPrompts.length" title="当前没有可用的场景规划规则" description="请先在提示词中心启用 API_SCENARIO_PLAN 规则；内置演示项目可在 AI 主演示页补齐初始化资产。" type="warning" :closable="false" show-icon />
            <el-button type="primary" :icon="MagicStick" :loading="planSubmitting" :disabled="designLoading || designRequirementLoading || !designDocumentVersionId || !designPlanPromptId" @click="generateDesignPlan">生成场景方案目录</el-button>
          </el-form>
          <el-divider content-position="left">场景规划历史</el-divider>
          <div v-if="scenarioPlans.length" class="api-plan-history">
            <button v-for="plan in scenarioPlans" :key="plan.id" type="button" @click="selectScenarioPlan(plan)">
              <div><strong>{{ planScopeLabel(plan) }}</strong><el-tag :type="planStatusType(plan)" size="small">{{ planStatusLabels[plan.generation_status] }}</el-tag></div>
              <span>{{ plan.summary || (['QUEUED', 'RUNNING'].includes(plan.generation_status) ? 'AI 正在后台分析需求与接口契约' : plan.error_message || '暂无规划摘要') }}</span>
              <small>{{ formatSuggestionTime(plan.created_at) }}<template v-if="plan.generation_status === 'SUCCEEDED'"> · {{ plan.items.length }} 个候选场景</template></small>
            </button>
          </div>
          <el-empty v-else description="尚未生成场景方案目录" :image-size="72" />
        </section>

        <section v-else-if="designStage === 2 && selectedPlan" class="api-design-stage">
          <div class="api-stage-heading">
            <div><h3>场景方案目录</h3><p>{{ planScopeLabel(selectedPlan) }}</p></div>
            <el-tag :type="planStatusType(selectedPlan)">{{ planStatusLabels[selectedPlan.generation_status] }}</el-tag>
          </div>
          <el-alert v-if="['QUEUED', 'RUNNING'].includes(selectedPlan.generation_status)" title="AI 正在后台生成场景方案目录" description="可以关闭窗口，稍后重新打开查看；页面会自动刷新状态。" type="info" show-icon :closable="false" />
          <el-alert v-else-if="selectedPlan.generation_status === 'FAILED'" title="场景方案目录生成失败" :description="selectedPlan.error_message ?? '模型调用或输出结构校验失败。'" type="error" show-icon :closable="false" />
          <template v-else>
            <p class="api-plan-summary">{{ selectedPlan.summary }}</p>
            <div class="api-plan-selection-tools">
              <span>
                未生成 {{ newPlanItemIds.length }} 项
                <template v-if="generatedPlanItemCount"> · 历史已生成 {{ generatedPlanItemCount }} 项（本轮默认跳过）</template>
              </span>
              <div>
                <el-button size="small" type="primary" plain :disabled="!newPlanItemIds.length" @click="selectAllNewPlanItems">一键全选未生成项</el-button>
                <el-button size="small" :disabled="!selectedPlanItemIds.length" @click="clearSelectedPlanItems">取消全选</el-button>
              </div>
            </div>
            <el-checkbox-group v-model="selectedPlanItemIds" class="api-plan-candidates">
              <div
                v-for="item in selectedPlan.items"
                :key="item.id"
                class="api-plan-candidate"
                :class="{
                  'is-generated': !planItemIsNew(item),
                  'is-current-selection': selectedPlanItemIds.includes(item.id),
                }"
              >
                <el-checkbox :value="item.id" :disabled="!planItemCanGenerate(item)" />
                <div class="api-plan-candidate-body">
                  <header><strong>{{ item.name }}</strong><el-tag size="small">{{ planCategoryLabels[item.category] }}</el-tag><el-tag size="small" type="warning">{{ item.priority }}</el-tag></header>
                  <p>{{ item.objective }}</p>
                  <span>{{ item.rationale }}</span>
                  <small>覆盖 {{ item.requirement_ids.length }} 项需求 · {{ item.api_definition_ids.length }} 个接口 · {{ item.api_flow.join(' → ') }}</small>
                </div>
                <div class="api-plan-candidate-action">
                  <el-tag :type="item.latest_suggestion ? designStatusType(item.latest_suggestion) : 'info'" size="small">{{ planItemHistoryLabel(item) }}</el-tag>
                  <small v-if="item.latest_suggestion">再次全选时不会重复选择</small>
                  <el-button v-if="item.latest_suggestion" text type="primary" @click.stop="showPlanSuggestion(item)">查看编排</el-button>
                </div>
              </div>
            </el-checkbox-group>
            <div class="api-plan-generate-bar">
              <div><strong>已选择 {{ selectedPlanItemIds.length }} 个候选场景</strong><span>默认只选择从未生成的方案；失败或已拒绝的方案可手动勾选重试。每个候选都会建立独立任务。</span></div>
              <el-select v-model="designPromptId" placeholder="选择完整编排规则" style="width:290px"><el-option v-for="prompt in designPrompts" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" /></el-select>
              <el-button type="primary" :icon="MagicStick" :loading="detailSubmitting" :disabled="!selectedPlanItemIds.length || !designPromptId" @click="generateSelectedPlanItems">分别生成完整编排</el-button>
            </div>
          </template>
        </section>

        <section v-else class="api-design-stage">
          <div class="api-stage-heading"><div><h3>完整场景编排记录</h3><p>逐条审核 AI 生成的完整 DSL，接受后生成可继续编辑的草稿场景。</p></div></div>
          <div class="config-form-grid api-design-review-grid">
            <section>
              <el-table :data="pagedDesignSuggestions" max-height="500" highlight-current-row @row-click="selectSuggestion">
                <el-table-column label="编排信息" min-width="300"><template #default="{ row }: { row: ApiDesignSuggestion }"><div class="api-suggestion-summary"><strong>{{ suggestionName(row) }}</strong><span :title="suggestionRationale(row)">{{ suggestionRationale(row) }}</span><small v-if="row.requirement_source">{{ row.requirement_source.scope_mode === 'DOCUMENT' ? '整份需求' : row.requirement_source.code }} · 完整需求 V{{ row.requirement_source.document_version_no }}<template v-if="row.generation_status === 'SUCCEEDED'"> · {{ suggestionNodeCount(row) }} 个节点</template></small><small>{{ formatSuggestionTime(row.created_at) }}</small></div></template></el-table-column>
                <el-table-column label="状态" width="150"><template #default="{ row }: { row: ApiDesignSuggestion }"><div class="api-suggestion-state"><el-tag :type="designStatusType(row)" size="small">{{ designStatusLabel(row) }}</el-tag><el-tag v-if="row.generation_status === 'SUCCEEDED' && row.repair_used" size="small">输出修复</el-tag></div></template></el-table-column>
              </el-table>
              <el-empty v-if="!designSuggestions.length" description="尚未生成完整场景编排" :image-size="70" />
              <el-pagination v-if="designSuggestionTotal" class="records-pagination" small layout="total, sizes, prev, pager, next" :current-page="designSuggestionPage" :page-size="designSuggestionPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="designSuggestionTotal" @current-change="changeDesignSuggestionPage" @size-change="changeDesignSuggestionPageSize" />
            </section>
            <section v-if="selectedSuggestion">
              <div class="api-card-header"><div><strong>{{ suggestionName(selectedSuggestion) }}</strong><span>{{ selectedSuggestion.actual_model ?? '模型待分配' }} · {{ designStatusLabel(selectedSuggestion) }}</span></div></div>
              <div class="api-suggestion-detail-meta"><span>来源：{{ selectedSuggestion.source_filename }} · OpenAPI V{{ selectedSuggestion.source_version }}</span><span v-if="selectedSuggestion.requirement_source">需求：{{ selectedSuggestion.requirement_source.scope_mode === 'DOCUMENT' ? '整份需求' : `${selectedSuggestion.requirement_source.code} · ${selectedSuggestion.requirement_source.title}` }} · 完整需求 V{{ selectedSuggestion.requirement_source.document_version_no }} · {{ selectedSuggestion.requirement_source.scope_count }} 个节点</span><span>提交：{{ formatSuggestionTime(selectedSuggestion.created_at) }}</span></div>
              <el-alert v-if="['QUEUED', 'RUNNING'].includes(selectedSuggestion.generation_status)" :title="selectedSuggestion.generation_status === 'QUEUED' ? '编排任务正在等待执行' : 'AI 正在后台生成完整场景编排'" type="info" show-icon :closable="false" />
              <el-alert v-else-if="selectedSuggestion.generation_status === 'FAILED'" title="完整场景编排生成失败" :description="selectedSuggestion.error_message ?? '模型调用或输出校验失败。'" type="error" show-icon :closable="false" />
              <template v-else>
                <el-input v-model="designJson" type="textarea" :rows="16" :disabled="selectedSuggestion.status !== 'DRAFT'" />
                <el-input v-model="designNote" style="margin-top:10px" maxlength="500" placeholder="人工审核说明" :disabled="selectedSuggestion.status !== 'DRAFT'" />
                <div v-if="selectedSuggestion.status === 'DRAFT'" class="requirement-actions" style="margin-top:12px"><el-button :loading="designSaving" @click="saveDesignEdit">保存编辑</el-button><el-button type="danger" plain :loading="designSaving" @click="decideDesign('REJECT')">拒绝</el-button><el-button type="success" :loading="designSaving" @click="decideDesign('ACCEPT')">接受并生成草稿资产</el-button></div>
                <div v-else-if="selectedSuggestion.status === 'ACCEPTED'" class="api-created-asset"><div v-if="selectedSuggestion.created_scenario"><span>已生成草稿场景</span><strong><code>{{ selectedSuggestion.created_scenario.code }}</code> · {{ selectedSuggestion.created_scenario.name }}</strong><small>V{{ selectedSuggestion.created_scenario.version_no ?? '—' }} · {{ selectedSuggestion.created_scenario.status === 'DRAFT' ? '草稿' : selectedSuggestion.created_scenario.status }}</small></div><div v-else><strong>建议已接受</strong><small>生成的资产当前不可用或已归档</small></div><el-button v-if="selectedSuggestion.created_scenario" type="success" plain @click="openCreatedScenario">前往测试编排</el-button></div>
                <el-alert v-else title="已拒绝，审计历史保留" type="info" show-icon :closable="false" style="margin-top:12px" />
              </template>
              <el-collapse class="api-scenario-trace"><el-collapse-item title="技术追踪信息" name="technical"><span>建议内部记录 {{ selectedSuggestion.id }} · AI 调用内部记录 {{ selectedSuggestion.ai_call_id ?? '—' }} · Prompt 版本记录 {{ selectedSuggestion.prompt_version_id ?? '—' }} · 输出结构记录 {{ selectedSuggestion.output_schema_id ?? '—' }} · 场景内部记录 {{ selectedSuggestion.created_scenario_id ?? '—' }}</span></el-collapse-item><el-collapse-item v-if="selectedSuggestion.raw_response" title="脱敏后的 AI 原始输出" name="raw"><pre class="contract-viewer api-raw-output">{{ formattedRawResponse }}</pre></el-collapse-item></el-collapse>
            </section>
          </div>
        </section>
      </div>
    </el-dialog>

    <el-dialog v-model="requirementPickerVisible" title="用此 API 发起测试设计" width="620px">
      <div v-loading="requirementPickerLoading" class="api-requirement-picker">
        <el-alert title="先选择这个 API 要验证的业务需求。进入后，AI 会分析该需求及其子需求，并推荐完整接口闭环；当前 API 会被强制保留。" type="info" :closable="false" show-icon />
        <el-select v-model="selectedRequirementId" filterable placeholder="按需求编号或名称选择" style="width:100%">
          <el-option v-for="item in flatRequirementOptions" :key="item.id" :label="`${item.code} · ${item.title}`" :value="item.id" :disabled="item.status !== 'ACTIVE' || !item.current_version_id" />
        </el-select>
      </div>
      <template #footer><el-button @click="requirementPickerVisible = false">取消</el-button><el-button type="primary" :disabled="!selectedRequirementId" @click="startApiFirstDesign">进入 AI 测试设计</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="API 契约详情" size="58%">
      <template v-if="selected"><div class="api-detail-title"><el-tag :type="methodType(selected.method)" effect="dark">{{ selected.method }}</el-tag><code>{{ selected.path }}</code><el-button type="primary" :icon="MagicStick" @click="openRequirementPicker">用此 API 发起测试设计</el-button></div><h3>{{ selected.name }}</h3><p class="api-description">{{ selected.description || selected.summary || '暂无描述' }}</p><el-alert title="选择业务需求后，AI 会综合需求语义与全部接口契约设计测试闭环；当前 API 将作为指定起点保留。" type="info" :closable="false" show-icon /><el-tabs><el-tab-pane label="参数"><pre class="contract-viewer">{{ pretty(selected.parameters) }}</pre></el-tab-pane><el-tab-pane label="请求体"><pre class="contract-viewer">{{ pretty(selected.request_schema) }}</pre></el-tab-pane><el-tab-pane label="响应"><pre class="contract-viewer">{{ pretty(selected.response_schema) }}</pre></el-tab-pane><el-tab-pane label="鉴权"><pre class="contract-viewer">{{ pretty(selected.auth_info) }}</pre></el-tab-pane></el-tabs></template>
    </el-drawer>
  </div>
</template>
