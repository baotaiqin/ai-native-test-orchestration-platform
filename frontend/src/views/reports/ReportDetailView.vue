<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Download, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { downloadEvidence } from '@/api/evidence'
import { getApiErrorMessage } from '@/api/http'
import {
  exportReport,
  getReport,
  getReportCases,
  getReportEvidence,
  getReportRequirementSources,
  getReportSteps,
} from '@/api/reports'
import { useAuthStore } from '@/stores/auth'
import type {
  ReportCasePage,
  ReportDataField,
  ReportDetailResponse,
  ReportEvidence,
  ReportEvidencePage,
  ReportExportFormat,
  ReportRequirementCapture,
  ReportRequirementSource,
  ReportRequirementSourcePage,
  ReportStepPage,
} from '@/types/reports'
import {
  prepareReportExport,
  readReportAuthIdentity,
  reportExportErrorMessage,
  triggerReportDownload,
} from '@/utils/report-export'
import {
  availabilityLabel,
  displayDataField,
  displayReportValue,
  formatReportBytes,
  formatReportDate,
  formatReportDuration,
  formatReportMilliseconds,
  runStatusLabel,
  runStatusTagType,
  runTypeLabel,
  safeDownloadName,
} from '@/utils/report-display'
import { parseApiDateTime } from '@/utils/datetime'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const detail = ref<ReportDetailResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const cases = ref<ReportCasePage | null>(null)
const steps = ref<ReportStepPage | null>(null)
const evidence = ref<ReportEvidencePage | null>(null)
const requirementSources = ref<ReportRequirementSourcePage | null>(null)
const casePageSize = ref(10)
const stepPageSize = ref(10)
const evidencePageSize = ref(10)
const requirementSourcePageSize = ref(10)
const casesLoading = ref(false)
const stepsLoading = ref(false)
const evidenceLoading = ref(false)
const requirementSourcesLoading = ref(false)
const casesError = ref<string | null>(null)
const stepsError = ref<string | null>(null)
const evidenceError = ref<string | null>(null)
const requirementSourcesError = ref<string | null>(null)
const stepCaseRunIdDraft = ref<number>()
const appliedStepCaseRunId = ref<number>()
const evidenceCaseRunIdDraft = ref<number>()
const evidenceStepRunIdDraft = ref<number>()
const appliedEvidenceCaseRunId = ref<number>()
const appliedEvidenceStepRunId = ref<number>()
const requirementCaseRunIdDraft = ref<number>()
const appliedRequirementCaseRunId = ref<number>()
const requirementSourcesRequestedPage = ref(1)
const requirementSourcesCompletenessSupported = ref(false)
const downloadingId = ref<string | null>(null)
const exportingFormat = ref<ReportExportFormat | null>(null)
let detailSequence = 0
let casesSequence = 0
let stepsSequence = 0
let evidenceSequence = 0
let requirementSourcesSequence = 0
let detailContextSequence = 0
let exportSequence = 0
let detailIdentityFingerprint: string | null = null

const runId = computed(() => {
  const value = Array.isArray(route.params.runId) ? route.params.runId[0] : route.params.runId
  return typeof value === 'string' ? value : ''
})
const summary = computed(() => detail.value?.summary ?? null)
const caseRunOptions = computed(() => {
  const options = [...(detail.value?.cases.items ?? []), ...(cases.value?.items ?? [])]
  return options.filter((item, index) => options.findIndex(candidate => candidate.id === item.id) === index)
})
const stepRunOptions = computed(() => {
  const options = [...(detail.value?.steps.items ?? []), ...(steps.value?.items ?? [])]
  return options.filter((item, index) => options.findIndex(candidate => candidate.id === item.id) === index)
})
const reportIsTerminal = computed(() => {
  const status = summary.value?.status
  return Boolean(status && ['SUCCESS', 'FAILED', 'CANCELLED', 'TIMEOUT'].includes(status))
})
const platformCaseDifference = computed(() => {
  const item = summary.value
  if (!item || !['FAILED', 'TIMEOUT'].includes(item.status)) return false
  return item.case_status_counts.total > 0
    && item.case_status_counts.success === item.case_status_counts.total
})
const dataFields: Array<{ key: 'actual_request' | 'response' | 'extractions' | 'assertions' | 'traces'; label: string }> = [
  { key: 'actual_request', label: '请求配置快照' },
  { key: 'response', label: '响应摘要' },
  { key: 'extractions', label: '提取结果' },
  { key: 'assertions', label: '断言结果' },
  { key: 'traces', label: '执行轨迹' },
]

function systemStatusLabel(value: string | null | undefined): string {
  if (!value) return '未记录'
  const labels: Record<string, string> = {
    ACTIVE: '启用', ARCHIVED: '已归档', DRAFT: '草稿', APPROVED: '已批准',
    PENDING: '等待中', PASS: '通过', FAIL: '未通过', SUCCEEDED: '成功',
    COMPLETED: '已完成', ACCEPTED: '已接受', REJECTED: '已拒绝', RUNNING: '运行中',
    SUCCESS: '成功', FAILED: '失败', REVIEW: '待复核', TIMEOUT: '超时',
  }
  return labels[value] ?? value
}

function triggerTypeLabel(value: string): string {
  const labels: Record<string, string> = { MANUAL: '手动触发', API: 'API 触发', SYSTEM: '系统触发' }
  return labels[value] ?? value
}

function versionBasisLabel(): string {
  return '运行创建时锁定 · 仅引用该版本定义'
}

function requirementTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    SECTION: '需求章节', FEATURE: '功能需求', BUSINESS: '业务需求',
    NON_FUNCTIONAL: '非功能需求', CONSTRAINT: '约束条件',
  }
  return labels[value] ?? value
}

function sourceTypeLabel(value: string | null): string {
  if (!value) return '未记录'
  const labels: Record<string, string> = {
    MANUAL: '人工录入', IMPORT: '文档导入', AI: 'AI 生成',
    AI_GENERATED: 'AI 生成', OPENAPI: 'OpenAPI 导入',
  }
  return labels[value] ?? value
}

function relationTypeLabel(value: string): string {
  const labels: Record<string, string> = { COVERAGE: '覆盖需求', TRACE: '追踪关联' }
  return labels[value] ?? value
}

function relationSourceLabel(value: string): string {
  const labels: Record<string, string> = { AI: 'AI 生成', MANUAL: '人工建立', SYSTEM: '系统建立' }
  return labels[value] ?? value
}

function relationConfidenceLabel(source: ReportRequirementSource): string {
  const percentage = `${Math.round(source.confidence * 100)}%`
  return source.source === 'AI'
    ? `AI 建议置信度 ${percentage}`
    : `关联置信度 ${percentage}`
}

function bindingNoteLabel(value: string): string {
  const labels: Record<string, string> = {
    EXACT_REQUIREMENT_AND_ASSET_VERSION: '需求版本和用例版本均已精确锁定',
    LEGACY_REQUIREMENT_AND_ASSET_VERSION_UNKNOWN: '历史关联未记录双方版本',
    LEGACY_REQUIREMENT_BASELINE_UNKNOWN: '历史关联未记录需求版本',
    ASSET_LEVEL_LINK_HISTORICAL_VERSION_NOT_RECORDED: '历史资产级关联未记录用例版本',
  }
  return labels[value] ?? value
}

function historicalScopeLabel(value: string): string {
  return value === 'RUN_CREATION_SNAPSHOT' ? '运行创建时的不可变快照' : value
}

function stepTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    START: '开始', END: '结束', HTTP: 'API 请求', EXTRACT: '提取数据',
    ASSERT_STATUS: '状态码断言', ASSERT_JSONPATH: 'JSONPath 断言',
    SET_VARIABLE: '设置变量', WAIT: '等待', API_CLEANUP: 'API 清理',
    AI_ASSERTION: 'AI 语义断言', GOTO: '打开页面', RELOAD: '刷新页面',
    BACK: '后退', FORWARD: '前进', FILL: '填写', CLICK: '单击',
    DOUBLE_CLICK: '双击', RIGHT_CLICK: '右键单击', CLEAR: '清空', HOVER: '悬停',
    DRAG_DROP: '拖放', UPLOAD: '上传文件', DOWNLOAD: '下载文件', SELECT: '选择选项',
    CHECK: '勾选', UNCHECK: '取消勾选', RADIO: '选择单选项', PRESS: '按键',
    ENTER: '回车', TAB: '切换焦点', WAIT_ELEMENT: '等待元素', WAIT_URL: '等待网址',
    WAIT_NETWORK_IDLE: '等待网络空闲', WAIT_TIME: '等待时长', WAIT_TEXT: '等待文本',
    COOKIE: '设置 Cookie', LOCAL_STORAGE: '设置本地存储',
    SESSION_STORAGE: '设置会话存储', JS_EVAL: '执行 JavaScript',
    NEW_TAB: '新建标签页', SWITCH_TAB: '切换标签页', CLOSE_TAB: '关闭标签页',
    ASSERT_VISIBLE: '断言元素可见', ASSERT_EXISTS: '断言元素存在',
    ASSERT_ENABLED: '断言元素可用', ASSERT_HIDDEN: '断言元素隐藏',
    ASSERT_CLICKABLE: '断言元素可点击', ASSERT_TEXT: '断言包含文本',
    ASSERT_TEXT_EQUAL: '断言文本相等', ASSERT_INPUT_VALUE: '断言输入值',
    ASSERT_URL: '断言网址', ASSERT_TITLE: '断言标题', ASSERT_ATTRIBUTE: '断言属性',
    ASSERT_ELEMENT_COUNT: '断言元素数量', ASSERT_DOWNLOAD_SUCCESS: '断言下载成功',
    ASSERT_NETWORK_REQUEST: '断言网络请求',
    ASSERT_SCREENSHOT_VISUAL_COMPARE: '断言截图视觉一致', ASSERT_AI_SEMANTIC: 'AI 语义断言',
  }
  return labels[value] ?? value
}

function reportCaseById(caseRunId: number) {
  return caseRunOptions.value.find(item => item.id === caseRunId)
}

function caseRunLabel(caseRunId: number): string {
  const item = reportCaseById(caseRunId)
  if (!item) return '用例运行记录'
  const code = item.target.asset_code
  const name = item.target.asset_name
  const target = code && name && code !== name
    ? `${code} · ${name}`
    : code || name || runTypeLabel(item.target.kind)
  return `第 ${item.sequence_no} 项 · ${target}`
}

function capturedTargetLabel(caseRunId: number, targetType: ReportRequirementCapture['target_type']): string {
  const item = reportCaseById(caseRunId)
  const code = item?.target.asset_code
  const name = item?.target.asset_name
  return code && name && code !== name
    ? `${code} · ${name}`
    : code || name || runTypeLabel(targetType)
}

function capturedVersionLabel(caseRunId: number): string {
  const versionNo = reportCaseById(caseRunId)?.target.version_no
  return versionNo ? `V${versionNo}` : '已锁定执行版本'
}

function stepLabel(stepRunId: number | null): string {
  if (stepRunId === null) return '未关联步骤'
  const item = stepRunOptions.value.find(step => step.id === stepRunId)
  return item ? `第 ${item.sequence_no} 步 · ${item.name}` : '已关联执行步骤'
}

function requirementCaptureStatusLabel(status: ReportRequirementCapture['status']): string {
  if (status === 'CAPTURED') return '已记录来源'
  if (status === 'CAPTURED_EMPTY') return '创建时未关联需求'
  if (status === 'UNSUPPORTED_TARGET') return '此目标类型尚不支持来源捕获'
  return '历史运行未记录，无法判断当时关联'
}

function requirementCaptureStatusType(
  status: ReportRequirementCapture['status'],
): 'success' | 'warning' | 'info' {
  if (status === 'CAPTURED') return 'success'
  if (status === 'CAPTURED_EMPTY') return 'info'
  return 'warning'
}

function fallbackRequirementCapture(caseItem: ReportCasePage['items'][number]): ReportRequirementCapture {
  return {
    case_run_id: caseItem.id,
    status: 'NOT_RECORDED',
    captured_at: null,
    captured_at_time_basis: null,
    target_type: caseItem.target.kind,
    target_asset_id: caseItem.target.asset_id,
    target_version_id: caseItem.target.version_id,
    total: 0,
    consistency_basis: null,
    note: '历史用例运行未保存需求来源捕获字段；不能根据当前关联补造当时状态。',
  }
}

function caseRequirementCapture(caseItem: ReportCasePage['items'][number]): ReportRequirementCapture {
  return (caseItem as Partial<ReportCasePage['items'][number]>).requirement_capture
    ?? fallbackRequirementCapture(caseItem)
}

function initialRequirementSourcePage(response: ReportDetailResponse): ReportRequirementSourcePage {
  const supplied = (response as Partial<ReportDetailResponse>).requirement_sources
  if (supplied) {
    requirementSourcesCompletenessSupported.value = true
    return supplied
  }
  requirementSourcesCompletenessSupported.value = false
  return {
    run_id: response.summary.run_id,
    case_run_id: null,
    captures: response.cases.items.map(fallbackRequirementCapture),
    items: [],
    total: 0,
    page: 1,
    page_size: 10,
    has_more: false,
    next_page: null,
    continuation_path: null,
  }
}

function requirementSourceEmptyDescription(): string {
  const captures = requirementSources.value?.captures ?? []
  if (captures.some((item) => item.status === 'NOT_RECORDED')) return '历史运行未记录来源，无法判断创建时是否有关联需求'
  if (captures.some((item) => item.status === 'UNSUPPORTED_TARGET')) return '此目标类型尚不支持来源捕获，不能据此判断没有需求'
  if (captures.length && captures.every((item) => item.status === 'CAPTURED_EMPTY')) return '创建运行时未关联需求'
  return captures.length ? '当前页没有需求来源项' : '当前筛选没有用例来源记录'
}

function requirementVersionLabel(source: ReportRequirementSource): string {
  if (source.requirement_version_binding === 'REQUIREMENT_VERSION_UNKNOWN'
    || source.requirement_version_id === null) return '需求版本未知'
  return source.requirement_version_no === null
    ? '固定版本（版本号未知）'
    : `V${source.requirement_version_no}`
}

function assetVersionBindingLabel(source: ReportRequirementSource): string {
  if (source.asset_version_binding === 'EXACT_EXECUTION_VERSION') {
    return source.link_asset_version_id === null
      ? '精确版本绑定已记录，但关系资产版本未记录'
      : '精确关联执行版本已记录'
  }
  return '旧资产级关系：关联版本未知，不证明精确执行版本覆盖'
}

function targetTypeLabel(source: ReportRequirementSource): string {
  return source.link_asset_type === 'TEST_CASE' ? 'API 用例' : 'Web 用例'
}

function sourceTargetLabel(source: ReportRequirementSource): string {
  return capturedTargetLabel(source.case_run_id, source.target_type)
}

function sourceTargetVersionLabel(source: ReportRequirementSource): string {
  return capturedVersionLabel(source.case_run_id)
}

function requirementAuditTime(
  value: string | null,
  basis: 'UTC' | 'LEGACY_UNKNOWN' | null,
): string {
  if (!value) return '未记录'
  if (basis === 'LEGACY_UNKNOWN') return `${value}（历史时间，时区未记录）`
  const parsed = parseApiDateTime(value)
  if (Number.isNaN(parsed.getTime())) return `${value}（UTC 记录）`
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed)}（北京时间，源 UTC）`
}

function openCapturedRequirement(source: ReportRequirementSource): void {
  if (!summary.value || source.requirement_version_id === null
    || source.requirement_version_binding !== 'EXACT_REQUIREMENT_VERSION') return
  void router.push({
    name: route.meta.projectScoped ? 'project-requirements' : 'requirements',
    params: route.meta.projectScoped ? { projectId: summary.value.project.id } : {},
    query: {
      project_id: String(summary.value.project.id),
      requirement_id: String(source.requirement_id),
      requirement_version_id: String(source.requirement_version_id),
      tab: 'links',
    },
  })
}

function openCapturedTarget(source: ReportRequirementSource): void {
  if (!summary.value) return
  const commonQuery = {
    project_id: String(summary.value.project.id),
    version_id: String(source.target_version_id),
    link_source: 'requirement',
  }
  if (source.link_asset_type === 'TEST_CASE') {
    void router.push({ name: route.meta.projectScoped ? 'project-test-cases' : 'test-cases', params: route.meta.projectScoped ? { projectId: summary.value.project.id } : {}, query: { ...commonQuery, test_case_id: String(source.target_asset_id) } })
  } else {
    void router.push({ name: route.meta.projectScoped ? 'project-web-assets' : 'web-assets', params: route.meta.projectScoped ? { projectId: summary.value.project.id } : {}, query: { ...commonQuery, web_case_id: String(source.target_asset_id) } })
  }
}

function detailErrorMessage(value: unknown): string {
  const statusCode = (value as { response?: { status?: number } }).response?.status
  if (statusCode === 403 || statusCode === 404) return '报告不存在，或当前用户无权访问。'
  return getApiErrorMessage(value, '报告加载失败，请稍后重试。')
}

function sectionErrorMessage(value: unknown, section: string): string {
  const statusCode = (value as { response?: { status?: number } }).response?.status
  if (statusCode === 403 || statusCode === 404) return `${section}不存在，或当前用户无权访问。`
  return getApiErrorMessage(value, `${section}加载失败，请稍后重试。`)
}

function rateLabel(): string {
  const rate = summary.value?.case_success_rate
  if (!rate || rate.value === null) return '暂无已评估结果'
  return `${(rate.value * 100).toFixed(1)}%（${rate.numerator}/${rate.denominator}）`
}

function targetName(): string {
  const target = summary.value?.target
  if (!target) return '未记录'
  return target.asset_name || target.asset_code || `${runTypeLabel(target.kind)}资产`
}

function fieldOf(caseItem: ReportCasePage['items'][number], key: typeof dataFields[number]['key']): ReportDataField {
  return caseItem.execution_result[key]
}

function beginDetailContext(): number {
  const contextSequence = ++detailContextSequence
  invalidateExportContext()
  detailIdentityFingerprint = null
  ++casesSequence
  ++stepsSequence
  ++evidenceSequence
  ++requirementSourcesSequence
  cases.value = null
  steps.value = null
  evidence.value = null
  requirementSources.value = null
  casesLoading.value = false
  stepsLoading.value = false
  evidenceLoading.value = false
  requirementSourcesLoading.value = false
  casesError.value = null
  stepsError.value = null
  evidenceError.value = null
  requirementSourcesError.value = null
  stepCaseRunIdDraft.value = undefined
  appliedStepCaseRunId.value = undefined
  evidenceCaseRunIdDraft.value = undefined
  evidenceStepRunIdDraft.value = undefined
  appliedEvidenceCaseRunId.value = undefined
  appliedEvidenceStepRunId.value = undefined
  requirementCaseRunIdDraft.value = undefined
  appliedRequirementCaseRunId.value = undefined
  requirementSourcesRequestedPage.value = 1
  requirementSourcesCompletenessSupported.value = false
  return contextSequence
}

function invalidateExportContext(): void {
  ++exportSequence
  exportingFormat.value = null
}

function isCurrentDetailRequest(
  contextSequence: number,
  requestSequence: number,
  requestedRunId: string,
  identityFingerprint: string,
): boolean {
  const currentIdentity = readReportAuthIdentity()
  return contextSequence === detailContextSequence
    && requestSequence === detailSequence
    && requestedRunId === runId.value
    && identityFingerprint === currentIdentity?.fingerprint
}

async function loadDetail(): Promise<void> {
  if (!runId.value) return
  const contextSequence = beginDetailContext()
  const requestSequence = ++detailSequence
  const requestedRunId = runId.value
  const requestedIdentity = readReportAuthIdentity()
  loading.value = true
  error.value = null
  detail.value = null
  if (!requestedIdentity) {
    loading.value = false
    return
  }
  try {
    const response = await getReport(requestedRunId)
    if (!isCurrentDetailRequest(
      contextSequence,
      requestSequence,
      requestedRunId,
      requestedIdentity.fingerprint,
    )) return
    detail.value = response
    detailIdentityFingerprint = requestedIdentity.fingerprint
    cases.value = response.cases
    steps.value = response.steps
    evidence.value = response.evidence
    requirementSources.value = initialRequirementSourcePage(response)
  } catch (loadError) {
    if (!isCurrentDetailRequest(
      contextSequence,
      requestSequence,
      requestedRunId,
      requestedIdentity.fingerprint,
    )) return
    error.value = detailErrorMessage(loadError)
  } finally {
    if (isCurrentDetailRequest(
      contextSequence,
      requestSequence,
      requestedRunId,
      requestedIdentity.fingerprint,
    )) loading.value = false
  }
}

async function loadCases(page = 1): Promise<void> {
  if (!runId.value) return
  const contextSequence = detailContextSequence
  const requestSequence = ++casesSequence
  const requestedRunId = runId.value
  casesLoading.value = true
  casesError.value = null
  try {
    const response = await getReportCases(requestedRunId, page, casePageSize.value)
    if (contextSequence !== detailContextSequence || requestSequence !== casesSequence
      || runId.value !== requestedRunId) return
    cases.value = response
  } catch (loadError) {
    if (contextSequence === detailContextSequence && requestSequence === casesSequence
      && runId.value === requestedRunId) {
      casesError.value = sectionErrorMessage(loadError, '用例运行记录')
    }
  } finally {
    if (contextSequence === detailContextSequence && requestSequence === casesSequence
      && runId.value === requestedRunId) casesLoading.value = false
  }
}

async function loadSteps(page = 1): Promise<void> {
  if (!runId.value) return
  const contextSequence = detailContextSequence
  const requestSequence = ++stepsSequence
  const requestedRunId = runId.value
  const requestedCaseId = appliedStepCaseRunId.value
  stepsLoading.value = true
  stepsError.value = null
  try {
    const response = await getReportSteps(requestedRunId, {
      case_run_id: requestedCaseId,
      page,
      page_size: stepPageSize.value,
    })
    if (contextSequence !== detailContextSequence || requestSequence !== stepsSequence
      || runId.value !== requestedRunId
      || appliedStepCaseRunId.value !== requestedCaseId) return
    steps.value = response
  } catch (loadError) {
    if (contextSequence === detailContextSequence && requestSequence === stepsSequence
      && runId.value === requestedRunId && appliedStepCaseRunId.value === requestedCaseId) {
      stepsError.value = sectionErrorMessage(loadError, '步骤运行记录')
    }
  } finally {
    if (contextSequence === detailContextSequence && requestSequence === stepsSequence
      && runId.value === requestedRunId && appliedStepCaseRunId.value === requestedCaseId) {
      stepsLoading.value = false
    }
  }
}

async function loadEvidence(page = 1): Promise<void> {
  if (!runId.value) return
  const contextSequence = detailContextSequence
  const requestSequence = ++evidenceSequence
  const requestedRunId = runId.value
  const requestedCaseId = appliedEvidenceCaseRunId.value
  const requestedStepId = appliedEvidenceStepRunId.value
  evidenceLoading.value = true
  evidenceError.value = null
  try {
    const response = await getReportEvidence(requestedRunId, {
      case_run_id: requestedCaseId,
      step_run_id: requestedStepId,
      page,
      page_size: evidencePageSize.value,
    })
    if (contextSequence !== detailContextSequence || requestSequence !== evidenceSequence
      || runId.value !== requestedRunId
      || appliedEvidenceCaseRunId.value !== requestedCaseId
      || appliedEvidenceStepRunId.value !== requestedStepId) return
    evidence.value = response
  } catch (loadError) {
    if (contextSequence === detailContextSequence && requestSequence === evidenceSequence
      && runId.value === requestedRunId && appliedEvidenceCaseRunId.value === requestedCaseId
      && appliedEvidenceStepRunId.value === requestedStepId) {
      evidenceError.value = sectionErrorMessage(loadError, '运行证据')
    }
  } finally {
    if (contextSequence === detailContextSequence && requestSequence === evidenceSequence
      && runId.value === requestedRunId && appliedEvidenceCaseRunId.value === requestedCaseId
      && appliedEvidenceStepRunId.value === requestedStepId) {
      evidenceLoading.value = false
    }
  }
}

function requirementSourceRequestIsCurrent(
  contextSequence: number,
  requestSequence: number,
  requestedRunId: string,
  requestedCaseRunId: number | undefined,
  identityFingerprint: string,
  requestedDetail: ReportDetailResponse,
): boolean {
  const currentIdentity = readReportAuthIdentity()
  return contextSequence === detailContextSequence
    && requestSequence === requirementSourcesSequence
    && requestedRunId === runId.value
    && requestedCaseRunId === appliedRequirementCaseRunId.value
    && identityFingerprint === currentIdentity?.fingerprint
    && identityFingerprint === detailIdentityFingerprint
    && requestedDetail === detail.value
}

async function loadRequirementSources(page = 1): Promise<void> {
  const requestedDetail = detail.value
  if (!runId.value || !requestedDetail) return
  const contextSequence = detailContextSequence
  const requestSequence = ++requirementSourcesSequence
  const requestedRunId = runId.value
  const requestedCaseRunId = appliedRequirementCaseRunId.value
  const requestedIdentity = readReportAuthIdentity()
  requirementSourcesRequestedPage.value = page
  requirementSources.value = null
  requirementSourcesLoading.value = true
  requirementSourcesError.value = null
  if (!requestedIdentity || requestedIdentity.fingerprint !== detailIdentityFingerprint) {
    requirementSourcesLoading.value = false
    reconcileStoredIdentity()
    return
  }
  try {
    const response = await getReportRequirementSources(requestedRunId, {
      case_run_id: requestedCaseRunId,
      page,
      page_size: requirementSourcePageSize.value,
    })
    if (!requirementSourceRequestIsCurrent(
      contextSequence,
      requestSequence,
      requestedRunId,
      requestedCaseRunId,
      requestedIdentity.fingerprint,
      requestedDetail,
    )) return
    requirementSources.value = response
    requirementSourcesCompletenessSupported.value = true
  } catch (loadError) {
    if (requirementSourceRequestIsCurrent(
      contextSequence,
      requestSequence,
      requestedRunId,
      requestedCaseRunId,
      requestedIdentity.fingerprint,
      requestedDetail,
    )) {
      requirementSourcesError.value = sectionErrorMessage(loadError, '需求来源')
    }
  } finally {
    if (requirementSourceRequestIsCurrent(
      contextSequence,
      requestSequence,
      requestedRunId,
      requestedCaseRunId,
      requestedIdentity.fingerprint,
      requestedDetail,
    )) requirementSourcesLoading.value = false
  }
}

async function applyStepFilter(): Promise<void> {
  appliedStepCaseRunId.value = stepCaseRunIdDraft.value
  await loadSteps(1)
}

async function clearStepFilter(): Promise<void> {
  stepCaseRunIdDraft.value = undefined
  appliedStepCaseRunId.value = undefined
  await loadSteps(1)
}

async function applyEvidenceFilter(): Promise<void> {
  appliedEvidenceCaseRunId.value = evidenceCaseRunIdDraft.value
  appliedEvidenceStepRunId.value = evidenceStepRunIdDraft.value
  await loadEvidence(1)
}

async function clearEvidenceFilter(): Promise<void> {
  evidenceCaseRunIdDraft.value = undefined
  evidenceStepRunIdDraft.value = undefined
  appliedEvidenceCaseRunId.value = undefined
  appliedEvidenceStepRunId.value = undefined
  await loadEvidence(1)
}

async function applyRequirementFilter(): Promise<void> {
  appliedRequirementCaseRunId.value = requirementCaseRunIdDraft.value
  await loadRequirementSources(1)
}

async function clearRequirementFilter(): Promise<void> {
  requirementCaseRunIdDraft.value = undefined
  appliedRequirementCaseRunId.value = undefined
  await loadRequirementSources(1)
}

async function download(item: ReportEvidence): Promise<void> {
  if (downloadingId.value) return
  downloadingId.value = item.id
  try {
    const blob = await downloadEvidence(item.id)
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = safeDownloadName(item.file_name)
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
    ElMessage.success('证据下载已开始')
  } catch (downloadError) {
    ElMessage.error(getApiErrorMessage(downloadError, '证据下载失败，请稍后重试。'))
  } finally {
    downloadingId.value = null
  }
}

interface ExportOperation {
  sequence: number
  detailContextSequence: number
  runId: string
  format: ReportExportFormat
  identityFingerprint: string
  detail: ReportDetailResponse
}

function exportOperationIsCurrent(operation: ExportOperation): boolean {
  const currentIdentity = readReportAuthIdentity()
  return operation.sequence === exportSequence
    && operation.detailContextSequence === detailContextSequence
    && operation.runId === runId.value
    && operation.format === exportingFormat.value
    && operation.identityFingerprint === currentIdentity?.fingerprint
    && operation.identityFingerprint === detailIdentityFingerprint
    && operation.detail === detail.value
}

function resetDetailForMissingIdentity(): void {
  beginDetailContext()
  ++detailSequence
  loading.value = false
  error.value = null
  detail.value = null
}

function reconcileStoredIdentity(): void {
  const identity = readReportAuthIdentity()
  if (!identity) {
    const redirect = route.fullPath
    resetDetailForMissingIdentity()
    authStore.signOut()
    void router.replace({ name: 'login', query: { redirect } })
    return
  }
  authStore.token = identity.token
  authStore.user = identity.user
  void loadDetail()
}

function onAuthStorageChange(event: StorageEvent): void {
  if (event.storageArea && event.storageArea !== localStorage) return
  if (event.key !== null && event.key !== 'access_token' && event.key !== 'current_user') return
  reconcileStoredIdentity()
}

async function downloadFullReport(format: ReportExportFormat): Promise<void> {
  if (exportingFormat.value || !detail.value || !runId.value) return
  const identity = readReportAuthIdentity()
  if (!identity || identity.fingerprint !== detailIdentityFingerprint) {
    reconcileStoredIdentity()
    return
  }
  const operation: ExportOperation = {
    sequence: ++exportSequence,
    detailContextSequence,
    runId: runId.value,
    format,
    identityFingerprint: identity.fingerprint,
    detail: detail.value,
  }
  exportingFormat.value = format
  try {
    const response = await exportReport(operation.runId, format)
    const exported = await prepareReportExport(response, format, operation.runId)
    if (!exportOperationIsCurrent(operation)) return
    triggerReportDownload(exported)
    ElMessage.success(`完整${format === 'markdown' ? ' Markdown' : ' HTML'}报告下载已开始`)
  } catch (downloadError) {
    if (!exportOperationIsCurrent(operation)) return
    const message = await reportExportErrorMessage(downloadError)
    if (message && exportOperationIsCurrent(operation)) ElMessage.error(message)
  } finally {
    if (exportOperationIsCurrent(operation)) exportingFormat.value = null
  }
}

function openRunAudit(): void {
  if (!summary.value) return
  void router.push({ name: route.meta.projectScoped ? 'project-runs' : 'runs', params: route.meta.projectScoped ? { projectId: summary.value.project.id } : {}, query: { project_id: String(summary.value.project.id), run_id: summary.value.run_id } })
}

watch(runId, loadDetail, { immediate: true })
watch([() => authStore.token, () => authStore.user?.id], invalidateExportContext)
onMounted(() => window.addEventListener('storage', onAuthStorageChange))
onBeforeUnmount(() => {
  window.removeEventListener('storage', onAuthStorageChange)
  invalidateExportContext()
  ++detailContextSequence
  ++detailSequence
  ++casesSequence
  ++stepsSequence
  ++evidenceSequence
  ++requirementSourcesSequence
})
</script>

<template>
  <div class="report-detail-page">
    <header class="page-heading detail-page-heading">
      <div class="heading-main">
        <el-button circle :icon="ArrowLeft" aria-label="返回报告目录" @click="router.push({ name: route.meta.projectScoped ? 'project-reports' : 'reports', params: route.meta.projectScoped ? { projectId: summary?.project.id ?? route.params.projectId } : {} })" />
        <div>
          <span class="eyebrow dark">不可变运行报告</span>
          <h1>{{ summary?.run_code ?? '运行报告' }}</h1>
          <p>{{ summary ? runTypeLabel(summary.run_type) : '运行记录' }} · 只读历史报告，登录与项目授权仍然生效。</p>
        </div>
      </div>
      <div class="heading-actions">
        <el-tag v-if="summary" :type="runStatusTagType(summary.status)" size="large">{{ runStatusLabel(summary.status) }}</el-tag>
        <el-button
          v-if="summary && ['FAILED', 'TIMEOUT'].includes(summary.status)"
          @click="router.push({ name: route.meta.projectScoped ? 'project-defect-drafts' : 'defect-drafts', params: route.meta.projectScoped ? { projectId: summary.project.id } : {}, query: { project_id: String(summary.project.id), run_id: runId } })"
        >生成缺陷草稿</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="loadDetail">刷新</el-button>
      </div>
    </header>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false">
      <template #default><el-button link type="primary" @click="loadDetail">重试</el-button></template>
    </el-alert>
    <div v-if="loading && !detail" class="loading-panel" v-loading="true" />

    <template v-if="detail && summary">
      <el-card class="export-card" shadow="never">
        <div class="export-card-content">
          <div>
            <span class="eyebrow dark">完整服务端报告</span>
            <h2>下载完整报告</h2>
            <p>由服务端汇总全部用例运行、需求来源快照、步骤运行与证据；不使用当前页面分页拼装。</p>
          </div>
          <div class="export-actions">
            <el-button
              data-testid="export-markdown"
              :icon="Download"
              :loading="exportingFormat === 'markdown'"
              :disabled="Boolean(exportingFormat)"
              @click="downloadFullReport('markdown')"
            >下载完整 Markdown</el-button>
            <el-button
              data-testid="export-html"
              type="primary"
              :icon="Download"
              :loading="exportingFormat === 'html'"
              :disabled="Boolean(exportingFormat)"
              @click="downloadFullReport('html')"
            >下载完整 HTML</el-button>
          </div>
        </div>
        <el-alert
          v-if="!reportIsTerminal"
          class="snapshot-alert"
          type="warning"
          title="当前运行尚未结束；下载内容是导出时刻的当前快照，后续可能变化。"
          show-icon
          :closable="false"
        />
      </el-card>
      <el-alert
        v-if="platformCaseDifference"
        class="fact-alert"
        type="warning"
        title="平台运行失败，但已记录的用例运行均成功"
        description="报告保留两个层级的历史事实，不根据步骤或用例运行结果自行改判平台运行。"
        show-icon
        :closable="false"
      />
      <el-alert v-if="summary.error_type || summary.error_message" class="fact-alert" type="error" :title="summary.error_type || '运行错误'" :closable="false">
        {{ summary.error_message || '未记录' }}
      </el-alert>

      <section class="metric-grid">
        <article><span>运行原计数</span><strong>{{ summary.recorded_counts.total }}</strong><small>成功 {{ summary.recorded_counts.passed }} · 失败 {{ summary.recorded_counts.failed }} · 复核 {{ summary.recorded_counts.review }} · 超时 {{ summary.recorded_counts.timeout }}</small></article>
        <article><span>用例实际计数</span><strong>{{ summary.case_status_counts.total }}</strong><small>成功 {{ summary.case_status_counts.success }} · 失败 {{ summary.case_status_counts.failed }} · 取消 {{ summary.case_status_counts.cancelled }} · 跳过 {{ summary.case_status_counts.skipped }} · 未完成 {{ summary.case_status_counts.unfinished }}</small></article>
        <article><span>用例成功率</span><strong>{{ rateLabel() }}</strong><small>{{ summary.case_success_rate.definition }}</small></article>
        <article><span>执行耗时</span><strong>{{ formatReportDuration(summary.duration) }}</strong><small>观测时间 {{ formatReportDate(summary.duration.observed_at) }}</small></article>
      </section>

      <el-card class="summary-card" shadow="never">
        <div class="section-heading-row">
          <div><span class="eyebrow dark">历史执行依据</span><h2>{{ targetName() }}</h2></div>
          <el-tag type="info">{{ runTypeLabel(summary.run_type) }}</el-tag>
        </div>
        <div class="reference-grid">
          <div><span>执行锁定版本</span><strong>{{ summary.target.version_no ? `V${summary.target.version_no}` : '版本号未记录' }}</strong><small>{{ summary.target.version_available ? '版本记录可用' : '版本记录不可用' }}</small></div>
          <div><span>当前资产版本</span><strong>{{ summary.target.current_version_id ? (summary.target.is_current_version ? '与执行版本一致' : '已有更新版本') : '未记录' }}</strong><small>当前元数据，不替代执行锁定版本</small></div>
          <div><span>版本关系</span><strong>{{ summary.target.is_current_version === null ? '未记录' : summary.target.is_current_version ? '执行版本仍为当前版本' : '执行版本与当前版本不同' }}</strong><small>{{ versionBasisLabel() }}</small></div>
          <div><span>项目</span><strong>{{ summary.project.name }}</strong><small>{{ systemStatusLabel(summary.project.status) }} · 当前信息</small></div>
          <div><span>环境</span><strong>{{ summary.environment?.name ?? '未记录' }}</strong><small>{{ summary.environment ? '当前环境元数据' : '未记录' }}</small></div>
          <div><span>Runner</span><strong>{{ summary.runner?.name ?? '未记录' }}</strong><small>{{ summary.runner ? '当前 Runner 元数据' : '未记录' }}</small></div>
          <div><span>开始 / 结束</span><strong>{{ formatReportDate(summary.started_at) }}</strong><small>{{ formatReportDate(summary.ended_at) }}</small></div>
          <div><span>触发方式</span><strong>{{ triggerTypeLabel(summary.trigger_type) }}</strong><small>创建 {{ formatReportDate(summary.created_at) }}</small></div>
        </div>
      </el-card>

      <el-card class="section-card cases-card" shadow="never">
        <div class="section-heading-row"><div><h2>用例运行与已存结果</h2><p>结果来自类型化持久记录；缺失内容明确显示“未记录”。</p></div><span>共 {{ cases?.total ?? 0 }} 条</span></div>
        <el-alert v-if="casesError" :title="casesError" type="error" show-icon :closable="false"><template #default><el-button link type="primary" @click="loadCases(cases?.page ?? 1)">重试</el-button></template></el-alert>
        <div v-loading="casesLoading" class="case-list">
          <article v-for="item in cases?.items ?? []" :key="item.id" class="case-report-item">
            <header><div><strong>第 {{ item.sequence_no }} 项 · {{ item.target.asset_name || item.target.asset_code || '执行资产' }}</strong><span>{{ runTypeLabel(item.target.kind) }} · {{ item.target.version_no ? `锁定 V${item.target.version_no}` : '已锁定执行版本' }}</span></div><el-tag :type="runStatusTagType(item.status)">{{ runStatusLabel(item.status) }}</el-tag></header>
            <div class="case-meta">
              <span>顺序 {{ item.sequence_no }}</span><span>耗时 {{ formatReportMilliseconds(item.duration_ms) }}</span><span>重试 {{ item.retry_count }}</span><span>结果记录 {{ availabilityLabel(item.execution_result.availability) }}</span>
              <el-tag size="small" :type="requirementCaptureStatusType(caseRequirementCapture(item).status)">{{ requirementCaptureStatusLabel(caseRequirementCapture(item).status) }}</el-tag>
              <span>需求来源 {{ caseRequirementCapture(item).total }} 条</span>
            </div>
            <el-alert v-if="item.error_type || item.error_message" type="error" :title="item.error_type || '用例运行错误'" :closable="false">{{ item.error_message || '未记录' }}</el-alert>
            <div class="result-field-grid">
              <div v-for="field in dataFields" :key="field.key" :data-field="field.key">
                <header><span>{{ field.label }}</span><el-tag size="small" type="info">{{ availabilityLabel(fieldOf(item, field.key).availability) }}</el-tag></header>
                <pre>{{ displayDataField(fieldOf(item, field.key)) }}</pre>
                <small>{{ fieldOf(item, field.key).note }}</small>
              </div>
            </div>
          </article>
          <el-empty v-if="!casesLoading && !casesError && !(cases?.items.length)" description="没有用例运行记录" />
        </div>
        <el-pagination v-if="cases?.total" class="section-pagination" :current-page="cases.page" :page-size="casePageSize" :page-sizes="[10, 20, 50, 100]" :total="cases.total" layout="total, sizes, prev, pager, next" @current-change="loadCases" @size-change="(value: number) => { casePageSize = value; loadCases(1) }" />
        <p v-if="cases?.has_more" class="continuation-note">还有后续用例运行记录；使用分页继续读取，未静默截断。</p>
      </el-card>

      <el-card class="section-card requirement-source-card" shadow="never">
        <div class="section-heading-row">
          <div>
            <h2>运行创建时需求来源</h2>
            <p>只展示创建运行时固化的来源事实，不用当前关联补全历史。AI 置信度是生成关联时的建议值，不参与执行结果或通过率计算。</p>
          </div>
          <span>服务端记录 {{ requirementSources?.total ?? 0 }} 条</span>
        </div>
        <div class="section-filter">
          <el-select v-model="requirementCaseRunIdDraft" clearable placeholder="全部用例运行">
            <el-option v-for="item in caseRunOptions" :key="item.id" :label="caseRunLabel(item.id)" :value="item.id" />
          </el-select>
          <el-button type="primary" @click="applyRequirementFilter">应用过滤</el-button>
          <el-button @click="clearRequirementFilter">清除</el-button>
        </div>
        <el-alert
          v-if="requirementSourcesError"
          :title="requirementSourcesError"
          type="error"
          show-icon
          :closable="false"
        >
          <template #default><el-button link type="primary" @click="loadRequirementSources(requirementSourcesRequestedPage)">重试</el-button></template>
        </el-alert>
        <template v-else>
          <el-alert
            v-if="!requirementSourcesCompletenessSupported && requirementSources"
            class="requirement-history-alert"
            type="warning"
            title="旧报告响应未提供需求来源字段"
            description="这些用例按“历史运行未记录”展示；不能据此判断创建时没有需求，也不会用当前关联补造。"
            show-icon
            :closable="false"
          />
          <div v-if="requirementSources?.captures.length" class="requirement-capture-grid">
            <article v-for="capture in requirementSources.captures" :key="capture.case_run_id">
              <header>
                <strong>{{ caseRunLabel(capture.case_run_id) }}</strong>
                <el-tag size="small" :type="requirementCaptureStatusType(capture.status)">{{ requirementCaptureStatusLabel(capture.status) }}</el-tag>
              </header>
              <p><b>{{ runTypeLabel(capture.target_type) }}</b> · {{ capturedTargetLabel(capture.case_run_id, capture.target_type) }} · {{ capturedVersionLabel(capture.case_run_id) }}</p>
              <p>已固化来源 {{ capture.total }} 条 · {{ requirementAuditTime(capture.captured_at, capture.captured_at_time_basis) }}</p>
              <small>{{ capture.note }}</small>
            </article>
          </div>
          <el-table
            v-loading="requirementSourcesLoading"
            :data="requirementSources?.items ?? []"
            class="requirement-source-table"
            table-layout="fixed"
          >
            <el-table-column type="expand" width="48">
              <template #default="{ row }">
                <div class="requirement-source-detail">
                  <div><span>来源记录</span><strong>第 {{ row.sequence_no }} 条 · {{ caseRunLabel(row.case_run_id) }}</strong></div>
                  <div><span>内容校验指纹</span><code>{{ row.requirement_content_hash || '未记录' }}</code></div>
                  <div><span>需求来源类型</span><strong>{{ sourceTypeLabel(row.requirement_source_type) }}</strong></div>
                  <div><span>需求及版本</span><strong>{{ row.requirement_code }} · {{ requirementVersionLabel(row) }}</strong></div>
                  <div><span>执行目标及版本</span><strong>{{ targetTypeLabel(row) }} · {{ sourceTargetLabel(row) }} · {{ sourceTargetVersionLabel(row) }}</strong></div>
                  <div><span>关系版本绑定</span><strong>{{ assetVersionBindingLabel(row) }}</strong></div>
                  <div><span>关系沿革</span><strong>{{ row.supersedes_link_id === null ? '首次建立关联' : '继承上一版关联' }}</strong></div>
                  <div><span>关系事实</span><strong>{{ relationTypeLabel(row.relation_type) }} · {{ relationSourceLabel(row.source) }} · {{ relationConfidenceLabel(row) }}</strong></div>
                  <div><span>关系创建人</span><strong>{{ row.link_created_by || '未记录' }}</strong></div>
                  <div><span>版本绑定说明</span><strong>{{ bindingNoteLabel(row.binding_note) }}</strong></div>
                  <div><span>关系创建时间</span><strong>{{ requirementAuditTime(row.link_created_at, row.link_created_at_time_basis) }}</strong></div>
                  <div><span>快照捕获时间</span><strong>{{ requirementAuditTime(row.captured_at, row.captured_at_time_basis) }}</strong></div>
                  <div><span>历史范围</span><strong>{{ historicalScopeLabel(row.historical_scope) }}</strong></div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="捕获的需求" min-width="250">
              <template #default="{ row }">
                <strong class="requirement-title">{{ row.requirement_code }} · {{ row.requirement_title }}</strong>
                <small>{{ requirementTypeLabel(row.requirement_type) }} · {{ systemStatusLabel(row.requirement_status) }}</small>
              </template>
            </el-table-column>
            <el-table-column label="需求版本" min-width="190">
              <template #default="{ row }">
                <span>{{ requirementVersionLabel(row) }}</span>
                <el-button
                  v-if="row.requirement_version_binding === 'EXACT_REQUIREMENT_VERSION' && row.requirement_version_id !== null"
                  link
                  type="primary"
                  @click="openCapturedRequirement(row)"
                >打开固定需求版本</el-button>
                <small v-else>不跳转当前版本</small>
              </template>
            </el-table-column>
            <el-table-column label="执行目标" min-width="190">
              <template #default="{ row }">
                <span>{{ sourceTargetLabel(row) }}</span>
                <small>{{ sourceTargetVersionLabel(row) }} · {{ targetTypeLabel(row) }}</small>
                <el-button link type="primary" @click="openCapturedTarget(row)">打开固定执行版本</el-button>
              </template>
            </el-table-column>
            <el-table-column label="关系版本绑定" min-width="220">
              <template #default="{ row }"><span>{{ assetVersionBindingLabel(row) }}</span></template>
            </el-table-column>
            <el-table-column label="需求关联" min-width="210">
              <template #default="{ row }">
                <span>{{ relationTypeLabel(row.relation_type) }} · {{ relationSourceLabel(row.source) }}</span>
                <small>{{ relationConfidenceLabel(row) }}；生成或建联时记录，不代表执行通过率</small>
              </template>
            </el-table-column>
            <el-table-column label="审计时间" min-width="250">
              <template #default="{ row }">
                <span>关系：{{ requirementAuditTime(row.link_created_at, row.link_created_at_time_basis) }}</span>
                <small>捕获：{{ requirementAuditTime(row.captured_at, row.captured_at_time_basis) }}</small>
              </template>
            </el-table-column>
          </el-table>
          <el-empty
            v-if="!requirementSourcesLoading && requirementSources && !requirementSources.items.length"
            :description="requirementSourceEmptyDescription()"
          />
          <el-pagination
            v-if="requirementSources?.total"
            class="section-pagination"
            :current-page="requirementSources.page"
            :page-size="requirementSourcePageSize"
            :page-sizes="[10, 20, 50, 100]"
            :total="requirementSources.total"
            layout="total, sizes, prev, pager, next"
            @current-change="loadRequirementSources"
            @size-change="(value: number) => { requirementSourcePageSize = value; loadRequirementSources(1) }"
          />
          <p v-if="requirementSourcesCompletenessSupported && requirementSources?.has_more" class="continuation-note">
            服务端报告在当前筛选下共 {{ requirementSources.total }} 条来源；本页后仍有后续页，请继续分页读取。
          </p>
          <p v-else-if="requirementSourcesCompletenessSupported && requirementSources" class="continuation-note">
            服务端报告在当前筛选下共 {{ requirementSources.total }} 条来源；当前页后无更多来源。
          </p>
        </template>
      </el-card>

      <el-card class="section-card" shadow="never">
        <div class="section-heading-row"><div><h2>步骤运行</h2><p>可按具体用例过滤，并使用服务端分页继续读取。</p></div><span>共 {{ steps?.total ?? 0 }} 条</span></div>
        <div class="section-filter"><el-select v-model="stepCaseRunIdDraft" clearable placeholder="全部用例运行"><el-option v-for="item in caseRunOptions" :key="item.id" :label="caseRunLabel(item.id)" :value="item.id" /></el-select><el-button type="primary" @click="applyStepFilter">应用过滤</el-button><el-button @click="clearStepFilter">清除</el-button></div>
        <el-alert v-if="stepsError" :title="stepsError" type="error" show-icon :closable="false"><template #default><el-button link type="primary" @click="loadSteps(steps?.page ?? 1)">重试</el-button></template></el-alert>
        <el-table v-loading="stepsLoading" :data="steps?.items ?? []" table-layout="fixed">
          <el-table-column label="顺序" width="90"><template #default="{ row }">第 {{ row.sequence_no }} 步</template></el-table-column><el-table-column label="所属用例" min-width="190"><template #default="{ row }">{{ caseRunLabel(row.case_run_id) }}</template></el-table-column><el-table-column prop="name" label="步骤" min-width="170" /><el-table-column label="类型" width="120"><template #default="{ row }">{{ stepTypeLabel(row.type) }}</template></el-table-column><el-table-column label="状态" width="95"><template #default="{ row }"><el-tag :type="runStatusTagType(row.status)">{{ runStatusLabel(row.status) }}</el-tag></template></el-table-column><el-table-column label="耗时" width="110"><template #default="{ row }">{{ formatReportMilliseconds(row.duration_ms) }}</template></el-table-column><el-table-column label="错误" min-width="180"><template #default="{ row }">{{ row.error_message || row.error_type || '未记录' }}</template></el-table-column>
        </el-table>
        <el-empty v-if="!stepsLoading && !stepsError && !(steps?.items.length)" description="没有符合条件的步骤运行" />
        <el-pagination v-if="steps?.total" class="section-pagination" :current-page="steps.page" :page-size="stepPageSize" :page-sizes="[10, 20, 50, 100]" :total="steps.total" layout="total, sizes, prev, pager, next" @current-change="loadSteps" @size-change="(value: number) => { stepPageSize = value; loadSteps(1) }" />
        <p v-if="steps?.has_more" class="continuation-note">还有后续步骤运行；使用分页继续读取。</p>
      </el-card>

      <el-card class="section-card" shadow="never">
        <div class="section-heading-row"><div><h2>运行证据</h2><p>页面只展示安全元数据；下载时由系统自动校验运行、用例和步骤关系。</p></div><span>共 {{ evidence?.total ?? 0 }} 条</span></div>
        <div class="section-filter"><el-select v-model="evidenceCaseRunIdDraft" clearable placeholder="全部用例运行"><el-option v-for="item in caseRunOptions" :key="item.id" :label="caseRunLabel(item.id)" :value="item.id" /></el-select><el-select v-model="evidenceStepRunIdDraft" clearable placeholder="全部步骤"><el-option v-for="item in stepRunOptions" :key="item.id" :label="stepLabel(item.id)" :value="item.id" /></el-select><el-button type="primary" @click="applyEvidenceFilter">应用过滤</el-button><el-button @click="clearEvidenceFilter">清除</el-button></div>
        <el-alert v-if="evidenceError" :title="evidenceError" type="error" show-icon :closable="false"><template #default><el-button link type="primary" @click="loadEvidence(evidence?.page ?? 1)">重试</el-button></template></el-alert>
        <el-table v-loading="evidenceLoading" :data="evidence?.items ?? []" table-layout="fixed">
          <el-table-column prop="artifact_type" label="类型" width="140" /><el-table-column label="文件" min-width="190"><template #default="{ row }"><span class="file-name">{{ row.file_name }}</span><small>{{ row.mime }}</small></template></el-table-column><el-table-column label="关联" min-width="230"><template #default="{ row }"><span>运行 {{ summary.run_code }}</span><small>{{ caseRunLabel(row.case_run_id) }} · {{ stepLabel(row.step_run_id) }}</small></template></el-table-column><el-table-column label="大小" width="100"><template #default="{ row }">{{ formatReportBytes(row.size) }}</template></el-table-column><el-table-column label="SHA256" min-width="190"><template #default="{ row }"><code class="hash-cell">{{ row.sha256 }}</code></template></el-table-column><el-table-column label="元数据" min-width="170"><template #default="{ row }"><span class="metadata-cell">{{ displayReportValue(row.metadata) }}</span></template></el-table-column><el-table-column label="生成时间" width="175"><template #default="{ row }">{{ formatReportDate(row.created_at) }}</template></el-table-column><el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" :icon="Download" :loading="downloadingId === row.id" @click="download(row)">下载</el-button></template></el-table-column>
        </el-table>
        <el-empty v-if="!evidenceLoading && !evidenceError && !(evidence?.items.length)" description="没有符合条件的证据" />
        <el-pagination v-if="evidence?.total" class="section-pagination" :current-page="evidence.page" :page-size="evidencePageSize" :page-sizes="[10, 20, 50, 100]" :total="evidence.total" layout="total, sizes, prev, pager, next" @current-change="loadEvidence" @size-change="(value: number) => { evidencePageSize = value; loadEvidence(1) }" />
        <p v-if="evidence?.has_more" class="continuation-note">还有后续证据；使用分页继续读取，不会自动读取正文。</p>
      </el-card>

      <el-card class="section-card" shadow="never">
        <div class="section-heading-row"><div><h2>AI 分析与自愈审计</h2><p>仅显示安全引用；原始 AI 内容不会暴露。</p></div><el-button type="primary" plain @click="openRunAudit">前往运行中心审计</el-button></div>
        <div class="ai-audit-grid">
          <article><span>失败分析</span><strong>{{ detail.related_ai.failure_analyses.total }}</strong><small v-if="detail.related_ai.failure_analyses.latest">最新状态：{{ systemStatusLabel(detail.related_ai.failure_analyses.latest.status) }}</small><small v-else>未记录</small></article>
          <article><span>自愈提案</span><strong>{{ detail.related_ai.healing_proposals.total }}</strong><small v-if="detail.related_ai.healing_proposals.latest">最新状态：{{ systemStatusLabel(detail.related_ai.healing_proposals.latest.status) }}</small><small v-else>未记录</small></article>
          <article><span>原始 AI 内容</span><strong>{{ detail.related_ai.raw_ai_content_exposed ? '已暴露' : '未暴露' }}</strong><small>报告只使用安全摘要与既有审计入口</small></article>
        </div>
      </el-card>

      <p class="pagination-note">{{ detail.pagination_note }}</p>
    </template>
  </div>
</template>

<style scoped>
.report-detail-page { max-width: 1580px; margin: 0 auto; }
.detail-page-heading, .heading-main, .heading-actions, .section-heading-row, .case-report-item > header, .case-meta, .section-filter { display: flex; align-items: center; }
.detail-page-heading, .section-heading-row, .case-report-item > header { justify-content: space-between; }
.heading-main, .heading-actions, .section-filter { gap: 12px; }
.heading-main h1 { margin-bottom: 5px; }
.heading-main code { color: #66758c; font-size: 12px; }
.loading-panel { min-height: 360px; }
.fact-alert { margin-bottom: 14px; }
.export-card { margin-bottom: 18px; border: 1px solid #dce5f2; border-radius: 16px; }
.export-card-content { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.export-card-content h2 { margin: 5px 0; font-size: 19px; }
.export-card-content p { margin: 0; color: #7b889b; font-size: 12px; }
.export-actions { display: flex; flex-shrink: 0; gap: 10px; }
.snapshot-alert { margin-top: 14px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }
.metric-grid article, .ai-audit-grid article { padding: 18px; border: 1px solid #e3e9f2; border-radius: 14px; background: white; }
.metric-grid span, .metric-grid small, .ai-audit-grid span, .ai-audit-grid small { display: block; color: #7d899b; font-size: 11px; line-height: 1.55; }
.metric-grid strong, .ai-audit-grid strong { display: block; margin: 8px 0 6px; color: #233a5d; font-size: 21px; }
.summary-card, .section-card { margin-bottom: 18px; border: 1px solid #e3e9f2; border-radius: 16px; }
.section-heading-row { gap: 16px; margin-bottom: 16px; }
.section-heading-row h2 { margin: 5px 0 0; font-size: 19px; }
.section-heading-row p { margin: 5px 0 0; color: #8490a3; font-size: 12px; }
.section-heading-row > span { color: #77849a; font-size: 12px; }
.reference-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.reference-grid > div { padding: 14px; border-radius: 11px; background: #f7f9fc; }
.reference-grid span, .reference-grid small { display: block; color: #7d899d; font-size: 11px; }
.reference-grid strong { display: block; margin: 6px 0; overflow-wrap: anywhere; }
.case-list { min-height: 80px; }
.case-report-item { margin-bottom: 14px; padding: 16px; border: 1px solid #e4eaf2; border-radius: 13px; background: #fbfcfe; }
.case-report-item > header span { display: block; margin-top: 5px; color: #7c899b; font-size: 11px; }
.case-meta { flex-wrap: wrap; gap: 14px; margin: 12px 0; color: #738096; font-size: 11px; }
.requirement-history-alert { margin-bottom: 14px; }
.requirement-capture-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.requirement-capture-grid article { min-width: 0; padding: 13px; border: 1px solid #dfe7f1; border-radius: 11px; background: #f8fafc; }
.requirement-capture-grid header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.requirement-capture-grid p { margin: 8px 0 0; color: #52627a; font-size: 11px; line-height: 1.55; }
.requirement-capture-grid small { display: block; margin-top: 7px; color: #7f8ca0; font-size: 10px; line-height: 1.55; }
.requirement-source-table { min-height: 72px; }
.requirement-source-table span, .requirement-source-table small, .requirement-source-table .el-button { display: block; }
.requirement-source-table small { margin-top: 5px; color: #7e8ba0; font-size: 10px; line-height: 1.45; }
.requirement-title { display: block; overflow-wrap: anywhere; color: #2d405f; }
.requirement-source-detail { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding: 4px 16px 14px 56px; }
.requirement-source-detail > div { min-width: 0; padding: 10px; border-radius: 9px; background: #f6f8fb; }
.requirement-source-detail span, .requirement-source-detail strong, .requirement-source-detail code { display: block; overflow-wrap: anywhere; }
.requirement-source-detail span { margin-bottom: 5px; color: #7d899d; font-size: 10px; }
.requirement-source-detail strong, .requirement-source-detail code { color: #3c4e68; font-size: 11px; line-height: 1.5; }
.result-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
.result-field-grid > div { min-width: 0; padding: 12px; border: 1px solid #e6ebf3; border-radius: 10px; background: white; }
.result-field-grid header { display: flex; align-items: center; justify-content: space-between; }
.result-field-grid pre { min-height: 44px; max-height: 190px; margin: 9px 0; overflow: auto; color: #34445d; font-family: "JetBrains Mono", Consolas, monospace; font-size: 11px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.result-field-grid small { color: #8995a7; font-size: 10px; }
.section-filter { flex-wrap: wrap; margin-bottom: 14px; }
.section-filter .el-input-number { width: 180px; }
.section-filter .el-select { width: 300px; }
.section-pagination { justify-content: flex-end; margin-top: 16px; }
.continuation-note, .pagination-note { color: #75839a; font-size: 11px; }
.file-name, .file-name + small, .metadata-cell { display: block; }
.file-name + small { margin-top: 4px; color: #8793a7; font-size: 10px; }
.hash-cell { display: block; overflow-wrap: anywhere; color: #50617b; font-size: 10px; }
.metadata-cell { max-height: 72px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.ai-audit-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
@media (max-width: 1360px) { .metric-grid, .reference-grid { grid-template-columns: repeat(2, 1fr); } .requirement-source-detail { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .export-card-content { align-items: flex-start; flex-direction: column; } .export-actions { flex-wrap: wrap; } .requirement-capture-grid, .requirement-source-detail { grid-template-columns: 1fr; } }
</style>
