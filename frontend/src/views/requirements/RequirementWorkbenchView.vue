<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { DocumentAdd, EditPen, MagicStick, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getApiErrorMessage } from '@/api/http'
import { getProjectMembers, getProjects } from '@/api/projects'
import {
  deleteRequirementReview, getProjectRequirementReviews, getRequirementReviews,
} from '@/api/requirement-reviews'
import {
  deleteCaseDesignTask, getCaseDesignTasks, getProjectCaseDesignTasks,
} from '@/api/test-cases'
import {
  getRequirement,
  getRequirementDocumentVersions,
  getRequirementTreeState,
  getRequirementVersions,
  importMarkdown,
  previewMarkdown,
} from '@/api/requirements'
import { useAuthStore } from '@/stores/auth'
import { formatApiDateTime } from '@/utils/datetime'
import type { Project, ProjectMember } from '@/types/project'
import type { RequirementReview } from '@/types/requirement-review'
import type { CaseDesignTask } from '@/types/test-case'
import type {
  MarkdownPreviewNode,
  Requirement,
  RequirementDocumentVersion,
  RequirementTreeNode,
  RequirementVersion,
} from '@/types/requirement'
import {
  identityIsCurrent,
  isIdentityStorageEvent,
  readRequestIdentity,
  type RequestIdentity,
} from '@/utils/request-context'
import CaseSuggestionPanel from '@/views/requirements/components/CaseSuggestionPanel.vue'
import RequirementLinksImpactPanel from '@/views/requirements/components/RequirementLinksImpactPanel.vue'
import RequirementReviewPanel from '@/views/requirements/components/RequirementReviewPanel.vue'
import RequirementTreeEditorDialog from '@/views/requirements/components/RequirementTreeEditorDialog.vue'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const projects = ref<Project[]>([])
const projectId = ref<number>()
const members = ref<ProjectMember[]>([])
const tree = ref<RequirementTreeNode[]>([])
const selected = ref<Requirement | null>(null)
const aiSelectionConfirmed = ref(false)
const versions = ref<RequirementVersion[]>([])
const documentVersions = ref<RequirementDocumentVersion[]>([])
const {
  items: pagedDocumentVersions, total: documentVersionTotal, page: documentVersionPage,
  pageSize: documentVersionPageSize, changePage: changeDocumentVersionPage,
  changePageSize: changeDocumentVersionPageSize,
} = useClientPagination(documentVersions)
const viewVersionId = ref<number | null>(null)
const viewDocumentVersionId = ref<number | null>(null)
const detailTab = ref<'content' | 'links'>(route.query.tab === 'links' ? 'links' : 'content')
const sideTab = ref<'versions' | 'reviews' | 'ai-cases'>(
  route.query.ai_tab === 'reviews' || route.query.ai_tab === 'ai-cases'
    ? route.query.ai_tab
    : 'versions',
)
const loading = ref(false)
const loadError = ref<string | null>(null)
const importVisible = ref(false)
const importFilename = ref('')
const importContent = ref('')
const previewNodes = ref<MarkdownPreviewNode[]>([])
const aiRecordsVisible = ref(false)
const aiRecordsTab = ref<'reviews' | 'designs'>('reviews')
const aiRecordsScope = ref<'project' | 'requirement'>('project')
const aiRecordsRequirement = ref<{ id: number; code: string; title: string } | null>(null)
const reviewRecordsLoading = ref(false)
const reviewRecords = ref<RequirementReview[]>([])
const reviewRecordsTotal = ref(0)
const reviewRecordsActiveCount = ref(0)
const reviewRecordsPage = ref(1)
const reviewRecordsPageSize = ref(10)
const reviewRecordsError = ref<string | null>(null)
const deletingReviewId = ref<number | null>(null)
const designRecordsLoading = ref(false)
const designRecords = ref<CaseDesignTask[]>([])
const designRecordsTotal = ref(0)
const designRecordsActiveCount = ref(0)
const designRecordsPage = ref(1)
const designRecordsPageSize = ref(10)
const designRecordsError = ref<string | null>(null)
const deletingDesignTaskId = ref<number | null>(null)
const reviewPanelRef = ref<InstanceType<typeof RequirementReviewPanel> | null>(null)
const caseSuggestionPanelRef = ref<InstanceType<typeof CaseSuggestionPanel> | null>(null)
const treeEditorRef = ref<InstanceType<typeof RequirementTreeEditorDialog> | null>(null)
const treeProps = { children: 'children', label: 'title' }

let alive = true
let projectsSequence = 0
let treeSequence = 0
let selectionSequence = 0
let writeSequence = 0
let reviewRecordsSequence = 0
let reviewRecordsTimer: number | undefined
let designRecordsSequence = 0
let designRecordsTimer: number | undefined

const designTaskStatusLabel = {
  QUEUED: '等待分析', RUNNING: '正在分析', SUCCEEDED: '分析完成', FAILED: '分析失败',
} as const
const designTaskStatusType = {
  QUEUED: 'info', RUNNING: 'warning', SUCCEEDED: 'success', FAILED: 'danger',
} as const
const reviewStatusLabel = {
  DRAFT: '待确认', ACCEPTED: '已确认', REJECTED: '已拒绝',
} as const
const reviewStatusType = {
  DRAFT: 'warning', ACCEPTED: 'success', REJECTED: 'danger',
} as const
const verificationTypeLabel = {
  AUTO: '自动判断', API: 'API 测试', WEB: 'Web 自动化', PERFORMANCE: '性能测试', PLATFORM: '平台流程', MANUAL: '人工验证',
} as const
const automationReadinessLabel = {
  READY: '可自动执行', NEEDS_CLARIFICATION: '需要澄清', MANUAL_ONLY: '仅人工验证',
} as const

function positiveQueryId(value: unknown): number | null {
  const candidate = Array.isArray(value) ? value[0] : value
  if (typeof candidate !== 'string' || !/^[1-9]\d*$/.test(candidate)) return null
  const parsed = Number(candidate)
  return Number.isSafeInteger(parsed) ? parsed : null
}

const requestedProjectId = positiveQueryId(route.query.project_id)
const requestedRequirementId = positiveQueryId(route.query.requirement_id)
const requestedVersionId = positiveQueryId(route.query.requirement_version_id)
const currentProject = computed(() => projects.value.find((item) => item.id === projectId.value) ?? null)
const currentMember = computed(() => members.value.find((item) => item.user_id === authStore.user?.id) ?? null)
const canWriteProject = computed(() => Boolean(
  currentProject.value?.status === 'ACTIVE'
  && (
    authStore.user?.roles.includes('ADMIN')
    || currentProject.value?.owner_id === authStore.user?.id
    || currentMember.value?.role === 'PROJECT_OWNER'
    || currentMember.value?.role === 'TESTER'
  ),
))
const canWriteRequirement = computed(() => Boolean(
  canWriteProject.value && selected.value?.status === 'ACTIVE',
))
const displayedTechnicalVersion = computed(() => (
  versions.value.find((item) => item.id === viewVersionId.value)
  ?? selected.value?.current_version
  ?? null
))
const displayedDocumentVersion = computed(() => (
  documentVersions.value.find((item) => item.id === viewDocumentVersionId.value)
  ?? documentVersions.value[0]
  ?? null
))
const displayedDocumentNode = computed(() => (
  displayedDocumentVersion.value?.snapshot.find(
    (item) => item.requirement_id === selected.value?.id,
  ) ?? null
))
const displayedMarkdown = computed(() => (
  displayedDocumentVersion.value
    ? displayedDocumentNode.value?.markdown_content ?? '此需求在所选历史文档版本中尚不存在。'
    : displayedTechnicalVersion.value?.markdown_content ?? ''
))
const selectedVersionLabel = computed(() => (
  displayedDocumentVersion.value ? `文档 V${displayedDocumentVersion.value.version_no}` : '--'
))
const aiRecordsScopeTitle = computed(() => (
  aiRecordsScope.value === 'requirement' && aiRecordsRequirement.value
    ? `${aiRecordsRequirement.value.code} · ${aiRecordsRequirement.value.title}`
    : `${currentProject.value?.name ?? '当前项目'} · 全部需求`
))

function requestContextCurrent(identity: RequestIdentity, contextProjectId?: number): boolean {
  return alive && identityIsCurrent(identity) && projectId.value === contextProjectId
}

function containsRequirement(nodes: RequirementTreeNode[], id: number): boolean {
  return nodes.some((node) => node.id === id || containsRequirement(node.children ?? [], id))
}

function findTreeNode(nodes: RequirementTreeNode[], id?: number): RequirementTreeNode | null {
  if (!id) return null
  for (const node of nodes) {
    if (node.id === id) return node
    const nested = findTreeNode(node.children ?? [], id)
    if (nested) return nested
  }
  return null
}

function outlineForRequirement(requirementId: number): string {
  return findTreeNode(tree.value, requirementId)?.outline_number ?? '历史'
}

function reviewDocumentVersion(review: RequirementReview): number | null {
  const value = review.context_snapshot.document_version
  if (!value || typeof value !== 'object' || !("version_no" in value)) return null
  const versionNo = (value as { version_no?: unknown }).version_no
  return typeof versionNo === 'number' ? versionNo : null
}

function invalidateContext(clearProjects = false): void {
  treeSequence += 1
  selectionSequence += 1
  writeSequence += 1
  tree.value = []
  documentVersions.value = []
  members.value = []
  selected.value = null
  aiSelectionConfirmed.value = false
  sideTab.value = 'versions'
  versions.value = []
  viewVersionId.value = null
  viewDocumentVersionId.value = null
  loadError.value = null
  loading.value = false
  aiRecordsVisible.value = false
  aiRecordsScope.value = 'project'
  aiRecordsRequirement.value = null
  reviewRecordsSequence += 1
  reviewRecords.value = []
  reviewRecordsTotal.value = 0
  reviewRecordsActiveCount.value = 0
  reviewRecordsError.value = null
  if (reviewRecordsTimer !== undefined) window.clearTimeout(reviewRecordsTimer)
  reviewRecordsTimer = undefined
  designRecordsSequence += 1
  designRecords.value = []
  designRecordsTotal.value = 0
  designRecordsActiveCount.value = 0
  designRecordsError.value = null
  if (designRecordsTimer !== undefined) window.clearTimeout(designRecordsTimer)
  designRecordsTimer = undefined
  if (clearProjects) {
    projectsSequence += 1
    projects.value = []
    projectId.value = undefined
  }
}

function formatRecordTime(value: string): string {
  return formatApiDateTime(value)
}

function scheduleReviewRecordsPolling(): void {
  if (reviewRecordsTimer !== undefined) window.clearTimeout(reviewRecordsTimer)
  reviewRecordsTimer = undefined
  if (!alive || !projectId.value || reviewRecordsActiveCount.value === 0) return
  reviewRecordsTimer = window.setTimeout(() => { void loadReviewRecords(true) }, 3_000)
}

async function loadReviewRecords(silent = false): Promise<void> {
  const currentProjectId = projectId.value
  const currentRequirementId = aiRecordsScope.value === 'requirement'
    ? aiRecordsRequirement.value?.id
    : null
  const identity = readRequestIdentity()
  if (!currentProjectId || !identity || (aiRecordsScope.value === 'requirement' && !currentRequirementId)) return
  const sequence = ++reviewRecordsSequence
  if (!silent) reviewRecordsLoading.value = true
  try {
    const result = currentRequirementId
      ? await getRequirementReviews(currentRequirementId).then((items) => ({
          items: items.slice(
            (reviewRecordsPage.value - 1) * reviewRecordsPageSize.value,
            reviewRecordsPage.value * reviewRecordsPageSize.value,
          ),
          total: items.length,
          active_count: items.filter((item) => ['QUEUED', 'RUNNING'].includes(item.generation_status)).length,
        }))
      : await getProjectRequirementReviews(
          currentProjectId, reviewRecordsPage.value, reviewRecordsPageSize.value,
        )
    if (
      sequence !== reviewRecordsSequence || !requestContextCurrent(identity, currentProjectId)
      || aiRecordsScope.value !== (currentRequirementId ? 'requirement' : 'project')
      || (currentRequirementId && aiRecordsRequirement.value?.id !== currentRequirementId)
    ) return
    reviewRecords.value = result.items
    reviewRecordsTotal.value = result.total
    reviewRecordsActiveCount.value = result.active_count
    reviewRecordsError.value = null
    if (result.total > 0 && !result.items.length && reviewRecordsPage.value > 1) {
      reviewRecordsPage.value = Math.max(1, Math.ceil(result.total / reviewRecordsPageSize.value))
      void loadReviewRecords()
      return
    }
    scheduleReviewRecordsPolling()
  } catch (error) {
    if (sequence === reviewRecordsSequence && requestContextCurrent(identity, currentProjectId)) {
      reviewRecordsError.value = getApiErrorMessage(error, 'AI 评审记录加载失败')
      scheduleReviewRecordsPolling()
    }
  } finally {
    if (sequence === reviewRecordsSequence) reviewRecordsLoading.value = false
  }
}

function openProjectAiRecords(): void {
  aiRecordsScope.value = 'project'
  aiRecordsRequirement.value = null
  reviewRecordsPage.value = 1
  designRecordsPage.value = 1
  aiRecordsVisible.value = true
  void Promise.all([loadReviewRecords(), loadDesignRecords()])
}

function openReviewRecords(): void {
  if (!selected.value) return
  aiRecordsScope.value = 'requirement'
  aiRecordsRequirement.value = {
    id: selected.value.id,
    code: outlineForRequirement(selected.value.id),
    title: selected.value.title,
  }
  aiRecordsTab.value = 'reviews'
  reviewRecordsPage.value = 1
  designRecordsPage.value = 1
  aiRecordsVisible.value = true
  void Promise.all([loadReviewRecords(), loadDesignRecords()])
}

function changeReviewRecordsPage(value: number): void {
  reviewRecordsPage.value = value
  void loadReviewRecords()
}

function changeReviewRecordsPageSize(value: number): void {
  reviewRecordsPageSize.value = value
  reviewRecordsPage.value = 1
  void loadReviewRecords()
}

function onReviewCreated(): void {
  reviewRecordsPage.value = 1
  void loadReviewRecords()
}

async function openReviewRequirement(review: RequirementReview): Promise<void> {
  aiSelectionConfirmed.value = true
  sideTab.value = 'reviews'
  await selectRequirement(review.requirement_id)
  await nextTick()
  reviewPanelRef.value?.openDetail(review)
}

async function removeReview(review: RequirementReview): Promise<void> {
  if (review.generation_status === 'QUEUED' || review.generation_status === 'RUNNING') return
  try {
    await ElMessageBox.confirm(
      '删除后，该条 AI 评审不会再显示；需求内容和模型调用审计不会被删除。',
      '删除 AI 评审记录',
      { type: 'warning', confirmButtonText: '删除记录', cancelButtonText: '保留' },
    )
  } catch { return }
  deletingReviewId.value = review.id
  try {
    await deleteRequirementReview(review.id)
    ElMessage.success('AI 评审记录已删除')
    await loadReviewRecords()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'AI 评审记录删除失败'))
  } finally {
    deletingReviewId.value = null
  }
}

function scheduleDesignRecordsPolling(): void {
  if (designRecordsTimer !== undefined) window.clearTimeout(designRecordsTimer)
  designRecordsTimer = undefined
  if (!alive || !projectId.value || designRecordsActiveCount.value === 0) return
  designRecordsTimer = window.setTimeout(() => { void loadDesignRecords(true) }, 3_000)
}

async function loadDesignRecords(silent = false): Promise<void> {
  const currentProjectId = projectId.value
  const currentRequirementId = aiRecordsScope.value === 'requirement'
    ? aiRecordsRequirement.value?.id
    : null
  const identity = readRequestIdentity()
  if (!currentProjectId || !identity || (aiRecordsScope.value === 'requirement' && !currentRequirementId)) return
  const sequence = ++designRecordsSequence
  if (!silent) designRecordsLoading.value = true
  try {
    const result = currentRequirementId
      ? await getCaseDesignTasks(currentRequirementId).then((items) => ({
          items: items.slice(
            (designRecordsPage.value - 1) * designRecordsPageSize.value,
            designRecordsPage.value * designRecordsPageSize.value,
          ),
          total: items.length,
          active_count: items.filter((item) => ['QUEUED', 'RUNNING'].includes(item.status)).length,
        }))
      : await getProjectCaseDesignTasks(
          currentProjectId, designRecordsPage.value, designRecordsPageSize.value,
        )
    if (
      sequence !== designRecordsSequence || !requestContextCurrent(identity, currentProjectId)
      || aiRecordsScope.value !== (currentRequirementId ? 'requirement' : 'project')
      || (currentRequirementId && aiRecordsRequirement.value?.id !== currentRequirementId)
    ) return
    designRecords.value = result.items
    designRecordsTotal.value = result.total
    designRecordsActiveCount.value = result.active_count
    designRecordsError.value = null
    if (result.total > 0 && !result.items.length && designRecordsPage.value > 1) {
      designRecordsPage.value = Math.max(1, Math.ceil(result.total / designRecordsPageSize.value))
      void loadDesignRecords(true)
      return
    }
    scheduleDesignRecordsPolling()
  } catch (error) {
    if (sequence === designRecordsSequence && requestContextCurrent(identity, currentProjectId)) {
      designRecordsError.value = getApiErrorMessage(error, 'AI 设计记录加载失败')
      scheduleDesignRecordsPolling()
    }
  } finally {
    if (sequence === designRecordsSequence) designRecordsLoading.value = false
  }
}

function openDesignRecords(): void {
  if (!selected.value) return
  aiRecordsScope.value = 'requirement'
  aiRecordsRequirement.value = {
    id: selected.value.id,
    code: outlineForRequirement(selected.value.id),
    title: selected.value.title,
  }
  aiRecordsTab.value = 'designs'
  reviewRecordsPage.value = 1
  designRecordsPage.value = 1
  aiRecordsVisible.value = true
  void Promise.all([loadReviewRecords(), loadDesignRecords()])
}

function changeDesignRecordsPage(value: number): void {
  designRecordsPage.value = value
  void loadDesignRecords()
}

function changeDesignRecordsPageSize(value: number): void {
  designRecordsPageSize.value = value
  designRecordsPage.value = 1
  void loadDesignRecords()
}

function onDesignTaskCreated(): void {
  designRecordsPage.value = 1
  void loadDesignRecords(true)
}

async function removeDesignTask(task: CaseDesignTask): Promise<void> {
  if (task.status === 'QUEUED' || task.status === 'RUNNING') return
  try {
    await ElMessageBox.confirm(
      '删除后，该条需求侧 AI 设计结果将不再显示；需求、正式用例和模型调用审计不会被删除。',
      '删除 AI 设计记录',
      { type: 'warning', confirmButtonText: '删除记录', cancelButtonText: '保留' },
    )
  } catch { return }
  deletingDesignTaskId.value = task.id
  try {
    await deleteCaseDesignTask(task.id)
    ElMessage.success('AI 设计记录已删除')
    await loadDesignRecords()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'AI 设计记录删除失败'))
  } finally {
    deletingDesignTaskId.value = null
  }
}

async function openDesignTaskRequirement(task: CaseDesignTask): Promise<void> {
  aiSelectionConfirmed.value = true
  sideTab.value = 'ai-cases'
  await selectRequirement(task.requirement_id)
  await nextTick()
  caseSuggestionPanelRef.value?.openDesignRecord(task)
}

async function loadProjects(): Promise<void> {
  const identity = readRequestIdentity()
  if (!identity) return
  const sequence = ++projectsSequence
  try {
    const result = await getProjects(true)
    if (!alive || sequence !== projectsSequence || !identityIsCurrent(identity)) return
    projects.value = result.items
    const desired = requestedProjectId && result.items.some((item) => item.id === requestedProjectId)
      ? requestedProjectId
      : projectId.value && result.items.some((item) => item.id === projectId.value)
        ? projectId.value
        : result.items[0]?.id
    if (projectId.value === desired) await loadTree(requestedRequirementId ?? undefined)
    else projectId.value = desired
  } catch (error) {
    if (alive && sequence === projectsSequence && identityIsCurrent(identity)) {
      loadError.value = getApiErrorMessage(error, '项目列表加载失败')
    }
  }
}

async function loadTree(preferredId?: number): Promise<void> {
  const currentProjectId = projectId.value
  const identity = readRequestIdentity()
  const sequence = ++treeSequence
  selectionSequence += 1
  tree.value = []
  documentVersions.value = []
  members.value = []
  selected.value = null
  versions.value = []
  viewVersionId.value = null
  viewDocumentVersionId.value = null
  loadError.value = null
  if (!currentProjectId || !identity) return
  loading.value = true
  try {
    const currentUserId = authStore.user?.id
    const memberLookupNeeded = !authStore.user?.roles.includes('ADMIN')
      && currentProject.value?.owner_id !== currentUserId
    const [treeResult, documentVersionResult, memberResult] = await Promise.all([
      getRequirementTreeState(currentProjectId),
      getRequirementDocumentVersions(currentProjectId),
      memberLookupNeeded
        ? getProjectMembers(currentProjectId).catch(() => [] as ProjectMember[])
        : Promise.resolve([] as ProjectMember[]),
    ])
    if (sequence !== treeSequence || !requestContextCurrent(identity, currentProjectId)) return
    tree.value = treeResult.items
    documentVersions.value = documentVersionResult
    viewDocumentVersionId.value = treeResult.current_document_version?.id
      ?? documentVersionResult[0]?.id
      ?? null
    members.value = memberResult
    const requested = preferredId ?? requestedRequirementId
    const targetId = requested && containsRequirement(treeResult.items, requested)
      ? requested
      : undefined
    if (targetId) {
      const explicitAiEntry = route.query.open_ai_design === '1' && targetId === requestedRequirementId
      const requestedAiTab = route.query.ai_tab === 'reviews' || route.query.ai_tab === 'ai-cases'
        ? route.query.ai_tab
        : null
      if (explicitAiEntry || requestedAiTab) {
        aiSelectionConfirmed.value = true
        sideTab.value = explicitAiEntry ? 'ai-cases' : requestedAiTab ?? 'versions'
      }
      await selectRequirement(targetId, targetId === requestedRequirementId ? requestedVersionId : null)
    }
  } catch (error) {
    if (sequence === treeSequence && requestContextCurrent(identity, currentProjectId)) {
      loadError.value = getApiErrorMessage(error, '需求树加载失败')
    }
  } finally {
    if (sequence === treeSequence && requestContextCurrent(identity, currentProjectId)) loading.value = false
  }
}

async function selectRequirement(
  requirementId: number,
  preferredVersionId: number | null = null,
  authorizedTreeNode: RequirementTreeNode | null = null,
): Promise<void> {
  const currentProjectId = projectId.value
  const identity = readRequestIdentity()
  const sequence = ++selectionSequence
  selected.value = authorizedTreeNode?.project_id === currentProjectId ? authorizedTreeNode : null
  versions.value = []
  viewVersionId.value = null
  loadError.value = null
  if (!currentProjectId || !identity) return
  loading.value = true
  try {
    const [detail, versionResult] = await Promise.all([
      getRequirement(requirementId),
      getRequirementVersions(requirementId),
    ])
    if (
      sequence !== selectionSequence
      || !requestContextCurrent(identity, currentProjectId)
      || detail.id !== requirementId
      || detail.project_id !== currentProjectId
    ) return
    selected.value = detail
    versions.value = versionResult.filter((item) => item.requirement_id === requirementId)
    viewVersionId.value = preferredVersionId && versions.value.some((item) => item.id === preferredVersionId)
      ? preferredVersionId
      : detail.current_version_id
  } catch (error) {
    if (sequence === selectionSequence && requestContextCurrent(identity, currentProjectId)) {
      loadError.value = getApiErrorMessage(error, '需求详情或版本加载失败')
    }
  } finally {
    if (sequence === selectionSequence && requestContextCurrent(identity, currentProjectId)) loading.value = false
  }
}

function onTreeClick(node: RequirementTreeNode): void {
  aiSelectionConfirmed.value = true
  void selectRequirement(node.id, null, detailTab.value === 'links' ? null : node)
}

function openTreeEditor(review?: RequirementReview): void {
  if (!projectId.value || !canWriteProject.value) return
  treeEditorRef.value?.open(review)
}

async function onDocumentPublished(
  _version: RequirementDocumentVersion,
  preferredRequirementId?: number,
): Promise<void> {
  await loadTree(preferredRequirementId ?? selected.value?.id)
}

async function chooseFile(file: UploadFile): Promise<void> {
  const currentProjectId = projectId.value
  const identity = readRequestIdentity()
  if (!currentProjectId || !identity || !file.raw || !canWriteProject.value) return
  importFilename.value = file.name
  importContent.value = await file.raw.text()
  try {
    const result = await previewMarkdown({
      project_id: currentProjectId, filename: file.name, content: importContent.value,
    })
    if (requestContextCurrent(identity, currentProjectId)) previewNodes.value = result
  } catch (error) {
    if (requestContextCurrent(identity, currentProjectId)) {
      ElMessage.error(getApiErrorMessage(error, 'Markdown 解析失败'))
    }
  }
}

async function confirmImport(): Promise<void> {
  const currentProjectId = projectId.value
  const identity = readRequestIdentity()
  if (!currentProjectId || !identity || !importContent.value || !canWriteProject.value) return
  const sequence = ++writeSequence
  try {
    const count = await importMarkdown({
      project_id: currentProjectId,
      filename: importFilename.value,
      content: importContent.value,
    })
    if (sequence !== writeSequence || !requestContextCurrent(identity, currentProjectId)) return
    importVisible.value = false
    ElMessage.success(`已导入 ${count} 个需求节点`)
    await loadTree()
  } catch (error) {
    if (sequence === writeSequence && requestContextCurrent(identity, currentProjectId)) {
      ElMessage.error(getApiErrorMessage(error, 'Markdown 导入失败；文件内容已保留'))
    }
  }
}

function viewDocumentVersion(version: RequirementDocumentVersion): void {
  viewDocumentVersionId.value = version.id
}

function onIdentityStorage(event: StorageEvent): void {
  if (!isIdentityStorageEvent(event)) return
  invalidateContext(true)
  const identity = readRequestIdentity()
  if (!identity) {
    authStore.signOut()
    void router.replace({ name: 'login', query: { redirect: route.fullPath } })
    return
  }
  authStore.token = identity.token
  authStore.user = identity.user
  void loadProjects()
}

watch(projectId, (id, previousId) => {
  if (id === previousId) return
  invalidateContext()
  if (id) {
    reviewRecordsPage.value = 1
    designRecordsPage.value = 1
    void loadTree(id === requestedProjectId ? requestedRequirementId ?? undefined : undefined)
    void loadReviewRecords(true)
    void loadDesignRecords(true)
  }
})

watch(sideTab, (tab) => {
  if (tab === 'reviews' || tab === 'ai-cases') {
    viewDocumentVersionId.value = documentVersions.value[0]?.id ?? null
  }
})

onMounted(() => {
  window.addEventListener('storage', onIdentityStorage)
  void loadProjects()
})
onBeforeUnmount(() => {
  alive = false
  invalidateContext(true)
  window.removeEventListener('storage', onIdentityStorage)
  if (designRecordsTimer !== undefined) window.clearTimeout(designRecordsTimer)
  if (reviewRecordsTimer !== undefined) window.clearTimeout(reviewRecordsTimer)
})
</script>

<template>
  <div class="requirement-page">
    <header class="page-heading requirement-heading">
      <div><span class="eyebrow dark">需求中心</span><h1>需求管理</h1><p>Markdown 结构化导入、版本追踪、精确关联与确定性影响分析。</p></div>
      <div class="requirement-actions">
        <el-select v-model="projectId" data-testid="requirement-project" placeholder="选择项目" style="width: 240px"><el-option v-for="project in projects" :key="project.id" :label="`${project.name}${project.status === 'ARCHIVED' ? '（已归档）' : ''}`" :value="project.id" /></el-select>
        <el-button :icon="UploadFilled" :disabled="!canWriteProject" @click="importVisible = true">导入 Markdown</el-button>
        <el-button :icon="MagicStick" @click="openProjectAiRecords">AI 记录</el-button>
        <el-button type="primary" :icon="EditPen" :disabled="!canWriteProject" @click="openTreeEditor()">编辑需求树</el-button>
      </div>
    </header>

    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />
    <el-alert v-if="currentProject?.status === 'ARCHIVED'" title="当前项目已归档：授权历史可读，所有写入已禁用。" type="warning" :closable="false" show-icon />
    <el-alert v-else-if="currentProject && !canWriteProject" title="当前角色为只读：历史、Diff、关联和影响分析仍可查看。" type="info" :closable="false" show-icon />
    <el-alert v-if="!projects.length && !loadError" title="请先在项目管理中创建项目" type="info" :closable="false" show-icon />
    <el-alert v-if="route.query.open_ai_design === '1'" title="已从 API 定义带入接口和所选需求，AI 测试设计会自动打开并保留该接口。" type="success" :closable="false" show-icon />
    <section :class="['requirement-workbench', { 'links-expanded': detailTab === 'links' }]" v-loading="loading">
      <aside class="requirement-tree-panel">
        <header><div><strong>需求树</strong><span>{{ tree.length ? '独立滚动查看' : '暂无需求' }}</span></div><el-button link :icon="Refresh" aria-label="刷新需求树" @click="loadTree(selected?.id)" /></header>
        <div class="requirement-tree-scroll" role="region" aria-label="需求树节点" tabindex="0">
          <el-tree :data="tree" :props="treeProps" node-key="id" :current-node-key="aiSelectionConfirmed ? selected?.id : undefined" default-expand-all highlight-current :expand-on-click-node="false" @node-click="onTreeClick">
            <template #default="{ data }: { data: RequirementTreeNode }"><span class="requirement-node"><small>{{ data.outline_number }}</small><span>{{ data.title }}</span></span></template>
          </el-tree>
        </div>
      </aside>

      <main class="requirement-content-panel">
        <template v-if="selected && currentProject">
          <header class="requirement-detail-header"><div><span>{{ outlineForRequirement(selected.id) }} · {{ selected.type }}</span><h2>{{ selected.title }}</h2></div><div><el-tag>{{ selectedVersionLabel }}</el-tag><el-tag type="info">{{ verificationTypeLabel[selected.verification_type] }}</el-tag><el-tag :type="selected.automation_readiness === 'READY' ? 'success' : 'warning'">{{ automationReadinessLabel[selected.automation_readiness] }}</el-tag><el-tag v-if="displayedDocumentVersion?.id !== documentVersions[0]?.id" type="warning">历史快照</el-tag></div></header>
          <el-alert v-if="selected.status === 'ARCHIVED'" title="此需求已归档，关联和版本写入已禁用。" type="warning" :closable="false" show-icon />
          <el-tabs v-model="detailTab" class="requirement-detail-tabs">
            <el-tab-pane label="需求内容" name="content"><pre class="markdown-viewer">{{ displayedMarkdown || '暂无 Markdown 内容' }}</pre></el-tab-pane>
            <el-tab-pane label="关联与影响" name="links">
              <RequirementLinksImpactPanel v-if="detailTab === 'links'" :key="`${projectId}-${selected.id}`" :project="currentProject" :requirement="selected" :versions="versions" :can-write="canWriteRequirement" :initial-version-id="viewVersionId" />
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else description="选择或创建一个需求" :image-size="100" />
      </main>

      <aside class="requirement-version-panel">
        <el-tabs v-model="sideTab" class="requirement-side-tabs">
          <el-tab-pane label="版本" name="versions">
            <header class="side-tab-header"><div><strong>完整需求文档</strong><span>{{ documentVersions.length }} 个发布版本</span></div></header>
            <div class="document-version-note">一个版本对应整棵需求树；节点修改不会再各自形成业务版本。</div>
            <div class="version-list"><button v-for="version in pagedDocumentVersions" :key="version.id" :class="['version-item', { current: version.id === viewDocumentVersionId }]" @click="viewDocumentVersion(version)"><div><strong>V{{ version.version_no }}</strong><el-tag v-if="version.id === documentVersions[0]?.id" size="small" type="success">当前</el-tag><el-tag v-else-if="version.id === viewDocumentVersionId" size="small">查看中</el-tag></div><span>{{ version.change_summary || '无变更说明' }}</span><small>{{ formatApiDateTime(version.created_at) }}</small></button></div>
            <el-pagination v-if="documentVersionTotal" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="documentVersionPage" :page-size="documentVersionPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="documentVersionTotal" @current-change="changeDocumentVersionPage" @size-change="changeDocumentVersionPageSize" />
          </el-tab-pane>
          <el-tab-pane name="reviews" :disabled="!selected || !aiSelectionConfirmed">
            <template #label><span class="ai-tab-label"><el-icon><MagicStick /></el-icon>AI 评审</span></template>
            <RequirementReviewPanel v-if="selected && aiSelectionConfirmed" ref="reviewPanelRef" :requirement-id="selected.id" @review-created="onReviewCreated" @open-review-records="openReviewRecords" @create-version-from-review="openTreeEditor" />
          </el-tab-pane>
          <el-tab-pane name="ai-cases" :disabled="!selected || !aiSelectionConfirmed">
            <template #label><span class="ai-tab-label"><el-icon><MagicStick /></el-icon>AI 用例</span></template>
            <CaseSuggestionPanel v-if="selected && aiSelectionConfirmed" ref="caseSuggestionPanelRef" :requirement-id="selected.id" :auto-open="route.query.open_ai_design === '1'" @design-task-created="onDesignTaskCreated" @open-design-records="openDesignRecords" />
          </el-tab-pane>
        </el-tabs>
        <div v-if="!selected || !aiSelectionConfirmed" class="ai-selection-gate"><el-icon><MagicStick /></el-icon><strong>先选择需求</strong><span>点击左侧需求树中的一个节点后，才能进行 AI 评审和 AI 测试设计。</span></div>
      </aside>
    </section>

    <el-dialog v-model="aiRecordsVisible" title="AI 记录" width="min(1120px, 92vw)" top="5vh" append-to-body class="ai-records-dialog">
      <div class="ai-records-scope"><div><span>{{ aiRecordsScope === 'project' ? '项目范围' : '需求范围' }}</span><strong>{{ aiRecordsScopeTitle }}</strong></div><small>{{ aiRecordsScope === 'project' ? '集中查看当前项目所有需求的后台 AI 任务。' : '这里只显示当前选中需求产生的记录。' }}</small></div>
      <el-tabs v-model="aiRecordsTab" class="ai-records-tabs">
        <el-tab-pane name="reviews">
          <template #label><span>评审记录（{{ reviewRecordsTotal }}）</span></template>
          <div class="design-records-intro"><div><strong>AI 需求评审</strong><span>查看后台状态、历史结论和人工决策。</span></div><el-button :icon="Refresh" :loading="reviewRecordsLoading" @click="loadReviewRecords">刷新</el-button></div>
          <el-alert v-if="reviewRecordsActiveCount" :title="`后台有 ${reviewRecordsActiveCount} 个评审任务正在处理`" type="warning" :closable="false" show-icon />
          <el-alert v-if="reviewRecordsError" :title="reviewRecordsError" type="error" :closable="false" show-icon />
          <el-table v-loading="reviewRecordsLoading" :data="reviewRecords" class="design-records-table">
            <el-table-column label="需求" min-width="220"><template #default="{ row }: { row: RequirementReview }"><div class="design-record-requirement"><strong>{{ outlineForRequirement(row.requirement_id) }} · {{ row.requirement_title }}</strong><span>{{ reviewDocumentVersion(row) ? `生成时完整文档 V${reviewDocumentVersion(row)}` : '旧记录未保存完整文档版本' }}</span></div></template></el-table-column>
            <el-table-column label="状态" width="105"><template #default="{ row }: { row: RequirementReview }"><el-tag v-if="row.generation_status === 'SUCCEEDED'" :type="reviewStatusType[row.status]">{{ reviewStatusLabel[row.status] }}</el-tag><el-tag v-else :type="row.generation_status === 'FAILED' ? 'danger' : 'warning'">{{ row.generation_status === 'FAILED' ? '生成失败' : row.generation_status === 'RUNNING' ? '正在生成' : '等待生成' }}</el-tag></template></el-table-column>
            <el-table-column label="评审结论" min-width="260" show-overflow-tooltip><template #default="{ row }: { row: RequirementReview }">{{ row.error_message || (row.human_result ?? row.structured_result)?.overall_summary || 'AI 正在后台生成评审…' }}</template></el-table-column>
            <el-table-column label="生成时间" width="175"><template #default="{ row }: { row: RequirementReview }">{{ formatRecordTime(row.created_at) }}</template></el-table-column>
            <el-table-column label="操作" width="145" fixed="right"><template #default="{ row }: { row: RequirementReview }"><el-button link type="primary" @click="openReviewRequirement(row)">查看详情</el-button><el-button v-if="row.generation_status === 'SUCCEEDED' || row.generation_status === 'FAILED'" link type="danger" :disabled="!canWriteProject" :loading="deletingReviewId === row.id" @click="removeReview(row)">删除</el-button><span v-else class="design-record-processing">处理中</span></template></el-table-column>
          </el-table>
          <el-empty v-if="!reviewRecordsLoading && !reviewRecords.length" :description="aiRecordsScope === 'project' ? '当前项目还没有 AI 评审记录' : '当前需求还没有 AI 评审记录'" :image-size="72" />
          <el-pagination v-if="reviewRecordsTotal" class="records-pagination" background layout="total, sizes, prev, pager, next" :current-page="reviewRecordsPage" :page-size="reviewRecordsPageSize" :page-sizes="[10, 20, 50, 100]" :total="reviewRecordsTotal" @current-change="changeReviewRecordsPage" @size-change="changeReviewRecordsPageSize" />
        </el-tab-pane>
        <el-tab-pane name="designs">
          <template #label><span>设计记录（{{ designRecordsTotal }}）</span></template>
          <div class="design-records-intro"><div><strong>AI 测试设计</strong><span>关闭设计窗口或切换需求不会停止后台任务。</span></div><el-button :icon="Refresh" :loading="designRecordsLoading" @click="loadDesignRecords()">刷新</el-button></div>
          <el-alert v-if="designRecordsActiveCount" :title="`后台有 ${designRecordsActiveCount} 个设计任务正在处理`" type="warning" :closable="false" show-icon />
          <el-alert v-if="designRecordsError" :title="designRecordsError" type="error" :closable="false" show-icon />
          <el-table v-loading="designRecordsLoading" :data="designRecords" class="design-records-table">
            <el-table-column label="需求" min-width="220"><template #default="{ row }: { row: CaseDesignTask }"><div class="design-record-requirement"><strong>{{ outlineForRequirement(row.requirement_id) }} · {{ row.requirement_title }}</strong><span>生成时需求范围已固定</span></div></template></el-table-column>
            <el-table-column label="状态" width="120"><template #default="{ row }: { row: CaseDesignTask }"><el-tag :type="row.status === 'SUCCEEDED' && row.source === 'RULE_FALLBACK' ? 'warning' : designTaskStatusType[row.status]">{{ row.status === 'SUCCEEDED' && row.source === 'RULE_FALLBACK' ? '规则补全完成' : designTaskStatusLabel[row.status] }}</el-tag></template></el-table-column>
            <el-table-column label="结果" min-width="165"><template #default="{ row }: { row: CaseDesignTask }"><span>{{ row.source === 'AI' ? (row.plan?.platform_completed_checkpoint_count ? 'AI 推荐 · 平台已补全' : 'AI 推荐') : row.source === 'RULE_FALLBACK' ? '平台规则候选' : '等待生成' }}</span><small v-if="row.error_message" class="design-record-error">{{ row.error_message }}</small></template></el-table-column>
            <el-table-column label="提交时间" width="175"><template #default="{ row }: { row: CaseDesignTask }">{{ formatRecordTime(row.created_at) }}</template></el-table-column>
            <el-table-column label="操作" width="145" fixed="right"><template #default="{ row }: { row: CaseDesignTask }"><el-button link type="primary" @click="openDesignTaskRequirement(row)">查看详情</el-button><el-button v-if="row.status === 'SUCCEEDED' || row.status === 'FAILED'" link type="danger" :disabled="!canWriteProject" :loading="deletingDesignTaskId === row.id" @click="removeDesignTask(row)">删除</el-button><span v-else class="design-record-processing">处理中</span></template></el-table-column>
          </el-table>
          <el-empty v-if="!designRecordsLoading && !designRecords.length" :description="aiRecordsScope === 'project' ? '当前项目还没有 AI 设计记录' : '当前需求还没有 AI 设计记录'" :image-size="72" />
          <el-pagination v-if="designRecordsTotal" class="records-pagination" background layout="total, sizes, prev, pager, next" :current-page="designRecordsPage" :page-size="designRecordsPageSize" :page-sizes="[10, 20, 50, 100]" :total="designRecordsTotal" @current-change="changeDesignRecordsPage" @size-change="changeDesignRecordsPageSize" />
        </el-tab-pane>
      </el-tabs>
    </el-dialog>

    <el-dialog v-model="importVisible" title="导入 Markdown 需求" width="760px"><el-upload drag :auto-upload="false" accept=".md,text/markdown" :limit="1" :on-change="chooseFile"><el-icon class="el-icon--upload"><DocumentAdd /></el-icon><div class="el-upload__text">拖入 Markdown 文件，或<em>点击选择</em></div></el-upload><div v-if="previewNodes.length" class="import-preview"><header><strong>解析预览</strong><span>{{ previewNodes.length }} 个节点</span></header><div v-for="node in previewNodes" :key="node.temp_id" class="preview-node" :style="{ paddingLeft: `${(node.level - 1) * 18 + 10}px` }"><span>H{{ node.level }}</span>{{ node.title }}</div></div><template #footer><el-button @click="importVisible = false">取消</el-button><el-button type="primary" :disabled="!previewNodes.length" @click="confirmImport">确认导入</el-button></template></el-dialog>
    <RequirementTreeEditorDialog v-if="projectId" ref="treeEditorRef" :project-id="projectId" :tree="tree" :can-write="canWriteProject" @published="onDocumentPublished" />
  </div>
</template>

<style scoped>
.requirement-detail-tabs{margin-top:14px}.requirement-content-panel{min-width:0}.requirement-workbench.links-expanded{grid-template-columns:285px minmax(0,1fr)}.links-expanded .requirement-version-panel{display:none}.requirement-node{display:flex;align-items:center;gap:6px}.requirement-node small{min-width:28px;color:#58739b}.markdown-viewer{min-height:280px;white-space:pre-wrap}.version-item.current{outline:2px solid #6c7cff}.requirement-actions{flex-wrap:wrap}.document-version-note{margin:0 0 12px;padding:9px 10px;border-radius:8px;background:#f4f7fb;color:#718096;font-size:12px;line-height:1.5}
.requirement-tree-panel{display:grid;height:clamp(560px,calc(100vh - 190px),760px);min-height:0;grid-template-rows:58px minmax(0,1fr);align-self:start;overflow:hidden}.requirement-tree-panel>header>div{min-width:0}.requirement-tree-panel>header strong,.requirement-tree-panel>header span{display:block}.requirement-tree-panel>header span{margin-top:3px;color:#929caf;font-size:12px;line-height:1.35}.requirement-tree-scroll{min-height:0;overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}.requirement-tree-scroll:focus-visible{outline:2px solid #4e83e5;outline-offset:-2px}
.ai-tab-label{display:inline-flex;min-width:0;align-items:center;gap:4px;font-weight:650;white-space:nowrap}.requirement-side-tabs :deep(.el-tabs__nav){display:flex;width:100%}.requirement-side-tabs :deep(.el-tabs__item){min-width:0;flex:1;justify-content:center;padding:0 4px}.requirement-side-tabs :deep(.el-tabs__item .ai-tab-label){color:#416d9f}.requirement-side-tabs :deep(.el-tabs__item.is-active .ai-tab-label){color:#1677ff}.requirement-side-tabs :deep(.el-tabs__item.is-active .ai-tab-label .el-icon){filter:drop-shadow(0 0 5px rgba(22,119,255,.35))}.ai-selection-gate{display:flex;min-height:260px;align-items:center;justify-content:center;flex-direction:column;gap:8px;padding:24px;color:#8792a6;text-align:center}.ai-selection-gate .el-icon{color:#8aaee0;font-size:30px}.ai-selection-gate strong{color:#526078;font-size:15px}.ai-selection-gate span{font-size:13px;line-height:1.6}
.design-records-intro{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:16px}.design-records-intro strong,.design-records-intro span,.design-record-requirement strong,.design-record-requirement span,.design-record-error{display:block}.design-records-intro span,.design-record-requirement span{margin-top:4px;color:#7f899d;font-size:13px}.design-records-table{margin-top:14px}.design-record-error{margin-top:5px;color:#d15c5c;font-size:12px;line-height:1.45;white-space:normal}.design-record-processing{color:#929caf;font-size:12px}.records-pagination{display:flex;justify-content:flex-end;margin-top:18px}
.ai-records-scope{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:8px;padding:13px 16px;border:1px solid #dfe8f5;border-radius:12px;background:#f7faff}.ai-records-scope span,.ai-records-scope strong{display:block}.ai-records-scope span{margin-bottom:4px;color:#6f7d94;font-size:11px}.ai-records-scope strong{color:#25344f}.ai-records-scope small{color:#7d899d}.ai-records-tabs{min-height:470px}
@media(max-width:1100px){.requirement-tree-panel{height:520px}}
</style>
