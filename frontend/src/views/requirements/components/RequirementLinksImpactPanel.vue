<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getApiErrorMessage } from '@/api/http'
import {
  createRequirementLink,
  getRequirementImpact,
  getRequirementLinks,
  getRequirementTraceability,
  removeRequirementLink,
} from '@/api/requirement-links'
import { getRequirementDiff } from '@/api/requirements'
import { getTestCases, getTestCaseVersions } from '@/api/test-cases'
import { getWebCases, getWebCaseVersions } from '@/api/web-assets'
import type { Project } from '@/types/project'
import type { Requirement, RequirementDiff, RequirementVersion } from '@/types/requirement'
import type {
  RequirementAssetType,
  RequirementImpactItem,
  RequirementImpactPage,
  RequirementImpactStatus,
  RequirementLink,
  RequirementLinkPage,
  RequirementTraceItem,
  RequirementTracePage,
} from '@/types/requirement-link'
import type { TestCaseAsset, TestCaseVersion } from '@/types/test-case'
import type { WebCaseResponse, WebCaseVersionResponse } from '@/types/web'
import {
  identityIsCurrent,
  isIdentityStorageEvent,
  readRequestIdentity,
  type RequestIdentity,
} from '@/utils/request-context'
import { parseApiDateTime } from '@/utils/datetime'

const props = defineProps<{
  project: Project
  requirement: Requirement
  versions: RequirementVersion[]
  canWrite: boolean
  initialVersionId?: number | null
}>()

const router = useRouter()
const route = useRoute()
const activeTab = ref<'links' | 'impact' | 'traceability'>('links')
const linkPage = ref<RequirementLinkPage | null>(null)
const linksLoading = ref(false)
const linksError = ref<string | null>(null)
const linkPageNumber = ref(1)
const pageSize = ref(10)
const testCases = ref<TestCaseAsset[]>([])
const webCases = ref<WebCaseResponse[]>([])
const assetsLoading = ref(false)
const assetsError = ref<string | null>(null)
const assetVersions = ref<Array<TestCaseVersion | WebCaseVersionResponse>>([])
const assetVersionsLoading = ref(false)
const assetVersionsError = ref<string | null>(null)
const saving = ref(false)
const confirmingRemoveIds = ref<number[]>([])
const removingIds = ref<number[]>([])
const form = reactive<{
  assetType: RequirementAssetType
  assetId: number | null
  requirementVersionId: number | null
  assetVersionId: number | null
  confidence: number
}>({
  assetType: 'TEST_CASE',
  assetId: null,
  requirementVersionId: props.initialVersionId ?? props.requirement.current_version_id,
  assetVersionId: null,
  confidence: 1,
})

const impact = ref<RequirementImpactPage | null>(null)
const impactDiff = ref<RequirementDiff | null>(null)
const impactLoading = ref(false)
const impactError = ref<string | null>(null)
const impactPageNumber = ref(1)
const impactSelection = reactive<{ from: number | null; to: number | null }>({
  from: null,
  to: props.initialVersionId ?? props.requirement.current_version_id,
})
const tracePage = ref<RequirementTracePage | null>(null)
const traceLoading = ref(false)
const traceError = ref<string | null>(null)
const tracePageNumber = ref(1)

let alive = true
let linksSequence = 0
let assetsSequence = 0
let versionsSequence = 0
let impactSequence = 0
let traceSequence = 0
let mutationGeneration = 0
let nextWriteOperationId = 0
const activeWriteOperationId = ref<number | null>(null)

const assetOptions = computed(() => (
  form.assetType === 'TEST_CASE' ? testCases.value : webCases.value
).filter((item) => item.status !== 'ARCHIVED'))
const selectedAsset = computed(() => assetOptions.value.find((item) => item.id === form.assetId) ?? null)
const selectedRequirementVersion = computed(() => (
  props.versions.find((item) => item.id === form.requirementVersionId) ?? null
))
const selectedAssetVersion = computed(() => (
  assetVersions.value.find((item) => item.id === form.assetVersionId) ?? null
))
const formComplete = computed(() => Boolean(
  form.assetId && form.requirementVersionId && form.assetVersionId,
))
const writeBusy = computed(() => activeWriteOperationId.value !== null)
const submitSummary = computed(() => {
  const kind = form.assetType === 'TEST_CASE' ? '原 TestCase' : '独立 WebCase'
  const requirementVersion = selectedRequirementVersion.value
  const assetVersion = selectedAssetVersion.value
  return `${props.requirement.code} V${requirementVersion?.version_no ?? '?'} → ${kind}「${selectedAsset.value?.name ?? '未选择'}」V${assetVersion?.version_no ?? '?'}`
})

function startIdentity(): RequestIdentity | null {
  return readRequestIdentity()
}

function contextCurrent(
  identity: RequestIdentity,
  requirementId = props.requirement.id,
  projectId = props.project.id,
): boolean {
  return alive
    && identityIsCurrent(identity)
    && props.requirement.id === requirementId
    && props.project.id === projectId
}

function beginWriteOperation(): number | null {
  if (writeBusy.value) return null
  const operationId = ++nextWriteOperationId
  activeWriteOperationId.value = operationId
  return operationId
}

function operationCurrent(operationId: number, generation: number): boolean {
  return activeWriteOperationId.value === operationId && mutationGeneration === generation
}

function finishWriteOperation(operationId: number): void {
  if (activeWriteOperationId.value !== operationId) return
  saving.value = false
  confirmingRemoveIds.value = []
  removingIds.value = []
  activeWriteOperationId.value = null
}

function invalidateWriteContext(): void {
  mutationGeneration += 1
  activeWriteOperationId.value = null
  saving.value = false
  confirmingRemoveIds.value = []
  removingIds.value = []
}

async function loadLinks(page = linkPageNumber.value): Promise<void> {
  const identity = startIdentity()
  if (!identity) return
  const requirementId = props.requirement.id
  const projectId = props.project.id
  const sequence = ++linksSequence
  linksLoading.value = true
  linksError.value = null
  try {
    const result = await getRequirementLinks(requirementId, page, pageSize.value, true)
    if (sequence !== linksSequence || !contextCurrent(identity, requirementId, projectId)) return
    linkPage.value = result
    linkPageNumber.value = result.page
  } catch (error) {
    if (sequence === linksSequence && contextCurrent(identity, requirementId, projectId)) {
      linksError.value = getApiErrorMessage(error, '关联历史加载失败')
    }
  } finally {
    if (sequence === linksSequence && contextCurrent(identity, requirementId, projectId)) {
      linksLoading.value = false
    }
  }
}

async function loadAssets(): Promise<void> {
  const identity = startIdentity()
  if (!identity) return
  const projectId = props.project.id
  const requirementId = props.requirement.id
  const sequence = ++assetsSequence
  assetsLoading.value = true
  assetsError.value = null
  testCases.value = []
  webCases.value = []
  try {
    const [testCaseResult, webCaseResult] = await Promise.all([
      getTestCases(projectId),
      getWebCases(projectId, true),
    ])
    if (sequence !== assetsSequence || !contextCurrent(identity, requirementId, projectId)) return
    testCases.value = testCaseResult
    webCases.value = webCaseResult.items
  } catch (error) {
    if (sequence === assetsSequence && contextCurrent(identity, requirementId, projectId)) {
      assetsError.value = getApiErrorMessage(error, '可关联资产加载失败')
    }
  } finally {
    if (sequence === assetsSequence && contextCurrent(identity, requirementId, projectId)) {
      assetsLoading.value = false
    }
  }
}

async function loadAssetVersions(): Promise<void> {
  const identity = startIdentity()
  const assetId = form.assetId
  const assetType = form.assetType
  const requirementId = props.requirement.id
  const projectId = props.project.id
  const sequence = ++versionsSequence
  assetVersions.value = []
  form.assetVersionId = null
  assetVersionsError.value = null
  if (!identity || !assetId) return
  assetVersionsLoading.value = true
  try {
    const result = assetType === 'TEST_CASE'
      ? await getTestCaseVersions(assetId)
      : await getWebCaseVersions(assetId)
    if (
      sequence !== versionsSequence
      || !contextCurrent(identity, requirementId, projectId)
      || form.assetType !== assetType
      || form.assetId !== assetId
    ) return
    assetVersions.value = result
  } catch (error) {
    if (
      sequence === versionsSequence
      && contextCurrent(identity, requirementId, projectId)
      && form.assetType === assetType
      && form.assetId === assetId
    ) assetVersionsError.value = getApiErrorMessage(error, '资产版本加载失败')
  } finally {
    if (sequence === versionsSequence && contextCurrent(identity, requirementId, projectId)) {
      assetVersionsLoading.value = false
    }
  }
}

async function submitLink(): Promise<void> {
  if (!props.canWrite) {
    ElMessage.warning('当前项目、需求状态或角色不允许新增关联')
    return
  }
  if (!formComplete.value) {
    ElMessage.warning('请显式选择资产、需求版本和资产版本')
    return
  }
  const identity = startIdentity()
  if (!identity || writeBusy.value) return
  const requirementId = props.requirement.id
  const projectId = props.project.id
  const generation = mutationGeneration
  const operationId = beginWriteOperation()
  if (operationId === null) return
  const payload = {
    asset_type: form.assetType,
    asset_id: form.assetId!,
    requirement_version_id: form.requirementVersionId!,
    asset_version_id: form.assetVersionId!,
    relation_type: 'COVERAGE' as const,
    confidence: form.confidence,
  }
  saving.value = true
  try {
    await createRequirementLink(requirementId, payload)
    if (!operationCurrent(operationId, generation) || !contextCurrent(identity, requirementId, projectId)) return
    ElMessage.success('精确版本关联已创建；历史修订保持不变')
    await loadLinks(1)
  } catch (error) {
    if (operationCurrent(operationId, generation) && contextCurrent(identity, requirementId, projectId)) {
      ElMessage.error(getApiErrorMessage(error, '关联创建失败；选择已保留，可手动重试'))
    }
  } finally {
    finishWriteOperation(operationId)
  }
}

async function removeLink(link: RequirementLink): Promise<void> {
  if (!props.canWrite || link.status !== 'ACTIVE' || writeBusy.value) return
  const identity = startIdentity()
  if (!identity) return
  const requirementId = props.requirement.id
  const projectId = props.project.id
  const generation = mutationGeneration
  const operationId = beginWriteOperation()
  if (operationId === null) return
  confirmingRemoveIds.value = [link.id]
  let confirmed = false
  try {
    try {
      await ElMessageBox.confirm(
        `移除 ${assetKindLabel(link)}「${link.asset.name}」的当前关联？历史记录会保留。`,
        '移除需求关联',
        { type: 'warning' },
      )
      confirmed = true
    } catch { return }
    const linkStillCurrent = linkPage.value?.items.some((item) => (
      item.id === link.id && item.status === 'ACTIVE'
    )) ?? false
    if (
      !operationCurrent(operationId, generation)
      || !contextCurrent(identity, requirementId, projectId)
      || !props.canWrite
      || !linkStillCurrent
    ) return
    confirmingRemoveIds.value = []
    removingIds.value = [link.id]
    await removeRequirementLink(requirementId, link.id)
    if (!operationCurrent(operationId, generation) || !contextCurrent(identity, requirementId, projectId)) return
    ElMessage.success('关联已移除，历史仍可追溯')
    await loadLinks(linkPageNumber.value)
  } catch (error) {
    if (
      confirmed
      && operationCurrent(operationId, generation)
      && contextCurrent(identity, requirementId, projectId)
    ) {
      ElMessage.error(getApiErrorMessage(error, '移除失败；不会自动重试'))
    }
  } finally {
    finishWriteOperation(operationId)
  }
}

async function loadImpact(page = impactPageNumber.value): Promise<void> {
  if (!impactSelection.from || !impactSelection.to) {
    ElMessage.warning('请选择起始与目标需求版本')
    return
  }
  const identity = startIdentity()
  if (!identity) return
  const requirementId = props.requirement.id
  const projectId = props.project.id
  const fromId = impactSelection.from
  const toId = impactSelection.to
  const fromVersion = props.versions.find((item) => item.id === fromId)
  const toVersion = props.versions.find((item) => item.id === toId)
  if (!fromVersion || !toVersion) return
  const sequence = ++impactSequence
  impactLoading.value = true
  impactError.value = null
  impact.value = null
  impactDiff.value = null
  try {
    const [impactResult, diffResult] = await Promise.all([
      getRequirementImpact(requirementId, fromId, toId, page, pageSize.value),
      getRequirementDiff(requirementId, fromVersion.version_no, toVersion.version_no),
    ])
    if (
      sequence !== impactSequence
      || !contextCurrent(identity, requirementId, projectId)
      || impactSelection.from !== fromId
      || impactSelection.to !== toId
    ) return
    impact.value = impactResult
    impactDiff.value = diffResult
    impactPageNumber.value = impactResult.page
  } catch (error) {
    if (sequence === impactSequence && contextCurrent(identity, requirementId, projectId)) {
      impactError.value = getApiErrorMessage(error, '影响分析失败；没有将失败当作空结果')
    }
  } finally {
    if (sequence === impactSequence && contextCurrent(identity, requirementId, projectId)) {
      impactLoading.value = false
    }
  }
}

async function loadTraceability(page = tracePageNumber.value): Promise<void> {
  const identity = startIdentity()
  if (!identity) return
  const requirementId = props.requirement.id
  const projectId = props.project.id
  const sequence = ++traceSequence
  traceLoading.value = true
  traceError.value = null
  try {
    const result = await getRequirementTraceability(requirementId, page, pageSize.value)
    if (sequence !== traceSequence || !contextCurrent(identity, requirementId, projectId)) return
    tracePage.value = result
    tracePageNumber.value = result.page
  } catch (error) {
    if (sequence === traceSequence && contextCurrent(identity, requirementId, projectId)) {
      traceError.value = getApiErrorMessage(error, '历史执行追溯加载失败')
    }
  } finally {
    if (sequence === traceSequence && contextCurrent(identity, requirementId, projectId)) {
      traceLoading.value = false
    }
  }
}

function resetForContext(): void {
  linksSequence += 1
  assetsSequence += 1
  versionsSequence += 1
  impactSequence += 1
  traceSequence += 1
  invalidateWriteContext()
  linkPage.value = null
  impact.value = null
  impactDiff.value = null
  tracePage.value = null
  linksError.value = null
  impactError.value = null
  traceError.value = null
  linkPageNumber.value = 1
  impactPageNumber.value = 1
  tracePageNumber.value = 1
  form.assetId = null
  form.assetVersionId = null
  form.requirementVersionId = props.initialVersionId ?? props.requirement.current_version_id
  impactSelection.to = props.initialVersionId ?? props.requirement.current_version_id
  impactSelection.from = props.versions.find((item) => item.id !== impactSelection.to)?.id ?? impactSelection.to
  void Promise.all([loadLinks(1), loadAssets(), loadTraceability(1)])
}

function onIdentityStorage(event: StorageEvent): void {
  if (!isIdentityStorageEvent(event)) return
  linksSequence += 1
  assetsSequence += 1
  versionsSequence += 1
  impactSequence += 1
  traceSequence += 1
  invalidateWriteContext()
  linkPage.value = null
  impact.value = null
  tracePage.value = null
  testCases.value = []
  webCases.value = []
  assetVersions.value = []
  linksError.value = '登录身份已变化，请等待页面重新加载'
}

function assetKindLabel(link: RequirementLink): string {
  return link.asset_type === 'TEST_CASE'
    ? `原 TestCase${link.asset.case_type ? ` · ${link.asset.case_type}` : ''}`
    : '独立 WebCase'
}

function requirementVersionLabel(link: RequirementLink): string {
  return link.requirement_baseline_known && link.requirement_version
    ? `V${link.requirement_version.version_no}`
    : '需求来源版本未知'
}

function assetVersionLabel(link: RequirementLink): string {
  return link.asset_version_known && link.asset_version
    ? `V${link.asset_version.version_no}`
    : '历史版本未记录'
}

function auditTimeLabel(value: string | null, basis: 'UTC' | 'LEGACY_UNKNOWN' | null): string {
  if (!value) return '—'
  if (basis === 'LEGACY_UNKNOWN') return `${value}（旧记录时区未知）`
  const parsed = parseApiDateTime(value)
  if (Number.isNaN(parsed.getTime())) return `${value}（UTC 记录）`
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed)}（北京时间，源 UTC）`
}

function impactStatusLabel(status: RequirementImpactStatus): string {
  if (status === 'NO_CHANGE') return '无变化'
  if (status === 'BASELINE_UNKNOWN') return '基线未知'
  return '可能过期'
}

function impactStatusType(status: RequirementImpactStatus): 'success' | 'warning' | 'info' {
  return status === 'NO_CHANGE' ? 'success' : status === 'POSSIBLY_OUTDATED' ? 'warning' : 'info'
}

function goToAsset(link: RequirementLink): void {
  const query: Record<string, string> = {
    project_id: String(props.project.id),
    version_id: String(link.asset_version_id ?? ''),
    link_source: 'requirement',
  }
  if (!link.asset_version_id) delete query.version_id
  if (link.asset_type === 'TEST_CASE') {
    query.test_case_id = String(link.asset_id)
    void router.push({ name: route.meta.projectScoped ? 'project-test-cases' : 'test-cases', params: route.meta.projectScoped ? { projectId: props.project.id } : {}, query })
  } else {
    query.web_case_id = String(link.asset_id)
    void router.push({ name: route.meta.projectScoped ? 'project-web-assets' : 'web-assets', params: route.meta.projectScoped ? { projectId: props.project.id } : {}, query })
  }
}

function openTraceReport(item: RequirementTraceItem): void {
  void router.push({ name: route.meta.projectScoped ? 'project-report-detail' : 'report-detail', params: route.meta.projectScoped ? { projectId: props.project.id, runId: item.run_id } : { runId: item.run_id } })
}

function openTraceEvidence(item: RequirementTraceItem): void {
  void router.push({
    name: route.meta.projectScoped ? 'project-evidence' : 'evidence',
    params: route.meta.projectScoped ? { projectId: props.project.id } : {},
    query: { project_id: String(props.project.id), run_id: item.run_id },
  })
}

watch(() => [props.project.id, props.requirement.id] as const, resetForContext)
watch(() => form.assetType, () => {
  form.assetId = null
  form.assetVersionId = null
  assetVersions.value = []
  assetVersionsError.value = null
})
watch(() => form.assetId, () => { void loadAssetVersions() })
watch(() => [impactSelection.from, impactSelection.to], () => {
  impactSequence += 1
  traceSequence += 1
  impact.value = null
  impactDiff.value = null
  impactError.value = null
})

onMounted(() => {
  window.addEventListener('storage', onIdentityStorage)
  resetForContext()
})
onBeforeUnmount(() => {
  alive = false
  linksSequence += 1
  assetsSequence += 1
  versionsSequence += 1
  impactSequence += 1
  invalidateWriteContext()
  window.removeEventListener('storage', onIdentityStorage)
})
</script>

<template>
  <section class="requirement-links-panel">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="关联管理" name="links">
        <el-alert
          title="关联固定到明确的需求版本和资产版本；这是当前关联历史，不是旧 Run 快照。"
          type="info"
          :closable="false"
          show-icon
        />
        <section class="link-form">
          <div class="link-form-grid">
            <el-form-item label="资产种类">
              <el-select v-model="form.assetType" data-testid="link-asset-type" :disabled="!canWrite || writeBusy">
                <el-option label="原 TestCase（含其 WEB 类型）" value="TEST_CASE" />
                <el-option label="独立 WebCase" value="WEB_CASE" />
              </el-select>
            </el-form-item>
            <el-form-item label="资产">
              <el-select v-model="form.assetId" data-testid="link-asset" filterable :loading="assetsLoading" :disabled="!canWrite || writeBusy" placeholder="选择明确资产">
                <el-option v-for="asset in assetOptions" :key="`${form.assetType}-${asset.id}`" :value="asset.id" :label="`${form.assetType === 'TEST_CASE' ? `原 TestCase · ${'case_type' in asset ? asset.case_type : ''}` : '独立 WebCase'} · ${asset.code} · ${asset.name}`" />
              </el-select>
            </el-form-item>
            <el-form-item label="需求版本">
              <el-select v-model="form.requirementVersionId" data-testid="link-requirement-version" :disabled="!canWrite || writeBusy">
                <el-option v-for="version in versions" :key="version.id" :value="version.id" :label="`V${version.version_no}`" />
              </el-select>
            </el-form-item>
            <el-form-item label="资产版本">
              <el-select v-model="form.assetVersionId" data-testid="link-asset-version" :loading="assetVersionsLoading" :disabled="!canWrite || writeBusy || !form.assetId" placeholder="选择明确版本">
                <el-option v-for="version in assetVersions" :key="version.id" :value="version.id" :label="`V${version.version_no}`" />
              </el-select>
            </el-form-item>
            <el-form-item label="置信度">
              <el-input-number v-model="form.confidence" :min="0" :max="1" :step="0.05" :disabled="!canWrite || writeBusy" />
            </el-form-item>
          </div>
          <el-alert v-if="assetsError || assetVersionsError" :title="assetsError || assetVersionsError || ''" type="error" :closable="false" show-icon />
          <div class="confirmation-row">
            <div><small>提交前确认</small><strong>{{ submitSummary }}</strong><span>关系：COVERAGE · 不跟随 current 自动漂移</span></div>
            <el-button type="primary" data-testid="create-link" :disabled="!canWrite || !formComplete || writeBusy" :loading="saving" @click="submitLink">确认创建关联</el-button>
          </div>
          <el-alert v-if="!canWrite" title="当前为只读：归档项目、归档需求或 VIEWER 不能写入；已有历史仍可读取。" type="warning" :closable="false" show-icon />
        </section>

        <el-alert v-if="linksError" :title="linksError" type="error" :closable="false" show-icon />
        <el-table v-loading="linksLoading" :data="linkPage?.items ?? []" size="small" table-layout="fixed" class="link-table">
          <el-table-column label="资产" min-width="190"><template #default="{ row }: { row: RequirementLink }"><strong>{{ row.asset.name }}</strong><small>{{ assetKindLabel(row) }} · {{ row.asset.code }}</small><el-button link type="primary" @click="goToAsset(row)">打开资产版本</el-button></template></el-table-column>
          <el-table-column label="固定版本" min-width="160"><template #default="{ row }: { row: RequirementLink }"><span>需求 {{ requirementVersionLabel(row) }}</span><span>资产 {{ assetVersionLabel(row) }}</span></template></el-table-column>
          <el-table-column label="修订" min-width="120"><template #default="{ row }: { row: RequirementLink }"><el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ row.status === 'ACTIVE' ? '当前关联' : '已移除' }}</el-tag><span>{{ row.supersedes_link_id ? '已更新关联' : '首次关联' }}</span></template></el-table-column>
          <el-table-column label="来源 / 语义" min-width="150"><template #default="{ row }: { row: RequirementLink }"><span>{{ row.source }} · {{ row.relation_type }} · {{ row.confidence }}</span><span>{{ row.binding_note }}</span><small>当前关联历史，非 Run 快照</small></template></el-table-column>
          <el-table-column label="审计时间" min-width="195"><template #default="{ row }: { row: RequirementLink }"><span>创建：{{ auditTimeLabel(row.created_at, row.created_at_time_basis) }}</span><span v-if="row.removed_at">移除：{{ auditTimeLabel(row.removed_at, row.removed_at_time_basis) }}</span></template></el-table-column>
          <el-table-column label="操作" width="65"><template #default="{ row }: { row: RequirementLink }"><el-button v-if="row.status === 'ACTIVE'" link type="danger" :data-testid="`remove-link-${row.id}`" :disabled="!canWrite || writeBusy" :loading="removingIds.includes(row.id)" @click="removeLink(row)">移除</el-button><span v-else>历史</span></template></el-table-column>
        </el-table>
        <el-empty v-if="!linksLoading && !linksError && linkPage?.total === 0" description="当前需求暂无关联历史" :image-size="70" />
        <el-pagination v-if="linkPage?.total" v-model:current-page="linkPageNumber" :page-size="pageSize" :page-sizes="[10, 20, 50, 100]" :total="linkPage?.total ?? 0" layout="total, sizes, prev, pager, next" @current-change="loadLinks" @size-change="(value: number) => { pageSize = value; linkPageNumber = 1; loadLinks(1) }" />
      </el-tab-pane>

      <el-tab-pane label="影响分析" name="impact">
        <el-alert title="只计算确定性潜在关联范围，需人工确认；不会改用例、创建版本、调用 AI 或发起 Run。" type="warning" :closable="false" show-icon />
        <div class="impact-toolbar">
          <el-select v-model="impactSelection.from" data-testid="impact-from" placeholder="起始需求版本"><el-option v-for="version in versions" :key="version.id" :label="`V${version.version_no}`" :value="version.id" /></el-select>
          <span>→</span>
          <el-select v-model="impactSelection.to" data-testid="impact-to" placeholder="目标需求版本"><el-option v-for="version in versions" :key="version.id" :label="`V${version.version_no}`" :value="version.id" /></el-select>
          <el-button type="primary" data-testid="analyze-impact" :loading="impactLoading" @click="loadImpact(1)">分析选定范围</el-button>
        </div>
        <el-alert v-if="impactError" :title="impactError" type="error" :closable="false" show-icon />
        <template v-if="impact">
          <section class="impact-facts">
            <div><small>内容变化事实</small><strong>{{ impact.comparison.content_changed ? '有变化' : '无变化' }}</strong></div>
            <div><small>行变化</small><strong>+{{ impact.comparison.additions }} / -{{ impact.comparison.deletions }}</strong></div>
            <div><small>潜在关联数</small><strong>{{ impact.total }}</strong></div>
          </section>
          <el-alert v-if="!impact.comparison.content_changed" title="选定版本内容无变化；关联记录的基线结论仍单独展示。" type="success" :closable="false" show-icon />
          <el-alert v-if="impact.total === 0" title="选定需求当前没有有效关联，因此没有潜在影响项。" type="info" :closable="false" show-icon />
          <pre class="diff-viewer">{{ impactDiff?.unified_diff || '两个选定版本内容相同' }}</pre>
          <el-table :data="impact.items" size="small" table-layout="fixed">
            <el-table-column label="关联资产" min-width="210"><template #default="{ row }: { row: RequirementImpactItem }"><strong>{{ row.asset.name }}</strong><span>{{ assetKindLabel(row) }} · {{ row.asset.code }}</span><span>{{ assetVersionLabel(row) }}</span></template></el-table-column>
            <el-table-column label="选定范围变化" min-width="220"><template #default="{ row }: { row: RequirementImpactItem }"><el-tag :type="impactStatusType(row.selected_scope_status)">{{ impactStatusLabel(row.selected_scope_status) }}</el-tag><span>{{ row.selected_scope_reason }}</span></template></el-table-column>
            <el-table-column label="记录基线 → 目标" min-width="250"><template #default="{ row }: { row: RequirementImpactItem }"><el-tag :type="impactStatusType(row.recorded_baseline_status)">{{ impactStatusLabel(row.recorded_baseline_status) }}</el-tag><span>{{ requirementVersionLabel(row) }}</span><span>{{ row.recorded_baseline_reason }}</span></template></el-table-column>
          </el-table>
          <p class="scope-note">{{ impact.scope_note }}</p>
          <el-pagination v-if="impact.total" v-model:current-page="impactPageNumber" :page-size="pageSize" :page-sizes="[10, 20, 50, 100]" :total="impact.total" layout="total, sizes, prev, pager, next" @current-change="loadImpact" @size-change="(value: number) => { pageSize = value; impactPageNumber = 1; loadImpact(1) }" />
        </template>
      </el-tab-pane>

      <el-tab-pane label="执行追溯" name="traceability">
        <el-alert
          title="只展示创建 Run 时保存的不可变需求来源快照；不会用当前关联补造旧执行。"
          type="info"
          :closable="false"
          show-icon
        />
        <el-alert v-if="traceError" :title="traceError" type="error" :closable="false" show-icon>
          <template #default><el-button link type="primary" @click="loadTraceability()">重试</el-button></template>
        </el-alert>
        <el-table
          v-loading="traceLoading"
          :data="tracePage?.items ?? []"
          size="small"
          table-layout="fixed"
          class="trace-table"
        >
          <el-table-column label="固定来源" min-width="205">
            <template #default="{ row }: { row: RequirementTraceItem }">
              <strong>需求 {{ row.requirement_version_no ? `V${row.requirement_version_no}` : '版本未知' }}</strong>
              <span>{{ row.requirement_version_binding }}</span>
              <small>{{ row.target_type }} · 固定执行版本</small>
            </template>
          </el-table-column>
          <el-table-column label="历史 Run / Case" min-width="230">
            <template #default="{ row }: { row: RequirementTraceItem }">
              <strong>{{ row.run_code }}</strong>
              <code>{{ row.run_id }}</code>
              <span>Run {{ row.run_status }} · Case {{ row.case_sequence_no }} / {{ row.case_status }}</span>
            </template>
          </el-table-column>
          <el-table-column label="证据" width="115">
            <template #default="{ row }: { row: RequirementTraceItem }">
              <strong>{{ row.evidence_count }}</strong>
              <small>件已授权证据</small>
            </template>
          </el-table-column>
          <el-table-column label="捕获时间" min-width="190">
            <template #default="{ row }: { row: RequirementTraceItem }">{{ auditTimeLabel(row.captured_at, 'UTC') }}</template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }: { row: RequirementTraceItem }">
              <el-button link type="primary" @click="openTraceReport(row)">报告</el-button>
              <el-button link :disabled="row.evidence_count === 0" @click="openTraceEvidence(row)">证据</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!traceLoading && !traceError && tracePage?.total === 0" description="此需求还没有被已创建 Run 的来源快照引用" :image-size="70" />
        <p v-if="tracePage" class="scope-note">{{ tracePage.scope_note }}</p>
        <el-pagination
          v-if="tracePage?.total"
          v-model:current-page="tracePageNumber"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="tracePage?.total ?? 0"
          layout="total, sizes, prev, pager, next"
          @current-change="loadTraceability"
          @size-change="(value: number) => { pageSize = value; tracePageNumber = 1; loadTraceability(1) }"
        />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.requirement-links-panel{min-width:0}.link-form{display:grid;gap:12px;margin:12px 0 18px;padding:14px;border:1px solid #e4e7ed;border-radius:10px;background:#fafbfc}.link-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 12px}.confirmation-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px;border-radius:8px;background:#eef3ff}.confirmation-row>div{display:grid;gap:3px;min-width:0}.confirmation-row small,.confirmation-row span,.link-table small,.trace-table small,.scope-note{color:#6f7b91}.link-table :deep(.cell),.trace-table :deep(.cell){display:grid;gap:4px;white-space:normal}.impact-toolbar{display:flex;align-items:center;gap:10px;margin:14px 0}.impact-toolbar .el-select{min-width:210px}.impact-facts{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}.impact-facts>div{display:grid;gap:4px;padding:12px;border:1px solid #e4e7ed;border-radius:8px}.impact-facts small{color:#7a8599}.impact-facts strong{font-size:20px}.diff-viewer{max-height:260px;margin:12px 0;padding:12px;overflow:auto;border-radius:8px;background:#172033;color:#e8edf6;font:12px/1.6 JetBrains Mono,Consolas,monospace}.scope-note{font-size:13px}@media(max-width:900px){.link-form-grid{grid-template-columns:1fr}.confirmation-row,.impact-toolbar{align-items:stretch;flex-direction:column}.impact-facts{grid-template-columns:1fr}}
</style>
