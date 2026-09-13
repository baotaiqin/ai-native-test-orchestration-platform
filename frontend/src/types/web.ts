import type { WebHealingContext } from './web-healing'

export type WebCaseStatus = 'DRAFT' | 'APPROVED' | 'ARCHIVED'
export type WebCaseVersionStatus = 'DRAFT' | 'APPROVED' | 'RETIRED'
export type WebAssetStatus = 'ACTIVE' | 'ARCHIVED'
export type LocatorStrategy = 'css' | 'xpath' | 'text' | 'role' | 'label' | 'placeholder' | 'test_id'
export type LocatorSource = 'MANUAL' | 'IMPORTED' | 'HEALED'
export type WebFailurePolicy = 'STOP' | 'CONTINUE'
export type WebTraceStatus = 'SUCCESS' | 'FAILED' | 'SKIPPED' | 'TIMEOUT' | 'CANCELLED'
export type WebLocatorAttemptStatus = 'NOT_FOUND' | 'ACTION_FAILED' | 'SUCCESS'

export interface WebLocatorAttempt {
  strategy: LocatorStrategy
  priority: number
  status: WebLocatorAttemptStatus
}

export interface WebExecutionTrace {
  node_id: string
  status: WebTraceStatus
  duration_ms: number
  error_type: string | null
  error_message: string | null
  locator_attempts: WebLocatorAttempt[]
  healing_context?: WebHealingContext | null
}

export interface WebSessionRecoveryAudit {
  status: 'NOT_NEEDED' | 'SUCCESS' | 'FAILED'
  reason: 'PROFILE_EXPIRED' | 'EXPIRY_CONDITION' | 'NOT_EXPIRED'
  login_web_case_version_id: number
  duration_ms: number
  error_type: string | null
  persistence_status: 'UPDATED' | 'CONFLICT' | 'NOT_ATTEMPTED' | null
}

export interface WebLocator {
  strategy: LocatorStrategy | null
  value: string | null
  element_version_id: number | null
}

export interface WebActionBase {
  timeout_ms: number
  failure_policy: WebFailurePolicy
}

export interface GotoAction extends WebActionBase {
  type: 'GOTO'
  url: string
}

export interface ReloadAction extends WebActionBase {
  type: 'RELOAD'
}

export interface BackAction extends WebActionBase {
  type: 'BACK'
}

export interface ForwardAction extends WebActionBase {
  type: 'FORWARD'
}

export interface NewTabAction extends WebActionBase {
  type: 'NEW_TAB'
  url: string
  value: string
}

export interface SwitchTabAction extends WebActionBase {
  type: 'SWITCH_TAB'
  value: string
}

export interface CloseTabAction extends WebActionBase {
  type: 'CLOSE_TAB'
  value: string
}

export interface FillAction extends WebActionBase {
  type: 'FILL'
  locator: WebLocator
  value: string
}

export interface ClickAction extends WebActionBase {
  type: 'CLICK'
  locator: WebLocator
}

export interface DoubleClickAction extends WebActionBase {
  type: 'DOUBLE_CLICK'
  locator: WebLocator
}

export interface RightClickAction extends WebActionBase {
  type: 'RIGHT_CLICK'
  locator: WebLocator
}

export interface ClearAction extends WebActionBase {
  type: 'CLEAR'
  locator: WebLocator
}

export interface HoverAction extends WebActionBase {
  type: 'HOVER'
  locator: WebLocator
}

export interface DragDropAction extends WebActionBase {
  type: 'DRAG_DROP'
  locator: WebLocator
  value: string
}

export interface UploadAction extends WebActionBase {
  type: 'UPLOAD'
  locator: WebLocator
  key: string
  value: string
}

export interface DownloadAction extends WebActionBase {
  type: 'DOWNLOAD'
  locator: WebLocator
  value: string
}

export interface SelectAction extends WebActionBase {
  type: 'SELECT'
  locator: WebLocator
  value: string
}

export interface CheckAction extends WebActionBase {
  type: 'CHECK'
  locator: WebLocator
}

export interface UncheckAction extends WebActionBase {
  type: 'UNCHECK'
  locator: WebLocator
}

export interface RadioAction extends WebActionBase {
  type: 'RADIO'
  locator: WebLocator
}

export interface PressAction extends WebActionBase {
  type: 'PRESS'
  locator: WebLocator
  key: string
}

export interface EnterAction extends WebActionBase {
  type: 'ENTER'
  locator: WebLocator
}

export interface TabAction extends WebActionBase {
  type: 'TAB'
  locator: WebLocator
}

export interface WaitElementAction extends WebActionBase {
  type: 'WAIT_ELEMENT'
  locator: WebLocator
}

export interface WaitUrlAction extends WebActionBase {
  type: 'WAIT_URL'
  url: string
}

export interface WaitNetworkIdleAction extends WebActionBase {
  type: 'WAIT_NETWORK_IDLE'
}

export interface WaitTimeAction extends WebActionBase {
  type: 'WAIT_TIME'
  value: string
}

export interface WaitTextAction extends WebActionBase {
  type: 'WAIT_TEXT'
  locator: WebLocator
  value: string
}

export interface CookieAction extends WebActionBase {
  type: 'COOKIE'
  key: string
  value: string
}

export interface LocalStorageAction extends WebActionBase {
  type: 'LOCAL_STORAGE'
  key: string
  value: string
}

export interface SessionStorageAction extends WebActionBase {
  type: 'SESSION_STORAGE'
  key: string
  value: string
}

export interface JsEvalAction extends WebActionBase {
  type: 'JS_EVAL'
  value: string
}

export type WebAction =
  | GotoAction
  | ReloadAction
  | BackAction
  | ForwardAction
  | NewTabAction
  | SwitchTabAction
  | CloseTabAction
  | FillAction
  | ClickAction
  | DoubleClickAction
  | RightClickAction
  | ClearAction
  | HoverAction
  | DragDropAction
  | UploadAction
  | DownloadAction
  | SelectAction
  | CheckAction
  | UncheckAction
  | RadioAction
  | PressAction
  | EnterAction
  | TabAction
  | WaitElementAction
  | WaitUrlAction
  | WaitNetworkIdleAction
  | WaitTimeAction
  | WaitTextAction
  | CookieAction
  | LocalStorageAction
  | SessionStorageAction
  | JsEvalAction

export interface AssertVisible {
  type: 'ASSERT_VISIBLE'
  locator: WebLocator
  timeout_ms: number
}

export interface AssertExists {
  type: 'ASSERT_EXISTS'
  locator: WebLocator
  timeout_ms: number
}

export interface AssertEnabled {
  type: 'ASSERT_ENABLED'
  locator: WebLocator
  timeout_ms: number
}

export interface AssertHidden {
  type: 'ASSERT_HIDDEN'
  locator: WebLocator
  timeout_ms: number
}

export interface AssertClickable {
  type: 'ASSERT_CLICKABLE'
  locator: WebLocator
  timeout_ms: number
}

export interface AssertText {
  type: 'ASSERT_TEXT'
  locator: WebLocator
  expected: string
  timeout_ms: number
}

export interface AssertTextEqual {
  type: 'ASSERT_TEXT_EQUAL'
  locator: WebLocator
  expected: string
  timeout_ms: number
}

export interface AssertInputValue {
  type: 'ASSERT_INPUT_VALUE'
  locator: WebLocator
  expected: string
  timeout_ms: number
}

export interface AssertUrl {
  type: 'ASSERT_URL'
  expected: string
  timeout_ms: number
}

export interface AssertTitle {
  type: 'ASSERT_TITLE'
  expected: string
  timeout_ms: number
}

export interface AssertAttribute {
  type: 'ASSERT_ATTRIBUTE'
  locator: WebLocator
  key: string
  expected: string
  timeout_ms: number
}

export interface AssertElementCount {
  type: 'ASSERT_ELEMENT_COUNT'
  locator: WebLocator
  expected: string
  timeout_ms: number
}

export interface AssertDownloadSuccess {
  type: 'ASSERT_DOWNLOAD_SUCCESS'
  expected: string
  timeout_ms: number
}

export interface AssertNetworkRequest {
  type: 'ASSERT_NETWORK_REQUEST'
  expected: string
  timeout_ms: number
}

export interface AssertScreenshotVisualCompare {
  type: 'ASSERT_SCREENSHOT_VISUAL_COMPARE'
  expected: string
  timeout_ms: number
}

export interface AssertAiSemantic {
  type: 'ASSERT_AI_SEMANTIC'
  prompt_id: number | null
  criteria: string
  confidence_threshold: number
  timeout_ms: number
}

export type WebAssertion =
  | AssertVisible
  | AssertExists
  | AssertEnabled
  | AssertHidden
  | AssertClickable
  | AssertText
  | AssertTextEqual
  | AssertInputValue
  | AssertUrl
  | AssertTitle
  | AssertAttribute
  | AssertElementCount
  | AssertDownloadSuccess
  | AssertNetworkRequest
  | AssertScreenshotVisualCompare
  | AssertAiSemantic

export interface WebCaseContent {
  start_url: string
  natural_language_steps: string[]
  actions: WebAction[]
  assertions: WebAssertion[]
  session_profile_id: number | null
  browser: 'CHROME'
  headless: boolean
  browser_config: {
    window_width: number
    window_height: number
    language: string | null
    user_agent: string | null
    proxy: {
      server: string
      username: string | null
      password: string | null
    } | null
    download_path: string | null
  }
  total_timeout_ms: number
  parameters: Record<string, unknown>
}

export interface WebCaseCreateRequest {
  project_id: number
  name: string
  content: WebCaseContent
  change_note?: string | null
}

export interface WebCaseVersionCreateRequest {
  content: WebCaseContent
  change_note: string
}

export interface WebCaseUpdateRequest {
  name?: string | null
}

export interface WebCaseResponse {
  id: number
  project_id: number
  code: string
  name: string
  status: WebCaseStatus
  current_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface WebCaseVersionResponse {
  id: number
  web_case_id: number
  version_no: number
  content: WebCaseContent
  change_note: string | null
  status: WebCaseVersionStatus
  approved_by: string | null
  approved_at: string | null
  created_by: string
  created_at: string
}

export interface WebCaseDetailResponse extends WebCaseResponse {
  current_version: WebCaseVersionResponse | null
}

export interface WebCaseListResponse {
  items: WebCaseResponse[]
  total: number
}

export interface WebPageCreateRequest {
  project_id: number
  code: string
  name: string
  url_pattern?: string | null
  description?: string | null
}

export interface WebPageUpdateRequest {
  name?: string | null
  url_pattern?: string | null
  description?: string | null
}

export interface WebPageResponse {
  id: number
  project_id: number
  code: string
  name: string
  url_pattern: string | null
  description: string | null
  status: WebAssetStatus
  created_by: string
  created_at: string
  updated_at: string
}

export interface WebElementCreateRequest {
  project_id: number
  page_id: number
  name: string
  description?: string | null
  element_type?: string
}

export interface WebElementUpdateRequest {
  name?: string | null
  description?: string | null
  element_type?: string | null
}

export interface ElementLocatorCreateRequest {
  strategy: LocatorStrategy
  value: string
  priority: number
  source?: LocatorSource
}

export interface WebElementVersionCreateRequest {
  description?: string | null
  element_type?: string
  locators: ElementLocatorCreateRequest[]
}

export interface ElementLocatorResponse {
  id: number
  element_version_id: number
  strategy: LocatorStrategy
  value: string
  priority: number
  source: LocatorSource
}

export interface WebElementVersionResponse {
  id: number
  element_id: number
  version_no: number
  description: string | null
  element_type: string
  locators: ElementLocatorResponse[]
  created_by: string
  created_at: string
}

export interface WebElementResponse {
  id: number
  project_id: number
  page_id: number
  name: string
  description: string | null
  element_type: string
  status: WebAssetStatus
  current_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface SessionProfileCreateRequest {
  project_id: number
  environment_id?: number | null
  name: string
  storage_state: Record<string, unknown>
  metadata?: Record<string, string>
  expires_at?: string | null
  refresh_ttl_seconds?: number
  login_web_case_id?: number | null
  login_web_case_version_id?: number | null
  expiry_condition?: SessionRecoveryCondition | null
  success_condition?: SessionRecoveryCondition | null
}

export interface SessionProfileUpdateRequest {
  name?: string | null
  storage_state?: Record<string, unknown> | null
  metadata?: Record<string, string> | null
  expires_at?: string | null
  status?: WebAssetStatus | null
  refresh_ttl_seconds?: number | null
  login_web_case_id?: number | null
  login_web_case_version_id?: number | null
  expiry_condition?: SessionRecoveryCondition | null
  success_condition?: SessionRecoveryCondition | null
}

export type SessionRecoveryCondition =
  | { type: 'URL_EQUALS' | 'URL_CONTAINS'; value: string; locator?: null }
  | { type: 'LOCATOR_VISIBLE' | 'LOCATOR_HIDDEN'; value?: null; locator: WebLocator }

export interface SessionProfileResponse {
  id: number
  project_id: number
  environment_id: number | null
  name: string
  status: WebAssetStatus
  metadata: Record<string, string> | null
  expires_at: string | null
  storage_state_fingerprint: string
  revision: number
  refresh_ttl_seconds: number
  login_web_case_id: number | null
  login_web_case_version_id: number | null
  expiry_condition: SessionRecoveryCondition | null
  success_condition: SessionRecoveryCondition | null
  created_by: string
  created_at: string
  updated_at: string
}
