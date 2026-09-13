<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Delete, Edit, Plus, Refresh } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import {
  approveWebCase,
  archiveWebCase,
  archiveWebElement,
  archiveWebPage,
  createWebCase,
  createWebCaseVersion,
  createWebElement,
  createWebElementVersion,
  createWebPage,
  createSessionProfile,
  getSessionProfiles,
  getWebCase,
  getWebCaseVersions,
  getWebCases,
  getWebElementVersions,
  getWebElements,
  getWebPages,
  restoreWebCase,
  restoreWebElement,
  restoreWebPage,
  restoreSessionProfile,
  archiveSessionProfile,
  updateSessionProfile,
  updateWebCase,
  updateWebElement,
  updateWebPage,
} from '@/api/web-assets'
import { getApiErrorMessage } from '@/api/http'
import { apiDateTimeMs, formatApiDateTime } from '@/utils/datetime'
import { promptOptionLabel } from '@/utils/prompt-display'
import { getEnvironments } from '@/api/environments'
import { getProjectMembers, getProjects } from '@/api/projects'
import { getPrompts } from '@/api/prompt-center'
import { useAuthStore } from '@/stores/auth'
import WebRecordingWorkbench from '@/views/web/WebRecordingWorkbench.vue'
import WebDesignWorkbench from '@/views/web/WebDesignWorkbench.vue'
import type { Environment } from '@/types/environment'
import type { Project, ProjectMember } from '@/types/project'
import type { PromptDefinition } from '@/types/prompt-center'
import type { WebPlanItem } from '@/types/web-design'
import type {
  ElementLocatorCreateRequest,
  LocatorStrategy,
  SessionProfileResponse,
  WebAction,
  WebAssertion,
  WebCaseContent,
  WebCaseDetailResponse,
  WebCaseResponse,
  WebCaseStatus,
  WebCaseVersionResponse,
  WebCaseVersionStatus,
  WebElementResponse,
  WebElementVersionResponse,
  WebLocator,
  WebPageResponse,
} from '@/types/web'
import { identityIsCurrent, isIdentityStorageEvent, readRequestIdentity } from '@/utils/request-context'
import {
  WEB_ACTION_TYPES,
  WEB_TAB_ALIAS_HELP,
  hasWebLocator,
  makeWebAction,
  makeWebLocator,
  validWebTabAlias,
  validWebUrlOrTemplate,
  webActionSummary,
} from '@/utils/web-actions'
import {
  WEB_ASSERTION_TYPES,
  hasWebAssertionExpected,
  makeWebAssertion,
  validWebAssertionExpected,
  webAssertionExpectedPlaceholder,
  webAssertionRequiresNonEmptyExpected,
  webAssertionSummary,
  webAssertionTypeLabel,
} from '@/utils/web-assertions'
import AssetRequirementLinksPanel from '@/views/requirements/components/AssetRequirementLinksPanel.vue'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

type AssetTab = 'cases' | 'pages' | 'profiles' | 'recordings' | 'design'
type LocatorMode = 'direct' | 'reference'

interface WebAssetDeepLink {
  projectId: number
  webCaseId: number
  versionId: number | null
  sourceRunId: string | null
  source: 'HEALING' | 'REQUIREMENT'
}

const route = useRoute()
const router = useRouter()
const projects = ref<Project[]>([])
const projectMembers = ref<ProjectMember[]>([])
const projectId = ref<number | undefined>()
const projectsLoading = ref(false)
const projectError = ref<string | null>(null)
const requestedTab = typeof route.query.tab === 'string' && ['cases', 'pages', 'profiles', 'recordings', 'design'].includes(route.query.tab)
  ? route.query.tab as AssetTab
  : 'cases'
const activeTab = ref<AssetTab>(requestedTab)
const recordingPlanItemId = ref<number | null>(null)
const recordingStartUrl = ref('')

const webCases = ref<WebCaseResponse[]>([])
const casesLoading = ref(false)
const casesError = ref<string | null>(null)
const selectedCaseId = ref<number | null>(null)
const caseDetail = ref<WebCaseDetailResponse | null>(null)
const caseVersions = ref<WebCaseVersionResponse[]>([])
const caseDetailLoading = ref(false)
const caseDetailError = ref<string | null>(null)
const selectedVersionId = ref<number | null>(null)
const editorCaseId = ref<number | null>(null)
const caseName = ref('')
const changeNote = ref('')
const editorContent = ref<WebCaseContent>(blankContent())
const caseSaving = ref(false)
const caseApproving = ref(false)
const caseArchiving = ref(false)
const aiAssertionPrompts = ref<PromptDefinition[]>([])

const pages = ref<WebPageResponse[]>([])
const pagesLoading = ref(false)
const pagesError = ref<string | null>(null)
const selectedPageId = ref<number | null>(null)
const elements = ref<WebElementResponse[]>([])
const elementsLoading = ref(false)
const elementsError = ref<string | null>(null)
const selectedElementId = ref<number | null>(null)
const elementVersions = ref<WebElementVersionResponse[]>([])
const elementVersionsLoading = ref(false)
const elementVersionVisible = ref(false)
const pageDialogVisible = ref(false)
const elementDialogVisible = ref(false)
const editingPageId = ref<number | null>(null)
const editingElementId = ref<number | null>(null)
const pageForm = ref({ code: '', name: '', url_pattern: '', description: '' })
const elementForm = ref({ name: '', element_type: 'OTHER', description: '' })
const elementVersionForm = ref({ description: '', element_type: 'OTHER', locators: [] as ElementLocatorCreateRequest[] })
const pageSaving = ref(false)
const elementSaving = ref(false)
const elementVersionSaving = ref(false)

const sessionProfiles = ref<SessionProfileResponse[]>([])
const casePages = useClientPagination(webCases)
const caseVersionPages = useClientPagination(caseVersions)
const webPagePages = useClientPagination(pages)
const elementPages = useClientPagination(elements)
const elementVersionPages = useClientPagination(elementVersions)
const profilePages = useClientPagination(sessionProfiles)
const profilesLoading = ref(false)
const profilesError = ref<string | null>(null)
const environments = ref<Environment[]>([])
const environmentsLoading = ref(false)
const environmentsError = ref<string | null>(null)
const assetsVersion = ref(0)
const profileDialogVisible = ref(false)
const profileSaving = ref(false)
const editingProfileId = ref<number | null>(null)
const profileForm = ref({
  name: '',
  environment_id: null as number | null,
  storage_state: '',
  metadata: '',
  expires_at: '',
  recovery_enabled: false,
  login_web_case_id: null as number | null,
  expiry_condition: '{\n  "type": "URL_CONTAINS",\n  "value": "/login"\n}',
  success_condition: '{\n  "type": "URL_CONTAINS",\n  "value": "/home"\n}',
  refresh_ttl_seconds: 86400,
})

const deepLinkNotice = ref<string | null>(null)
const sourceRunId = ref<string | null>(null)
const sourceRunProjectId = ref<number | null>(null)
let pendingDeepLink: WebAssetDeepLink | null = null
const authStore = useAuthStore()
const reverseExpanded = ref<string[]>(queryText(route.query.link_source) === 'requirement' ? ['requirements'] : [])

let projectSequence = 0
let caseDetailSequence = 0
let pageSequence = 0
let elementVersionSequence = 0
let caseWriteGeneration = 0
let nextCaseWriteOperationId = 0
let activeCaseWriteOperationId: number | null = null
let alive = true

const activeProjects = computed(() => projects.value.filter((project) => project.status === 'ACTIVE'))
const currentProject = computed(() => projects.value.find((project) => project.id === projectId.value) ?? null)
const currentProjectMember = computed(() => projectMembers.value.find((member) => member.user_id === authStore.user?.id) ?? null)
const canWriteProject = computed(() => Boolean(
  currentProject.value?.status === 'ACTIVE'
  && (
    authStore.user?.roles.includes('ADMIN')
    || currentProject.value?.owner_id === authStore.user?.id
    || currentProjectMember.value?.role === 'PROJECT_OWNER'
    || currentProjectMember.value?.role === 'TESTER'
  ),
))
const activePages = computed(() => pages.value.filter((page) => page.status === 'ACTIVE'))
const activeElements = computed(() => elements.value.filter((element) => element.status === 'ACTIVE'))
const selectedCase = computed(() => webCases.value.find((item) => item.id === selectedCaseId.value) ?? null)
const selectedVersion = computed(() => caseVersions.value.find((item) => item.id === selectedVersionId.value) ?? null)
const selectedPage = computed(() => pages.value.find((item) => item.id === selectedPageId.value) ?? null)
const selectedElement = computed(() => elements.value.find((item) => item.id === selectedElementId.value) ?? null)
const currentSessionProfile = computed(() => sessionProfiles.value.find((item) => item.id === editorContent.value.session_profile_id) ?? null)

const locatorStrategies: LocatorStrategy[] = ['css', 'xpath', 'text', 'role', 'label', 'placeholder', 'test_id']
const actionTypes = WEB_ACTION_TYPES
const webTabAliasHelp = WEB_TAB_ALIAS_HELP
const assertionTypes = WEB_ASSERTION_TYPES

function blankContent(): WebCaseContent {
  return {
    start_url: '',
    natural_language_steps: [],
    actions: [makeAction('GOTO')],
    assertions: [],
    session_profile_id: null,
    browser: 'CHROME',
    headless: true,
    browser_config: {
      window_width: 1280,
      window_height: 720,
      language: null,
      user_agent: null,
      proxy: null,
      download_path: null,
    },
    total_timeout_ms: 900000,
    parameters: {},
  }
}

function cloneContent(content: WebCaseContent): WebCaseContent {
  const cloned = JSON.parse(JSON.stringify(content)) as Partial<WebCaseContent>
  return {
    ...blankContent(),
    ...cloned,
    browser_config: {
      ...blankContent().browser_config,
      ...(cloned.browser_config ?? {}),
    },
    natural_language_steps: Array.isArray(cloned.natural_language_steps) ? cloned.natural_language_steps : [],
  }
}

function toggleProxy(enabled: boolean): void {
  editorContent.value.browser_config.proxy = enabled
    ? { server: '', username: null, password: null }
    : null
}

function makeLocator(): WebLocator {
  return makeWebLocator()
}

function makeAction(type: WebAction['type']): WebAction {
  return makeWebAction(type)
}

function makeAssertion(type: WebAssertion['type']): WebAssertion {
  return makeWebAssertion(type)
}

function safeError(error: unknown, fallback: string): string {
  return getApiErrorMessage(error, fallback).trim() || fallback
}

function invalidateCaseWriteContext(): void {
  caseWriteGeneration += 1
  activeCaseWriteOperationId = null
  caseSaving.value = false
}

function caseWriteCurrent(
  operationId: number,
  generation: number,
  identity: NonNullable<ReturnType<typeof readRequestIdentity>>,
  projectAtStart: number,
  caseAtStart: number | null,
  versionAtStart: number | null,
): boolean {
  return alive
    && activeCaseWriteOperationId === operationId
    && caseWriteGeneration === generation
    && identityIsCurrent(identity)
    && projectId.value === projectAtStart
    && editorCaseId.value === caseAtStart
    && selectedVersionId.value === versionAtStart
}

function finishCaseWrite(operationId: number): void {
  if (activeCaseWriteOperationId !== operationId) return
  activeCaseWriteOperationId = null
  caseSaving.value = false
}

function queryText(value: unknown): string | null {
  const candidate = Array.isArray(value) ? value[0] : value
  if (typeof candidate !== 'string') return null
  const text = candidate.trim()
  return text || null
}

function positiveQueryId(value: unknown): number | null {
  const text = queryText(value)
  if (!text || !/^[1-9]\d*$/.test(text)) return null
  const parsed = Number(text)
  return Number.isSafeInteger(parsed) ? parsed : null
}

function safeSourceRunId(value: unknown): string | null {
  const text = queryText(value)
  if (!text || text.length > 128 || /[\r\n]/.test(text)) return null
  return text
}

function initializeDeepLink(): void {
  const hasProjectQuery = route.query.project_id !== undefined
  const hasCaseQuery = route.query.web_case_id !== undefined
  const hasVersionQuery = route.query.version_id !== undefined
  const hasRunQuery = route.query.run_id !== undefined
  const fromRequirement = queryText(route.query.link_source) === 'requirement'
  const projectIdFromQuery = positiveQueryId(route.query.project_id)
  const webCaseIdFromQuery = positiveQueryId(route.query.web_case_id)
  const versionIdFromQuery = positiveQueryId(route.query.version_id)
  const sourceRunIdFromQuery = safeSourceRunId(route.query.run_id)
  const messages: string[] = []
  const sourceLabel = fromRequirement ? '需求关联' : 'Healing'

  if (hasProjectQuery && projectIdFromQuery === null) messages.push(`${sourceLabel}定位的项目参数无效，已使用默认项目。`)
  if (hasCaseQuery && webCaseIdFromQuery === null) messages.push(`${sourceLabel}定位的 Web 用例参数无效，已使用默认内容。`)
  if (hasVersionQuery && versionIdFromQuery === null) messages.push(`${sourceLabel}定位的版本参数无效，已使用默认版本。`)
  if (hasRunQuery && sourceRunIdFromQuery === null) messages.push('来源 Run 标识无效，无法提供返回入口。')
  if ((webCaseIdFromQuery !== null || versionIdFromQuery !== null) && projectIdFromQuery === null) {
    messages.push(`${sourceLabel}定位缺少有效项目参数，未自动定位目标。`)
  } else if (versionIdFromQuery !== null && webCaseIdFromQuery === null) {
    messages.push(`${sourceLabel}定位缺少有效 Web 用例参数，未自动定位目标版本。`)
  }

  pendingDeepLink = projectIdFromQuery !== null && webCaseIdFromQuery !== null
    && (versionIdFromQuery !== null || fromRequirement)
    ? {
      projectId: projectIdFromQuery,
      webCaseId: webCaseIdFromQuery,
      versionId: versionIdFromQuery,
      sourceRunId: sourceRunIdFromQuery,
      source: fromRequirement ? 'REQUIREMENT' : 'HEALING',
    }
    : null
  sourceRunId.value = sourceRunIdFromQuery && projectIdFromQuery !== null ? sourceRunIdFromQuery : null
  sourceRunProjectId.value = sourceRunId.value ? projectIdFromQuery : null
  deepLinkNotice.value = messages.length ? messages.join(' ') : null
}

function dateLabel(value: string | null | undefined): string {
  return formatApiDateTime(value)
}

function projectLabel(project: Project): string {
  return `${project.name}（${project.code}）`
}

function caseStatusLabel(status: WebCaseStatus | WebCaseVersionStatus): string {
  if (status === 'APPROVED') return '已批准'
  if (status === 'ARCHIVED') return '已归档'
  if (status === 'RETIRED') return '已退役'
  return '草稿'
}

function caseStatusType(status: WebCaseStatus | WebCaseVersionStatus): 'success' | 'warning' | 'info' {
  if (status === 'APPROVED') return 'success'
  if (status === 'ARCHIVED' || status === 'RETIRED') return 'info'
  return 'warning'
}

function assetStatusLabel(status: 'ACTIVE' | 'ARCHIVED'): string {
  return status === 'ACTIVE' ? '启用' : '已归档'
}

function profileFingerprintLabel(value: string): string {
  return value ? `${value.slice(0, 12)}…` : '—'
}

function profileMetadataLabel(metadata: Record<string, string> | null): string {
  if (!metadata) return '—'
  const keys = Object.keys(metadata)
  if (!keys.length) return '0 项'
  const preview = keys.slice(0, 3).join('、')
  return keys.length > 3 ? `${preview} 等 ${keys.length} 项` : preview
}

function addAction(): void {
  editorContent.value.actions.push(makeAction('CLICK'))
}

function replaceAction(index: number, type: WebAction['type']): void {
  editorContent.value.actions.splice(index, 1, makeAction(type))
}

function removeAction(index: number): void {
  if (editorContent.value.actions.length <= 1) {
    ElMessage.warning('至少保留一个 Web 操作')
    return
  }
  editorContent.value.actions.splice(index, 1)
}

function addAssertion(): void {
  editorContent.value.assertions.push(makeAssertion('ASSERT_VISIBLE'))
}

function replaceAssertion(index: number, type: WebAssertion['type']): void {
  editorContent.value.assertions.splice(index, 1, makeAssertion(type))
}

function removeAssertion(index: number): void {
  editorContent.value.assertions.splice(index, 1)
}

function hasLocator(action: WebAction | WebAssertion): action is (WebAction & { locator: WebLocator }) | (WebAssertion & { locator: WebLocator }) {
  return hasWebLocator(action)
}

function getLocator(item: WebAction | WebAssertion): WebLocator | null {
  return hasLocator(item) ? item.locator : null
}

function locatorMode(item: WebAction | WebAssertion): LocatorMode {
  return getLocator(item)?.element_version_id ? 'reference' : 'direct'
}

function updateLocatorMode(item: WebAction | WebAssertion, mode: LocatorMode): void {
  const locator = getLocator(item)
  if (!locator) return
  if (mode === 'reference') {
    const referenceId = activeElements.value.find((element) => element.current_version_id)?.current_version_id
    if (!referenceId) {
      ElMessage.warning('当前项目没有可引用的元素版本，请先配置定位器')
      return
    }
    locator.strategy = null
    locator.value = null
    locator.element_version_id = referenceId
  } else {
    locator.element_version_id = null
    locator.strategy = 'css'
    locator.value = ''
  }
}

function updateLocatorReference(item: WebAction | WebAssertion, value: number | undefined): void {
  const locator = getLocator(item)
  if (locator) {
    locator.element_version_id = value ?? null
    if (!value) ElMessage.warning('请选择有效的 Element 当前版本')
  }
}

function updateLocatorStrategy(item: WebAction | WebAssertion, value: LocatorStrategy | undefined): void {
  const locator = getLocator(item)
  if (locator) locator.strategy = value ?? null
}

function updateLocatorValue(item: WebAction | WebAssertion, value: string): void {
  const locator = getLocator(item)
  if (locator) locator.value = value
}

function actionSummary(action: WebAction): string {
  return webActionSummary(action)
}

function assertionSummary(assertion: WebAssertion): string {
  return webAssertionSummary(assertion)
}

function caseEditable(): boolean {
  return canWriteProject.value && (editorCaseId.value === null || selectedCase.value?.status !== 'ARCHIVED')
}

function resetCaseEditor(): void {
  invalidateCaseWriteContext()
  editorCaseId.value = null
  caseDetail.value = null
  caseVersions.value = []
  selectedVersionId.value = null
  caseName.value = ''
  changeNote.value = ''
  editorContent.value = blankContent()
  caseDetailError.value = null
}

function newCase(): void {
  if (!canWriteProject.value) return
  resetCaseEditor()
  activeTab.value = 'cases'
}

function addNaturalLanguageStep(): void {
  if (editorContent.value.natural_language_steps.length >= 200) {
    ElMessage.warning('自然语言步骤最多 200 项')
    return
  }
  editorContent.value.natural_language_steps.push('')
}

function removeNaturalLanguageStep(index: number): void {
  editorContent.value.natural_language_steps.splice(index, 1)
}

function uploadFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string' || !result.includes(',')) {
        reject(new Error('文件读取结果无效'))
        return
      }
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
}

async function chooseActionUploadFile(index: number, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  const action = editorContent.value.actions[index]
  if (!file || action?.type !== 'UPLOAD') return
  if (!file.size || file.size > 1024 * 1024 || file.name.length > 255
    || file.name === '.' || file.name === '..' || /[\\/]/.test(file.name)) {
    ElMessage.warning('上传文件必须为 1 byte～1 MiB，且文件名不能包含路径')
    return
  }
  try {
    action.key = file.name
    action.value = await uploadFileAsBase64(file)
  } catch (error) {
    action.key = ''
    action.value = ''
    ElMessage.error(safeError(error, '文件读取失败'))
  }
}

async function chooseAssertionBaseline(index: number, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  const assertion = editorContent.value.assertions[index]
  if (!file || assertion?.type !== 'ASSERT_SCREENSHOT_VISUAL_COMPARE') return
  if (!file.size || file.size > 1024 * 1024 || !file.name.toLowerCase().endsWith('.png')) {
    ElMessage.warning('截图基线必须是 1 byte～1 MiB 的 PNG 文件')
    return
  }
  try {
    assertion.expected = await uploadFileAsBase64(file)
  } catch (error) {
    assertion.expected = ''
    ElMessage.error(safeError(error, '基线文件读取失败'))
  }
}

function validateLocator(item: WebAction | WebAssertion): boolean {
  const locator = getLocator(item)
  if (!locator) return true
  if (locator.element_version_id) return true
  if (!locator.strategy || !locator.value?.trim()) {
    ElMessage.warning('请为每个目标配置完整的定位器，或引用已有元素版本')
    return false
  }
  return true
}

function validateEditorContent(): boolean {
  if (!Number.isInteger(editorContent.value.total_timeout_ms)
    || editorContent.value.total_timeout_ms < 1000
    || editorContent.value.total_timeout_ms > 86400000) {
    ElMessage.warning('总超时必须是 1,000～86,400,000 毫秒的整数')
    return false
  }
  const browserConfig = editorContent.value.browser_config
  if (!Number.isInteger(browserConfig.window_width)
    || browserConfig.window_width < 320 || browserConfig.window_width > 7680
    || !Number.isInteger(browserConfig.window_height)
    || browserConfig.window_height < 240 || browserConfig.window_height > 4320) {
    ElMessage.warning('浏览器窗口宽度必须为 320～7,680，高度必须为 240～4,320')
    return false
  }
  if (browserConfig.language
    && !/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(browserConfig.language)) {
    ElMessage.warning('浏览器语言格式无效，例如 zh-CN 或 en-US')
    return false
  }
  if (browserConfig.user_agent
    && (browserConfig.user_agent.length > 512 || /[\r\n]/.test(browserConfig.user_agent))) {
    ElMessage.warning('User-Agent 不能超过 512 字符或包含换行')
    return false
  }
  if (browserConfig.download_path
    && (!/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(browserConfig.download_path)
      || browserConfig.download_path.split('/').some((part) => part === '.' || part === '..'))) {
    ElMessage.warning('下载路径必须是安全的 Runner 临时目录相对路径')
    return false
  }
  if (browserConfig.proxy) {
    const proxy = browserConfig.proxy
    if (!/^(?:https?|socks5):\/\/[^\s/@]+(?::\d{1,5})?(?:\/[^\s]*)?$/.test(proxy.server)) {
      ElMessage.warning('代理服务器必须是无内嵌认证信息的 HTTP、HTTPS 或 SOCKS5 URL')
      return false
    }
    const secretReference = /^\{\{secret\.[A-Za-z_][A-Za-z0-9_.-]*\}\}$/
    if ((proxy.username === null) !== (proxy.password === null)
      || (proxy.username !== null && (!secretReference.test(proxy.username)
        || !secretReference.test(proxy.password ?? '')))) {
      ElMessage.warning('代理用户名和密码必须成对使用 {{secret.NAME}} 引用')
      return false
    }
  }
  if (editorContent.value.natural_language_steps.some((step) => !step.trim())) {
    ElMessage.warning('自然语言步骤不能留空，请填写内容或删除空行')
    return false
  }
  if (editorContent.value.natural_language_steps.length > 200) {
    ElMessage.warning('自然语言步骤最多 200 项，请删除多余步骤')
    return false
  }
  for (const action of editorContent.value.actions) {
    if (!Number.isInteger(action.timeout_ms) || action.timeout_ms < 100 || action.timeout_ms > 600000) {
      ElMessage.warning(`${action.type} 超时必须是 100～600,000 毫秒的整数`)
      return false
    }
    if (action.failure_policy !== 'STOP' && action.failure_policy !== 'CONTINUE') {
      ElMessage.warning(`${action.type} 失败策略无效`)
      return false
    }
    if (!validateLocator(action)) return false
    if ((action.type === 'GOTO' || action.type === 'WAIT_URL') && !action.url.trim()) {
      ElMessage.warning(`${action.type} 必须填写 URL`)
      return false
    }
    if (action.type === 'NEW_TAB' && !validWebUrlOrTemplate(action.url)) {
      ElMessage.warning('NEW_TAB 必须填写合法的 http(s) URL 或运行时模板')
      return false
    }
    if ((action.type === 'NEW_TAB' || action.type === 'SWITCH_TAB' || action.type === 'CLOSE_TAB')
      && !validWebTabAlias(action.value, action.type !== 'NEW_TAB')) {
      ElMessage.warning(action.type === 'NEW_TAB'
        ? 'NEW_TAB 标签页别名格式无效，且不能使用初始页别名 main'
        : `${action.type} 标签页别名格式无效`)
      return false
    }
    if ((action.type === 'FILL' || action.type === 'SELECT' || action.type === 'DRAG_DROP'
      || action.type === 'WAIT_TEXT' || action.type === 'JS_EVAL') && !action.value.trim()) {
      ElMessage.warning(`${action.type} 必须填写值`)
      return false
    }
    if (action.type === 'UPLOAD' && (!action.key || !action.value
      || action.value.length > 1400000 || !/^[A-Za-z0-9+/]+={0,2}$/.test(action.value))) {
      ElMessage.warning('UPLOAD 必须选择 1 MiB 以内的本地文件')
      return false
    }
    if (action.type === 'WAIT_TIME'
      && (!/^[1-9][0-9]{0,5}$/.test(action.value) || Number(action.value) > 600000)) {
      ElMessage.warning('WAIT_TIME 必须填写 1～600,000 毫秒的整数')
      return false
    }
    if ((action.type === 'COOKIE' || action.type === 'LOCAL_STORAGE' || action.type === 'SESSION_STORAGE')
      && !action.key.trim()) {
      ElMessage.warning(`${action.type} 必须填写键名`)
      return false
    }
    if (action.type === 'PRESS' && !action.key.trim()) {
      ElMessage.warning('PRESS 必须填写按键')
      return false
    }
  }
  for (const assertion of editorContent.value.assertions) {
    if (!Number.isInteger(assertion.timeout_ms) || assertion.timeout_ms < 100 || assertion.timeout_ms > 600000) {
      ElMessage.warning(`${assertion.type} 超时必须是 100～600,000 毫秒的整数`)
      return false
    }
    if (!validateLocator(assertion)) return false
    if (assertion.type === 'ASSERT_ATTRIBUTE' && !assertion.key.trim()) {
      ElMessage.warning('ASSERT_ATTRIBUTE 必须填写属性名')
      return false
    }
    if (assertion.type === 'ASSERT_AI_SEMANTIC'
      && (!Number.isInteger(assertion.prompt_id) || Number(assertion.prompt_id) <= 0
        || !assertion.criteria.trim() || assertion.criteria !== assertion.criteria.trim()
        || assertion.criteria.length > 4000
        || typeof assertion.confidence_threshold !== 'number'
        || assertion.confidence_threshold < 0 || assertion.confidence_threshold > 1)) {
      ElMessage.warning('ASSERT_AI_SEMANTIC 必须选择 Prompt、填写已 trim 的 Criteria，并设置 0～1 阈值')
      return false
    }
    if (!validWebAssertionExpected(assertion)) {
      const detail = webAssertionRequiresNonEmptyExpected(assertion) ? '必须填写期望值' : '期望值格式无效或超长'
      ElMessage.warning(`${assertion.type} ${detail}`)
      return false
    }
  }
  return true
}

function contentForSave(): WebCaseContent {
  const content = cloneContent(editorContent.value)
  content.natural_language_steps = content.natural_language_steps.map((step) => step.trim())
  content.assertions.forEach((assertion) => {
    if (assertion.type === 'ASSERT_AI_SEMANTIC') assertion.criteria = assertion.criteria.trim()
  })
  content.browser_config.language = content.browser_config.language?.trim() || null
  content.browser_config.user_agent = content.browser_config.user_agent?.trim() || null
  content.browser_config.download_path = content.browser_config.download_path?.trim() || null
  if (content.browser_config.proxy) {
    content.browser_config.proxy.server = content.browser_config.proxy.server.trim()
    content.browser_config.proxy.username = content.browser_config.proxy.username?.trim() || null
    content.browser_config.proxy.password = content.browser_config.proxy.password?.trim() || null
  }
  return content
}

async function loadAiAssertionPrompts(): Promise<void> {
  try {
    aiAssertionPrompts.value = await getPrompts(false, 'AI_ASSERTION')
  } catch (error) {
    aiAssertionPrompts.value = []
    ElMessage.error(safeError(error, 'AI_ASSERTION Prompt 加载失败'))
  }
}

async function loadCaseList(sequence: number): Promise<void> {
  const identity = readRequestIdentity()
  const currentProjectId = projectId.value
  if (!currentProjectId || !identity) return
  casesLoading.value = true
  casesError.value = null
  try {
    const response = await getWebCases(currentProjectId, true)
    if (!alive || sequence !== projectSequence || projectId.value !== currentProjectId || !identityIsCurrent(identity)) return
    webCases.value = response.items
    if (selectedCaseId.value && !webCases.value.some((item) => item.id === selectedCaseId.value)) {
      selectedCaseId.value = null
    }
    if (!selectedCaseId.value && webCases.value[0]) await selectCase(webCases.value[0])
  } catch (error) {
    if (alive && sequence === projectSequence && identityIsCurrent(identity)) casesError.value = safeError(error, 'Web 用例加载失败，请稍后重试')
  } finally {
    if (alive && sequence === projectSequence && identityIsCurrent(identity)) casesLoading.value = false
  }
}

async function selectCase(item: WebCaseResponse, force = false): Promise<void> {
  if (!force && selectedCaseId.value === item.id && caseDetail.value?.id === item.id) return
  invalidateCaseWriteContext()
  selectedCaseId.value = item.id
  const identity = readRequestIdentity()
  const sequence = ++caseDetailSequence
  caseDetailLoading.value = true
  caseDetailError.value = null
  caseDetail.value = null
  caseVersions.value = []
  selectedVersionId.value = null
  try {
    const [detail, versions] = await Promise.all([getWebCase(item.id), getWebCaseVersions(item.id)])
    if (!identity || !alive || sequence !== caseDetailSequence || projectId.value !== item.project_id || !identityIsCurrent(identity)) return
    caseDetail.value = detail
    caseVersions.value = versions
    editorCaseId.value = detail.id
    caseName.value = detail.name
    selectedVersionId.value = detail.current_version_id ?? versions[0]?.id ?? null
    const version = versions.find((candidate) => candidate.id === selectedVersionId.value) ?? detail.current_version
    editorContent.value = version ? cloneContent(version.content) : blankContent()
    changeNote.value = ''
  } catch (error) {
    if (alive && sequence === caseDetailSequence && identity && identityIsCurrent(identity)) caseDetailError.value = safeError(error, 'Web 用例详情加载失败，请稍后重试')
  } finally {
    if (alive && sequence === caseDetailSequence && identity && identityIsCurrent(identity)) caseDetailLoading.value = false
  }
}

function selectVersion(version: WebCaseVersionResponse): void {
  invalidateCaseWriteContext()
  selectedVersionId.value = version.id
  editorContent.value = cloneContent(version.content)
  changeNote.value = ''
}

async function saveCase(): Promise<void> {
  if (caseSaving.value || !caseEditable()) return
  if (!projectId.value || !caseName.value.trim() || !editorContent.value.start_url.trim()) {
    ElMessage.warning('请填写 Web 用例名称和起始 URL')
    return
  }
  if (editorContent.value.actions.length === 0) {
    ElMessage.warning('至少配置一个 Web 操作')
    return
  }
  if (!validateEditorContent()) return
  if (editorCaseId.value && !changeNote.value.trim()) {
    ElMessage.warning('保存新版本必须填写变更说明')
    return
  }
  const identity = readRequestIdentity()
  if (!identity) return
  const projectAtStart = projectId.value
  const caseAtStart = editorCaseId.value
  const versionAtStart = selectedVersionId.value
  const generation = caseWriteGeneration
  const operationId = ++nextCaseWriteOperationId
  activeCaseWriteOperationId = operationId
  caseSaving.value = true
  try {
    if (caseAtStart === null) {
      const created = await createWebCase({
        project_id: projectAtStart,
        name: caseName.value.trim(),
        content: contentForSave(),
        change_note: '创建 Web 用例',
      })
      if (!caseWriteCurrent(operationId, generation, identity, projectAtStart, caseAtStart, versionAtStart)) return
      selectedCaseId.value = created.id
      ElMessage.success('Web 用例 V1 已创建，当前为草稿')
    } else {
      if (selectedCase.value?.name !== caseName.value.trim()) {
        await updateWebCase(caseAtStart, { name: caseName.value.trim() })
        if (!caseWriteCurrent(operationId, generation, identity, projectAtStart, caseAtStart, versionAtStart)) return
      }
      await createWebCaseVersion(caseAtStart, {
        content: contentForSave(),
        change_note: changeNote.value.trim(),
      })
      if (!caseWriteCurrent(operationId, generation, identity, projectAtStart, caseAtStart, versionAtStart)
        || selectedCaseId.value !== caseAtStart) return
      ElMessage.success('Web 用例新版本已保存，需重新批准后才能执行')
    }
    if (!identityIsCurrent(identity)) return
    await loadProjectAssets(projectAtStart, { keepCase: true })
  } catch (error) {
    if (caseWriteCurrent(operationId, generation, identity, projectAtStart, caseAtStart, versionAtStart)) {
      ElMessage.error(safeError(error, 'Web 用例保存失败，请稍后重试'))
    }
  } finally {
    finishCaseWrite(operationId)
  }
}

async function approveSelectedCase(): Promise<void> {
  const item = selectedCase.value
  if (!item || item.status !== 'DRAFT' || caseApproving.value || !canWriteProject.value) return
  try {
    await ElMessageBox.confirm('批准后，当前 Web 用例版本才会出现在运行中心。批准前请确认节点和定位器配置完整。', '批准 Web 用例执行？', {
      type: 'warning', confirmButtonText: '批准执行', cancelButtonText: '暂不批准',
    })
  } catch { return }
  const projectAtStart = projectId.value
  caseApproving.value = true
  try {
    await approveWebCase(item.id)
    if (projectAtStart && projectId.value === projectAtStart) {
      await loadProjectAssets(projectAtStart, { keepCase: true })
      ElMessage.success('Web 用例已批准，可在运行中心选择')
    }
  } catch (error) {
    ElMessage.error(safeError(error, 'Web 用例批准失败，请检查当前版本和定位器'))
  } finally {
    caseApproving.value = false
  }
}

async function toggleCaseArchive(): Promise<void> {
  const item = selectedCase.value
  if (!item || caseArchiving.value || !canWriteProject.value) return
  const archived = item.status === 'ARCHIVED'
  try {
    await ElMessageBox.confirm(archived ? '恢复后 Web 用例将回到草稿状态。' : '归档后将不能创建版本或执行该 Web 用例。', archived ? '恢复 Web 用例？' : '归档 Web 用例？', {
      type: 'warning', confirmButtonText: archived ? '确认恢复' : '确认归档', cancelButtonText: '取消',
    })
  } catch { return }
  if (!projectId.value) return
  caseArchiving.value = true
  try {
    if (archived) await restoreWebCase(item.id)
    else await archiveWebCase(item.id)
    await loadProjectAssets(projectId.value, { keepCase: true })
    ElMessage.success(archived ? 'Web 用例已恢复' : 'Web 用例已归档')
  } catch (error) {
    ElMessage.error(safeError(error, archived ? 'Web 用例恢复失败' : 'Web 用例归档失败'))
  } finally {
    caseArchiving.value = false
  }
}

async function loadPages(sequence: number): Promise<void> {
  if (!projectId.value) return
  pagesLoading.value = true
  pagesError.value = null
  try {
    const response = await getWebPages(projectId.value)
    if (sequence !== projectSequence || projectId.value === undefined) return
    pages.value = response
    if (!selectedPageId.value || !pages.value.some((item) => item.id === selectedPageId.value)) {
      selectedPageId.value = activePages.value[0]?.id ?? pages.value[0]?.id ?? null
    }
  } catch (error) {
    if (sequence === projectSequence) pagesError.value = safeError(error, 'Web 页面加载失败，请稍后重试')
  } finally {
    if (sequence === projectSequence) pagesLoading.value = false
  }
}

async function loadElements(pageId: number): Promise<void> {
  const sequence = ++pageSequence
  elementsLoading.value = true
  elementsError.value = null
  elements.value = []
  try {
    const response = await getWebElements(pageId)
    if (sequence !== pageSequence || selectedPageId.value !== pageId) return
    elements.value = response
    selectedElementId.value = response[0]?.id ?? null
  } catch (error) {
    if (sequence === pageSequence) elementsError.value = safeError(error, 'Web 元素加载失败，请稍后重试')
  } finally {
    if (sequence === pageSequence) elementsLoading.value = false
  }
}

function selectPageRow(row: WebPageResponse | undefined): void {
  if (row) selectedPageId.value = row.id
}

function openPageEditor(page?: WebPageResponse): void {
  editingPageId.value = page?.id ?? null
  pageForm.value = {
    code: page?.code ?? '', name: page?.name ?? '', url_pattern: page?.url_pattern ?? '', description: page?.description ?? '',
  }
  pageDialogVisible.value = true
}

async function savePage(): Promise<void> {
  if (!projectId.value || !pageForm.value.name.trim() || (!editingPageId.value && !pageForm.value.code.trim())) {
    ElMessage.warning('请填写 Page code 和名称')
    return
  }
  pageSaving.value = true
  try {
    if (editingPageId.value) {
      await updateWebPage(editingPageId.value, {
        name: pageForm.value.name.trim(), url_pattern: pageForm.value.url_pattern || null, description: pageForm.value.description || null,
      })
    } else {
      await createWebPage({
        project_id: projectId.value, code: pageForm.value.code.trim(), name: pageForm.value.name.trim(),
        url_pattern: pageForm.value.url_pattern || null, description: pageForm.value.description || null,
      })
    }
    pageDialogVisible.value = false
    await loadProjectAssets(projectId.value, { keepCase: true })
    ElMessage.success(editingPageId.value ? 'Web 页面已更新' : 'Web 页面已创建')
  } catch (error) {
    ElMessage.error(safeError(error, 'Web 页面保存失败，请稍后重试'))
  } finally {
    pageSaving.value = false
  }
}

async function togglePageArchive(page: WebPageResponse): Promise<void> {
  if (!projectId.value) return
  try {
    if (page.status === 'ARCHIVED') await restoreWebPage(page.id)
    else await archiveWebPage(page.id)
    await loadProjectAssets(projectId.value, { keepCase: true })
    ElMessage.success(page.status === 'ARCHIVED' ? 'Web 页面已恢复' : 'Web 页面已归档')
  } catch (error) {
    ElMessage.error(safeError(error, page.status === 'ARCHIVED' ? 'Web 页面恢复失败' : 'Web 页面归档失败'))
  }
}

function openElementEditor(element?: WebElementResponse): void {
  editingElementId.value = element?.id ?? null
  elementForm.value = { name: element?.name ?? '', element_type: element?.element_type ?? 'OTHER', description: element?.description ?? '' }
  elementDialogVisible.value = true
}

async function saveElement(): Promise<void> {
  if (!projectId.value || !selectedPageId.value || !elementForm.value.name.trim()) {
    ElMessage.warning('请先选择 Page 并填写 Element 名称')
    return
  }
  elementSaving.value = true
  try {
    let element: WebElementResponse
    if (editingElementId.value) {
      element = await updateWebElement(editingElementId.value, {
        name: elementForm.value.name.trim(), element_type: elementForm.value.element_type.trim() || 'OTHER', description: elementForm.value.description || null,
      })
    } else {
      element = await createWebElement({
        project_id: projectId.value, page_id: selectedPageId.value, name: elementForm.value.name.trim(),
        element_type: elementForm.value.element_type.trim() || 'OTHER', description: elementForm.value.description || null,
      })
    }
    elementDialogVisible.value = false
    await loadElements(selectedPageId.value)
    if (!editingElementId.value) openElementVersionEditor(element)
    ElMessage.success(editingElementId.value ? 'Web 元素已更新' : 'Web 元素已创建，请继续配置定位器')
  } catch (error) {
    ElMessage.error(safeError(error, 'Web 元素保存失败，请稍后重试'))
  } finally {
    elementSaving.value = false
  }
}

function emptyLocator(): ElementLocatorCreateRequest {
  return { strategy: 'css', value: '', priority: 1, source: 'MANUAL' }
}

async function openElementVersionEditor(element: WebElementResponse): Promise<void> {
  selectedElementId.value = element.id
  const sequence = ++elementVersionSequence
  elementVersionsLoading.value = true
  elementVersionVisible.value = true
  try {
    const versions = await getWebElementVersions(element.id)
    if (sequence !== elementVersionSequence || selectedElementId.value !== element.id) return
    elementVersions.value = versions
    const current = versions.find((version) => version.id === element.current_version_id) ?? versions[0]
    elementVersionForm.value = current
      ? { description: current.description ?? '', element_type: current.element_type, locators: current.locators.map((locator) => ({ strategy: locator.strategy, value: locator.value, priority: locator.priority, source: locator.source })) }
      : { description: '', element_type: element.element_type, locators: [emptyLocator()] }
  } catch (error) {
    if (sequence === elementVersionSequence) ElMessage.error(safeError(error, '定位器版本加载失败，请稍后重试'))
  } finally {
    if (sequence === elementVersionSequence) elementVersionsLoading.value = false
  }
}

function addLocator(): void {
  const priorities = elementVersionForm.value.locators.map((locator) => locator.priority)
  const priority = Array.from({ length: 20 }, (_, index) => index + 1).find((value) => !priorities.includes(value)) ?? priorities.length + 1
  elementVersionForm.value.locators.push({ ...emptyLocator(), priority })
}

function removeLocator(index: number): void {
  if (elementVersionForm.value.locators.length <= 1) {
    ElMessage.warning('至少保留一个定位器')
    return
  }
  elementVersionForm.value.locators.splice(index, 1)
}

async function saveElementVersion(): Promise<void> {
  if (!selectedElement.value) return
  const locators = elementVersionForm.value.locators
  if (locators.some((locator) => !locator.value.trim() || locator.priority < 1 || locator.priority > 20)) {
    ElMessage.warning('请填写有效定位器，并将优先级设置为 1～20')
    return
  }
  const priorities = locators.map((locator) => locator.priority)
  if (new Set(priorities).size !== priorities.length) {
    ElMessage.warning('同一元素版本的定位器优先级不能重复')
    return
  }
  elementVersionSaving.value = true
  const elementId = selectedElement.value.id
  try {
    await createWebElementVersion(elementId, {
      description: elementVersionForm.value.description || null,
      element_type: elementVersionForm.value.element_type || 'OTHER',
      locators: locators.map((locator) => ({ ...locator, value: locator.value.trim() })),
    })
    elementVersionVisible.value = false
    if (selectedPageId.value) await loadElements(selectedPageId.value)
    ElMessage.success('元素的新定位器版本已保存')
  } catch (error) {
    ElMessage.error(safeError(error, '定位器版本保存失败，请稍后重试'))
  } finally {
    elementVersionSaving.value = false
  }
}

async function toggleElementArchive(element: WebElementResponse): Promise<void> {
  try {
    if (element.status === 'ARCHIVED') await restoreWebElement(element.id)
    else await archiveWebElement(element.id)
    if (selectedPageId.value) await loadElements(selectedPageId.value)
    ElMessage.success(element.status === 'ARCHIVED' ? 'Web 元素已恢复' : 'Web 元素已归档')
  } catch (error) {
    ElMessage.error(safeError(error, 'Web 元素状态更新失败，请稍后重试'))
  }
}

function clearProfileForm(): void {
  profileForm.value = {
    name: '',
    environment_id: null,
    storage_state: '',
    metadata: '',
    expires_at: '',
    recovery_enabled: false,
    login_web_case_id: null,
    expiry_condition: '{\n  "type": "URL_CONTAINS",\n  "value": "/login"\n}',
    success_condition: '{\n  "type": "URL_CONTAINS",\n  "value": "/home"\n}',
    refresh_ttl_seconds: 86400,
  }
  editingProfileId.value = null
}

function openProfileEditor(profile?: SessionProfileResponse): void {
  editingProfileId.value = profile?.id ?? null
  profileForm.value = {
    name: profile?.name ?? '',
    environment_id: profile?.environment_id ?? null,
    storage_state: '',
    metadata: profile?.metadata ? JSON.stringify(profile.metadata, null, 2) : '',
    expires_at: profile?.expires_at ?? '',
    recovery_enabled: Boolean(profile?.login_web_case_id),
    login_web_case_id: profile?.login_web_case_id ?? null,
    expiry_condition: profile?.expiry_condition
      ? JSON.stringify(profile.expiry_condition, null, 2)
      : '{\n  "type": "URL_CONTAINS",\n  "value": "/login"\n}',
    success_condition: profile?.success_condition
      ? JSON.stringify(profile.success_condition, null, 2)
      : '{\n  "type": "URL_CONTAINS",\n  "value": "/home"\n}',
    refresh_ttl_seconds: profile?.refresh_ttl_seconds ?? 86400,
  }
  profileDialogVisible.value = true
}

function parseJsonObject(value: string, label: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('object')
    return parsed as Record<string, unknown>
  } catch {
    ElMessage.warning(`${label} 必须是有效的 JSON 对象`)
    return null
  }
}

function parseMetadata(value: string): Record<string, string> | undefined | null {
  if (!value.trim()) return undefined
  const parsed = parseJsonObject(value, '元数据')
  if (!parsed) return null
  const metadata: Record<string, string> = {}
  for (const [key, item] of Object.entries(parsed)) {
    if (typeof item !== 'string') {
      ElMessage.warning('元数据的值必须全部是文本')
      return null
    }
    metadata[key] = item
  }
  return metadata
}

async function saveSessionProfile(): Promise<void> {
  if (!projectId.value || !profileForm.value.name.trim()) {
    ElMessage.warning('请填写会话配置名称')
    return
  }
  if (!/^[A-Za-z][A-Za-z0-9_.-]*$/.test(profileForm.value.name.trim())) {
    ElMessage.warning('名称需以字母开头，只能包含字母、数字、点、下划线和连字符')
    return
  }
  const metadata = parseMetadata(profileForm.value.metadata)
  if (metadata === null) return
  let storageState: Record<string, unknown> | null = null
  if (!editingProfileId.value) {
    if (!profileForm.value.storage_state.trim()) {
      ElMessage.warning('新建会话配置必须粘贴会话状态 JSON')
      return
    }
    storageState = parseJsonObject(profileForm.value.storage_state, '会话状态')
    if (!storageState) return
  } else if (profileForm.value.storage_state.trim()) {
    storageState = parseJsonObject(profileForm.value.storage_state, '会话状态')
    if (!storageState) return
  }
  let expiresAt: string | null = null
  if (profileForm.value.expires_at) {
    const expiresDate = new Date(profileForm.value.expires_at)
    if (Number.isNaN(expiresDate.getTime())) {
      ElMessage.warning('请选择有效的过期时间')
      return
    }
    expiresAt = expiresDate.toISOString()
  }
  let recoveryPayload: {
    login_web_case_id: number | null
    login_web_case_version_id: number | null
    expiry_condition: import('@/types/web').SessionRecoveryCondition | null
    success_condition: import('@/types/web').SessionRecoveryCondition | null
  } = {
    login_web_case_id: null,
    login_web_case_version_id: null,
    expiry_condition: null,
    success_condition: null,
  }
  if (profileForm.value.recovery_enabled) {
    const loginCase = webCases.value.find((item) => item.id === profileForm.value.login_web_case_id)
    if (!loginCase || loginCase.status !== 'APPROVED' || !loginCase.current_version_id) {
      ElMessage.warning('请选择已批准且具有固定当前版本的登录 Web 用例')
      return
    }
    const expiry = parseJsonObject(profileForm.value.expiry_condition, '失效条件')
    const success = parseJsonObject(profileForm.value.success_condition, '成功条件')
    if (!expiry || !success) return
    recoveryPayload = {
      login_web_case_id: loginCase.id,
      login_web_case_version_id: loginCase.current_version_id,
      expiry_condition: expiry as import('@/types/web').SessionRecoveryCondition,
      success_condition: success as import('@/types/web').SessionRecoveryCondition,
    }
  }
  const projectAtStart = projectId.value
  const profileIdAtStart = editingProfileId.value
  profileSaving.value = true
  // Do not retain Storage State while the request is in flight. Metadata is server-validated safe text.
  const storageStatePayload = storageState
  profileForm.value.storage_state = ''
  try {
    if (profileIdAtStart) {
      await updateSessionProfile(profileIdAtStart, {
        name: profileForm.value.name.trim(),
        ...(storageStatePayload ? { storage_state: storageStatePayload } : {}),
        ...(metadata !== undefined ? { metadata } : {}),
        expires_at: expiresAt,
        refresh_ttl_seconds: profileForm.value.refresh_ttl_seconds,
        ...recoveryPayload,
      })
    } else {
      await createSessionProfile({
        project_id: projectAtStart,
        environment_id: profileForm.value.environment_id,
        name: profileForm.value.name.trim(),
        storage_state: storageStatePayload ?? {},
        metadata: metadata ?? {},
        expires_at: expiresAt,
        refresh_ttl_seconds: profileForm.value.refresh_ttl_seconds,
        ...recoveryPayload,
      })
    }
    if (projectId.value !== projectAtStart) return
    profileDialogVisible.value = false
    await loadProjectAssets(projectAtStart, { keepCase: true })
    ElMessage.success(profileIdAtStart ? '会话配置已更新' : '会话配置已创建')
  } catch (error) {
    if (projectId.value === projectAtStart) ElMessage.error(safeError(error, '会话配置保存失败，请稍后重试'))
  } finally {
    profileSaving.value = false
    profileForm.value.storage_state = ''
  }
}

async function toggleProfileArchive(profile: SessionProfileResponse): Promise<void> {
  try {
    await ElMessageBox.confirm(
      profile.status === 'ACTIVE'
        ? '归档后该会话配置不能用于新的录制或运行。'
        : '恢复后该会话配置会回到可选状态，请确认其会话仍然有效。',
      profile.status === 'ACTIVE' ? '归档会话配置？' : '恢复会话配置？',
      { type: 'warning', confirmButtonText: profile.status === 'ACTIVE' ? '确认归档' : '确认恢复', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  if (!projectId.value) return
  try {
    if (profile.status === 'ACTIVE') await archiveSessionProfile(profile.id)
    else await restoreSessionProfile(profile.id)
    await loadProjectAssets(projectId.value, { keepCase: true })
    ElMessage.success(profile.status === 'ACTIVE' ? '会话配置已归档' : '会话配置已恢复')
  } catch (error) {
    ElMessage.error(safeError(error, '会话配置状态更新失败，请稍后重试'))
  }
}

async function loadProfiles(sequence: number): Promise<void> {
  if (!projectId.value) return
  profilesLoading.value = true
  profilesError.value = null
  try {
    const response = await getSessionProfiles(projectId.value)
    if (sequence === projectSequence) sessionProfiles.value = response
  } catch (error) {
    if (sequence === projectSequence) profilesError.value = safeError(error, '会话配置加载失败，请稍后重试')
  } finally {
    if (sequence === projectSequence) profilesLoading.value = false
  }
}

async function loadEnvironments(sequence: number): Promise<void> {
  if (!projectId.value) return
  environmentsLoading.value = true
  environmentsError.value = null
  try {
    const response = await getEnvironments(projectId.value)
    if (sequence === projectSequence) environments.value = response.filter((item) => item.enabled)
  } catch (error) {
    if (sequence === projectSequence) environmentsError.value = safeError(error, '环境加载失败，请稍后重试')
  } finally {
    if (sequence === projectSequence) environmentsLoading.value = false
  }
}

async function applyPendingDeepLink(id: number, sequence: number): Promise<void> {
  const deepLink = pendingDeepLink
  if (!deepLink || deepLink.projectId !== id || sequence !== projectSequence || projectId.value !== id) return
  pendingDeepLink = null
  activeTab.value = 'cases'

  const item = webCases.value.find((candidate) => candidate.id === deepLink.webCaseId)
  if (!item || item.project_id !== id || (deepLink.source === 'HEALING' && item.status === 'ARCHIVED')) {
    deepLinkNotice.value = `${deepLink.source === 'HEALING' ? '自愈' : '需求关联'}目标 Web 用例当前不可用，已保留现有默认选择。`
    return
  }

  await selectCase(item, true)
  if (sequence !== projectSequence || projectId.value !== id || selectedCaseId.value !== item.id) return
  if (!caseDetail.value || caseDetail.value.id !== item.id) {
    deepLinkNotice.value = `${deepLink.source === 'HEALING' ? '自愈' : '需求关联'}目标 Web 用例详情加载失败，已保留现有选择。`
    return
  }

  if (deepLink.versionId === null) {
    deepLinkNotice.value = `已从需求关联定位到独立 WebCase「${item.name}」；关联未记录可定位的历史资产版本。`
    return
  }

  const version = caseVersions.value.find((candidate) => (
    candidate.id === deepLink.versionId && candidate.web_case_id === item.id
  ))
  if (!version) {
    deepLinkNotice.value = `${deepLink.source === 'HEALING' ? '自愈创建的' : '需求关联记录的'}目标版本当前不可用，已保留该 Web 用例的默认版本。`
    return
  }
  selectVersion(version)
  const draftNotice = deepLink.source === 'HEALING'
    ? item.status === 'DRAFT' || version.status === 'DRAFT'
      ? '这是自愈创建的草稿版本，请检查内容后手动批准；页面不会自动批准。'
      : '请检查该版本；页面不会自动批准。'
    : '这是需求关联固定的独立 WebCase 版本；页面不会修改当前版本。'
  const sourceNotice = deepLink.sourceRunId ? `来源 Run：${deepLink.sourceRunId}。` : ''
  deepLinkNotice.value = `已定位到${deepLink.source === 'HEALING' ? ' Web 用例' : '独立 Web 用例'}「${item.name}」V${version.version_no}。${draftNotice}${sourceNotice}`
}

async function loadProjectAssets(id: number, options: { keepCase?: boolean } = {}): Promise<void> {
  const sequence = ++projectSequence
  assetsVersion.value += 1
  if (!options.keepCase) resetCaseEditor()
  const previousCaseId = options.keepCase ? selectedCaseId.value : null
  const previousPageId = selectedPageId.value
  const identity = readRequestIdentity()
  projectMembers.value = []
  const memberRequest = identity
    && !authStore.user?.roles.includes('ADMIN')
    && currentProject.value?.owner_id !== authStore.user?.id
    ? getProjectMembers(id).catch(() => [] as ProjectMember[])
    : Promise.resolve([] as ProjectMember[])
  const [, , , , members] = await Promise.all([
    loadCaseList(sequence),
    loadPages(sequence),
    loadProfiles(sequence),
    loadEnvironments(sequence),
    memberRequest,
  ])
  if (sequence !== projectSequence || projectId.value !== id) return
  if (identity && identityIsCurrent(identity)) projectMembers.value = members
  if (previousCaseId) {
    const item = webCases.value.find((candidate) => candidate.id === previousCaseId)
    if (item) await selectCase(item, true)
  }
  if (previousPageId) {
    const page = pages.value.find((candidate) => candidate.id === previousPageId)
    if (page) {
      selectedPageId.value = page.id
      await loadElements(page.id)
    }
  }
  await applyPendingDeepLink(id, sequence)
}

async function loadProjects(): Promise<void> {
  const identity = readRequestIdentity()
  if (!identity) return
  projectsLoading.value = true
  try {
    const response = await getProjects(false)
    if (!alive || !identityIsCurrent(identity)) return
    projects.value = response.items.filter((project) => project.status === 'ACTIVE')
    const requestedProjectId = positiveQueryId(route.query.project_id)
    if (requestedProjectId !== null && projects.value.some((project) => project.id === requestedProjectId)) {
      projectId.value = requestedProjectId
    } else if (requestedProjectId !== null && route.query.project_id !== undefined) {
      deepLinkNotice.value = [deepLinkNotice.value, 'Healing 定位的项目当前不可用，已使用默认项目。'].filter(Boolean).join(' ')
      pendingDeepLink = null
      if (!projectId.value || !projects.value.some((project) => project.id === projectId.value)) projectId.value = projects.value[0]?.id
    } else if (!projectId.value || !projects.value.some((project) => project.id === projectId.value)) {
      projectId.value = projects.value[0]?.id
    }
  } catch (error) {
    if (alive && identityIsCurrent(identity)) projectError.value = safeError(error, '项目列表加载失败，请稍后重试')
  } finally {
    if (alive && identityIsCurrent(identity)) projectsLoading.value = false
  }
}

function returnToSourceRun(): void {
  if (!sourceRunId.value) return
  const query: Record<string, string> = { run_id: sourceRunId.value }
  if (sourceRunProjectId.value) query.project_id = String(sourceRunProjectId.value)
  void router.push({
    name: route.meta.projectScoped ? 'project-runs' : 'runs',
    params: route.meta.projectScoped ? { projectId: sourceRunProjectId.value ?? projectId.value } : {},
    query,
  })
}

function startPlanRecording(item: WebPlanItem): void {
  recordingPlanItemId.value = item.id
  recordingStartUrl.value = item.start_url_hint ?? ''
  activeTab.value = 'recordings'
}

watch(projectId, (id, previousId) => {
  if (id === previousId || !id) return
  selectedCaseId.value = null
  selectedPageId.value = null
  selectedElementId.value = null
  projectMembers.value = []
  elements.value = []
  environments.value = []
  environmentsError.value = null
  recordingPlanItemId.value = null
  recordingStartUrl.value = ''
  void loadProjectAssets(id)
})

watch(selectedPageId, (id, previousId) => {
  if (id === previousId || !id) return
  void loadElements(id)
})

watch(selectedVersionId, (id, previousId) => {
  if (!id || id === previousId) return
  const version = caseVersions.value.find((item) => item.id === id)
  if (version) editorContent.value = cloneContent(version.content)
})

function onIdentityStorage(event: StorageEvent): void {
  if (!isIdentityStorageEvent(event)) return
  invalidateCaseWriteContext()
  projectSequence += 1
  caseDetailSequence += 1
  pageSequence += 1
  elementVersionSequence += 1
  projects.value = []
  projectMembers.value = []
  webCases.value = []
  caseDetail.value = null
  caseVersions.value = []
  selectedCaseId.value = null
  selectedVersionId.value = null
  deepLinkNotice.value = '登录身份已变化，旧身份的 Web 资产与需求关联已清除。'
}

onMounted(() => {
  window.addEventListener('storage', onIdentityStorage)
  initializeDeepLink()
  void loadProjects()
  void loadAiAssertionPrompts()
})
onBeforeUnmount(() => {
  alive = false
  invalidateCaseWriteContext()
  projectSequence += 1
  caseDetailSequence += 1
  pageSequence += 1
  elementVersionSequence += 1
  window.removeEventListener('storage', onIdentityStorage)
})
</script>

<template>
  <div class="web-assets-page">
    <header class="page-heading web-assets-heading">
      <div>
        <span class="eyebrow">Web 自动化资产</span>
        <h1>Web 自动化资产</h1>
        <p>管理 Web 用例、页面、元素与已有会话配置。版本内容保存后不可变，批准后才可进入运行中心。</p>
      </div>
      <div class="heading-actions">
        <el-button v-if="sourceRunId" @click="returnToSourceRun">返回原 Run</el-button>
        <el-select v-model="projectId" placeholder="选择 ACTIVE 项目" :loading="projectsLoading" :disabled="projectsLoading || activeProjects.length === 0" style="width: 260px">
          <el-option v-for="project in activeProjects" :key="project.id" :label="projectLabel(project)" :value="project.id" />
        </el-select>
        <el-button :icon="Refresh" :disabled="!projectId" @click="projectId && loadProjectAssets(projectId, { keepCase: true })">刷新资源</el-button>
      </div>
    </header>

    <el-alert v-if="projectId && !canWriteProject" title="当前角色为只读：Web 用例历史与录制整理结果仍可查看，创建、保存、批准和 AI 决策已禁用。" type="info" :closable="false" show-icon />

    <el-alert v-if="projectError" :title="projectError" type="error" :closable="false" show-icon />
    <el-alert v-if="deepLinkNotice" :title="deepLinkNotice" type="warning" :closable="false" show-icon />
    <el-card v-if="!projectsLoading && activeProjects.length === 0" shadow="never"><el-empty description="暂无可管理的 ACTIVE 项目" /></el-card>

    <el-card v-else class="asset-card" shadow="never">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="Web 用例" name="cases">
          <div class="case-layout">
            <aside class="asset-list-panel" v-loading="casesLoading">
               <div class="panel-toolbar"><strong>Web 用例</strong><el-button text data-testid="new-web-case" :icon="Plus" :disabled="!canWriteProject" @click="newCase">新建</el-button></div>
              <el-alert v-if="casesError" :title="casesError" type="error" :closable="false" show-icon />
              <button v-for="item in casePages.items.value" :key="item.id" class="asset-list-item" :class="{ active: item.id === selectedCaseId }" @click="selectCase(item)">
                <span><strong>{{ item.name }}</strong><small>{{ item.code }} · {{ item.current_version_id ? '已有当前版本' : '暂无版本' }}</small></span>
                <el-tag size="small" :type="caseStatusType(item.status)">{{ caseStatusLabel(item.status) }}</el-tag>
              </button>
              <el-empty v-if="!casesLoading && !webCases.length" description="暂无 Web 用例" :image-size="70" />
              <el-pagination v-if="casePages.total.value" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="casePages.page.value" :page-size="casePages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="casePages.total.value" @current-change="casePages.changePage" @size-change="casePages.changePageSize" />
            </aside>

            <section class="case-editor-panel" v-loading="caseDetailLoading">
              <template v-if="editorCaseId || caseName || activeTab === 'cases'">
                <div class="editor-toolbar">
                  <div><span class="eyebrow">用例版本编辑器</span><h2>{{ editorCaseId ? caseName : '新建 Web 用例' }}</h2></div>
                  <div class="toolbar-actions">
                    <el-tag v-if="selectedCase" :type="caseStatusType(selectedCase.status)">{{ caseStatusLabel(selectedCase.status) }}</el-tag>
                    <el-button v-if="selectedCase?.status === 'DRAFT'" type="warning" :loading="caseApproving" :disabled="!canWriteProject" @click="approveSelectedCase"><el-icon><CircleCheck /></el-icon>批准执行</el-button>
                    <el-button v-if="selectedCase" :loading="caseArchiving" :disabled="!canWriteProject" @click="toggleCaseArchive">{{ selectedCase.status === 'ARCHIVED' ? '恢复' : '归档' }}</el-button>
                     <el-button type="primary" data-testid="save-web-case" :loading="caseSaving" :disabled="!caseEditable()" @click="saveCase">{{ editorCaseId ? '保存新版本' : '创建 V1' }}</el-button>
                  </div>
                </div>
                <el-alert v-if="caseDetailError" :title="caseDetailError" type="error" :closable="false" show-icon />
                <div class="case-basic-grid">
                  <el-form-item label="名称"><el-input v-model="caseName" data-testid="web-case-name" maxlength="255" /></el-form-item>
                  <el-form-item label="起始 URL"><el-input v-model="editorContent.start_url" data-testid="web-case-start-url" maxlength="2048" placeholder="https://example.test" /></el-form-item>
                  <el-form-item label="变更说明"><el-input v-model="changeNote" data-testid="web-case-change-note" maxlength="500" :placeholder="editorCaseId ? '保存新版本必填' : '创建 Web 用例'" /></el-form-item>
                  <el-form-item label="总超时（毫秒）"><el-input-number v-model="editorContent.total_timeout_ms" :min="1000" :max="86400000" /></el-form-item>
                  <el-form-item label="浏览器"><el-input model-value="Chrome" disabled /></el-form-item>
                  <el-form-item label="运行模式"><el-switch v-model="editorContent.headless" active-text="Headless" inactive-text="有界面" /></el-form-item>
                  <el-form-item label="窗口宽度"><el-input-number v-model="editorContent.browser_config.window_width" :min="320" :max="7680" /></el-form-item>
                  <el-form-item label="窗口高度"><el-input-number v-model="editorContent.browser_config.window_height" :min="240" :max="4320" /></el-form-item>
                  <el-form-item label="浏览器语言"><el-input v-model="editorContent.browser_config.language" maxlength="35" placeholder="例如 zh-CN（可选）" /></el-form-item>
                  <el-form-item label="User-Agent"><el-input v-model="editorContent.browser_config.user_agent" maxlength="512" placeholder="留空使用 Chrome 默认值" /></el-form-item>
                  <el-form-item label="下载相对路径" class="field-span-2">
                    <el-input v-model="editorContent.browser_config.download_path" maxlength="128" placeholder="例如 downloads/reports；仅限本次 Runner 临时目录" />
                  </el-form-item>
                  <el-form-item label="使用代理"><el-switch :model-value="Boolean(editorContent.browser_config.proxy)" @update:model-value="toggleProxy" /></el-form-item>
                  <template v-if="editorContent.browser_config.proxy">
                    <el-form-item label="代理服务器"><el-input v-model="editorContent.browser_config.proxy.server" maxlength="2048" placeholder="http://proxy.example:8080" /></el-form-item>
                    <el-form-item label="代理用户名密文"><el-input v-model="editorContent.browser_config.proxy.username" maxlength="256" placeholder="{{secret.PROXY_USER}}（可选）" /></el-form-item>
                    <el-form-item label="代理密码密文"><el-input v-model="editorContent.browser_config.proxy.password" maxlength="256" show-password placeholder="{{secret.PROXY_PASSWORD}}（可选）" /></el-form-item>
                  </template>
                  <el-form-item label="会话配置" class="field-span-2">
                    <el-select v-model="editorContent.session_profile_id" clearable placeholder="不使用已有会话配置" class="full-width">
                      <el-option v-for="profile in sessionProfiles.filter((item) => item.status === 'ACTIVE')" :key="profile.id" :label="`${profile.name}（${profile.expires_at ? `有效至 ${dateLabel(profile.expires_at)}` : '未设置过期时间'}）`" :value="profile.id" />
                    </el-select>
                    <small v-if="currentSessionProfile" class="safe-note">已选择安全会话资产；页面不展示会话状态、Cookie 或 Token。</small>
                    <small v-else class="safe-note">仅选择已有安全会话资产，不在此页面编辑敏感内容。</small>
                  </el-form-item>
                </div>

                <div class="dsl-section">
                  <div class="section-heading"><div><h3>自然语言步骤</h3><p>用于帮助团队理解和维护流程，会随当前 Web 用例版本正式保存。</p></div><el-button text :icon="Plus" @click="addNaturalLanguageStep">添加步骤</el-button></div>
                  <div v-for="(step, index) in editorContent.natural_language_steps" :key="index" class="natural-step-row"><span class="step-number">{{ index + 1 }}</span><el-input v-model="editorContent.natural_language_steps[index]" maxlength="2000" show-word-limit placeholder="例如：打开登录页并填写用户名" /><el-button link type="danger" @click="removeNaturalLanguageStep(index)"><el-icon><Delete /></el-icon></el-button></div>
                  <el-empty v-if="editorContent.natural_language_steps.length === 0" description="暂无自然语言步骤（可选）" :image-size="50" />
                </div>

                <div class="dsl-section">
                  <div class="section-heading"><div><h3>结构化操作</h3><p>类型切换会重建该项，避免旧字段混入新的 DSL 对象。</p></div><el-button text data-testid="add-manual-action" :icon="Plus" @click="addAction">添加操作</el-button></div>
                  <div v-for="(action, index) in editorContent.actions" :key="index" class="dsl-row" :data-testid="`manual-action-row-${index}`">
                    <div class="dsl-row-heading"><span class="step-number">{{ index + 1 }}</span><el-select :model-value="action.type" :data-testid="`manual-action-type-${index}`" style="width: 205px" @update:model-value="replaceAction(index, $event)"><el-option v-for="type in actionTypes" :key="type" :label="type" :value="type" /></el-select><span class="readable-step">{{ actionSummary(action) }}</span><el-button link type="danger" @click="removeAction(index)"><el-icon><Delete /></el-icon></el-button></div>
                    <div class="dsl-fields">
                      <el-input v-if="action.type === 'GOTO' || action.type === 'WAIT_URL' || action.type === 'NEW_TAB'" v-model="action.url" :data-testid="`manual-action-url-${index}`" placeholder="URL 或运行时模板" />
                      <template v-if="action.type === 'NEW_TAB' || action.type === 'SWITCH_TAB' || action.type === 'CLOSE_TAB'">
                        <el-input v-model="action.value" :data-testid="`manual-action-tab-alias-${index}`" placeholder="标签页别名" :title="webTabAliasHelp"><template #prepend>标签页别名</template></el-input>
                        <el-text type="info" size="small">{{ webTabAliasHelp }}</el-text>
                      </template>
                      <el-input v-if="action.type === 'FILL' || action.type === 'SELECT'" v-model="action.value" :data-testid="`manual-action-value-${index}`" placeholder="输入值" />
                      <el-input v-else-if="action.type === 'DRAG_DROP'" v-model="action.value" :data-testid="`manual-action-value-${index}`" placeholder="目标 CSS Locator" />
                      <template v-else-if="action.type === 'UPLOAD'">
                        <input type="file" :data-testid="`manual-action-upload-${index}`" @change="chooseActionUploadFile(index, $event)" />
                        <el-text type="info" size="small">{{ action.key ? `${action.key} · 已安全编码到固定版本` : '请选择 1 MiB 以内文件；不保存本机路径' }}</el-text>
                      </template>
                      <el-input v-else-if="action.type === 'DOWNLOAD'" v-model="action.value" :data-testid="`manual-action-value-${index}`" placeholder="期望下载文件名（留空则接受任意）" />
                      <el-input v-else-if="action.type === 'WAIT_TEXT'" v-model="action.value" :data-testid="`manual-action-value-${index}`" placeholder="等待出现的文本" />
                      <el-input v-else-if="action.type === 'WAIT_TIME'" v-model="action.value" :data-testid="`manual-action-value-${index}`" placeholder="等待毫秒数，例如 1000"><template #prepend>毫秒</template></el-input>
                      <template v-else-if="action.type === 'COOKIE' || action.type === 'LOCAL_STORAGE' || action.type === 'SESSION_STORAGE'">
                        <el-input v-model="action.key" :data-testid="`manual-action-key-${index}`" placeholder="键名" />
                        <el-input v-model="action.value" :data-testid="`manual-action-value-${index}`" placeholder="值或 {{secret.NAME}}" />
                      </template>
                      <el-input v-else-if="action.type === 'JS_EVAL'" v-model="action.value" type="textarea" :rows="3" :data-testid="`manual-action-value-${index}`" placeholder="页面上下文 JavaScript 表达式" />
                      <el-input v-if="action.type === 'PRESS'" v-model="action.key" :data-testid="`manual-action-key-${index}`" placeholder="按键，例如 Enter" />
                      <template v-if="hasLocator(action)">
                        <el-select :model-value="locatorMode(action)" :data-testid="`manual-action-locator-mode-${index}`" style="width: 120px" @update:model-value="updateLocatorMode(action, $event)"><el-option label="直接 Locator" value="direct" /><el-option label="引用 Element" value="reference" /></el-select>
                        <el-select v-if="locatorMode(action) === 'reference'" :model-value="getLocator(action)?.element_version_id ?? undefined" :data-testid="`manual-action-element-version-${index}`" placeholder="选择元素当前版本" style="min-width: 230px" @update:model-value="updateLocatorReference(action, $event)"><el-option v-for="element in activeElements.filter((item) => item.current_version_id)" :key="element.id" :label="`${element.name} · 当前版本`" :value="element.current_version_id" /></el-select>
                        <template v-else><el-select :model-value="getLocator(action)?.strategy ?? undefined" :data-testid="`manual-action-locator-strategy-${index}`" placeholder="策略" style="width: 140px" @update:model-value="updateLocatorStrategy(action, $event)"><el-option v-for="strategy in locatorStrategies" :key="strategy" :label="strategy" :value="strategy" /></el-select><el-input :model-value="getLocator(action)?.value ?? ''" :data-testid="`manual-action-locator-value-${index}`" placeholder="Locator 值" @update:model-value="updateLocatorValue(action, $event)" /></template>
                      </template>
                      <el-input-number v-model="action.timeout_ms" :data-testid="`manual-action-timeout-${index}`" :min="100" :max="600000" controls-position="right" />
                      <el-select v-model="action.failure_policy" :data-testid="`manual-action-policy-${index}`" style="width: 120px"><el-option label="失败停止" value="STOP" /><el-option label="继续" value="CONTINUE" /></el-select>
                    </div>
                  </div>
                </div>

                <div class="dsl-section">
                  <div class="section-heading"><div><h3>结构化断言</h3><p>断言保存在当前不可变版本中，服务端批准时会再次校验。</p></div><el-button text :icon="Plus" @click="addAssertion">添加断言</el-button></div>
                  <div v-for="(assertion, index) in editorContent.assertions" :key="index" class="dsl-row" :data-testid="`manual-assertion-row-${index}`">
                    <div class="dsl-row-heading"><span class="step-number">{{ index + 1 }}</span><el-select :model-value="assertion.type" :data-testid="`manual-assertion-type-${index}`" style="width: 290px" @update:model-value="replaceAssertion(index, $event)"><el-option v-for="type in assertionTypes" :key="type" :label="webAssertionTypeLabel(type)" :value="type" /></el-select><span class="readable-step">{{ assertionSummary(assertion) }}</span><el-button link type="danger" @click="removeAssertion(index)"><el-icon><Delete /></el-icon></el-button></div>
                    <div class="dsl-fields">
                      <el-input v-if="hasWebAssertionExpected(assertion) && assertion.type !== 'ASSERT_SCREENSHOT_VISUAL_COMPARE'" v-model="assertion.expected" :data-testid="`manual-assertion-expected-${index}`" :placeholder="webAssertionExpectedPlaceholder(assertion)" />
                      <template v-if="assertion.type === 'ASSERT_SCREENSHOT_VISUAL_COMPARE'">
                        <input type="file" accept="image/png,.png" :data-testid="`manual-assertion-baseline-${index}`" @change="chooseAssertionBaseline(index, $event)" />
                        <el-text type="info" size="small">{{ assertion.expected ? 'PNG 基线已安全编码到固定版本' : '请选择 1 MiB 以内 PNG；不保存本机路径' }}</el-text>
                      </template>
                      <template v-if="assertion.type === 'ASSERT_AI_SEMANTIC'">
                        <el-select v-model="assertion.prompt_id" placeholder="选择启用的 AI_ASSERTION Prompt" style="min-width: 260px">
                          <el-option v-for="prompt in aiAssertionPrompts.filter((item) => item.enabled && item.current_version)" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" />
                        </el-select>
                        <el-input v-model="assertion.criteria" type="textarea" :rows="3" maxlength="4000" show-word-limit placeholder="说明当前页面必须满足的业务语义" />
                        <el-input-number v-model="assertion.confidence_threshold" :min="0" :max="1" :step="0.05" controls-position="right" />
                      </template>
                      <el-input v-if="assertion.type === 'ASSERT_ATTRIBUTE'" v-model="assertion.key" :data-testid="`manual-assertion-key-${index}`" placeholder="属性名，例如 data-status" />
                      <template v-if="hasLocator(assertion)">
                        <el-select :model-value="locatorMode(assertion)" :data-testid="`manual-assertion-locator-mode-${index}`" style="width: 120px" @update:model-value="updateLocatorMode(assertion, $event)"><el-option label="直接 Locator" value="direct" /><el-option label="引用 Element" value="reference" /></el-select>
                        <el-select v-if="locatorMode(assertion) === 'reference'" :model-value="getLocator(assertion)?.element_version_id ?? undefined" :data-testid="`manual-assertion-element-version-${index}`" placeholder="选择元素当前版本" style="min-width: 230px" @update:model-value="updateLocatorReference(assertion, $event)"><el-option v-for="element in activeElements.filter((item) => item.current_version_id)" :key="element.id" :label="`${element.name} · 当前版本`" :value="element.current_version_id" /></el-select>
                        <template v-else><el-select :model-value="getLocator(assertion)?.strategy ?? undefined" :data-testid="`manual-assertion-locator-strategy-${index}`" placeholder="策略" style="width: 140px" @update:model-value="updateLocatorStrategy(assertion, $event)"><el-option v-for="strategy in locatorStrategies" :key="strategy" :label="strategy" :value="strategy" /></el-select><el-input :model-value="getLocator(assertion)?.value ?? ''" :data-testid="`manual-assertion-locator-value-${index}`" placeholder="Locator 值" @update:model-value="updateLocatorValue(assertion, $event)" /></template>
                      </template>
                      <el-input-number v-model="assertion.timeout_ms" :data-testid="`manual-assertion-timeout-${index}`" :min="100" :max="600000" controls-position="right" />
                    </div>
                  </div>
                  <el-empty v-if="editorContent.assertions.length === 0" description="暂无断言（可选）" :image-size="50" />
                </div>

                <div v-if="editorCaseId" class="version-history">
                  <div class="section-heading"><div><h3>版本历史</h3><p>选择版本查看内容；保存始终创建新版本，不覆盖历史版本。</p></div></div>
                  <el-table :data="caseVersionPages.items.value" size="small" table-layout="fixed">
                    <el-table-column label="版本" width="90"><template #default="{ row }">V{{ row.version_no }}</template></el-table-column>
                    <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag size="small" :type="caseStatusType(row.status)">{{ caseStatusLabel(row.status) }}</el-tag></template></el-table-column>
                    <el-table-column prop="change_note" label="变更说明" min-width="180" />
                    <el-table-column label="创建时间" width="170"><template #default="{ row }">{{ dateLabel(row.created_at) }}</template></el-table-column>
                    <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" @click="selectVersion(row)">查看</el-button></template></el-table-column>
                  </el-table>
                  <el-pagination v-if="caseVersionPages.total.value" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="caseVersionPages.page.value" :page-size="caseVersionPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="caseVersionPages.total.value" @current-change="caseVersionPages.changePage" @size-change="caseVersionPages.changePageSize" />
                </div>
                <el-collapse v-if="caseDetail && projectId" v-model="reverseExpanded" class="version-history">
                  <el-collapse-item title="关联需求（含历史）" name="requirements">
                    <AssetRequirementLinksPanel
                      v-if="reverseExpanded.includes('requirements')"
                      :key="`WEB_CASE-${caseDetail.id}`"
                      asset-type="WEB_CASE"
                      :asset-id="caseDetail.id"
                      :project-id="projectId"
                      :focused-version-id="selectedVersionId"
                    />
                  </el-collapse-item>
                </el-collapse>
              </template>
              <el-empty v-else description="请选择或新建 Web 用例" />
            </section>
          </div>
        </el-tab-pane>

        <el-tab-pane label="页面与元素" name="pages">
          <div class="page-element-layout">
            <section class="asset-subpanel" v-loading="pagesLoading">
              <div class="panel-toolbar"><strong>Web 页面</strong><el-button text :icon="Plus" @click="openPageEditor()">新建</el-button></div>
              <el-alert v-if="pagesError" :title="pagesError" type="error" :closable="false" show-icon />
              <el-table :data="webPagePages.items.value" size="small" highlight-current-row @current-change="selectPageRow">
                <el-table-column prop="name" label="名称" min-width="150" /><el-table-column prop="code" label="编码" min-width="120" /><el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ assetStatusLabel(row.status) }}</el-tag></template></el-table-column><el-table-column label="操作" width="150"><template #default="{ row }"><el-button link :icon="Edit" @click="openPageEditor(row)">编辑</el-button><el-button link @click="togglePageArchive(row)">{{ row.status === 'ACTIVE' ? '归档' : '恢复' }}</el-button></template></el-table-column>
              </el-table>
              <el-empty v-if="!pagesLoading && !pages.length" description="暂无 Web 页面" :image-size="60" />
              <el-pagination v-if="webPagePages.total.value" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="webPagePages.page.value" :page-size="webPagePages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="webPagePages.total.value" @current-change="webPagePages.changePage" @size-change="webPagePages.changePageSize" />
            </section>
            <section class="asset-subpanel" v-loading="elementsLoading">
              <div class="panel-toolbar"><div><strong>Web 元素</strong><small v-if="selectedPage">当前页面：{{ selectedPage.name }}</small></div><el-button text :icon="Plus" :disabled="!selectedPageId" @click="openElementEditor()">新建</el-button></div>
              <el-alert v-if="elementsError" :title="elementsError" type="error" :closable="false" show-icon />
              <el-table :data="elementPages.items.value" size="small">
                <el-table-column prop="name" label="名称" min-width="150" /><el-table-column prop="element_type" label="类型" width="100" /><el-table-column label="当前定位器" min-width="130"><template #default="{ row }">{{ row.current_version_id ? '已配置' : '未配置' }}</template></el-table-column><el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ assetStatusLabel(row.status) }}</el-tag></template></el-table-column><el-table-column label="操作" width="210"><template #default="{ row }"><el-button link type="primary" @click="openElementVersionEditor(row)">定位器版本</el-button><el-button link :icon="Edit" @click="openElementEditor(row)">编辑</el-button><el-button link @click="toggleElementArchive(row)">{{ row.status === 'ACTIVE' ? '归档' : '恢复' }}</el-button></template></el-table-column>
              </el-table>
              <el-empty v-if="!elementsLoading && !elements.length" description="当前页面暂无元素" :image-size="60" />
              <el-pagination v-if="elementPages.total.value" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="elementPages.page.value" :page-size="elementPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="elementPages.total.value" @current-change="elementPages.changePage" @size-change="elementPages.changePageSize" />
            </section>
          </div>
        </el-tab-pane>

        <el-tab-pane label="AI 测试设计" name="design">
          <WebDesignWorkbench
            :project-id="projectId"
            :can-write="canWriteProject"
            :environments="environments"
            :session-profiles="sessionProfiles"
            @manual-record="startPlanRecording"
            @refresh-assets="projectId && loadProjectAssets(projectId, { keepCase: true })"
          />
        </el-tab-pane>

        <el-tab-pane label="录制工作台" name="recordings">
          <WebRecordingWorkbench
            :project-id="projectId"
            :can-write="canWriteProject"
            :web-cases="webCases"
            :session-profiles="sessionProfiles"
            :environments="environments"
            :environments-loading="environmentsLoading"
            :environments-error="environmentsError"
            :refresh-key="assetsVersion"
            :initial-plan-item-id="recordingPlanItemId"
            :initial-start-url="recordingStartUrl"
            @refresh-assets="projectId && loadProjectAssets(projectId, { keepCase: true })"
          />
        </el-tab-pane>

        <el-tab-pane label="会话配置" name="profiles">
          <el-alert title="安全提示" description="这里只展示已有会话配置的安全元数据；会话状态、Cookie、Token 和原始指纹不会展示或写入页面。" type="info" :closable="false" show-icon />
          <el-alert v-if="profilesError" :title="profilesError" type="error" :closable="false" show-icon class="section-gap" />
          <div class="panel-toolbar section-gap"><div><strong>会话配置</strong><small>可用于录制和 Web 运行；新建或更新时粘贴的会话状态只用于本次请求，提交后立即清除。</small></div><el-button type="primary" :icon="Plus" @click="openProfileEditor()">新建</el-button></div>
          <el-table v-loading="profilesLoading" :data="profilePages.items.value" class="section-gap" table-layout="fixed">
            <el-table-column prop="name" label="名称" min-width="160" /><el-table-column label="状态" width="100"><template #default="{ row }"><el-tag size="small" :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ assetStatusLabel(row.status) }}</el-tag></template></el-table-column><el-table-column label="环境" width="110"><template #default="{ row }">{{ row.environment_id ? environments.find((item) => item.id === row.environment_id)?.name ?? '详情未加载' : '未绑定' }}</template></el-table-column><el-table-column label="会话状态" width="130"><template #default="{ row }">{{ row.storage_state_fingerprint ? `已配置 R${row.revision}` : '未配置' }}</template></el-table-column><el-table-column label="自动恢复" width="150"><template #default="{ row }">{{ row.login_web_case_version_id ? '已配置登录流程' : '未配置' }}</template></el-table-column><el-table-column label="指纹摘要" width="150"><template #default="{ row }">{{ profileFingerprintLabel(row.storage_state_fingerprint) }}</template></el-table-column><el-table-column label="元数据摘要" min-width="150"><template #default="{ row }">{{ profileMetadataLabel(row.metadata) }}</template></el-table-column><el-table-column label="过期时间" min-width="170"><template #default="{ row }"><span :class="{ 'expiry-warning': row.expires_at && apiDateTimeMs(row.expires_at) <= Date.now() }">{{ row.expires_at ? dateLabel(row.expires_at) : '未设置' }}{{ row.expires_at && apiDateTimeMs(row.expires_at) <= Date.now() ? '（已过期）' : '' }}</span></template></el-table-column><el-table-column label="操作" width="150" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openProfileEditor(row)">编辑</el-button><el-button link @click="toggleProfileArchive(row)">{{ row.status === 'ACTIVE' ? '归档' : '恢复' }}</el-button></template></el-table-column>
          </el-table>
          <el-empty v-if="!profilesLoading && !profilesError && !sessionProfiles.length" description="当前项目暂无会话配置" :image-size="70" />
          <el-pagination v-if="profilePages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="profilePages.page.value" :page-size="profilePages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="profilePages.total.value" @current-change="profilePages.changePage" @size-change="profilePages.changePageSize" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="pageDialogVisible" :title="editingPageId ? '编辑 Web 页面' : '新建 Web 页面'" width="560px">
      <el-form label-position="top"><el-form-item v-if="!editingPageId" label="编码"><el-input v-model="pageForm.code" maxlength="64" placeholder="例如 login" /></el-form-item><el-form-item label="名称"><el-input v-model="pageForm.name" maxlength="255" /></el-form-item><el-form-item label="URL 匹配规则"><el-input v-model="pageForm.url_pattern" maxlength="2048" /></el-form-item><el-form-item label="描述"><el-input v-model="pageForm.description" type="textarea" maxlength="2000" /></el-form-item></el-form>
      <template #footer><el-button @click="pageDialogVisible = false">取消</el-button><el-button type="primary" :loading="pageSaving" @click="savePage">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="elementDialogVisible" :title="editingElementId ? '编辑 Web 元素' : '新建 Web 元素'" width="560px">
      <el-form label-position="top"><el-form-item label="名称"><el-input v-model="elementForm.name" maxlength="128" /></el-form-item><el-form-item label="Element 类型"><el-input v-model="elementForm.element_type" maxlength="32" placeholder="例如 BUTTON" /></el-form-item><el-form-item label="描述"><el-input v-model="elementForm.description" type="textarea" maxlength="1000" /></el-form-item></el-form>
      <template #footer><el-button @click="elementDialogVisible = false">取消</el-button><el-button type="primary" :loading="elementSaving" @click="saveElement">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="elementVersionVisible" title="元素定位器版本" width="800px">
      <div v-loading="elementVersionsLoading">
        <el-alert title="定位器版本不可变；保存会创建新版本。优先级数值越小越优先，系统会按优先级选择候选。" type="info" :closable="false" show-icon />
        <el-form label-position="top" class="version-form"><el-form-item label="Element 类型"><el-input v-model="elementVersionForm.element_type" maxlength="32" /></el-form-item><el-form-item label="版本说明"><el-input v-model="elementVersionForm.description" maxlength="1000" /></el-form-item></el-form>
        <div v-for="(locator, index) in elementVersionForm.locators" :key="index" class="locator-row"><el-select v-model="locator.strategy" style="width: 145px"><el-option v-for="strategy in locatorStrategies" :key="strategy" :label="strategy" :value="strategy" /></el-select><el-input v-model="locator.value" placeholder="定位器值" /><el-input-number v-model="locator.priority" :min="1" :max="20" controls-position="right" /><el-tag size="small">{{ locator.source }}</el-tag><el-button link type="danger" @click="removeLocator(index)"><el-icon><Delete /></el-icon></el-button></div>
        <el-button text :icon="Plus" @click="addLocator">添加定位器</el-button>
        <el-table v-if="elementVersions.length" :data="elementVersionPages.items.value" size="small" class="section-gap"><el-table-column label="版本" width="90"><template #default="{ row }">V{{ row.version_no }}</template></el-table-column><el-table-column label="定位器数量" width="100"><template #default="{ row }">{{ row.locators.length }}</template></el-table-column><el-table-column prop="description" label="说明" /><el-table-column label="创建时间" width="170"><template #default="{ row }">{{ dateLabel(row.created_at) }}</template></el-table-column></el-table>
        <el-pagination v-if="elementVersionPages.total.value" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="elementVersionPages.page.value" :page-size="elementVersionPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="elementVersionPages.total.value" @current-change="elementVersionPages.changePage" @size-change="elementVersionPages.changePageSize" />
      </div>
      <template #footer><el-button @click="elementVersionVisible = false">取消</el-button><el-button type="primary" :loading="elementVersionSaving" @click="saveElementVersion">保存新版本</el-button></template>
    </el-dialog>

    <el-dialog v-model="profileDialogVisible" :title="editingProfileId ? '编辑会话配置' : '新建会话配置'" width="620px" @close="clearProfileForm">
      <el-alert title="敏感信息提示" description="会话状态仅用于本次提交，页面不会回显、持久化或展示 Cookie、Token 或原始指纹。请确认内容来自可信来源。" type="warning" :closable="false" show-icon />
      <el-form label-position="top" class="profile-form section-gap">
        <el-form-item label="名称"><el-input v-model="profileForm.name" maxlength="128" placeholder="例如 member_session" /></el-form-item>
        <el-form-item label="绑定环境（可选）"><el-select v-model="profileForm.environment_id" clearable filterable :disabled="Boolean(editingProfileId)" :loading="environmentsLoading" placeholder="不绑定" class="full-width"><el-option v-for="environment in environments" :key="environment.id" :label="`${environment.name}（${environment.code}）`" :value="environment.id" /></el-select><span v-if="editingProfileId" class="safe-note">环境绑定创建后不可更新，当前值仅供查看。</span><span v-if="environmentsError" class="safe-note error-note">{{ environmentsError }}</span></el-form-item>
        <el-form-item label="会话状态 JSON"><el-input v-model="profileForm.storage_state" type="textarea" :rows="6" autocomplete="off" placeholder="新建必填；编辑时留空表示不更新" /></el-form-item>
        <el-form-item label="安全元数据 JSON（可选）"><el-input v-model="profileForm.metadata" type="textarea" :rows="3" autocomplete="off" placeholder="例如：{ &quot;source&quot;: &quot;manual&quot; }；值必须是文本" /></el-form-item>
        <el-form-item label="过期时间（可选）"><el-date-picker v-model="profileForm.expires_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" placeholder="未设置过期时间" class="full-width" /></el-form-item>
        <el-form-item label="自动登录恢复"><el-switch v-model="profileForm.recovery_enabled" active-text="启用" /><span class="safe-note">仅在明确失效条件命中时执行固定批准版本，刷新后原业务流程只执行一次。</span></el-form-item>
        <template v-if="profileForm.recovery_enabled">
          <el-form-item label="登录 Web 用例"><el-select v-model="profileForm.login_web_case_id" filterable class="full-width" placeholder="选择已批准登录流程"><el-option v-for="item in webCases.filter((candidate) => candidate.status === 'APPROVED' && candidate.current_version_id)" :key="item.id" :label="`${item.name}（${item.code}）`" :value="item.id" /></el-select></el-form-item>
          <el-form-item label="刷新有效期（秒）"><el-input-number v-model="profileForm.refresh_ttl_seconds" :min="60" :max="2592000" class="full-width" /></el-form-item>
          <el-form-item label="失效条件 JSON"><el-input v-model="profileForm.expiry_condition" type="textarea" :rows="4" placeholder="URL_EQUALS / URL_CONTAINS / LOCATOR_VISIBLE / LOCATOR_HIDDEN" /></el-form-item>
          <el-form-item label="登录成功条件 JSON"><el-input v-model="profileForm.success_condition" type="textarea" :rows="4" placeholder="明确 URL 或定位器条件" /></el-form-item>
        </template>
      </el-form>
      <template #footer><el-button @click="profileDialogVisible = false">取消</el-button><el-button type="primary" :loading="profileSaving" @click="saveSessionProfile">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.web-assets-page{display:flex;flex-direction:column;gap:18px}.web-assets-heading{align-items:flex-start}.web-assets-heading p{margin:6px 0 0;color:var(--text-secondary);font-size:13px}.heading-actions,.toolbar-actions,.panel-toolbar,.section-heading,.dsl-row-heading,.dsl-fields,.locator-row,.natural-step-row{display:flex;align-items:center;gap:10px}.heading-actions{justify-content:flex-end}.asset-card{border:1px solid var(--border-color);border-radius:14px}.case-layout,.page-element-layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px}.page-element-layout{grid-template-columns:1fr 1.5fr}.asset-list-panel,.case-editor-panel,.asset-subpanel{min-width:0}.asset-list-panel{padding-right:14px;border-right:1px solid var(--border-color)}.panel-toolbar,.section-heading{justify-content:space-between;margin-bottom:14px}.panel-toolbar strong{color:var(--text-primary);font-size:16px}.panel-toolbar small{display:block;margin-top:4px;color:var(--text-secondary);font-size:12px}.asset-list-item{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;padding:11px;border:0;border-radius:8px;background:transparent;color:inherit;text-align:left;cursor:pointer}.asset-list-item:hover,.asset-list-item.active{background:var(--surface-subtle)}.asset-list-item strong,.asset-list-item small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.asset-list-item strong{color:var(--text-primary)}.asset-list-item small,.section-heading p,.safe-note{color:var(--text-secondary);font-size:12px}.editor-toolbar{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:18px}.editor-toolbar h2{margin:4px 0 0;color:var(--text-primary)}.toolbar-actions{justify-content:flex-end;flex-wrap:wrap}.case-basic-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 14px}.field-span-2{grid-column:span 2}.full-width{width:100%}.safe-note{display:block;margin-top:5px}.dsl-section,.version-history{margin-top:24px}.section-heading{align-items:flex-start}.section-heading h3{margin:0;color:var(--text-primary);font-size:16px}.section-heading p{margin:4px 0 0}.natural-step-row{margin-top:10px}.natural-step-row>.el-input{flex:1}.dsl-row{margin-top:10px;padding:12px;border:1px solid var(--border-color);border-radius:9px}.dsl-row-heading{margin-bottom:10px}.step-number{display:grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--surface-subtle);color:var(--text-secondary);font-size:12px}.readable-step{flex:1;overflow:hidden;color:var(--text-secondary);font-size:13px;text-overflow:ellipsis;white-space:nowrap}.dsl-fields{flex-wrap:wrap}.dsl-fields>.el-input{min-width:180px;flex:1}.dsl-fields>.el-input-number{width:145px}.asset-subpanel{padding:4px}.section-gap{margin-top:14px}.locator-row{margin:12px 0}.locator-row>.el-input{flex:1}.locator-row>.el-input-number{width:130px}.version-form{display:grid;grid-template-columns:1fr 2fr;gap:12px;margin-top:16px}.version-form .el-form-item{margin-bottom:0}@media(max-width:1000px){.case-layout,.page-element-layout{grid-template-columns:1fr}.asset-list-panel{padding-right:0;border-right:0;border-bottom:1px solid var(--border-color);padding-bottom:14px}.case-basic-grid{grid-template-columns:1fr}.field-span-2{grid-column:auto}}@media(max-width:680px){.web-assets-heading,.heading-actions,.editor-toolbar,.section-heading{align-items:flex-start;flex-direction:column}.heading-actions,.heading-actions .el-select{width:100%!important}.dsl-fields{align-items:stretch;flex-direction:column}.dsl-fields>*{width:100%!important}.locator-row{align-items:stretch;flex-wrap:wrap}.locator-row>.el-input{min-width:100%}}
.profile-form{display:grid;grid-template-columns:1fr 1fr;column-gap:14px}.profile-form .el-form-item:nth-child(3),.profile-form .el-form-item:nth-child(4),.profile-form .el-form-item:nth-child(5){grid-column:span 2}.error-note{color:var(--el-color-danger)}.expiry-warning{color:var(--el-color-danger)}
</style>
