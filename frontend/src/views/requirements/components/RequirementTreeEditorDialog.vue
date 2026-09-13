<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import { Delete, FullScreen, Lock, MagicStick, Plus, Rank } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type Node from 'element-plus/es/components/tree/src/model/node'
import type { AllowDropType, NodeDropType } from 'element-plus/es/components/tree/src/tree.type'

import { getApiErrorMessage } from '@/api/http'
import {
  createRequirementRevisionPlan,
  getRequirementReview,
} from '@/api/requirement-reviews'
import { publishRequirementDocumentVersion } from '@/api/requirements'
import type {
  RequirementReview,
  RequirementRevisionAddition,
  RequirementRevisionContentGroup,
  RequirementRevisionDeletion,
  RequirementRevisionPlanState,
  RequirementRevisionSuggestion,
} from '@/types/requirement-review'
import type {
  RequirementDocumentDraftNode,
  RequirementDocumentVersion,
  RequirementTreeNode,
} from '@/types/requirement'

const props = defineProps<{
  projectId: number
  tree: RequirementTreeNode[]
  canWrite: boolean
}>()
const emit = defineEmits<{
  published: [version: RequirementDocumentVersion, preferredRequirementId?: number]
}>()

interface DraftNode extends RequirementDocumentDraftNode {
  code?: string
  change_state?: 'NEW' | 'MODIFIED'
  pending_delete?: boolean
  delete_mode?: 'SUBTREE' | 'PROMOTE_CHILDREN'
  delete_origin?: string
}

interface EditorNode extends DraftNode {
  children: EditorNode[]
  outline_number: string
}

const visible = ref(false)
const fullscreen = ref(false)
const publishing = ref(false)
const planning = ref(false)
const sourceReview = ref<RequirementReview | null>(null)
const revisionPlan = ref<RequirementRevisionPlanState | null>(null)
const workbenchTab = ref<'CONTENT' | 'ADD' | 'DELETE' | 'UNRESOLVED'>('CONTENT')
const selectedClientId = ref<string | null>(null)
const draft = ref<DraftNode[]>([])
const originalMarkdown = ref<Record<string, string>>({})
const revisionDrafts = ref<Record<string, string>>({})
const appliedContentCodes = ref(new Set<string>())
const appliedAdditionKeys = ref(new Set<string>())
const appliedDeletionCodes = ref(new Set<string>())
const additionClientIds = ref<Record<string, string>>({})
const form = reactive({ changeSummary: '' })
let localSequence = 0
let planTimer: ReturnType<typeof setTimeout> | null = null

const selectedNode = computed(() => (
  draft.value.find((item) => item.client_id === selectedClientId.value) ?? null
))

const planResult = computed(() => revisionPlan.value?.result ?? null)
const planSuggestions = computed(() => revisionPlan.value?.suggestions ?? [])
const suggestionMap = computed(() => new Map(
  planSuggestions.value.map((item) => [item.id, item]),
))

function sortedChildren(parentClientId: string | null): DraftNode[] {
  return draft.value
    .filter((item) => item.parent_client_id === parentClientId)
    .sort((left, right) => left.order_index - right.order_index)
}

const editorTree = computed<EditorNode[]>(() => {
  const build = (parentClientId: string | null, prefix = ''): EditorNode[] => (
    sortedChildren(parentClientId).map((item, index) => {
      const outlineNumber = prefix ? `${prefix}.${index + 1}` : `${index + 1}`
      return {
        ...item,
        order_index: index,
        outline_number: outlineNumber,
        children: build(item.client_id, outlineNumber),
      }
    })
  )
  return build(null)
})

function contextDescendants(review: RequirementReview): Array<Record<string, unknown>> {
  const descendants = review.context_snapshot?.descendants
  return Array.isArray(descendants)
    ? descendants.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : []
}

const reviewScopeIds = computed<Set<number>>(() => {
  const review = sourceReview.value
  if (!review) return new Set<number>()
  const ids = new Set<number>([review.requirement_id])
  const codes = new Set<string>()
  for (const item of contextDescendants(review)) {
    if (typeof item.id === 'number') ids.add(item.id)
    if (typeof item.code === 'string') codes.add(item.code)
  }
  for (const item of draft.value) {
    if (item.requirement_id && item.code && codes.has(item.code)) ids.add(item.requirement_id)
  }
  return ids
})

function isEditable(node: DraftNode | null | undefined, visiting = new Set<string>()): boolean {
  if (!node || !props.canWrite || node.pending_delete) return false
  if (!sourceReview.value) return true
  if (node.requirement_id) return reviewScopeIds.value.has(node.requirement_id)
  if (!node.parent_client_id || visiting.has(node.client_id)) return false
  visiting.add(node.client_id)
  return isEditable(draft.value.find((item) => item.client_id === node.parent_client_id), visiting)
}

function flattenTree(nodes: RequirementTreeNode[]): DraftNode[] {
  const result: DraftNode[] = []
  const append = (items: RequirementTreeNode[], parentClientId: string | null): void => {
    items.forEach((item, index) => {
      const clientId = `existing-${item.id}`
      result.push({
        client_id: clientId,
        requirement_id: item.id,
        code: item.code,
        parent_client_id: parentClientId,
        title: item.title,
        type: item.type,
        verification_type: item.verification_type,
        automation_readiness: item.automation_readiness,
        markdown_content: item.current_version?.markdown_content ?? '',
        order_index: index,
      })
      append(item.children ?? [], clientId)
    })
  }
  append(nodes, null)
  return result
}

function parseRevisionPlan(review: RequirementReview): RequirementRevisionPlanState | null {
  const value = review.context_snapshot?.revision_plan
  if (!value || typeof value !== 'object' || !('status' in value)) return null
  return value as RequirementRevisionPlanState
}

function initializePlan(plan: RequirementRevisionPlanState): void {
  revisionPlan.value = plan
  if (plan.status !== 'SUCCEEDED' || !plan.result) return
  for (const group of plan.result.content_revisions) {
    if (!(group.target_requirement_code in revisionDrafts.value)) {
      revisionDrafts.value[group.target_requirement_code] = group.proposed_markdown
    }
  }
  const firstCode = plan.result.content_revisions[0]?.target_requirement_code
  const firstNode = draft.value.find((item) => item.code === firstCode)
  if (firstNode) selectedClientId.value = firstNode.client_id
  if (!plan.result.content_revisions.length && plan.result.additions.length) workbenchTab.value = 'ADD'
  else if (!plan.result.content_revisions.length && plan.result.deletions.length) workbenchTab.value = 'DELETE'
}

function clearPlanTimer(): void {
  if (planTimer) clearTimeout(planTimer)
  planTimer = null
}

async function pollRevisionPlan(reviewId: number): Promise<void> {
  clearPlanTimer()
  try {
    const review = await getRequirementReview(reviewId)
    sourceReview.value = review
    const plan = parseRevisionPlan(review)
    if (plan) initializePlan(plan)
    if (plan?.status === 'QUEUED' || plan?.status === 'RUNNING') {
      planTimer = setTimeout(() => void pollRevisionPlan(reviewId), 2500)
    } else {
      planning.value = false
    }
  } catch (error) {
    planning.value = false
    ElMessage.error(getApiErrorMessage(error, '读取 AI 修订规划失败'))
  }
}

async function startRevisionPlan(review: RequirementReview): Promise<void> {
  planning.value = true
  try {
    const updated = await createRequirementRevisionPlan(review.id)
    sourceReview.value = updated
    const plan = parseRevisionPlan(updated)
    if (plan) initializePlan(plan)
    if (plan?.status === 'SUCCEEDED' || plan?.status === 'FAILED') planning.value = false
    else await pollRevisionPlan(review.id)
  } catch (error) {
    planning.value = false
    ElMessage.error(getApiErrorMessage(error, '创建 AI 修订规划失败'))
  }
}

function open(review?: RequirementReview): void {
  clearPlanTimer()
  fullscreen.value = false
  draft.value = flattenTree(props.tree)
  originalMarkdown.value = Object.fromEntries(
    draft.value.filter((item) => item.code).map((item) => [item.code as string, item.markdown_content]),
  )
  revisionDrafts.value = {}
  appliedContentCodes.value = new Set()
  appliedAdditionKeys.value = new Set()
  appliedDeletionCodes.value = new Set()
  additionClientIds.value = {}
  revisionPlan.value = review ? parseRevisionPlan(review) : null
  sourceReview.value = review ?? null
  workbenchTab.value = 'CONTENT'
  form.changeSummary = review
    ? `基于${reviewSourceVersionLabel(review)}的 AI 评审建议修订完整需求文档`
    : '编辑完整需求树'
  selectedClientId.value = review
    ? `existing-${review.requirement_id}`
    : draft.value[0]?.client_id ?? null
  visible.value = true
  if (review) {
    const plan = parseRevisionPlan(review)
    if (plan?.status === 'SUCCEEDED') initializePlan(plan)
    else void startRevisionPlan(review)
  }
}

function reviewDocumentVersion(review: RequirementReview): number | null {
  const value = review.context_snapshot.document_version
  if (!value || typeof value !== 'object' || !("version_no" in value)) return null
  const versionNo = (value as { version_no?: unknown }).version_no
  return typeof versionNo === 'number' ? versionNo : null
}

function reviewSourceVersionLabel(review: RequirementReview): string {
  const documentVersion = reviewDocumentVersion(review)
  return documentVersion === null
    ? `需求版本 V${review.requirement_version_no}`
    : `完整需求 V${documentVersion}`
}

defineExpose({ open })
onBeforeUnmount(clearPlanTimer)

function reindex(parentClientId: string | null): void {
  sortedChildren(parentClientId).forEach((item, index) => { item.order_index = index })
}

function addChild(parentClientId: string | null): void {
  if (!props.canWrite) return
  const parent = parentClientId
    ? draft.value.find((item) => item.client_id === parentClientId)
    : null
  if (sourceReview.value && (!parent || !isEditable(parent))) {
    ElMessage.warning('基于评审创建新版时，只能在本次评审范围内新增需求')
    return
  }
  const clientId = `new-${Date.now()}-${++localSequence}`
  draft.value.push({
    client_id: clientId,
    parent_client_id: parentClientId,
    title: '新需求',
    type: 'FEATURE',
    verification_type: 'AUTO',
    automation_readiness: 'READY',
    markdown_content: '',
    order_index: sortedChildren(parentClientId).length,
    change_state: 'NEW',
  })
  selectedClientId.value = clientId
  void nextTick()
}

function descendantClientIds(node: DraftNode): Set<string> {
  const result = new Set<string>([node.client_id])
  let changed = true
  while (changed) {
    changed = false
    for (const item of draft.value) {
      if (item.parent_client_id && result.has(item.parent_client_id) && !result.has(item.client_id)) {
        result.add(item.client_id)
        changed = true
      }
    }
  }
  return result
}

async function removeNode(node: DraftNode): Promise<void> {
  if (!isEditable(node)) return
  const descendants = descendantClientIds(node)
  try {
    await ElMessageBox.confirm(
      descendants.size > 1
        ? `将从新版本移除此需求及 ${descendants.size - 1} 个下级需求。历史版本仍会保留。`
        : '将从新版本移除此需求。历史版本仍会保留。',
      '移除需求',
      { type: 'warning', confirmButtonText: '从新版本移除', cancelButtonText: '保留' },
    )
  } catch { return }
  draft.value = draft.value.filter((item) => !descendants.has(item.client_id))
  reindex(node.parent_client_id)
  selectedClientId.value = draft.value[0]?.client_id ?? null
}

function allowDrag(node: Node): boolean {
  return isEditable(node.data as EditorNode)
}

function allowDrop(draggingNode: Node, dropNode: Node, type: AllowDropType): boolean {
  const dragging = draggingNode.data as EditorNode
  const drop = dropNode.data as EditorNode
  return type !== 'inner'
    && dragging.client_id !== drop.client_id
    && dragging.parent_client_id === drop.parent_client_id
    && isEditable(dragging)
    && isEditable(drop)
}

function onNodeDrop(
  draggingNode: Node,
  dropNode: Node,
  dropType: Exclude<NodeDropType, 'none'>,
): void {
  if (dropType === 'inner') return
  const dragging = draft.value.find((item) => item.client_id === (draggingNode.data as EditorNode).client_id)
  const drop = draft.value.find((item) => item.client_id === (dropNode.data as EditorNode).client_id)
  if (!dragging || !drop || dragging.parent_client_id !== drop.parent_client_id) return
  const siblings = sortedChildren(dragging.parent_client_id).filter((item) => item.client_id !== dragging.client_id)
  const dropIndex = siblings.findIndex((item) => item.client_id === drop.client_id)
  siblings.splice(dropIndex + (dropType === 'after' ? 1 : 0), 0, dragging)
  siblings.forEach((item, index) => { item.order_index = index })
}

function suggestionsFor(ids: string[]): RequirementRevisionSuggestion[] {
  return ids.map((id) => suggestionMap.value.get(id)).filter(
    (item): item is RequirementRevisionSuggestion => Boolean(item),
  )
}

function contentGroupFor(code?: string): RequirementRevisionContentGroup | null {
  if (!code) return null
  return planResult.value?.content_revisions.find(
    (item) => item.target_requirement_code === code,
  ) ?? null
}

const selectedContentGroup = computed(() => contentGroupFor(selectedNode.value?.code))

function selectCode(code: string): void {
  const node = draft.value.find((item) => item.code === code)
  if (node) selectedClientId.value = node.client_id
}

function applyContent(group: RequirementRevisionContentGroup): void {
  const node = draft.value.find((item) => item.code === group.target_requirement_code)
  if (!node || !isEditable(node)) return
  node.markdown_content = revisionDrafts.value[group.target_requirement_code] || group.proposed_markdown
  node.change_state = 'MODIFIED'
  appliedContentCodes.value.add(group.target_requirement_code)
  const nextGroup = planResult.value?.content_revisions.find(
    (item) => !appliedContentCodes.value.has(item.target_requirement_code),
  )
  if (nextGroup) selectCode(nextGroup.target_requirement_code)
  ElMessage.success(nextGroup ? '已应用并切换到下一个待处理需求' : '本轮内容修订已全部处理')
}

function undoContent(code: string): void {
  const node = draft.value.find((item) => item.code === code)
  if (!node) return
  node.markdown_content = originalMarkdown.value[code] ?? node.markdown_content
  node.change_state = undefined
  revisionDrafts.value[code] = planResult.value?.content_revisions.find(
    (item) => item.target_requirement_code === code,
  )?.proposed_markdown ?? node.markdown_content
  appliedContentCodes.value.delete(code)
}

function applyAddition(item: RequirementRevisionAddition): void {
  if (appliedAdditionKeys.value.has(item.client_key)) return
  const parent = draft.value.find((node) => node.code === item.parent_requirement_code)
  if (!parent || !isEditable(parent)) {
    ElMessage.warning('AI 推荐的父需求当前不可编辑，请转到待确认处理')
    return
  }
  const siblings = sortedChildren(parent.client_id)
  const afterIndex = siblings.findIndex((node) => node.code === item.insert_after_requirement_code)
  const insertIndex = afterIndex >= 0 ? afterIndex + 1 : siblings.length
  siblings.filter((node) => node.order_index >= insertIndex).forEach((node) => { node.order_index += 1 })
  const clientId = `ai-add-${item.client_key}-${Date.now()}`
  draft.value.push({
    client_id: clientId,
    parent_client_id: parent.client_id,
    title: item.title,
    type: item.requirement_type,
    verification_type: 'AUTO',
    automation_readiness: 'READY',
    markdown_content: item.proposed_markdown,
    order_index: insertIndex,
    change_state: 'NEW',
  })
  additionClientIds.value[item.client_key] = clientId
  appliedAdditionKeys.value.add(item.client_key)
  selectedClientId.value = clientId
}

function undoAddition(item: RequirementRevisionAddition): void {
  const clientId = additionClientIds.value[item.client_key]
  if (!clientId) return
  const node = draft.value.find((candidate) => candidate.client_id === clientId)
  if (!node) return
  const descendants = descendantClientIds(node)
  draft.value = draft.value.filter((candidate) => !descendants.has(candidate.client_id))
  reindex(node.parent_client_id)
  delete additionClientIds.value[item.client_key]
  appliedAdditionKeys.value.delete(item.client_key)
}

function applyAllAdditions(): void {
  for (const item of planResult.value?.additions ?? []) applyAddition(item)
  ElMessage.success('新增需求建议已加入修订草稿，可继续逐项检查')
}

function stageDeletion(item: RequirementRevisionDeletion): void {
  if (appliedDeletionCodes.value.has(item.target_requirement_code)) return
  const node = draft.value.find((candidate) => candidate.code === item.target_requirement_code)
  if (!node || !isEditable(node)) return
  const targets = item.delete_mode === 'SUBTREE' ? descendantClientIds(node) : new Set([node.client_id])
  for (const candidate of draft.value) {
    if (targets.has(candidate.client_id)) {
      candidate.pending_delete = true
      candidate.delete_mode = item.delete_mode
      candidate.delete_origin = item.target_requirement_code
    }
  }
  appliedDeletionCodes.value.add(item.target_requirement_code)
}

async function applyDeletion(item: RequirementRevisionDeletion): Promise<void> {
  try {
    await ElMessageBox.confirm(
      item.delete_mode === 'SUBTREE'
        ? '将把此需求及全部下级标记为“新版中移除”。历史版本和已有测试关联仍保留。'
        : '将把此需求标记为“新版中移除”，下级需求在发布时提升到当前父级。',
      '应用删除建议',
      { type: 'warning', confirmButtonText: '加入修订草稿', cancelButtonText: '暂不处理' },
    )
  } catch { return }
  stageDeletion(item)
}

function undoDeletion(item: RequirementRevisionDeletion): void {
  for (const node of draft.value) {
    if (node.delete_origin === item.target_requirement_code) {
      node.pending_delete = false
      node.delete_mode = undefined
      node.delete_origin = undefined
    }
  }
  appliedDeletionCodes.value.delete(item.target_requirement_code)
}

async function applyAllDeletions(): Promise<void> {
  const items = planResult.value?.deletions ?? []
  if (!items.length) return
  try {
    await ElMessageBox.confirm(
      `将把 ${items.length} 条删除建议加入修订草稿。不会立即删除数据，发布前仍可撤销。`,
      '应用全部删除建议',
      { type: 'warning', confirmButtonText: '确认并加入草稿', cancelButtonText: '取消' },
    )
  } catch { return }
  for (const item of items) stageDeletion(item)
}

function normalizedNodes(): RequirementDocumentDraftNode[] {
  const result: RequirementDocumentDraftNode[] = []
  const append = (
    inputParentClientId: string | null,
    outputParentClientId: string | null,
    startOrder = 0,
  ): number => {
    let order = startOrder
    for (const item of sortedChildren(inputParentClientId)) {
      if (item.pending_delete) {
        if (item.delete_mode === 'PROMOTE_CHILDREN' && item.delete_origin === item.code) {
          order = append(item.client_id, outputParentClientId, order)
        }
        continue
      }
      result.push({
        client_id: item.client_id,
        requirement_id: item.requirement_id,
        parent_client_id: outputParentClientId,
        title: item.title.trim(),
        type: item.type,
        verification_type: item.verification_type,
        automation_readiness: item.automation_readiness,
        markdown_content: item.markdown_content,
        order_index: order,
      })
      order += 1
      append(item.client_id, item.client_id)
    }
    return order
  }
  append(null, null)
  return result
}

async function publish(): Promise<void> {
  if (!props.canWrite || publishing.value) return
  const normalized = normalizedNodes()
  if (!normalized.length) { ElMessage.warning('完整需求文档至少保留一个需求'); return }
  if (normalized.some((item) => item.title.trim().length < 2)) {
    ElMessage.warning('每个需求标题至少需要 2 个字符')
    return
  }
  publishing.value = true
  try {
    const preferredRequirementId = selectedNode.value?.requirement_id
    const version = await publishRequirementDocumentVersion(props.projectId, {
      nodes: normalized,
      change_summary: form.changeSummary.trim() || undefined,
      source_review_id: sourceReview.value?.id,
    })
    visible.value = false
    clearPlanTimer()
    ElMessage.success(`完整需求文档 V${version.version_no} 已发布`)
    emit('published', version, preferredRequirementId)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '需求文档版本发布失败；草稿已保留'))
  } finally {
    publishing.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" class="revision-dialog" :fullscreen="fullscreen" width="min(1240px, 96vw)" top="3vh" append-to-body destroy-on-close @closed="clearPlanTimer">
    <template #header>
      <div class="dialog-titlebar">
        <strong>{{ sourceReview ? 'AI 辅助修订并创建新版' : '编辑完整需求树' }}</strong>
        <el-button class="fullscreen-button" :icon="FullScreen" @click="fullscreen = !fullscreen">{{ fullscreen ? '退出全屏' : '全屏查看' }}</el-button>
      </div>
    </template>
    <el-alert
      :title="sourceReview ? 'AI 将评审建议自动归类到需求，所有操作先进入修订草稿' : '这里编辑的是一份完整需求文档'"
      :description="sourceReview ? '只有本次评审需求及其全部下级可修改；应用建议不会立即发布，确认完整草稿后再统一创建新版本。' : '发布后生成项目级 V1/V2；新增、删除和重排会统一写入新版本，历史版本与已有测试关联保持不变。'"
      type="info"
      :closable="false"
      show-icon
    />

    <section v-if="sourceReview" class="revision-summary">
      <div>
        <strong>{{ sourceReview.requirement_title }} · 基于{{ reviewSourceVersionLabel(sourceReview) }}的 AI 评审修订</strong>
        <span>可修改范围：当前需求及 {{ Math.max(reviewScopeIds.size - 1, 0) }} 个下级需求</span>
      </div>
      <div v-if="revisionPlan?.status === 'SUCCEEDED' && planResult" class="plan-counts">
        <el-tag>内容修订 {{ planResult.content_revisions.length }}</el-tag>
        <el-tag type="success">新增 {{ planResult.additions.length }}</el-tag>
        <el-tag type="danger">删除 {{ planResult.deletions.length }}</el-tag>
        <el-tag v-if="planResult.unresolved.length" type="warning">待确认 {{ planResult.unresolved.length }}</el-tag>
      </div>
    </section>

    <div class="tree-editor-layout" :class="{ fullscreen }">
      <aside class="editor-tree">
        <header>
          <div><strong>版本目录</strong><span>同级上下拖动；绿色新增、蓝色修改、红色待移除</span></div>
          <el-button v-if="!sourceReview" type="primary" plain :icon="Plus" @click="addChild(null)">根需求</el-button>
        </header>
        <el-tree
          :data="editorTree"
          node-key="client_id"
          default-expand-all
          highlight-current
          draggable
          :allow-drag="allowDrag"
          :allow-drop="allowDrop"
          :current-node-key="selectedClientId"
          :expand-on-click-node="false"
          @node-click="selectedClientId = $event.client_id"
          @node-drop="onNodeDrop"
        >
          <template #default="{ data }: { data: EditorNode }">
            <span class="editor-node" :class="[{ locked: !isEditable(data), removed: data.pending_delete, added: data.change_state === 'NEW', modified: data.change_state === 'MODIFIED' }]">
              <span class="node-copy">
                <el-icon class="drag-handle"><Rank /></el-icon>
                <small>{{ data.outline_number }}</small>
                <span class="node-title">{{ data.title }}</span>
                <el-badge v-if="data.code && contentGroupFor(data.code)" :value="contentGroupFor(data.code)?.suggestion_ids.length" class="suggestion-badge" />
              </span>
              <span v-if="isEditable(data)" class="node-actions" @click.stop>
                <el-button link :icon="Plus" title="新增下级需求" aria-label="新增下级需求" @click="addChild(data.client_id)" />
                <el-button link type="danger" :icon="Delete" title="从新版本移除" aria-label="从新版本移除" @click="removeNode(data)" />
              </span>
              <el-icon v-else-if="!data.pending_delete" class="lock-icon" title="评审范围之外，仅供查看"><Lock /></el-icon>
              <span v-else class="removed-label">待移除</span>
            </span>
          </template>
        </el-tree>
      </aside>

      <main class="editor-form">
        <template v-if="sourceReview">
          <div v-if="planning || revisionPlan?.status === 'QUEUED' || revisionPlan?.status === 'RUNNING'" class="planning-state">
            <el-icon class="is-loading"><MagicStick /></el-icon>
            <strong>AI 正在归类评审建议并生成修订草稿</strong>
            <span>页面可关闭，后台会继续处理；再次从评审记录进入即可查看。</span>
          </div>
          <el-result v-else-if="revisionPlan?.status === 'FAILED'" icon="error" title="AI 修订规划生成失败" :sub-title="revisionPlan.error_message || '请检查模型连接和输出结构'">
            <template #extra><div class="retry-actions"><span>重新生成会创建一次新的后台调用，原失败结果仍保留在 AI 调用审计中。</span><el-button type="primary" @click="sourceReview && startRevisionPlan(sourceReview)">重新生成规划</el-button></div></template>
          </el-result>
          <el-tabs v-else-if="planResult" v-model="workbenchTab" class="revision-tabs">
            <el-tab-pane :label="`内容修订 ${planResult.content_revisions.length}`" name="CONTENT">
              <template v-if="selectedNode && selectedContentGroup">
                <div class="section-heading">
                  <div><strong>{{ selectedNode.title }}</strong><span>{{ selectedContentGroup.reason }}</span></div>
                  <div>
                    <el-button v-if="appliedContentCodes.has(selectedContentGroup.target_requirement_code)" @click="undoContent(selectedContentGroup.target_requirement_code)">撤销应用</el-button>
                    <el-button type="primary" @click="applyContent(selectedContentGroup)">{{ appliedContentCodes.has(selectedContentGroup.target_requirement_code) ? '重新应用修订稿' : '应用本需求全部建议' }}</el-button>
                  </div>
                </div>
                <div class="suggestion-chips">
                  <div v-for="item in suggestionsFor(selectedContentGroup.suggestion_ids)" :key="item.id"><el-tag size="small" effect="plain">{{ item.category }}</el-tag><span>{{ item.content }}</span></div>
                </div>
                <div class="compare-grid">
                  <section><header>原始需求内容</header><el-input :model-value="originalMarkdown[selectedContentGroup.target_requirement_code]" type="textarea" :rows="17" disabled /></section>
                  <section><header>AI 修订稿（可手工编辑）</header><el-input v-model="revisionDrafts[selectedContentGroup.target_requirement_code]" type="textarea" :rows="17" /></section>
                </div>
              </template>
              <el-empty v-else description="请在左侧选择带建议数量的需求" />
            </el-tab-pane>

            <el-tab-pane :label="`新增需求 ${planResult.additions.length}`" name="ADD">
              <div class="batch-actions"><span>新增建议会先加入修订草稿，发布后才分配稳定需求编号。</span><el-button v-if="planResult.additions.length" type="success" @click="applyAllAdditions">一键应用全部新增建议</el-button></div>
              <div class="structure-list">
                <article v-for="item in planResult.additions" :key="item.client_key" class="structure-card addition-card">
                  <header><div><el-tag type="success">新增需求</el-tag><strong>{{ item.title }}</strong></div><div><el-button v-if="appliedAdditionKeys.has(item.client_key)" @click="undoAddition(item)">撤销</el-button><el-button v-else type="success" @click="applyAddition(item)">加入修订草稿</el-button></div></header>
                  <p>{{ item.reason }}</p><small>建议父级：{{ item.parent_requirement_code }}{{ item.insert_after_requirement_code ? ` · 插入到 ${item.insert_after_requirement_code} 之后` : '' }}</small>
                  <div class="suggestion-chips compact"><div v-for="suggestion in suggestionsFor(item.suggestion_ids)" :key="suggestion.id"><el-tag size="small">{{ suggestion.category }}</el-tag><span>{{ suggestion.content }}</span></div></div>
                  <el-input v-model="item.proposed_markdown" type="textarea" :rows="5" />
                </article>
              </div>
              <el-empty v-if="!planResult.additions.length" description="AI 未建议新增需求" />
            </el-tab-pane>

            <el-tab-pane :label="`删除需求 ${planResult.deletions.length}`" name="DELETE">
              <div class="batch-actions warning"><span>这里只标记“新版中移除”，历史版本、测试关联和评审记录不会物理删除。</span><el-button v-if="planResult.deletions.length" type="danger" plain @click="applyAllDeletions">一键应用全部删除建议</el-button></div>
              <div class="structure-list">
                <article v-for="item in planResult.deletions" :key="item.target_requirement_code" class="structure-card deletion-card">
                  <header><div><el-tag type="danger">删除建议</el-tag><strong>{{ item.target_requirement_code }}</strong></div><div><el-button v-if="appliedDeletionCodes.has(item.target_requirement_code)" @click="undoDeletion(item)">撤销</el-button><el-button v-else type="danger" plain @click="applyDeletion(item)">标记为新版移除</el-button></div></header>
                  <p>{{ item.reason }}</p><small>{{ item.delete_mode === 'SUBTREE' ? '删除方式：当前需求及全部下级一起移除' : '删除方式：仅移除当前需求，下级提升到父级' }}</small>
                  <div class="suggestion-chips compact"><div v-for="suggestion in suggestionsFor(item.suggestion_ids)" :key="suggestion.id"><el-tag size="small" type="danger" effect="plain">{{ suggestion.category }}</el-tag><span>{{ suggestion.content }}</span></div></div>
                </article>
              </div>
              <el-empty v-if="!planResult.deletions.length" description="AI 未建议删除需求" />
            </el-tab-pane>

            <el-tab-pane :label="`待确认 ${planResult.unresolved.length}`" name="UNRESOLVED">
              <el-alert title="AI 无法可靠确定这些建议的目标，平台不会自动修改任何需求" type="warning" :closable="false" show-icon />
              <div class="structure-list unresolved-list"><article v-for="(item, index) in planResult.unresolved" :key="index" class="structure-card"><p>{{ item.reason }}</p><div class="suggestion-chips compact"><div v-for="suggestion in suggestionsFor(item.suggestion_ids)" :key="suggestion.id"><el-tag size="small" type="warning">{{ suggestion.category }}</el-tag><span>{{ suggestion.content }}</span></div></div></article></div>
            </el-tab-pane>
          </el-tabs>
        </template>

        <template v-else-if="selectedNode">
          <el-form label-position="top">
            <div class="editor-form-grid routing-fields">
              <el-form-item label="需求标题"><el-input v-model="selectedNode.title" maxlength="255" show-word-limit /></el-form-item>
              <el-form-item label="需求类型"><el-select v-model="selectedNode.type"><el-option label="功能" value="FEATURE" /><el-option label="规则" value="RULE" /><el-option label="验收标准" value="ACCEPTANCE_CRITERIA" /><el-option label="章节" value="SECTION" /></el-select></el-form-item>
              <el-form-item label="验证方式"><el-select v-model="selectedNode.verification_type"><el-option label="自动判断" value="AUTO" /><el-option label="API 测试" value="API" /><el-option label="Web 自动化" value="WEB" /><el-option label="性能测试" value="PERFORMANCE" /><el-option label="平台流程" value="PLATFORM" /><el-option label="人工验证" value="MANUAL" /></el-select></el-form-item>
              <el-form-item label="自动化就绪度"><el-select v-model="selectedNode.automation_readiness"><el-option label="可自动执行" value="READY" /><el-option label="需要澄清" value="NEEDS_CLARIFICATION" /><el-option label="仅人工验证" value="MANUAL_ONLY" /></el-select></el-form-item>
            </div>
            <el-form-item label="需求内容"><el-input v-model="selectedNode.markdown_content" type="textarea" :rows="17" placeholder="使用 Markdown 编写此节点的需求正文" /></el-form-item>
          </el-form>
        </template>
        <el-empty v-else description="新增或选择一个需求" />
      </main>
    </div>

    <el-form label-position="top" class="publish-summary"><el-form-item label="版本变更说明"><el-input v-model="form.changeSummary" maxlength="500" show-word-limit /></el-form-item></el-form>
    <template #footer><el-button @click="visible = false">{{ sourceReview && revisionPlan?.status !== 'SUCCEEDED' ? '关闭，后台继续' : '关闭，不保存草稿' }}</el-button><el-button type="primary" :loading="publishing" :disabled="!canWrite || (sourceReview ? revisionPlan?.status !== 'SUCCEEDED' : false)" @click="publish">发布完整需求新版本</el-button></template>
  </el-dialog>
</template>

<style scoped>
.dialog-titlebar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-right:34px}.dialog-titlebar strong{color:#17233c;font-size:20px;line-height:1.4}.fullscreen-button{border-color:#b9c7dd;background:#f7f9fc;color:#25334d;font-weight:600}.revision-summary{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:12px;padding:12px 16px;border:1px solid #d8d2ff;border-radius:12px;background:#f8f6ff}.revision-summary strong,.revision-summary span{display:block}.revision-summary span{margin-top:4px;color:#716b87;font-size:12px}.plan-counts{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:6px}.tree-editor-layout{display:grid;grid-template-columns:minmax(370px,35%) minmax(0,1fr);height:min(650px,67vh);margin-top:12px;border:1px solid #e1e8f2;border-radius:12px;overflow:hidden}.tree-editor-layout.fullscreen{height:calc(100vh - 250px)}.editor-tree{min-width:0;padding:14px;border-right:1px solid #e1e8f2;overflow:auto}.editor-tree>header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.editor-tree header strong,.editor-tree header span{display:block}.editor-tree header span{margin-top:3px;color:#8792a5;font-size:12px}.editor-tree :deep(.el-tree-node__content){height:36px;overflow:hidden;padding-right:4px}.editor-node{position:relative;display:flex;flex:1 1 0;width:0;height:36px;min-width:0;box-sizing:border-box;align-items:center;gap:6px;overflow:hidden;padding-right:62px}.editor-node.locked{color:#929cad}.editor-node.added{color:#2f8f61;background:#f0fbf5}.editor-node.modified{color:#2f65b0;background:#f2f7ff}.editor-node.removed{color:#c45656;background:#fff1f0;text-decoration:line-through}.node-copy{display:flex;min-width:0;width:100%;align-items:center;gap:6px;overflow:hidden}.node-copy small{flex:0 0 auto;color:#6680a6}.node-title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.drag-handle{flex:0 0 auto;color:#9aa7ba;cursor:grab}.locked .drag-handle{visibility:hidden}.suggestion-badge{margin-left:2px}.suggestion-badge :deep(.el-badge__content){position:static;transform:none}.node-actions{position:absolute;right:2px;top:50%;display:flex;width:58px;align-items:center;justify-content:flex-end;transform:translateY(-50%);background:inherit}.node-actions :deep(.el-button){margin-left:2px}.lock-icon{position:absolute;right:6px;top:50%;transform:translateY(-50%);color:#a8b0bf}.removed-label{position:absolute;right:4px;color:#d05b5b;font-size:11px;text-decoration:none}.editor-form{min-width:0;padding:14px 18px;overflow:auto}.planning-state{display:grid;min-height:420px;place-content:center;justify-items:center;gap:12px;color:#66738a;text-align:center}.planning-state .el-icon{color:#6552d9;font-size:34px}.planning-state strong{color:#332c6b;font-size:18px}.retry-actions{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px}.retry-actions span{max-width:480px;color:#7b8495;font-size:12px;line-height:1.5}.revision-tabs{height:100%}.revision-tabs :deep(.el-tabs__content){overflow:visible}.section-heading,.structure-card header,.batch-actions{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.section-heading strong,.section-heading span{display:block}.section-heading strong{font-size:17px}.section-heading span{margin-top:5px;color:#6f7a8d;font-size:13px;line-height:1.5}.suggestion-chips{display:grid;gap:7px;margin:12px 0}.suggestion-chips>div{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border-radius:8px;background:#f7f9fc;color:#566277;font-size:13px;line-height:1.45}.compare-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.compare-grid section{min-width:0}.compare-grid header{margin-bottom:7px;color:#39465d;font-weight:600}.batch-actions{align-items:center;margin-bottom:12px;padding:10px 12px;border-radius:8px;background:#f3f8ff;color:#5f6f87;font-size:13px}.batch-actions.warning{background:#fff8ee}.structure-list{display:grid;gap:12px}.structure-card{padding:14px;border:1px solid #e1e7ef;border-radius:10px;background:#fff}.structure-card header>div{display:flex;align-items:center;gap:8px}.structure-card p{margin:10px 0 5px;color:#4d596d;line-height:1.55}.structure-card small{color:#7a879a}.addition-card{border-left:4px solid #67c23a}.deletion-card{border-left:4px solid #f56c6c}.suggestion-chips.compact{margin:10px 0}.unresolved-list{margin-top:12px}.editor-form-grid{display:grid;grid-template-columns:minmax(0,1fr) 180px;gap:14px}.editor-form-grid.routing-fields{grid-template-columns:minmax(260px,1fr) repeat(3,minmax(150px,190px))}.publish-summary{margin-top:12px}.publish-summary :deep(.el-form-item){margin-bottom:0}@media(max-width:1100px){.editor-form-grid.routing-fields{grid-template-columns:1fr 1fr}}@media(max-width:900px){.revision-summary{align-items:flex-start;flex-direction:column}.tree-editor-layout{grid-template-columns:1fr;height:auto}.tree-editor-layout.fullscreen{height:auto}.editor-tree{max-height:320px;border-right:0;border-bottom:1px solid #e1e8f2}.compare-grid,.editor-form-grid,.editor-form-grid.routing-fields{grid-template-columns:1fr}.section-heading,.batch-actions{align-items:stretch;flex-direction:column}}
</style>
