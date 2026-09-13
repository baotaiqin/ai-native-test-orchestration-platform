<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'

import {
  createOutputSchema, createOutputSchemaVersion, generateAi, getAiCalls, getOutputSchemas,
  setOutputSchemaEnabled, validateStructuredOutput,
} from '@/api/ai-infrastructure'
import { getModels } from '@/api/model-center'
import { getProjects } from '@/api/projects'
import { getPrompts } from '@/api/prompt-center'
import type {
  AiCallLog, AiGenerateResult, OutputSchema, StructuredValidationResult,
} from '@/types/ai-infrastructure'
import type { ModelConfiguration } from '@/types/model-center'
import type { Project } from '@/types/project'
import type { PromptDefinition } from '@/types/prompt-center'
import { showAiGenerationError } from '@/utils/ai-error'
import { formatApiDateTime } from '@/utils/datetime'
import { promptOptionLabel } from '@/utils/prompt-display'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const projects = ref<Project[]>([])
const route = useRoute()
const projectId = ref<number>()
const models = ref<ModelConfiguration[]>([])
const prompts = ref<PromptDefinition[]>([])
const schemas = ref<OutputSchema[]>([])
const logs = ref<AiCallLog[]>([])
const {
  items: pagedSchemas, total: schemaTotal, page: schemaPage,
  pageSize: schemaPageSize, changePage: changeSchemaPage,
  changePageSize: changeSchemaPageSize,
} = useClientPagination(schemas)
const {
  items: pagedLogs, total: logTotal, page: logPage,
  pageSize: logPageSize, changePage: changeLogPage,
  changePageSize: changeLogPageSize,
} = useClientPagination(logs)
const activeTab = ref('schemas')
const schemaVisible = ref(false)
const editingSchema = ref<OutputSchema | null>(null)
const result = ref<StructuredValidationResult | null>(null)
const generateResult = ref<AiGenerateResult | null>(null)
const generating = ref(false)
const schemaForm = reactive({ name: '', description: '', schemaText: '' })
const validateForm = reactive({
  modelId: undefined as number | undefined,
  promptId: undefined as number | undefined,
  schemaId: undefined as number | undefined,
  rawOutput: '{\n  "summary": "示例结果"\n}',
  repairOutput: '', inputToken: 0, outputToken: 0, latencyMs: 0,
})
const generateForm = reactive({
  promptId: undefined as number | undefined,
  variablesText: '{\n  "requirement": "请填写待处理内容"\n}',
  entityType: '', entityId: '',
})
const selectedPrompt = computed(() =>
  prompts.value.find((item) => item.id === validateForm.promptId),
)

async function load(): Promise<void> {
  [models.value, prompts.value, schemas.value] = await Promise.all([
    getModels(), getPrompts(false), getOutputSchemas(),
  ])
  if (projectId.value) logs.value = await getAiCalls(projectId.value)
}

function openSchema(schema?: OutputSchema): void {
  editingSchema.value = schema ?? null
  Object.assign(schemaForm, {
    name: schema?.name ?? '', description: schema?.description ?? '',
    schemaText: JSON.stringify(schema?.schema_json ?? {
      type: 'object', required: ['summary'], additionalProperties: false,
      properties: { summary: { type: 'string' } },
    }, null, 2),
  })
  schemaVisible.value = true
}

async function saveSchema(): Promise<void> {
  try {
    const schemaJson = JSON.parse(schemaForm.schemaText) as Record<string, unknown>
    if (editingSchema.value) {
      await createOutputSchemaVersion(editingSchema.value.id, {
        description: schemaForm.description, schema_json: schemaJson,
      })
    } else {
      await createOutputSchema({
        name: schemaForm.name, description: schemaForm.description, schema_json: schemaJson,
      })
    }
    schemaVisible.value = false
    ElMessage.success(editingSchema.value ? 'Schema 新版本已创建' : 'Schema 已创建')
    await load()
  } catch { ElMessage.error('保存失败，请检查 Schema JSON 或名称') }
}

async function toggleSchema(schema: OutputSchema): Promise<void> {
  await setOutputSchemaEnabled(schema.id, !schema.enabled)
  await load()
}

async function runValidation(): Promise<void> {
  if (!projectId.value || !validateForm.modelId || !selectedPrompt.value?.current_version_id || !validateForm.schemaId) {
    ElMessage.warning('请选择项目、模型、Prompt 和输出 Schema')
    return
  }
  try {
    result.value = await validateStructuredOutput({
      project_id: projectId.value, task_type: selectedPrompt.value.task_type,
      model_config_id: validateForm.modelId,
      prompt_version_id: selectedPrompt.value.current_version_id,
      output_schema_id: validateForm.schemaId, raw_output: validateForm.rawOutput,
      repair_output: validateForm.repairOutput || undefined,
      input_token: validateForm.inputToken, output_token: validateForm.outputToken,
      latency_ms: validateForm.latencyMs,
    })
    ElMessage[result.value.success ? 'success' : 'warning'](
      result.value.success ? '结构化输出校验通过' : '校验失败，调用记录已保存',
    )
    logs.value = await getAiCalls(projectId.value)
  } catch { ElMessage.error('验证请求失败，请检查 Prompt 与 Schema 是否匹配') }
}

async function runGenerate(): Promise<void> {
  const prompt = prompts.value.find((item) => item.id === generateForm.promptId)
  if (!projectId.value || !prompt) {
    ElMessage.warning('请选择项目和 Prompt')
    return
  }
  generating.value = true
  try {
    generateResult.value = await generateAi({
      project_id: projectId.value, task_type: prompt.task_type, prompt_id: prompt.id,
      variables: JSON.parse(generateForm.variablesText),
      entity_type: generateForm.entityType || undefined,
      entity_id: generateForm.entityId || undefined,
    })
    ElMessage.success('AI 任务执行成功，调用记录已保存')
    logs.value = await getAiCalls(projectId.value)
  } catch (error) { showAiGenerationError(error, 'AI 调用失败') }
  finally { generating.value = false }
}

onMounted(async () => {
  projects.value = (await getProjects()).items
  const requestedProjectId = Number(route.query.project_id)
  projectId.value = projects.value.some((item) => item.id === requestedProjectId)
    ? requestedProjectId
    : projects.value[0]?.id
  await load()
})
watch(projectId, async (id) => { logs.value = id ? await getAiCalls(id) : [] })
</script>

<template>
  <div class="projects-page">
    <header class="page-heading"><div><span class="eyebrow dark">AI 输出与审计</span><h1>AI 结构化输出与审计</h1><p>按 Schema 校验、单次修复，以及模型调用的 Token、费用和延迟追踪。</p></div><div class="requirement-actions"><el-select v-model="projectId" placeholder="选择项目" style="width: 210px"><el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" /></el-select><el-button :icon="Refresh" @click="load">刷新</el-button></div></header>
    <section class="settings-tabs"><el-tabs v-model="activeTab"><el-tab-pane label="输出 Schema" name="schemas"><section class="configuration-panel"><header class="panel-heading"><div><h2>输出 Schema</h2><p>每次修改创建新版本，历史版本继续供既有 Prompt 与调用记录引用。</p></div><el-button type="primary" :icon="Plus" @click="openSchema()">新建 Schema</el-button></header><el-table :data="pagedSchemas"><el-table-column prop="name" label="名称" min-width="180" /><el-table-column label="版本" width="90"><template #default="{ row }: { row: OutputSchema }">V{{ row.version_no }}</template></el-table-column><el-table-column prop="description" label="说明" min-width="220" /><el-table-column label="状态" width="100"><template #default="{ row }: { row: OutputSchema }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column><el-table-column label="操作" width="200"><template #default="{ row }: { row: OutputSchema }"><el-button link type="primary" @click="openSchema(row)">创建新版本</el-button><el-button link @click="toggleSchema(row)">{{ row.enabled ? '停用' : '启用' }}</el-button></template></el-table-column></el-table><el-pagination v-if="schemaTotal" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="schemaPage" :page-size="schemaPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="schemaTotal" @current-change="changeSchemaPage" @size-change="changeSchemaPageSize" /></section></el-tab-pane>
      <el-tab-pane label="真实 AI 调用" name="generate"><section class="ai-validator"><el-form label-position="top"><el-form-item label="Prompt / 任务类型"><el-select v-model="generateForm.promptId" placeholder="按任务绑定自动选择模型"><el-option v-for="prompt in prompts" :key="prompt.id" :label="promptOptionLabel(prompt, true)" :value="prompt.id" /></el-select></el-form-item><div class="config-form-grid"><el-form-item label="关联实体类型"><el-input v-model="generateForm.entityType" placeholder="例如 REQUIREMENT" /></el-form-item><el-form-item label="关联实体 ID"><el-input v-model="generateForm.entityId" /></el-form-item></div><el-form-item label="Prompt 变量 JSON"><el-input v-model="generateForm.variablesText" type="textarea" :rows="12" /></el-form-item><el-alert title="调用会使用项目任务模型绑定；只有超时、限流、服务不可用或网络错误才触发一次备用模型切换。" type="info" :closable="false" /><el-button type="primary" :icon="VideoPlay" :loading="generating" style="margin-top: 16px" @click="runGenerate">执行 AI 任务</el-button></el-form><div v-if="generateResult" class="validation-result"><header><strong>{{ generateResult.actual_model }}</strong><div><el-tag v-if="generateResult.fallback_used" type="warning">已切换备用模型</el-tag><el-tag v-if="generateResult.repair_used">已修复输出</el-tag><el-tag type="success">{{ generateResult.total_token }} Token</el-tag></div></header><pre>{{ JSON.stringify(generateResult.parsed_result, null, 2) }}</pre></div></section></el-tab-pane>
      <el-tab-pane label="结构化验证台" name="validator"><section class="ai-validator"><el-form label-position="top"><div class="config-form-grid"><el-form-item label="模型"><el-select v-model="validateForm.modelId"><el-option v-for="model in models" :key="model.id" :label="`${model.name} · ${model.model_name}`" :value="model.id" /></el-select></el-form-item><el-form-item label="Prompt"><el-select v-model="validateForm.promptId"><el-option v-for="prompt in prompts" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" /></el-select></el-form-item><el-form-item label="输出 Schema"><el-select v-model="validateForm.schemaId"><el-option v-for="schema in schemas.filter((item) => item.enabled)" :key="schema.id" :label="`${schema.name} · V${schema.version_no}`" :value="schema.id" /></el-select></el-form-item><el-form-item label="Token（输入 / 输出）"><div class="token-inputs"><el-input-number v-model="validateForm.inputToken" :min="0" /><el-input-number v-model="validateForm.outputToken" :min="0" /></div></el-form-item></div><el-form-item label="模型原始输出"><el-input v-model="validateForm.rawOutput" type="textarea" :rows="10" /></el-form-item><el-form-item label="可选修复输出（留空时执行一次保守自动修复）"><el-input v-model="validateForm.repairOutput" type="textarea" :rows="5" /></el-form-item><el-button type="primary" :icon="VideoPlay" @click="runValidation">执行校验并记录</el-button></el-form><div v-if="result" :class="['validation-result', { failed: !result.success }]"><header><strong>{{ result.success ? '校验通过' : '校验失败' }}</strong><el-tag :type="result.repair_used ? 'warning' : 'info'">{{ result.repair_used ? '已修复一次' : '未使用修复' }}</el-tag></header><pre>{{ result.success ? JSON.stringify(result.parsed_result, null, 2) : result.validation_errors.join('\n') }}</pre></div></section></el-tab-pane>
      <el-tab-pane label="AI 调用日志" name="logs"><section class="configuration-panel"><el-table :data="pagedLogs"><el-table-column prop="task_type" label="任务" min-width="190" /><el-table-column prop="actual_model" label="实际模型" min-width="150" /><el-table-column label="结果" width="95"><template #default="{ row }: { row: AiCallLog }"><el-tag :type="row.success ? 'success' : 'danger'">{{ row.success ? '成功' : '失败' }}</el-tag></template></el-table-column><el-table-column prop="total_token" label="Token" width="100" /><el-table-column prop="estimated_cost" label="预估费用" width="115" /><el-table-column prop="latency_ms" label="延迟（毫秒）" width="115" /><el-table-column label="策略" min-width="150"><template #default="{ row }: { row: AiCallLog }"><el-tag v-if="row.fallback_used" size="small" type="warning">备用模型</el-tag><el-tag v-if="row.repair_used" size="small">输出修复</el-tag><span v-if="!row.fallback_used && !row.repair_used">直接返回</span></template></el-table-column><el-table-column label="时间" min-width="180"><template #default="{ row }: { row: AiCallLog }">{{ formatApiDateTime(row.created_at) }}</template></el-table-column></el-table><el-pagination v-if="logTotal" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="logPage" :page-size="logPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="logTotal" @current-change="changeLogPage" @size-change="changeLogPageSize" /></section></el-tab-pane></el-tabs></section>
    <el-dialog v-model="schemaVisible" :title="editingSchema ? '创建 Schema 新版本' : '新建输出 Schema'" width="760px"><el-form label-position="top"><el-form-item label="名称"><el-input v-model="schemaForm.name" :disabled="Boolean(editingSchema)" /></el-form-item><el-form-item label="说明"><el-input v-model="schemaForm.description" /></el-form-item><el-form-item label="JSON Schema"><el-input v-model="schemaForm.schemaText" type="textarea" :rows="18" /></el-form-item></el-form><template #footer><el-button @click="schemaVisible = false">取消</el-button><el-button type="primary" @click="saveSchema">保存</el-button></template></el-dialog>
  </div>
</template>
