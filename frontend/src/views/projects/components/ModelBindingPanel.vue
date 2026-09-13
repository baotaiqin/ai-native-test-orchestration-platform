<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  bulkApplyModelBinding, getConnections, getModelBindings, getModels, saveModelBinding,
} from '@/api/model-center'
import { getApiErrorMessage } from '@/api/http'
import type {
  AiTaskType, ModelConfiguration, ModelProviderConnection, ModelProviderConnectionAccessType,
  ProjectModelBinding,
} from '@/types/model-center'

const props = defineProps<{ projectId: number }>()
const models = ref<ModelConfiguration[]>([])
const connections = ref<ModelProviderConnection[]>([])
const bindings = ref<ProjectModelBinding[]>([])
const loading = ref(false)
const bulkModelId = ref<number>()
const bulkApplyingRole = ref<'PRIMARY' | 'FALLBACK' | null>(null)
const accessTypeLabels: Record<ModelProviderConnectionAccessType, string> = {
  DIRECT: '官方直连', AGGREGATOR: '聚合平台', SELF_HOSTED: '本地/自托管',
}
const tasks: { value: AiTaskType; label: string }[] = [
  { value: 'REQUIREMENT_REVIEW', label: '需求评审' },
  { value: 'API_DOC_REVIEW', label: 'API 文档评审' },
  { value: 'API_CASE_GENERATE', label: 'API 用例生成' },
  { value: 'API_TEST_DESIGN', label: 'AI 测试设计' },
  { value: 'API_SCENARIO_PLAN', label: 'API 场景方案规划' },
  { value: 'API_SCENARIO_GENERATE', label: 'API Scenario 推荐' },
  { value: 'WEB_CASE_GENERATE', label: 'Web 用例生成' },
  { value: 'WEB_TEST_PLAN', label: 'Web 用例方案规划' },
  { value: 'WEB_EXPLORATION_DECISION', label: 'Web MCP 探索决策' },
  { value: 'WEB_PLAN_RECONCILE', label: 'Web 方案事实校准' },
  { value: 'AI_ASSERTION', label: 'AI 语义断言' },
  { value: 'LOCATOR_HEALING', label: '定位器自愈' },
  { value: 'WEB_FAILURE_ANALYSIS', label: 'Web 失败分析' },
  { value: 'PERFORMANCE_ANALYSIS', label: '性能分析' },
  { value: 'DEFECT_DRAFT', label: '缺陷草稿' },
  { value: 'IMPACT_ANALYSIS', label: '变更影响分析' },
]
const largeContextTasks = new Set<AiTaskType>([
  'REQUIREMENT_REVIEW', 'API_DOC_REVIEW', 'API_TEST_DESIGN', 'API_CASE_GENERATE',
  'API_SCENARIO_PLAN', 'API_SCENARIO_GENERATE', 'WEB_CASE_GENERATE', 'WEB_TEST_PLAN',
  'WEB_PLAN_RECONCILE', 'PERFORMANCE_ANALYSIS', 'IMPACT_ANALYSIS',
])
const bulkCompatibleModels = computed(() => new Set(
  models.value
    .filter((model) => tasks.every((task) => !modelIncompatibility(model, task.value)))
    .map((model) => model.id),
))
const incompatibleBindingCount = computed(() => bindings.value.filter((binding) => {
  const primary = models.value.find((model) => model.id === binding.primary_model_id)
  const fallback = models.value.find((model) => model.id === binding.fallback_model_id)
  return Boolean(
    (primary && modelIncompatibility(primary, binding.task_type))
    || (fallback && modelIncompatibility(fallback, binding.task_type)),
  )
}).length)

function bindingFor(task: AiTaskType): ProjectModelBinding | undefined {
  return bindings.value.find((item) => item.task_type === task)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    [models.value, bindings.value, connections.value] = await Promise.all([
      getModels(), getModelBindings(props.projectId), getConnections(),
    ])
  }
  finally { loading.value = false }
}

async function setPrimary(task: AiTaskType, modelId: number): Promise<void> {
  const current = bindingFor(task)
  try {
    await saveModelBinding({
      project_id: props.projectId, task_type: task, primary_model_id: modelId,
      fallback_model_id: current?.fallback_model_id ?? null,
      max_fallback: current?.fallback_model_id ? 1 : 0,
    })
    ElMessage.success('主模型绑定已保存')
    await load()
  } catch (error) { ElMessage.error(getApiErrorMessage(error, '绑定失败，请检查模型配置是否可用')) }
}

async function setFallback(task: AiTaskType, fallbackId?: number): Promise<void> {
  const current = bindingFor(task)
  if (!current) { ElMessage.info('请先选择主模型'); return }
  try {
    await saveModelBinding({
      project_id: props.projectId, task_type: task,
      primary_model_id: current.primary_model_id,
      fallback_model_id: fallbackId ?? null, max_fallback: fallbackId ? 1 : 0,
    })
    ElMessage.success('Fallback 已保存')
    await load()
  } catch (error) { ElMessage.error(getApiErrorMessage(error, '备用模型必须启用、容量足够、类型一致且不能与主模型相同')) }
}

function modelLabel(model: ModelConfiguration): string {
  const connection = connections.value.find((item) => item.id === model.connection_id)
  const connectionLabel = connection
    ? `${connection.name} · ${accessTypeLabels[connection.access_type]}`
    : '渠道不可用'
  return `${model.name} · ${model.model_name} · ${connectionLabel}`
}

function modelIncompatibility(model: ModelConfiguration, task: AiTaskType): string | null {
  if (model.model_type === 'EMBEDDING') return 'Embedding 模型不能用于生成任务'
  const capacity = model.max_input_tokens ?? model.max_context
  const required = largeContextTasks.has(task) ? 16_384 : 8_192
  return capacity !== null && capacity < required
    ? `最大输入 ${capacity} Token，至少需要 ${required} Token`
    : null
}

function taskModelLabel(model: ModelConfiguration, task: AiTaskType): string {
  const reason = modelIncompatibility(model, task)
  return `${modelLabel(model)}${reason ? ` · 不可用：${reason}` : ''}`
}

function bulkModelLabel(model: ModelConfiguration): string {
  return `${modelLabel(model)}${bulkCompatibleModels.value.has(model.id) ? '' : ' · 不能覆盖全部任务'}`
}

function modelName(id: number | null): string {
  const model = models.value.find((item) => item.id === id)
  return model ? modelLabel(model) : '未配置'
}

function taskLabel(taskType: AiTaskType): string {
  return tasks.find((item) => item.value === taskType)?.label ?? taskType
}

async function applyModelToAll(role: 'PRIMARY' | 'FALLBACK'): Promise<void> {
  if (!bulkModelId.value || bulkApplyingRole.value) {
    if (!bulkModelId.value) ElMessage.warning('请先选择要批量应用的模型')
    return
  }
  const selectedModel = models.value.find((item) => item.id === bulkModelId.value)
  if (!selectedModel) return
  if (!bulkCompatibleModels.value.has(selectedModel.id)) {
    ElMessage.warning('所选模型的输入容量不足，不能覆盖全部 AI 任务')
    return
  }
  const roleLabel = role === 'PRIMARY' ? '主模型' : '备用模型'
  const description = role === 'PRIMARY'
    ? '这会把全部任务的主模型替换为所选模型；与新主模型冲突或类型不兼容的原备用模型会被清除。'
    : '这会为已有且类型兼容的主模型绑定设置统一备用模型；未配置主模型、主备相同或类型不兼容的任务会跳过。'
  try {
    await ElMessageBox.confirm(
      `确定将“${selectedModel.name}”设为所有任务的${roleLabel}吗？${description}`,
      `批量设置${roleLabel}`,
      { confirmButtonText: '确认应用', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  bulkApplyingRole.value = role
  try {
    const result = await bulkApplyModelBinding({
      project_id: props.projectId,
      model_id: bulkModelId.value,
      role,
    })
    await load()
    if (result.skipped_count) {
      const skipped = result.items
        .filter((item) => item.status === 'SKIPPED')
        .slice(0, 5)
        .map((item) => `${taskLabel(item.task_type)}：${item.message}`)
        .join('；')
      await ElMessageBox.alert(
        `已更新 ${result.applied_count} 项，${result.unchanged_count} 项无需修改，跳过 ${result.skipped_count} 项。${skipped}`,
        '批量应用完成',
        { confirmButtonText: '知道了', type: 'warning' },
      )
    } else {
      ElMessage.success(
        `已将所选模型应用为全部任务的${roleLabel}（更新 ${result.applied_count} 项，${result.unchanged_count} 项无需修改）`,
      )
    }
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, `批量设置${roleLabel}失败`))
  } finally {
    bulkApplyingRole.value = null
  }
}

onMounted(load)
</script>

<template>
  <section class="configuration-panel">
    <header class="panel-heading"><div><h2>任务模型绑定</h2><p>为每类 AI 任务设置主模型与最多一次的故障备用模型。</p></div></header>
    <el-alert v-if="incompatibleBindingCount" class="binding-warning" :title="`发现 ${incompatibleBindingCount} 项历史模型绑定容量不足`" description="这些绑定可能在长需求或大批量 API 输入时被模型服务拒绝，请在下表重新选择容量足够的通用模型。" type="warning" show-icon :closable="false" />
    <section class="bulk-model-binding">
      <div class="bulk-model-copy">
        <strong>一键应用模型</strong>
        <span>选择一个已启用模型，统一应用到当前项目的全部 AI 任务。</span>
      </div>
      <el-select v-model="bulkModelId" filterable placeholder="选择要批量应用的模型">
        <el-option v-for="model in models" :key="model.id" :label="bulkModelLabel(model)" :value="model.id" :disabled="!bulkCompatibleModels.has(model.id)" />
      </el-select>
      <div class="bulk-model-actions">
        <el-button
          type="primary"
          :loading="bulkApplyingRole === 'PRIMARY'"
          :disabled="loading || bulkApplyingRole !== null || !bulkModelId"
          @click="applyModelToAll('PRIMARY')"
        >设为所有任务的主模型</el-button>
        <el-button
          :loading="bulkApplyingRole === 'FALLBACK'"
          :disabled="loading || bulkApplyingRole !== null || !bulkModelId"
          @click="applyModelToAll('FALLBACK')"
        >设为所有任务的备用模型</el-button>
      </div>
    </section>
    <el-table v-loading="loading" :data="tasks">
      <el-table-column prop="label" label="任务类型" min-width="210" />
      <el-table-column label="主模型" min-width="300"><template #default="{ row }"><el-select :model-value="bindingFor(row.value)?.primary_model_id" placeholder="选择主模型" @change="setPrimary(row.value, $event)"><el-option v-for="model in models" :key="model.id" :label="taskModelLabel(model, row.value)" :value="model.id" :disabled="Boolean(modelIncompatibility(model, row.value))" /></el-select></template></el-table-column>
      <el-table-column label="备用模型" min-width="300"><template #default="{ row }"><el-select :model-value="bindingFor(row.value)?.fallback_model_id" clearable :disabled="!bindingFor(row.value)" placeholder="不启用备用模型" @change="setFallback(row.value, $event)"><el-option v-for="model in models.filter((item) => item.id !== bindingFor(row.value)?.primary_model_id)" :key="model.id" :label="taskModelLabel(model, row.value)" :value="model.id" :disabled="Boolean(modelIncompatibility(model, row.value))" /></el-select></template></el-table-column>
      <el-table-column label="实际策略" min-width="200"><template #default="{ row }"><span v-if="bindingFor(row.value)">{{ modelName(bindingFor(row.value)!.primary_model_id) }}<template v-if="bindingFor(row.value)!.fallback_model_id"> → {{ modelName(bindingFor(row.value)!.fallback_model_id) }}</template></span><el-tag v-else type="info">未绑定</el-tag></template></el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.binding-warning { margin-bottom: 16px; }
.bulk-model-binding {
  display: grid;
  grid-template-columns: minmax(220px, .8fr) minmax(320px, 1.2fr) auto;
  align-items: center;
  gap: 16px;
  margin-bottom: 18px;
  padding: 16px;
  border: 1px solid #dce6f3;
  border-radius: 10px;
  background: #f8fbff;
}
.bulk-model-copy { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.bulk-model-copy strong { color: #27364d; font-size: 15px; }
.bulk-model-copy span { color: #7b8798; font-size: 12px; line-height: 1.5; }
.bulk-model-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.bulk-model-actions .el-button + .el-button { margin-left: 0; }
@media (max-width: 1100px) {
  .bulk-model-binding { grid-template-columns: 1fr; }
  .bulk-model-actions { justify-content: flex-start; }
}
</style>
