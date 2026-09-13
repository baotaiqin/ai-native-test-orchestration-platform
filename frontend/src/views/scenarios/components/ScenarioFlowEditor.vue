<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, CopyDocument, Delete, Setting } from '@element-plus/icons-vue'

import type {
  ScenarioNode,
  ScenarioPreviewMode,
  ScenarioValidationIssue,
} from '@/types/scenario'

const props = defineProps<{
  nodes: ScenarioNode[]
  issues: ScenarioValidationIssue[]
  stopOnFailure: boolean
}>()

const emit = defineEmits<{
  edit: [node: ScenarioNode]
  move: [nodeId: string, offset: number]
  reorder: [nodeId: string, targetId: string]
  duplicate: [nodeId: string]
  remove: [nodeId: string]
  updateNode: [nodeId: string, patch: Partial<ScenarioNode>]
  debug: [mode: ScenarioPreviewMode, node: ScenarioNode]
}>()

const NODE_WIDTH = 244
const NODE_HEIGHT = 82
const ROW_HEIGHT = 124
const BASE_X = 74
const DEPTH_X = 286

const selectedId = ref<string>()
const draggingId = ref<string>()
const dragTargetId = ref<string>()

watch(
  () => props.nodes.map((item) => item.id),
  (ids) => {
    if (!selectedId.value || !ids.includes(selectedId.value)) selectedId.value = ids[0]
  },
  { immediate: true },
)

const selectedNode = computed(() => props.nodes.find((item) => item.id === selectedId.value))
function wouldCreateParentCycle(candidate: ScenarioNode): boolean {
  if (!selectedId.value) return false
  let current: ScenarioNode | undefined = candidate
  const visited = new Set<string>()
  while (current && !visited.has(current.id)) {
    if (current.id === selectedId.value) return true
    visited.add(current.id)
    current = current.parent_id
      ? props.nodes.find((item) => item.id === current?.parent_id)
      : undefined
  }
  return false
}

const parentCandidates = computed(() => props.nodes.filter(
  (item) => ['IF', 'ELSE', 'LOOP'].includes(item.type)
    && item.id !== selectedId.value
    && !wouldCreateParentCycle(item),
))

function nodeDepth(node: ScenarioNode): number {
  let depth = 0
  let parentId = node.parent_id
  const visited = new Set([node.id])
  while (parentId && depth < 4 && !visited.has(parentId)) {
    visited.add(parentId)
    const parent = props.nodes.find((item) => item.id === parentId)
    if (!parent) break
    depth += 1
    parentId = parent.parent_id
  }
  return depth
}

const layoutNodes = computed(() => props.nodes.map((node, index) => ({
  node,
  index,
  x: BASE_X + nodeDepth(node) * DEPTH_X,
  y: 34 + index * ROW_HEIGHT,
})))

const canvasWidth = computed(() => Math.max(
  760,
  ...layoutNodes.value.map((item) => item.x + NODE_WIDTH + 90),
))
const canvasHeight = computed(() => Math.max(430, 74 + props.nodes.length * ROW_HEIGHT))

function layoutById(id: string) {
  return layoutNodes.value.find((item) => item.node.id === id)
}

const sequenceEdges = computed(() => props.nodes.slice(0, -1).flatMap((node, index) => {
  const source = layoutById(node.id)
  const target = layoutById(props.nodes[index + 1]!.id)
  if (!source || !target) return []
  const sx = source.x + NODE_WIDTH / 2
  const sy = source.y + NODE_HEIGHT
  const tx = target.x + NODE_WIDTH / 2
  const ty = target.y
  const middle = (sy + ty) / 2
  return [{ key: `sequence-${node.id}-${target.node.id}`, d: `M ${sx} ${sy} C ${sx} ${middle}, ${tx} ${middle}, ${tx} ${ty}` }]
}))

const parentEdges = computed(() => props.nodes.flatMap((node) => {
  if (!node.parent_id) return []
  const source = layoutById(node.parent_id)
  const target = layoutById(node.id)
  if (!source || !target) return []
  const sx = source.x + NODE_WIDTH
  const sy = source.y + NODE_HEIGHT / 2
  const tx = target.x
  const ty = target.y + NODE_HEIGHT / 2
  const bend = sx + Math.max(32, (tx - sx) / 2)
  return [{ key: `parent-${source.node.id}-${node.id}`, d: `M ${sx} ${sy} C ${bend} ${sy}, ${bend} ${ty}, ${tx} ${ty}` }]
}))

function issueCount(nodeId: string): number {
  return props.issues.filter((item) => item.node_id === nodeId).length
}

function issueTitle(nodeId: string): string {
  return props.issues.filter((item) => item.node_id === nodeId).map((item) => item.message).join('；')
}

function nodeTone(node: ScenarioNode): string {
  if (['START', 'END'].includes(node.type)) return 'terminal'
  if (['IF', 'ELSE', 'LOOP'].includes(node.type)) return 'control'
  if (['ASSERT_STATUS', 'ASSERT_JSONPATH', 'AI_ASSERTION'].includes(node.type)) return 'assertion'
  if (['API_CLEANUP', 'SQL_CLEANUP'].includes(node.type)) return 'cleanup'
  return 'action'
}

function effectiveFailurePolicy(node: ScenarioNode): string {
  if (props.stopOnFailure && node.failure_policy === 'CONTINUE') return 'STOP（场景覆盖）'
  return node.failure_policy ?? (props.stopOnFailure ? 'STOP' : 'CONTINUE')
}

function canDebug(node: ScenarioNode): boolean {
  return node.enabled && !['START', 'END', 'IF', 'ELSE', 'LOOP'].includes(node.type)
}

function startDrag(event: DragEvent, node: ScenarioNode): void {
  if (['START', 'END'].includes(node.type)) {
    event.preventDefault()
    return
  }
  draggingId.value = node.id
  event.dataTransfer?.setData('text/plain', node.id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function dropOn(event: DragEvent, target: ScenarioNode): void {
  event.preventDefault()
  const sourceId = draggingId.value ?? event.dataTransfer?.getData('text/plain')
  draggingId.value = undefined
  dragTargetId.value = undefined
  if (!sourceId || sourceId === target.id || target.type === 'START') return
  emit('reorder', sourceId, target.id)
  selectedId.value = sourceId
}

function select(node: ScenarioNode): void {
  selectedId.value = node.id
}

function patchSelected(patch: Partial<ScenarioNode>): void {
  if (selectedNode.value) emit('updateNode', selectedNode.value.id, patch)
}
</script>

<template>
  <section class="flow-editor-shell">
    <div class="flow-help">
      <div><strong>可视化流程</strong><span>实线表示执行顺序，虚线表示 IF / ELSE / LOOP 父子作用域。</span></div>
      <span>拖动节点可调整顺序；右侧属性直接写入同一份 Scenario DSL。</span>
    </div>
    <div class="flow-workspace">
      <div class="flow-scroll">
        <div class="flow-canvas" :style="{ width: `${canvasWidth}px`, height: `${canvasHeight}px` }">
          <svg class="flow-edges" :width="canvasWidth" :height="canvasHeight" aria-hidden="true">
            <defs>
              <marker id="scenario-flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" />
              </marker>
              <marker id="scenario-parent-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" />
              </marker>
            </defs>
            <path v-for="edge in sequenceEdges" :key="edge.key" :d="edge.d" class="sequence-edge" />
            <path v-for="edge in parentEdges" :key="edge.key" :d="edge.d" class="parent-edge" />
          </svg>
          <article
            v-for="item in layoutNodes"
            :key="item.node.id"
            :class="[
              'flow-node', `tone-${nodeTone(item.node)}`,
              { selected: selectedId === item.node.id, disabled: !item.node.enabled, invalid: issueCount(item.node.id), dragging: draggingId === item.node.id, 'drag-target': dragTargetId === item.node.id },
            ]"
            :style="{ left: `${item.x}px`, top: `${item.y}px`, width: `${NODE_WIDTH}px`, height: `${NODE_HEIGHT}px` }"
            :draggable="!['START', 'END'].includes(item.node.type)"
            tabindex="0"
            @click="select(item.node)"
            @dblclick="emit('edit', item.node)"
            @keydown.enter="select(item.node)"
            @dragstart="startDrag($event, item.node)"
            @dragover.prevent="dragTargetId = item.node.id"
            @dragleave="dragTargetId = undefined"
            @drop="dropOn($event, item.node)"
            @dragend="draggingId = undefined; dragTargetId = undefined"
          >
            <div class="flow-node-index">{{ item.index + 1 }}</div>
            <div class="flow-node-copy">
              <div><span class="flow-node-type">{{ item.node.type }}</span><span v-if="item.node.parent_id" class="scope-label">作用域</span></div>
              <strong :title="item.node.name">{{ item.node.name }}</strong>
              <small>{{ item.node.id }}</small>
            </div>
            <span v-if="issueCount(item.node.id)" class="issue-badge" :title="issueTitle(item.node.id)">{{ issueCount(item.node.id) }}</span>
          </article>
        </div>
      </div>
      <aside class="flow-inspector">
        <template v-if="selectedNode">
          <div class="inspector-heading"><div><span>{{ selectedNode.type }}</span><strong>节点属性</strong></div><el-button link :icon="Setting" @click="emit('edit', selectedNode)">详细配置</el-button></div>
          <el-form label-position="top" size="small">
            <el-form-item label="节点名称"><el-input :model-value="selectedNode.name" maxlength="255" @update:model-value="patchSelected({ name: String($event) })" /></el-form-item>
            <el-form-item v-if="!['START', 'END'].includes(selectedNode.type)" label="父节点 / 作用域">
              <el-select :model-value="selectedNode.parent_id" clearable placeholder="无（顶层）" @update:model-value="patchSelected({ parent_id: $event ? String($event) : null })">
                <el-option v-for="parent in parentCandidates" :key="parent.id" :label="`${parent.type} · ${parent.name}`" :value="parent.id" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="!['START', 'END'].includes(selectedNode.type)" label="失败策略">
              <el-select :model-value="selectedNode.failure_policy" clearable placeholder="跟随场景" @update:model-value="patchSelected({ failure_policy: ($event || null) as ScenarioNode['failure_policy'] })">
                <el-option label="跟随场景" :value="null" /><el-option label="失败停止" value="STOP" /><el-option label="失败继续" value="CONTINUE" :disabled="stopOnFailure" /><el-option label="重试一次" value="RETRY_ONCE" />
              </el-select>
              <small>实际执行：{{ effectiveFailurePolicy(selectedNode) }}</small>
            </el-form-item>
            <el-form-item v-if="!['START', 'END'].includes(selectedNode.type)" label="启用"><el-switch :model-value="selectedNode.enabled" @update:model-value="patchSelected({ enabled: Boolean($event) })" /></el-form-item>
          </el-form>
          <div v-if="issueCount(selectedNode.id)" class="inspector-issues"><strong>校验问题</strong><p>{{ issueTitle(selectedNode.id) }}</p></div>
          <div class="inspector-actions">
            <el-button :icon="ArrowUp" :disabled="['START', 'END'].includes(selectedNode.type)" @click="emit('move', selectedNode.id, -1)">上移</el-button>
            <el-button :icon="ArrowDown" :disabled="['START', 'END'].includes(selectedNode.type)" @click="emit('move', selectedNode.id, 1)">下移</el-button>
            <el-button :icon="CopyDocument" :disabled="['START', 'END'].includes(selectedNode.type)" @click="emit('duplicate', selectedNode.id)">复制</el-button>
            <el-button type="danger" plain :icon="Delete" :disabled="['START', 'END'].includes(selectedNode.type)" @click="emit('remove', selectedNode.id)">删除</el-button>
          </div>
          <div v-if="canDebug(selectedNode)" class="debug-actions">
            <el-button type="success" plain @click="emit('debug', 'NODE', selectedNode)">测试节点</el-button>
            <el-button type="warning" plain @click="emit('debug', 'RUN_TO_HERE', selectedNode)">运行到此</el-button>
            <el-button type="primary" plain @click="emit('debug', 'RUN_FROM_HERE', selectedNode)">从此处运行</el-button>
          </div>
        </template>
        <el-empty v-else description="选择一个节点" :image-size="64" />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.flow-editor-shell{overflow:hidden;border:1px solid #dfe5ee;border-radius:10px;background:#f7f9fc}.flow-help{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:12px 16px;border-bottom:1px solid #e5e9f0;background:#fff;color:#687386;font-size:12px}.flow-help div{display:flex;align-items:center;gap:12px}.flow-help strong{color:#27364d;font-size:14px}.flow-workspace{display:grid;grid-template-columns:minmax(0,1fr) 292px;min-height:540px}.flow-scroll{overflow:auto;background-image:radial-gradient(#d8dee9 1px,transparent 1px);background-size:20px 20px}.flow-canvas{position:relative}.flow-edges{position:absolute;inset:0;pointer-events:none}.sequence-edge{fill:none;stroke:#7b8ca8;stroke-width:2;marker-end:url(#scenario-flow-arrow)}.parent-edge{fill:none;stroke:#8c72ce;stroke-width:1.8;stroke-dasharray:6 5;marker-end:url(#scenario-parent-arrow)}#scenario-flow-arrow path{fill:#7b8ca8}#scenario-parent-arrow path{fill:#8c72ce}.flow-node{position:absolute;box-sizing:border-box;display:grid;grid-template-columns:28px minmax(0,1fr) 24px;align-items:center;gap:9px;padding:12px;border:1px solid #cbd5e1;border-left-width:5px;border-radius:10px;background:#fff;box-shadow:0 5px 18px rgba(39,54,77,.09);cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s}.flow-node:hover,.flow-node.selected{border-color:#409eff;box-shadow:0 7px 22px rgba(64,158,255,.2);transform:translateY(-1px)}.flow-node.dragging{opacity:.42}.flow-node.drag-target{outline:3px solid rgba(64,158,255,.25)}.flow-node.disabled{filter:grayscale(.65);opacity:.58}.flow-node.invalid{border-color:#f56c6c}.tone-terminal{border-left-color:#56657a}.tone-control{border-left-color:#8c72ce}.tone-assertion{border-left-color:#e6a23c}.tone-cleanup{border-left-color:#f56c6c}.tone-action{border-left-color:#409eff}.flow-node-index{display:grid;place-items:center;width:27px;height:27px;border-radius:50%;background:#eef2f7;color:#56657a;font-size:12px}.flow-node-copy{display:grid;gap:3px;min-width:0}.flow-node-copy>div{display:flex;align-items:center;gap:6px}.flow-node-copy strong,.flow-node-copy small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.flow-node-copy strong{color:#27364d}.flow-node-copy small{color:#8a95a7;font-family:Consolas,monospace;font-size:10px}.flow-node-type{color:#5d6c82;font-size:10px;font-weight:700;letter-spacing:.05em}.scope-label{padding:1px 5px;border-radius:8px;background:#f0ebff;color:#7556ba;font-size:9px}.issue-badge{display:grid;place-items:center;width:20px;height:20px;border-radius:50%;background:#f56c6c;color:#fff;font-size:11px;font-weight:700}.flow-inspector{padding:16px;border-left:1px solid #e0e6ef;background:#fff}.inspector-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.inspector-heading>div{display:grid;gap:2px}.inspector-heading span{color:#7b879a;font-size:10px;font-weight:700}.inspector-heading strong{color:#27364d;font-size:16px}.flow-inspector :deep(.el-select){width:100%}.flow-inspector small{display:block;margin-top:5px;color:#8a95a7}.inspector-actions,.debug-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.inspector-actions .el-button,.debug-actions .el-button{margin:0}.inspector-issues{padding:10px;border-radius:7px;background:#fff2f2;color:#c45656;font-size:12px}.inspector-issues p{margin:5px 0 0;line-height:1.55}.debug-actions{padding-top:12px;border-top:1px solid #edf0f5}.debug-actions .el-button:last-child{grid-column:1/-1}@media(max-width:1150px){.flow-workspace{grid-template-columns:1fr}.flow-inspector{border-top:1px solid #e0e6ef;border-left:0}.flow-help{align-items:flex-start;flex-direction:column}.inspector-actions,.debug-actions{grid-template-columns:repeat(4,minmax(0,1fr))}.debug-actions{grid-template-columns:repeat(3,minmax(0,1fr))}.debug-actions .el-button:last-child{grid-column:auto}}@media(max-width:680px){.inspector-actions,.debug-actions{grid-template-columns:1fr 1fr}.debug-actions .el-button:last-child{grid-column:1/-1}}
</style>
