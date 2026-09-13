<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, CopyDocument, Delete, Plus, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'

import {
  approveScenario, createScenario, createScenarioVersion, deleteScenario, executeScenarioPreview, getScenario,
  getScenarioAiBaseline, getScenarioPreviewDefaults, getScenarioPreviewProfile, getScenarios,
  saveScenarioPreviewProfile, validateScenario,
} from '@/api/scenarios'
import { getApiErrorMessage } from '@/api/http'
import { getProjects } from '@/api/projects'
import { createSecret, getSecrets } from '@/api/secrets'
import type { Project } from '@/types/project'
import type { Secret, SecretType } from '@/types/secret'
import type {
  Scenario, ScenarioDsl, ScenarioExecutionPreview, ScenarioNode, ScenarioNodeType,
  ScenarioPreviewMode, ScenarioPreviewProfile, ScenarioValidationIssue,
} from '@/types/scenario'
import type { RuntimeResponseSnapshot } from '@/types/test-case'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import ScenarioFlowEditor from '@/views/scenarios/components/ScenarioFlowEditor.vue'

const projects = ref<Project[]>([])
const route = useRoute()
const projectId = ref<number>()
const scenarios = ref<Scenario[]>([])
const selectedId = ref<number>()
const selectedVersionId = ref<number>()
const requestedScenarioId = ref<number>()
const name = ref('')
const dsl = ref<ScenarioDsl>(defaultDsl())
const savedEditorSignature = ref('')
const changeNote = ref('')
const loading = ref(false)
const saving = ref(false)
const approving = ref(false)
const deletingScenarioId = ref<number>()
const drawerVisible = ref(false)
const editingNode = ref<ScenarioNode>()
const configText = ref('{}')
const cleanupQueryText = ref('[]')
const cleanupHeadersText = ref('[]')
const cleanupCookiesText = ref('[]')
const cleanupBodyText = ref('{"type":"NONE"}')
const cleanupParamsText = ref('{}')
const issues = ref<ScenarioValidationIssue[]>([])
const addType = ref<ScenarioNodeType>('HTTP')
const editorView = ref<'FLOW' | 'LIST'>('FLOW')
const executionVisible = ref(false)
const executionLoading = ref(false)
const executionProfileLoading = ref(false)
const executionProfileSaving = ref(false)
const executionContext = ref('{\n  "base_url": "https://api.example.test"\n}')
const executionResponses = ref<Record<string, string>>({})
const executionSavedSignature = ref('')
const executionPassedSignature = ref('')
const executionPersisted = ref(false)
const executionSavedAt = ref<string | null>(null)
const executionOutcome = ref<'SUCCESS' | 'FAILURE' | 'CANCELLED' | 'TIMEOUT'>('SUCCESS')
const executionResult = ref<ScenarioExecutionPreview | null>(null)
const executionMode = ref<ScenarioPreviewMode>('FULL')
const executionTarget = ref<ScenarioNode>()
const executionError = ref('')
const restoringAiBaseline = ref(false)
const secrets = ref<Secret[]>([])
const secretDialogVisible = ref(false)
const secretCreating = ref(false)
const pendingSecretBinding = ref<CredentialBinding>()
const newSecretName = ref('')
const newSecretType = ref<SecretType>('PASSWORD')
const newSecretValue = ref('')

interface CredentialBinding {
  key: string
  node: ScenarioNode
  path: Array<string | number>
  displayPath: string
  fieldName: string
  kind: 'SECRET' | 'RUNTIME' | 'UNBOUND'
  secretName?: string
  runtimeName?: string
  expectedType: SecretType
}

const exactSecretReference = /^\{\{secret\.([A-Za-z_][A-Za-z0-9_.-]*)\}\}$/
const exactRuntimeReference = /^\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}$/
const sensitiveKeys = new Set([
  'authorization', 'cookie', 'password', 'secret', 'token', 'api_key',
  'access_token', 'refresh_token', 'credential',
])
const sensitiveMarkers = [
  'authorization', 'cookie', 'password', 'secret', 'token', 'api_key', 'access_key', 'credential',
]

const nodeTypes: ScenarioNodeType[] = [
  'HTTP', 'IF', 'ELSE', 'LOOP', 'WAIT', 'SET_VARIABLE', 'EXTRACT',
  'ASSERT_STATUS', 'ASSERT_JSONPATH', 'AI_ASSERTION', 'SQL_QUERY', 'SQL_EXECUTE',
  'SQL_CLEANUP', 'PYTHON_SCRIPT', 'API_CLEANUP',
]
const editable = computed(() => Boolean(projectId.value))
const selectedScenario = computed(() => scenarios.value.find((item) => item.id === selectedId.value) ?? null)
const executionHttpNodes = computed(() => dsl.value.nodes.filter((item) => item.type === 'HTTP'))
const editorSignature = computed(() => JSON.stringify({ name: name.value, dsl: dsl.value }))
const scenarioEditorDirty = computed(() => Boolean(selectedId.value)
  && editorSignature.value !== savedEditorSignature.value)
const executionDraftSignature = computed(() => JSON.stringify({
  context: executionContext.value,
  responses: Object.keys(executionResponses.value).sort().map((key) => [key, executionResponses.value[key]]),
  cleanupOutcome: executionOutcome.value,
}))
const executionDirty = computed(() => executionDraftSignature.value !== executionSavedSignature.value)
const executionCanSave = computed(() => Boolean(
  selectedId.value && selectedVersionId.value
  && executionPassedSignature.value === executionDraftSignature.value
  && !scenarioEditorDirty.value
  && (executionDirty.value || !executionPersisted.value),
))
const credentialBindings = computed<CredentialBinding[]>(() => {
  const bindings: CredentialBinding[] = []
  const extracted = new Set<string>()
  const seen = new Set<string>()

  function addBinding(
    node: ScenarioNode,
    path: Array<string | number>,
    value: unknown,
    fieldName: string,
  ): void {
    const key = `${node.id}:${path.join('.')}`
    if (seen.has(key)) return
    seen.add(key)
    const secretMatch = typeof value === 'string' ? exactSecretReference.exec(value) : null
    const runtimeMatch = typeof value === 'string' ? exactRuntimeReference.exec(value) : null
    const runtimeName = runtimeMatch && !secretMatch ? runtimeMatch[1] : undefined
    bindings.push({
      key,
      node,
      path,
      displayPath: `config.${path.map((item) => typeof item === 'number' ? `[${item}]` : item).join('.').replace('.[', '[')}`,
      fieldName,
      kind: secretMatch ? 'SECRET' : runtimeName && extracted.has(runtimeName) ? 'RUNTIME' : 'UNBOUND',
      secretName: secretMatch?.[1],
      runtimeName,
      expectedType: credentialSecretType(fieldName),
    })
  }

  function scan(
    node: ScenarioNode,
    value: unknown,
    path: Array<string | number>,
    cookieValues = false,
  ): void {
    if (Array.isArray(value)) {
      value.forEach((child, index) => scan(node, child, [...path, index], cookieValues))
      return
    }
    if (!value || typeof value !== 'object') return
    const record = value as Record<string, unknown>
    const namedField = typeof record.name === 'string' && isSensitiveField(record.name)
      ? record.name
      : undefined
    if (namedField) addBinding(node, [...path, 'value'], record.value, namedField)
    for (const [key, child] of Object.entries(record)) {
      if (key === 'value' && (namedField || cookieValues)) {
        if (!namedField && cookieValues) addBinding(node, [...path, key], child, 'cookie')
        continue
      }
      if (key === 'cookies' && Array.isArray(child)) {
        scan(node, child, [...path, key], true)
        continue
      }
      if (isSensitiveField(key)) {
        addBinding(node, [...path, key], child, key)
        continue
      }
      if (key === 'key_value' && String(record.type ?? '').toUpperCase() === 'API_KEY') {
        addBinding(node, [...path, key], child, 'api_key')
        continue
      }
      scan(node, child, [...path, key], false)
    }
  }

  for (const item of dsl.value.nodes) {
    if (item.type === 'HTTP') scan(item, item.config, [])
    if (item.type === 'EXTRACT' && typeof item.config.name === 'string') {
      extracted.add(item.config.name)
    }
  }
  return bindings
})
const unboundCredentialCount = computed(() => credentialBindings.value.filter(
  (item) => item.kind === 'UNBOUND'
    || (item.kind === 'SECRET' && !secrets.value.some(
      (secret) => secret.name === item.secretName && secret.enabled,
    )),
).length)
const {
  items: pagedScenarios, total: scenarioTotal, page: scenarioPage,
  pageSize: scenarioPageSize, changePage: changeScenarioPage,
  changePageSize: changeScenarioPageSize,
} = useClientPagination(scenarios)

function defaultDsl(): ScenarioDsl {
  return {
    version: '1.0',
    nodes: [
      node('start', 'START', '开始'),
      node('end', 'END', '结束'),
    ],
    settings: {
      stop_on_failure: true, cleanup_policy: 'ALWAYS',
      max_loop_iterations: 100, initial_variables: ['base_url'],
    },
  }
}

function normalizedField(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
}

function isSensitiveField(value: string): boolean {
  const normalized = normalizedField(value)
  return sensitiveKeys.has(normalized) || sensitiveMarkers.some((marker) => normalized.includes(marker))
}

function credentialSecretType(fieldName: string): SecretType {
  const normalized = normalizedField(fieldName)
  if (normalized.includes('password')) return 'PASSWORD'
  if (normalized.includes('api_key') || normalized.includes('access_key')) return 'API_KEY'
  return 'TOKEN'
}

function compatibleSecrets(binding: CredentialBinding): Secret[] {
  const allowed: Record<SecretType, SecretType[]> = {
    PASSWORD: ['PASSWORD'],
    TOKEN: ['TOKEN', 'CLIENT_SECRET'],
    API_KEY: ['API_KEY', 'CLIENT_SECRET'],
    DB_PASSWORD: ['DB_PASSWORD'],
    CLIENT_SECRET: ['CLIENT_SECRET'],
  }
  return secrets.value.filter(
    (secret) => secret.enabled && allowed[binding.expectedType].includes(secret.secret_type),
  )
}

function setCredentialReference(binding: CredentialBinding, secretName: string): void {
  let target: unknown = binding.node.config
  for (const part of binding.path.slice(0, -1)) {
    if (!target || typeof target !== 'object') return
    target = (target as Record<string | number, unknown>)[part]
  }
  const finalPart = binding.path.at(-1)
  if (finalPart === undefined || !target || typeof target !== 'object') return
  ;(target as Record<string | number, unknown>)[finalPart] = `{{secret.${secretName}}}`
  issues.value = []
}

function bindCredentialSelection(binding: CredentialBinding, value: unknown): void {
  if (typeof value === 'string' && value) setCredentialReference(binding, value)
}

function openCreateSecret(binding: CredentialBinding): void {
  pendingSecretBinding.value = binding
  const suffix = normalizedField(`${binding.node.id}_${binding.fieldName}`)
  newSecretName.value = `scenario_${suffix}`.slice(0, 128)
  newSecretType.value = binding.expectedType
  newSecretValue.value = ''
  secretDialogVisible.value = true
}

async function createAndBindSecret(): Promise<void> {
  if (!projectId.value || !pendingSecretBinding.value) return
  if (!newSecretName.value.trim() || !newSecretValue.value) {
    ElMessage.warning('请填写 Secret 名称和值')
    return
  }
  secretCreating.value = true
  try {
    const created = await createSecret({
      project_id: projectId.value,
      environment_id: null,
      name: newSecretName.value.trim(),
      secret_type: newSecretType.value,
      value: newSecretValue.value,
    })
    secrets.value = [...secrets.value, created].sort((a, b) => a.name.localeCompare(b.name))
    setCredentialReference(pendingSecretBinding.value, created.name)
    secretDialogVisible.value = false
    newSecretValue.value = ''
    ElMessage.success('项目通用 Secret 已创建并绑定；保存场景后生效')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '创建 Secret 失败'))
  } finally {
    secretCreating.value = false
  }
}

function credentialState(binding: CredentialBinding): { label: string; type: 'success' | 'warning' | 'danger' | 'info' } {
  if (binding.kind === 'RUNTIME') return { label: `前置 EXTRACT：${binding.runtimeName}`, type: 'info' }
  if (binding.kind === 'UNBOUND') return { label: '需要绑定', type: 'danger' }
  const secret = secrets.value.find((item) => item.name === binding.secretName)
  if (!secret) return { label: 'Secret 不存在', type: 'warning' }
  if (!secret.enabled) return { label: 'Secret 已停用', type: 'warning' }
  return { label: '已绑定', type: 'success' }
}

function node(id: string, type: ScenarioNodeType, label: string): ScenarioNode {
  return {
    id, type, name: label, description: null, enabled: true, parent_id: null, config: {},
    failure_policy: null, timeout_ms: 30000,
  }
}

function defaultConfig(type: ScenarioNodeType): Record<string, unknown> {
  if (type === 'HTTP') return { method: 'GET', url: '{{base_url}}/' }
  if (type === 'IF') return { condition: '{{status}} == "READY"' }
  if (type === 'LOOP') return { iterations: 1 }
  if (type === 'WAIT') return { duration_ms: 1000 }
  if (type === 'SET_VARIABLE') return { name: 'variable_name', value: '' }
  if (type === 'EXTRACT') return { name: 'result', source: 'JSONPATH', expression: '$.data' }
  if (type === 'ASSERT_STATUS') return { expected: 200 }
  if (type === 'AI_ASSERTION') return {
    prompt_id: 0, criteria: '响应满足预期业务语义', confidence_threshold: 0.8,
  }
  if (type === 'SQL_QUERY') return {
    connection_id: 0, sql: 'SELECT 1 AS value', params: {},
    result_variable: 'sql_rows', max_rows: 100,
  }
  if (type === 'SQL_CLEANUP') return {
    cleanup_type: 'SQL', connection_id: 0,
    sql: 'DELETE FROM resources WHERE id=%(resource_id)s',
    params: {}, resource_id_param: 'resource_id', enabled: true,
  }
  if (type === 'SQL_EXECUTE') return {
    connection_id: 0, sql: '', params: {}, result_variable: 'affected_rows',
  }
  if (type === 'PYTHON_SCRIPT') return {
    script: 'context["result"] = len(context["items"])',
  }
  if (type === 'API_CLEANUP') return {
    cleanup_type: 'API', method: 'DELETE', url: '{{base_url}}/resources/{{resource_id}}',
    query_params: [], headers: [], cookies: [], body: { type: 'NONE' }, auth: { type: 'NONE' },
    enabled: true,
  }
  return {}
}

async function loadList(): Promise<void> {
  if (!projectId.value) return
  loading.value = true
  try { scenarios.value = await getScenarios(projectId.value) }
  finally { loading.value = false }
}

function createNew(): void {
  selectedId.value = undefined
  selectedVersionId.value = undefined
  name.value = '新建 API 场景'
  dsl.value = defaultDsl()
  changeNote.value = ''
  issues.value = []
  savedEditorSignature.value = editorSignature.value
}

function applyScenarioDetail(detail: Scenario): void {
  selectedId.value = detail.id
  selectedVersionId.value = detail.current_version?.id
  name.value = detail.name
  dsl.value = structuredClone(detail.current_version?.dsl ?? defaultDsl())
  changeNote.value = ''
  issues.value = []
  savedEditorSignature.value = editorSignature.value
}

async function selectScenario(row: Scenario): Promise<void> {
  const detail = await getScenario(row.id)
  applyScenarioDetail(detail)
}

async function deleteScenarioItem(row: Scenario): Promise<void> {
  if (row.status !== 'DRAFT' || deletingScenarioId.value) return
  const unsavedHint = selectedId.value === row.id && scenarioEditorDirty.value
    ? '当前编辑区还有未保存修改，删除后也会一并丢失。'
    : ''
  try {
    await ElMessageBox.confirm(
      `${unsavedHint}删除会移除该草稿及其全部版本和已保存的 Preview 模拟数据，且无法撤销。确认继续吗？`,
      `删除 Scenario「${row.name}」？`,
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }

  deletingScenarioId.value = row.id
  try {
    await deleteScenario(row.id)
    if (selectedId.value === row.id) createNew()
    await loadList()
    ElMessage.success('草稿 Scenario 已删除')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '删除 Scenario 失败，请稍后重试'))
  } finally {
    deletingScenarioId.value = undefined
  }
}

function replaceScenarioInList(updated: Scenario): void {
  const index = scenarios.value.findIndex((item) => item.id === updated.id)
  if (index >= 0) scenarios.value.splice(index, 1, updated)
}

async function approveSelectedScenario(): Promise<void> {
  const scenario = selectedScenario.value
  if (!scenario || scenario.status !== 'DRAFT' || approving.value) return

  try {
    await ElMessageBox.confirm(
      '批准后该场景才可在运行中心执行。当前版本将作为正式执行版本；新建版本后会回到草稿状态，需要再次批准。确认批准执行吗？',
      `批准 Scenario「${scenario.name}」？`,
      {
        type: 'warning',
        confirmButtonText: '批准执行',
        cancelButtonText: '暂不批准',
      },
    )
  } catch {
    return
  }

  const scenarioId = scenario.id
  if (selectedId.value !== scenarioId || selectedScenario.value?.status !== 'DRAFT') return

  approving.value = true
  try {
    const approved = await approveScenario(scenarioId)
    replaceScenarioInList(approved)

    await loadList()
    if (selectedId.value === scenarioId) {
      const refreshed = await getScenario(scenarioId)
      if (selectedId.value === scenarioId) applyScenarioDetail(refreshed)
    }
    ElMessage.success('Scenario 已批准执行，当前状态为 APPROVED')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '批准 Scenario 失败，请稍后重试'))
  } finally {
    approving.value = false
  }
}

function addNode(): void {
  const id = `${addType.value.toLowerCase()}_${Date.now().toString(36)}`
  const next = node(id, addType.value, `${addType.value} 节点`)
  next.config = defaultConfig(addType.value)
  dsl.value.nodes.splice(Math.max(1, dsl.value.nodes.length - 1), 0, next)
}

function move(index: number, offset: number): void {
  const target = index + offset
  if (index <= 0 || index >= dsl.value.nodes.length - 1) return
  if (target <= 0 || target >= dsl.value.nodes.length - 1) return
  const [item] = dsl.value.nodes.splice(index, 1)
  if (item) dsl.value.nodes.splice(target, 0, item)
}

function moveById(nodeId: string, offset: number): void {
  const index = dsl.value.nodes.findIndex((item) => item.id === nodeId)
  if (index >= 0) move(index, offset)
}

function reorderNode(nodeId: string, targetId: string): void {
  const sourceIndex = dsl.value.nodes.findIndex((item) => item.id === nodeId)
  const targetIndex = dsl.value.nodes.findIndex((item) => item.id === targetId)
  if (sourceIndex <= 0 || sourceIndex >= dsl.value.nodes.length - 1 || targetIndex <= 0) return
  const [source] = dsl.value.nodes.splice(sourceIndex, 1)
  if (!source) return
  const nextTargetIndex = dsl.value.nodes.findIndex((item) => item.id === targetId)
  const insertAt = nextTargetIndex < 0 ? dsl.value.nodes.length - 1 : nextTargetIndex
  dsl.value.nodes.splice(Math.max(1, insertAt), 0, source)
  issues.value = []
}

function duplicateById(nodeId: string): void {
  const index = dsl.value.nodes.findIndex((item) => item.id === nodeId)
  if (index >= 0) duplicate(index)
}

function removeById(nodeId: string): void {
  const index = dsl.value.nodes.findIndex((item) => item.id === nodeId)
  if (index >= 0) remove(index)
}

function updateNode(nodeId: string, patch: Partial<ScenarioNode>): void {
  const target = dsl.value.nodes.find((item) => item.id === nodeId)
  if (!target) return
  Object.assign(target, patch)
  issues.value = []
}

function duplicate(index: number): void {
  const source = dsl.value.nodes[index]
  if (!source || ['START', 'END'].includes(source.type)) return
  const copy = structuredClone(source)
  copy.id = `${source.type.toLowerCase()}_${Date.now().toString(36)}`
  copy.name = `${source.name} - 副本`
  dsl.value.nodes.splice(index + 1, 0, copy)
}

function remove(index: number): void {
  const target = dsl.value.nodes[index]
  if (!target || ['START', 'END'].includes(target.type)) return
  dsl.value.nodes.splice(index, 1)
  for (const item of dsl.value.nodes) if (item.parent_id === target.id) item.parent_id = null
}

function editConfig(item: ScenarioNode): void {
  editingNode.value = item
  configText.value = JSON.stringify(item.config, null, 2)
  if (['API_CLEANUP', 'SQL_CLEANUP'].includes(item.type)) {
    cleanupQueryText.value = JSON.stringify(item.config.query_params ?? [], null, 2)
    cleanupHeadersText.value = JSON.stringify(item.config.headers ?? [], null, 2)
    cleanupCookiesText.value = JSON.stringify(item.config.cookies ?? [], null, 2)
    cleanupBodyText.value = JSON.stringify(item.config.body ?? { type: 'NONE' }, null, 2)
    cleanupParamsText.value = JSON.stringify(item.config.params ?? {}, null, 2)
  }
  drawerVisible.value = true
}

function cleanupPolicyValue(): string {
  return String(editingNode.value?.config.policy ?? dsl.value.settings.cleanup_policy)
}

function setCleanupPolicy(value: string | number | boolean): void {
  if (editingNode.value) editingNode.value.config.policy = String(value)
}

function cleanupEnabledValue(): boolean {
  return editingNode.value?.config.enabled !== false
}

function setCleanupEnabled(value: boolean): void {
  if (editingNode.value) editingNode.value.config.enabled = value
}

function applyConfig(): void {
  if (!editingNode.value) return
  try {
    if (['API_CLEANUP', 'SQL_CLEANUP'].includes(editingNode.value.type)) {
      const current = editingNode.value.config
      if (editingNode.value.type === 'API_CLEANUP') {
        const {
          connection_id, sql, params, resource_id_param, timeout_ms, result_variable, ...apiConfig
        } = current
        void connection_id; void sql; void params; void resource_id_param; void timeout_ms; void result_variable
        editingNode.value.config = {
          ...apiConfig,
          cleanup_type: 'API',
          query_params: JSON.parse(cleanupQueryText.value) as unknown,
          headers: JSON.parse(cleanupHeadersText.value) as unknown,
          cookies: JSON.parse(cleanupCookiesText.value) as unknown,
          body: JSON.parse(cleanupBodyText.value) as unknown,
        }
      } else {
        const {
          method, url, query_params, headers, cookies, body, auth, timeout_ms, result_variable, ...sqlConfig
        } = current
        void method; void url; void query_params; void headers; void cookies; void body; void auth; void timeout_ms; void result_variable
        editingNode.value.config = {
          ...sqlConfig,
          cleanup_type: 'SQL',
          params: JSON.parse(cleanupParamsText.value) as unknown,
        }
      }
    } else {
      editingNode.value.config = JSON.parse(configText.value) as Record<string, unknown>
    }
    drawerVisible.value = false
  } catch { ElMessage.error('节点配置必须是有效 JSON') }
}

async function runValidation(showSuccess = true): Promise<boolean> {
  if (!projectId.value || !name.value.trim()) { ElMessage.warning('请填写场景名称'); return false }
  const result = await validateScenario(projectId.value, name.value, dsl.value)
  issues.value = result.issues
  if (result.valid && showSuccess && result.issues.length) ElMessage.warning(`DSL 结构通过，但有 ${result.issues.length} 项凭据待处理`)
  else if (result.valid && showSuccess) ElMessage.success(`DSL 校验通过，共 ${result.node_count} 个节点`)
  if (!result.valid) ElMessage.error(`发现 ${result.issues.length} 个 DSL 问题`)
  return result.valid
}

async function save(): Promise<void> {
  if (!await runValidation(false) || !projectId.value) return
  saving.value = true
  try {
    if (selectedId.value) {
      if (!changeNote.value.trim()) { ElMessage.warning('创建新版本必须填写变更说明'); return }
      const version = await createScenarioVersion(selectedId.value, name.value, dsl.value, changeNote.value)
      selectedVersionId.value = version.id
      ElMessage.success('Scenario 新版本已保存')
    } else {
      const created = await createScenario(projectId.value, name.value, dsl.value)
      selectedId.value = created.id
      ElMessage.success('Scenario V1 已创建')
    }
    changeNote.value = ''
    await loadList()
    if (selectedId.value) applyScenarioDetail(await getScenario(selectedId.value))
  } finally { saving.value = false }
}

function nodeIssues(id: string): ScenarioValidationIssue[] {
  return issues.value.filter((item) => item.node_id === id)
}

function canDebugNode(item: ScenarioNode): boolean {
  return !['START', 'END', 'ELSE', 'IF', 'LOOP'].includes(item.type) && item.enabled
}

function effectiveFailurePolicy(item: ScenarioNode): 'STOP' | 'CONTINUE' | 'RETRY_ONCE' {
  if (item.failure_policy === 'RETRY_ONCE') return 'RETRY_ONCE'
  if (dsl.value.settings.stop_on_failure) return 'STOP'
  return item.failure_policy ?? 'CONTINUE'
}

function blankSnapshot(): RuntimeResponseSnapshot {
  return {
    status_code: 200, json_body: {}, headers: {}, cookies: {}, response_time_ms: 0,
  }
}

function localPreviewProfile(): ScenarioPreviewProfile {
  return {
    scenario_id: selectedId.value ?? 0,
    scenario_version_id: selectedVersionId.value ?? 0,
    context: { base_url: 'https://api.example.test' },
    responses_by_node: Object.fromEntries(
      executionHttpNodes.value.map((item) => [item.id, blankSnapshot()]),
    ),
    cleanup_outcome: 'SUCCESS', source: 'GENERATED', saved_at: null,
  }
}

function applyExecutionProfile(profile: ScenarioPreviewProfile, baseline: boolean): void {
  executionContext.value = JSON.stringify(profile.context, null, 2)
  executionResponses.value = Object.fromEntries(executionHttpNodes.value.map((item) => [
    item.id,
    JSON.stringify(profile.responses_by_node[item.id] ?? blankSnapshot(), null, 2),
  ]))
  executionOutcome.value = profile.cleanup_outcome
  executionPersisted.value = profile.source === 'SAVED'
  executionSavedAt.value = profile.saved_at
  executionResult.value = null
  executionPassedSignature.value = ''
  if (baseline) executionSavedSignature.value = executionDraftSignature.value
}

async function openExecution(mode: ScenarioPreviewMode = 'FULL', target?: ScenarioNode): Promise<void> {
  executionMode.value = mode
  executionTarget.value = target
  executionResult.value = null
  executionError.value = ''
  executionVisible.value = true
  executionProfileLoading.value = true
  try {
    const profile = selectedId.value
      ? await getScenarioPreviewProfile(selectedId.value)
      : localPreviewProfile()
    applyExecutionProfile(profile, true)
  } catch (error) {
    executionError.value = getApiErrorMessage(error, '读取 Preview 模拟数据失败')
  } finally {
    executionProfileLoading.value = false
  }
}

async function generateDefaultPreviewData(): Promise<void> {
  executionProfileLoading.value = true
  try {
    const profile = selectedId.value
      ? await getScenarioPreviewDefaults(selectedId.value)
      : localPreviewProfile()
    applyExecutionProfile(profile, false)
    ElMessage.success('已从既有 OpenAPI 与节点依赖生成默认模拟数据，未调用 AI')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '生成默认模拟数据失败'))
  } finally {
    executionProfileLoading.value = false
  }
}

function parseExecutionDraft(): {
  context: Record<string, unknown>
  responses: Record<string, RuntimeResponseSnapshot>
} {
  const context = JSON.parse(executionContext.value) as Record<string, unknown>
  const responses = Object.fromEntries(Object.entries(executionResponses.value).map(
    ([key, value]) => [key, JSON.parse(value) as RuntimeResponseSnapshot],
  ))
  return { context, responses }
}

async function executePreview(): Promise<void> {
  executionLoading.value = true
  executionError.value = ''
  try {
    if (!await runValidation(false) || !projectId.value) {
      executionError.value = '请先修复 DSL 校验问题'
      return
    }
    let context: Record<string, unknown>
    let responses: Record<string, RuntimeResponseSnapshot>
    try {
      ({ context, responses } = parseExecutionDraft())
    } catch {
      executionError.value = 'Context / 各 HTTP 节点的模拟响应必须是有效 JSON'
      return
    }
    executionResult.value = await executeScenarioPreview(
      projectId.value, dsl.value,
      context,
      responses,
      executionOutcome.value,
      executionMode.value,
      executionTarget.value?.id,
    )
    executionPassedSignature.value = executionResult.value.status === 'PASS'
      ? executionDraftSignature.value
      : ''
  } catch (error) {
    executionResult.value = null
    executionError.value = getApiErrorMessage(error, '执行预览失败')
    ElMessage.error(executionError.value)
  } finally { executionLoading.value = false }
}

async function saveExecutionProfile(): Promise<void> {
  if (!selectedId.value || !selectedVersionId.value || !executionCanSave.value) return
  executionProfileSaving.value = true
  try {
    const { context, responses } = parseExecutionDraft()
    const saved = await saveScenarioPreviewProfile(selectedId.value, {
      scenario_version_id: selectedVersionId.value,
      context,
      responses_by_node: responses,
      cleanup_outcome: executionOutcome.value,
    })
    applyExecutionProfile(saved, true)
    ElMessage.success('模拟数据已覆盖保存到当前场景版本')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '保存模拟数据失败'))
  } finally {
    executionProfileSaving.value = false
  }
}

async function closeExecution(done?: () => void): Promise<void> {
  if (executionDirty.value) {
    try {
      await ElMessageBox.confirm(
        '当前 Preview 有未保存修改，关闭后将丢弃。确认关闭吗？',
        '丢弃未保存的模拟数据？',
        { type: 'warning', confirmButtonText: '丢弃并关闭', cancelButtonText: '继续编辑' },
      )
    } catch { return }
  }
  if (done) done()
  else executionVisible.value = false
}

async function restoreAiBaseline(): Promise<void> {
  if (!selectedId.value || restoringAiBaseline.value) return
  try {
    await ElMessageBox.confirm(
      '将读取该场景创建时已保存的 AI 编排快照并替换当前编辑内容；不会再次调用模型，也不会自动保存。',
      '恢复 AI 原始编排？',
      { type: 'warning', confirmButtonText: '恢复到编辑区', cancelButtonText: '取消' },
    )
  } catch { return }
  restoringAiBaseline.value = true
  try {
    const baseline = await getScenarioAiBaseline(selectedId.value)
    dsl.value = structuredClone(baseline.dsl)
    changeNote.value = '恢复 AI 原始编排'
    issues.value = []
    ElMessage.success('已恢复 AI 原始编排到编辑区；确认后请保存新版本')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '当前场景没有可恢复的 AI 原始编排'))
  } finally {
    restoringAiBaseline.value = false
  }
}

function technicalDescription(item: ScenarioNode): string {
  if (item.type === 'HTTP') return `${String(item.config.method ?? 'GET')} ${String(item.config.url ?? '')}`
  if (item.type === 'EXTRACT') return `从上一接口响应 ${String(item.config.expression ?? '')} 提取为 ${String(item.config.name ?? '变量')}`
  if (item.type === 'ASSERT_STATUS') return `校验上一接口响应状态码为 ${String(item.config.expected ?? '')}`
  if (item.type === 'ASSERT_JSONPATH') return `校验上一接口响应 ${String(item.config.expression ?? '')}`
  if (item.type === 'API_CLEANUP' || item.type === 'SQL_CLEANUP') return '按清理策略回收本场景创建的测试数据'
  if (item.type === 'START') return '场景执行起点'
  if (item.type === 'END') return '场景执行终点'
  return '该节点暂未提供业务描述'
}

onMounted(async () => {
  projects.value = (await getProjects()).items
  const requestedProjectId = Number(route.query.project_id)
  const scenarioId = Number(route.query.scenario_id)
  requestedScenarioId.value = Number.isInteger(scenarioId) && scenarioId > 0
    ? scenarioId
    : undefined
  projectId.value = projects.value.some((item) => item.id === requestedProjectId)
    ? requestedProjectId
    : projects.value[0]?.id
})
watch(projectId, async () => {
  createNew()
  await Promise.all([
    loadList(),
    projectId.value ? getSecrets(projectId.value).then((items) => { secrets.value = items }) : Promise.resolve(),
  ])
  const scenarioId = requestedScenarioId.value
  requestedScenarioId.value = undefined
  if (!scenarioId) return
  const index = scenarios.value.findIndex((item) => item.id === scenarioId)
  if (index < 0) return
  scenarioPage.value = Math.floor(index / scenarioPageSize.value) + 1
  await selectScenario(scenarios.value[index]!)
})
</script>

<template>
  <div class="scenario-page">
    <header class="page-heading scenario-heading"><div><span class="eyebrow dark">API 场景 DSL</span><h1>测试编排</h1><p>流程图与列表双视图编排，共享版本化 DSL、父子作用域、节点策略和执行语义。</p></div><div class="heading-actions"><el-select v-model="projectId" placeholder="选择项目" style="width:220px"><el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" /></el-select><el-button :icon="Refresh" @click="loadList">刷新</el-button><el-button type="primary" :icon="Plus" @click="createNew">新建场景</el-button></div></header>
    <div class="scenario-layout">
      <aside class="scenario-list" v-loading="loading"><div v-for="item in pagedScenarios" :key="item.id" :class="['scenario-list-item',{active:selectedId===item.id}]" @click="selectScenario(item)"><div class="scenario-list-item-header"><code>{{ item.code }}</code><el-button v-if="item.status === 'DRAFT'" link type="danger" :icon="Delete" :loading="deletingScenarioId === item.id" title="删除草稿" aria-label="删除草稿" @click.stop="deleteScenarioItem(item)" /></div><strong>{{ item.name }}</strong><span>{{ item.status }}</span></div><el-empty v-if="!scenarios.length" description="暂无场景" :image-size="70" /><el-pagination v-if="scenarioTotal" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="scenarioPage" :page-size="scenarioPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="scenarioTotal" @current-change="changeScenarioPage" @size-change="changeScenarioPageSize" /></aside>
      <main class="scenario-editor">
        <div class="editor-toolbar"><el-input v-model="name" placeholder="场景名称" maxlength="255" /><el-segmented v-model="editorView" :options="[{label:'流程图',value:'FLOW'},{label:'列表',value:'LIST'}]" /><el-select v-model="addType" style="width:190px"><el-option v-for="type in nodeTypes" :key="type" :value="type" /></el-select><el-button :icon="Plus" @click="addNode">添加节点</el-button><el-button @click="runValidation()">校验</el-button><el-button type="success" plain @click="openExecution()">配置并预览完整流程</el-button><el-button v-if="selectedId" :icon="RefreshLeft" :loading="restoringAiBaseline" @click="restoreAiBaseline">恢复 AI 原始编排</el-button><el-button v-if="selectedScenario?.status === 'DRAFT'" type="warning" :loading="approving" :disabled="saving || scenarioEditorDirty" :title="scenarioEditorDirty ? '请先保存当前修改，再批准新版本' : ''" @click="approveSelectedScenario">批准执行</el-button><el-button type="primary" :loading="saving" :disabled="!editable || approving" @click="save">{{ selectedId ? '保存新版本' : '创建 V1' }}</el-button></div>
        <section class="scenario-settings">
          <div class="setting-field"><span class="setting-label">主流程失败控制</span><el-switch v-model="dsl.settings.stop_on_failure" active-text="最终失败后停止" inactive-text="允许节点自行决定" /><small>开启后，所有普通节点最终失败都会停止后续主流程；节点可先重试一次，Cleanup 仍独立执行。</small></div>
          <div class="setting-field"><span class="setting-label">数据清理策略</span><el-select v-model="dsl.settings.cleanup_policy"><el-option label="始终清理" value="ALWAYS" /><el-option label="仅成功时清理" value="ON_SUCCESS" /><el-option label="失败、取消或超时时清理" value="ON_FAILURE" /><el-option label="不清理" value="NEVER" /></el-select><small>决定场景结束后何时执行 Cleanup 节点。</small></div>
          <div class="setting-field initial-variables"><span class="setting-label">初始 Runtime 变量</span><el-input-tag v-model="dsl.settings.initial_variables" placeholder="变量名，回车添加" /><small><code>base_url</code> 是运行环境提供的接口根地址变量，不是网址选择器。</small></div>
          <div v-if="selectedId" class="setting-field change-note"><span class="setting-label">版本变更说明</span><el-input v-model="changeNote" placeholder="保存新版本前必填" /><small>每次保存创建不可变的新版本。</small></div>
        </section>
        <section v-if="credentialBindings.length" class="credential-bindings">
          <div class="credential-bindings-heading"><div><strong>敏感凭据绑定</strong><p>真实值只保存在项目 Secret 中，不会写入场景 DSL 或发送给 AI。</p></div><el-tag :type="unboundCredentialCount ? 'warning' : 'success'">{{ unboundCredentialCount ? `${unboundCredentialCount} 项待处理` : '配置完整' }}</el-tag></div>
          <div v-for="binding in credentialBindings" :key="binding.key" class="credential-binding-row">
            <div class="credential-binding-location"><strong>{{ binding.node.name }}</strong><code>{{ binding.displayPath }}</code></div>
            <el-tag :type="credentialState(binding).type">{{ credentialState(binding).label }}</el-tag>
            <template v-if="binding.kind !== 'RUNTIME'">
              <el-select :model-value="binding.secretName" placeholder="选择已有 Secret" @update:model-value="bindCredentialSelection(binding, $event)">
                <el-option v-for="secret in compatibleSecrets(binding)" :key="secret.id" :value="secret.name" :label="`${secret.name} · ${secret.secret_type}${secret.environment_id ? ' · 环境专用' : ' · 项目通用'}`" />
              </el-select>
              <el-button @click="openCreateSecret(binding)">新建并绑定</el-button>
            </template>
            <span v-else class="credential-runtime-hint">运行时值来自前置提取节点，无需保存为 Secret</span>
          </div>
        </section>
        <el-alert v-if="issues.length" :title="`DSL 校验发现 ${issues.length} 个问题`" :type="issues.some((item) => item.severity === 'ERROR') ? 'error' : 'warning'" :closable="false" show-icon><template #default><div v-for="issue in issues" :key="`${issue.code}-${issue.node_id}`">[{{ issue.code }}] {{ issue.node_id ? `${issue.node_id}：` : '' }}{{ issue.message }}</div></template></el-alert>
        <ScenarioFlowEditor
          v-if="editorView === 'FLOW'"
          :nodes="dsl.nodes"
          :issues="issues"
          :stop-on-failure="dsl.settings.stop_on_failure"
          @edit="editConfig"
          @move="moveById"
          @reorder="reorderNode"
          @duplicate="duplicateById"
          @remove="removeById"
          @update-node="updateNode"
          @debug="openExecution"
        />
        <div v-else class="node-list">
          <article v-for="(item,index) in dsl.nodes" :key="item.id" :class="['node-card',{disabled:!item.enabled,error:nodeIssues(item.id).length,structure:['START','END'].includes(item.type)}]" :style="{marginLeft:item.parent_id?'32px':'0'}">
            <div class="node-main-row"><div class="node-index">{{ index + 1 }}</div><el-tag class="node-type" effect="dark" :title="item.type">{{ item.type }}</el-tag><div class="node-identity"><span class="node-field-label">节点名称</span><el-input v-model="item.name" /><p>{{ item.description || technicalDescription(item) }}</p></div><label v-if="!['START','END'].includes(item.type)" class="node-control parent-control"><span>父节点</span><el-select v-model="item.parent_id" clearable placeholder="无（顶层）"><el-option v-for="parent in dsl.nodes.filter((node)=>['IF','ELSE','LOOP'].includes(node.type)&&node.id!==item.id)" :key="parent.id" :label="parent.name" :value="parent.id" /></el-select></label><label v-if="!['START','END'].includes(item.type)" class="node-control policy-control"><span>节点策略</span><el-select v-model="item.failure_policy" clearable placeholder="跟随场景"><el-option label="跟随场景" :value="null" /><el-option label="失败停止" value="STOP" /><el-option label="失败继续" value="CONTINUE" :disabled="dsl.settings.stop_on_failure" /><el-option label="重试一次" value="RETRY_ONCE" /></el-select><small v-if="dsl.settings.stop_on_failure && item.failure_policy === 'CONTINUE'">实际执行：场景强制停止</small><small v-else>实际执行：{{ effectiveFailurePolicy(item) }}</small></label><label v-if="!['START','END'].includes(item.type)" class="node-enabled"><span>启用</span><el-switch v-model="item.enabled" /></label><div class="node-actions"><el-button link :icon="ArrowUp" @click="move(index,-1)" /><el-button link :icon="ArrowDown" @click="move(index,1)" /><el-button link :icon="CopyDocument" @click="duplicate(index)" /><el-button link type="danger" :icon="Delete" @click="remove(index)" /></div></div><div class="node-secondary-row"><el-button v-if="!['START','END'].includes(item.type)" link type="primary" @click="editConfig(item)">配置与描述</el-button><div v-if="canDebugNode(item)" class="node-debug-actions"><el-button link type="success" @click="openExecution('NODE', item)">测试节点</el-button><el-button link type="warning" @click="openExecution('RUN_TO_HERE', item)">运行到此</el-button><el-button link type="primary" @click="openExecution('RUN_FROM_HERE', item)">从此处运行</el-button></div><el-tag v-else-if="!['START','END'].includes(item.type)" type="info">结构节点随正常流程执行</el-tag></div>
            <div v-if="nodeIssues(item.id).length" class="node-errors">{{ nodeIssues(item.id).map((issue)=>issue.message).join('；') }}</div>
          </article>
        </div>
      </main>
    </div>
    <el-drawer v-model="drawerVisible" :title="`${editingNode?.type ?? ''} 节点配置`" size="48%"><template v-if="editingNode"><el-form label-position="top"><el-form-item label="节点 ID"><el-input v-model="editingNode.id" /></el-form-item><el-form-item label="节点描述"><el-input v-model="editingNode.description" type="textarea" :rows="3" placeholder="说明节点目的、输入来源、输出变量或断言意图" maxlength="1000" show-word-limit /></el-form-item><el-form-item label="超时（毫秒）"><el-input-number v-model="editingNode.timeout_ms" :min="100" :max="['API_CLEANUP', 'SQL_CLEANUP'].includes(editingNode.type) ? 120000 : 600000" /></el-form-item><template v-if="['API_CLEANUP', 'SQL_CLEANUP'].includes(editingNode.type)"><el-alert title="Cleanup 与 Case 使用同一 DSL：只保存模板、参数和 Secret 引用；API 节点仅在 Preview 中渲染/校验/计划，不执行真实网络。" type="info" :closable="false" show-icon /><div class="cleanup-form-grid"><el-form-item label="Cleanup 策略"><el-select :model-value="cleanupPolicyValue()" @update:model-value="setCleanupPolicy"><el-option label="始终" value="ALWAYS" /><el-option label="成功时" value="ON_SUCCESS" /><el-option label="失败/取消/超时时" value="ON_FAILURE" /><el-option label="不清理" value="NEVER" /></el-select></el-form-item><el-form-item label="启用"><el-switch :model-value="cleanupEnabledValue()" @update:model-value="setCleanupEnabled" /></el-form-item><el-form-item v-if="editingNode.type === 'API_CLEANUP'" label="方法"><el-select v-model="editingNode.config.method"><el-option v-for="method in ['DELETE', 'POST', 'PUT', 'PATCH']" :key="method" :value="method" /></el-select></el-form-item><el-form-item v-if="editingNode.type === 'API_CLEANUP'" label="URL" class="field-span-all"><el-input v-model="editingNode.config.url" placeholder="{{base_url}}/resources/{{resource_id}}" /></el-form-item><el-form-item v-if="editingNode.type === 'SQL_CLEANUP'" label="Connection ID"><el-input-number v-model="editingNode.config.connection_id" :min="1" /></el-form-item><el-form-item v-if="editingNode.type === 'SQL_CLEANUP'" label="Resource ID 参数"><el-input v-model="editingNode.config.resource_id_param" /></el-form-item><el-form-item v-if="editingNode.type === 'SQL_CLEANUP'" label="参数化 SQL" class="field-span-all"><el-input v-model="editingNode.config.sql" type="textarea" :rows="4" class="code-input" /></el-form-item><el-form-item label="Query 参数 JSON" v-if="editingNode.type === 'API_CLEANUP'" class="field-span-all"><el-input v-model="cleanupQueryText" type="textarea" :rows="3" class="code-input" /></el-form-item><el-form-item label="Headers JSON（敏感值只能使用 {{variable}}）" v-if="editingNode.type === 'API_CLEANUP'" class="field-span-all"><el-input v-model="cleanupHeadersText" type="textarea" :rows="3" class="code-input" /></el-form-item><el-form-item label="Cookies JSON" v-if="editingNode.type === 'API_CLEANUP'" class="field-span-all"><el-input v-model="cleanupCookiesText" type="textarea" :rows="3" class="code-input" /></el-form-item><el-form-item label="Body JSON" v-if="editingNode.type === 'API_CLEANUP'" class="field-span-all"><el-input v-model="cleanupBodyText" type="textarea" :rows="4" class="code-input" /></el-form-item><el-form-item v-if="editingNode.type === 'SQL_CLEANUP'" label="Params JSON（只作为参数值）" class="field-span-all"><el-input v-model="cleanupParamsText" type="textarea" :rows="4" class="code-input" /></el-form-item></div></template><el-form-item v-else label="Config JSON"><el-input v-model="configText" type="textarea" :rows="20" class="code-input" /></el-form-item></el-form><el-button type="primary" @click="applyConfig">应用配置</el-button></template></el-drawer>
    <el-dialog v-model="executionVisible" title="Scenario 离线预览" width="1180px" :before-close="closeExecution" destroy-on-close>
      <div v-loading="executionProfileLoading">
        <div class="execution-toolbar">
          <el-tag>模式：{{ executionMode }}</el-tag>
          <el-tag v-if="executionTarget">目标：{{ executionTarget.name }}</el-tag>
          <el-tag type="info">离线模拟，不访问真实接口</el-tag>
          <el-tag :type="executionDirty ? 'warning' : executionPersisted ? 'success' : 'info'">{{ executionDirty ? '已修改，未保存' : executionPersisted ? '模拟数据已保存' : '默认模拟数据，未保存' }}</el-tag>
          <span v-if="executionSavedAt" class="saved-at">上次保存：{{ executionSavedAt }}</span>
          <el-button :icon="Refresh" @click="generateDefaultPreviewData">生成默认模拟数据</el-button>
        </div>
        <el-alert :title="scenarioEditorDirty ? '当前场景编排尚未保存：可以手动运行预览，但要先保存场景新版本，才能保存该版本的模拟数据。' : '模拟数据已加载，进入弹窗不会自动执行；检查或调整后，请手动点击“运行完整流程预览”。'" description="“保存本次模拟数据”只保存 Context、各 HTTP 节点模拟响应和 Cleanup 假定结果，供下次预览复用；不会修改外面的节点编排。只有本次预览通过且数据未再改变时才能保存。" :type="scenarioEditorDirty ? 'warning' : 'info'" :closable="false" show-icon />
        <div class="execution-profile-grid">
          <el-form-item label="初始 Runtime Context JSON">
            <el-input v-model="executionContext" type="textarea" :rows="9" class="code-input" />
          </el-form-item>
          <div class="response-fixtures">
            <div class="response-fixtures-title">各 HTTP 节点模拟响应</div>
            <el-empty v-if="!executionHttpNodes.length" description="当前场景没有 HTTP 节点" :image-size="55" />
            <el-collapse v-else>
              <el-collapse-item v-for="item in executionHttpNodes" :key="item.id" :name="item.id">
                <template #title><el-tag size="small">{{ String(item.config.method ?? 'HTTP') }}</el-tag><strong>{{ item.name }}</strong><code>{{ item.id }}</code></template>
                <el-input v-model="executionResponses[item.id]" type="textarea" :rows="10" class="code-input" />
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
        <el-form-item label="预览时假定的场景结果（用于决定 Cleanup 是否执行）" class="cleanup-outcome-field"><el-select v-model="executionOutcome"><el-option label="成功 SUCCESS" value="SUCCESS" /><el-option label="失败 FAILURE" value="FAILURE" /><el-option label="已取消 CANCELLED" value="CANCELLED" /><el-option label="超时 TIMEOUT" value="TIMEOUT" /></el-select></el-form-item>
        <el-alert v-if="executionError" :title="executionError" type="error" :closable="false" show-icon />
        <el-alert v-if="executionResult" :title="`整体状态：${executionResult.status}｜停止原因：${executionResult.stop_reason ?? '无'}`" :description="executionResult.stop_message ?? undefined" :type="executionResult.status === 'FAIL' ? 'error' : executionResult.status === 'REVIEW' ? 'warning' : 'success'" :closable="false" show-icon />
        <el-empty v-if="!executionResult && !executionLoading && !executionError" description="尚未执行；请检查模拟数据后手动运行完整流程预览" :image-size="70" />
        <el-table v-if="executionResult" :data="executionResult.traces" max-height="390"><el-table-column prop="sequence" label="#" width="55" /><el-table-column prop="node_type" label="类型" width="130" /><el-table-column prop="node_id" label="节点" min-width="150" /><el-table-column prop="status" label="状态" width="90" /><el-table-column label="尝试" width="80"><template #default="{row}">{{ row.attempt }} / {{ row.max_attempts }}</template></el-table-column><el-table-column prop="duration_ms" label="耗时 ms" width="90" /><el-table-column prop="timeout_ms" label="Timeout ms" width="100" /><el-table-column prop="failure_policy" label="实际失败策略" width="120" /><el-table-column label="循环路径" width="120"><template #default="{row}">{{ row.iteration_path.join(' / ') || '-' }}</template></el-table-column><el-table-column label="错误" min-width="250"><template #default="{row}">{{ row.error_code ?? '-' }}{{ row.message ? `：${row.message}` : '' }}</template></el-table-column></el-table>
        <el-table v-if="executionResult?.assertion_results?.length" :data="executionResult.assertion_results" max-height="240" class="scenario-assertions"><el-table-column prop="sequence" label="#" width="55" /><el-table-column prop="name" label="名称" min-width="140" /><el-table-column prop="status" label="状态" width="90" /><el-table-column label="Expected / Actual" min-width="220"><template #default="{row}"><code>{{ JSON.stringify(row.expected) }} / {{ JSON.stringify(row.actual) }}</code></template></el-table-column><el-table-column prop="message" label="Message" min-width="250" /></el-table>
        <pre v-if="executionResult" class="execution-context-result">{{ JSON.stringify(executionResult.context, null, 2) }}</pre>
      </div>
      <template #footer><el-button @click="closeExecution()">关闭</el-button><el-button v-if="selectedId" type="success" plain :loading="executionProfileSaving" :disabled="!executionCanSave" title="保存当前 Context、各 HTTP 模拟响应和 Cleanup 假定结果；不会修改场景节点编排" @click="saveExecutionProfile">保存本次模拟数据</el-button><el-button type="primary" :loading="executionLoading" @click="executePreview">{{ executionMode === 'FULL' ? '运行完整流程预览' : '执行调试' }}</el-button></template>
    </el-dialog>
    <el-dialog v-model="secretDialogVisible" title="新建并绑定项目 Secret" width="520px" destroy-on-close @closed="newSecretValue = ''">
      <el-alert title="Secret 将创建为项目通用凭据；真实值加密保存，不会回填显示，也不会发送给 AI。" type="info" :closable="false" show-icon />
      <el-form label-position="top" class="secret-create-form">
        <el-form-item label="Secret 名称"><el-input v-model="newSecretName" maxlength="128" placeholder="例如 demo-login-password" /></el-form-item>
        <el-form-item label="类型"><el-select v-model="newSecretType"><el-option label="密码 PASSWORD" value="PASSWORD" /><el-option label="Token TOKEN" value="TOKEN" /><el-option label="API Key" value="API_KEY" /><el-option label="Client Secret" value="CLIENT_SECRET" /></el-select></el-form-item>
        <el-form-item label="真实值"><el-input v-model="newSecretValue" type="password" show-password autocomplete="new-password" placeholder="仅本次提交使用" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="secretDialogVisible = false">取消</el-button><el-button type="primary" :loading="secretCreating" @click="createAndBindSecret">创建并绑定</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.scenario-heading,.heading-actions,.editor-toolbar,.execution-toolbar{display:flex;align-items:center;gap:10px}.scenario-heading{justify-content:space-between}.scenario-layout{display:grid;grid-template-columns:260px 1fr;gap:16px;margin-top:18px}.scenario-list,.scenario-editor{min-height:600px;background:#fff;border:1px solid #e4e7ed;border-radius:10px}.scenario-list{padding:10px}.scenario-list-item{display:grid;gap:5px;padding:12px;border-radius:7px;cursor:pointer}.scenario-list-item-header{display:flex;align-items:center;justify-content:space-between;min-height:24px}.scenario-list-item-header .el-button{margin:-6px -6px -6px 0}.scenario-list-item:hover,.scenario-list-item.active{background:#eef4ff}.scenario-list-item span,.scenario-list-item code{font-size:12px;color:#7a8599}.scenario-editor{padding:16px;min-width:0}.editor-toolbar{flex-wrap:wrap}.editor-toolbar> :first-child{flex:1;min-width:260px}.scenario-settings{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;padding:14px;border:1px solid #e4e7ed;border-radius:9px;background:#fafbfd}.setting-field{display:grid;grid-template-columns:minmax(130px,auto) 1fr;align-items:center;gap:6px 12px}.setting-label{font-weight:600;color:#303133}.setting-field small{grid-column:1/-1;color:#7a8599;line-height:1.5}.initial-variables,.change-note{grid-column:1/-1}.flow-editor-shell{margin-top:14px}.node-list{display:grid;gap:10px;margin-top:14px}.node-card{position:relative;padding:12px;border:1px solid #dcdfe6;border-radius:8px}.node-card.error{border-color:#f56c6c;background:#fff7f7}.node-card.disabled{opacity:.55}.node-main-row{display:flex;align-items:flex-start;gap:10px}.node-identity{flex:1;min-width:220px}.node-identity p{margin:6px 2px 0;color:#7a8599;font-size:12px;line-height:1.45}.node-index{flex:0 0 24px;width:24px;height:24px;display:grid;place-items:center;border-radius:50%;background:#edf1f7;font-size:12px}.node-control{display:grid;gap:4px;width:155px;color:#7a8599;font-size:11px}.node-control small{color:#909399}.policy-control{width:165px}.node-enabled{display:grid;justify-items:center;gap:4px;color:#7a8599;font-size:11px}.node-actions,.node-debug-actions,.node-secondary-row{display:flex;align-items:center}.node-actions{white-space:nowrap}.node-secondary-row{gap:12px;margin:8px 0 0 68px}.node-debug-actions{flex-wrap:wrap}.node-errors{margin:8px 0 0 68px;color:#f56c6c;font-size:11px}.code-input :deep(textarea){font-family:JetBrains Mono,Consolas,monospace}.execution-toolbar{margin-bottom:14px;flex-wrap:wrap}.execution-toolbar .saved-at{color:#7a8599;font-size:12px}.execution-toolbar .el-button{margin-left:auto}.execution-profile-grid{display:grid;grid-template-columns:minmax(330px,.85fr) minmax(440px,1.15fr);gap:16px;margin-top:14px}.response-fixtures{min-width:0}.response-fixtures-title{margin-bottom:9px;color:#606266;font-weight:600}.response-fixtures :deep(.el-collapse-item__title){gap:8px}.response-fixtures :deep(.el-collapse-item__content){padding:0 8px 14px}.cleanup-outcome-field{margin-top:10px}.scenario-assertions{margin-top:14px}.execution-context-result{max-height:180px;overflow:auto;padding:10px;border-radius:6px;background:#f6f8fb}.scenario-page :deep(.el-dialog__body){max-height:calc(100vh - 190px);overflow:auto}
 .credential-bindings{display:grid;gap:10px;margin:0 0 14px;padding:14px;border:1px solid #e6a23c;border-radius:9px;background:#fffaf0}.credential-bindings-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.credential-bindings-heading p{margin:4px 0 0;color:#7a8599;font-size:12px}.credential-binding-row{display:grid;grid-template-columns:minmax(230px,1fr) 130px minmax(250px,1fr) 110px;align-items:center;gap:10px;padding-top:10px;border-top:1px solid #f2dfbd}.credential-binding-location{display:grid;gap:4px;min-width:0}.credential-binding-location code{overflow:hidden;color:#7a8599;font-size:11px;text-overflow:ellipsis}.credential-runtime-hint{grid-column:3/5;color:#7a8599;font-size:12px}.secret-create-form{margin-top:16px}
@media(max-width:1350px){.node-main-row{flex-wrap:wrap}.node-identity{flex-basis:calc(100% - 125px)}.node-control{width:190px}.node-actions{margin-left:auto}}
@media(max-width:1100px){.scenario-heading{align-items:flex-start;flex-direction:column}.scenario-layout{grid-template-columns:1fr}.editor-toolbar{align-items:stretch;flex-direction:column}.scenario-settings,.execution-profile-grid{grid-template-columns:1fr}.setting-field,.initial-variables,.change-note{grid-column:1}.credential-binding-row{grid-template-columns:minmax(200px,1fr) 120px minmax(220px,1fr) 110px}.node-main-row{align-items:stretch}.node-identity{flex-basis:100%}.node-control{width:calc(50% - 8px)}.node-secondary-row,.node-errors{margin-left:0}}
.node-main-row{display:grid;grid-template-columns:24px 116px minmax(180px,1fr) 145px 155px 48px 112px;align-items:start;gap:10px}.node-index{grid-column:1}.node-type{grid-column:2;box-sizing:border-box;width:116px;justify-content:center;overflow:hidden;text-overflow:ellipsis}.node-index,.node-type{margin-top:20px}.node-identity{display:grid;grid-column:3;gap:4px;min-width:0}.node-field-label{color:#7a8599;font-size:11px;line-height:16px}.node-identity p{margin:2px 2px 0}.parent-control{grid-column:4}.policy-control{grid-column:5}.node-control{box-sizing:border-box;width:100%;min-width:0}.node-enabled{grid-column:6}.node-actions{grid-column:7;justify-content:flex-end;gap:2px;margin-left:0;padding-top:20px}.node-actions .el-button{margin-left:0}.node-card.structure .node-identity{grid-column:3/7}.node-secondary-row,.node-errors{margin-left:160px}.node-secondary-row{min-height:28px}
@media(max-width:900px){.node-main-row{grid-template-columns:24px 116px minmax(0,1fr) 112px}.node-identity{grid-column:3}.node-actions{grid-column:4}.parent-control,.policy-control{grid-column:3/5}.parent-control{grid-row:2}.policy-control{grid-row:3}.node-enabled{grid-column:2;grid-row:2/4;align-self:center}.node-card.structure .node-identity{grid-column:3}.node-card.structure .node-actions{grid-column:4}.node-secondary-row,.node-errors{margin-left:160px}}
@media(max-width:620px){.credential-bindings-heading{align-items:flex-start}.credential-binding-row{grid-template-columns:1fr}.credential-runtime-hint{grid-column:1}.node-main-row{grid-template-columns:24px 92px minmax(0,1fr)}.node-type{width:92px;font-size:10px}.node-identity,.parent-control,.policy-control,.node-card.structure .node-identity{grid-column:3}.node-actions,.node-card.structure .node-actions{grid-column:3;grid-row:auto;justify-content:flex-start;padding-top:0}.node-enabled{grid-column:2}.node-secondary-row,.node-errors{margin-left:0}}
</style>
