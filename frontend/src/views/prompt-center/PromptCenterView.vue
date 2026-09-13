<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { CopyDocument, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { getOutputSchemas } from '@/api/ai-infrastructure'
import {
  copyPrompt, createPrompt, getPrompts, renderPrompt, restoreSystemPromptTemplate, updatePrompt,
  updateSystemPromptTemplate,
} from '@/api/prompt-center'
import type { OutputSchema } from '@/types/ai-infrastructure'
import type { AiTaskType } from '@/types/model-center'
import type {
  PromptCreateRequest, PromptDefinition, PromptRenderResult,
} from '@/types/prompt-center'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const prompts = ref<PromptDefinition[]>([])
const selected = ref<PromptDefinition | null>(null)
const outputSchemas = ref<OutputSchema[]>([])
const loading = ref(false)
const outputSchemasLoading = ref(false)
const editorVisible = ref(false)
const copyVisible = ref(false)
const testVisible = ref(false)
const renderResult = ref<PromptRenderResult | null>(null)
const variablesText = ref('{\n  "role": "资深测试专家",\n  "requirement": "示例需求"\n}')
const form = reactive({
  name: '', code: '', task_type: 'REQUIREMENT_REVIEW' as AiTaskType,
  description: '', system_prompt: '', user_template: '', change_note: '',
  output_schema_id: null as number | null,
})
const copyForm = reactive({ name: '', code: '' })
const isNew = computed(() => !selected.value)
const taskTypes: AiTaskType[] = [
  'REQUIREMENT_REVIEW', 'API_DOC_REVIEW', 'API_TEST_DESIGN', 'API_CASE_GENERATE',
  'API_SCENARIO_PLAN', 'API_SCENARIO_GENERATE',
  'WEB_CASE_GENERATE', 'WEB_TEST_PLAN', 'WEB_EXPLORATION_DECISION', 'WEB_PLAN_RECONCILE',
  'AI_ASSERTION', 'LOCATOR_HEALING',
  'WEB_FAILURE_ANALYSIS', 'PERFORMANCE_ANALYSIS', 'DEFECT_DRAFT', 'IMPACT_ANALYSIS',
]
const structuredOutputTasks = new Set<AiTaskType>([
  'WEB_CASE_GENERATE', 'WEB_TEST_PLAN', 'WEB_EXPLORATION_DECISION', 'WEB_PLAN_RECONCILE',
])
const requiresOutputSchema = computed(() => structuredOutputTasks.has(form.task_type))
const {
  items: pagedPrompts, total: promptTotal, page: promptPage,
  pageSize: promptPageSize, changePage: changePromptPage,
  changePageSize: changePromptPageSize,
} = useClientPagination(prompts)
function outputSchemaLabel(id: number | null): string {
  if (id === null) return '未绑定'
  const schema = outputSchemas.value.find((item) => item.id === id)
  return schema ? schema.name : '当前不可用或未加载'
}

function hasEnabledOutputSchema(id: number | null): boolean {
  return id !== null && outputSchemas.value.some((item) => item.id === id && item.enabled)
}

async function load(preferredId?: number): Promise<void> {
  loading.value = true
  outputSchemasLoading.value = true
  try {
    const [loadedPrompts, loadedSchemas] = await Promise.all([
      getPrompts(), getOutputSchemas(false),
    ])
    prompts.value = loadedPrompts
    outputSchemas.value = loadedSchemas
    const id = preferredId ?? selected.value?.id
    if (id) await selectPrompt(prompts.value.find((item) => item.id === id) ?? null)
  } finally {
    loading.value = false
    outputSchemasLoading.value = false
  }
}

function selectPrompt(prompt: PromptDefinition | null): void {
  selected.value = prompt
}

function openCreate(): void {
  selected.value = null
  Object.assign(form, {
    name: '', code: '', task_type: 'REQUIREMENT_REVIEW', description: '',
    system_prompt: '', user_template: '', change_note: '', output_schema_id: null,
  })
  editorVisible.value = true
}

function openVersion(): void {
  if (!selected.value?.current_version) return
  Object.assign(form, {
    name: selected.value.name, code: selected.value.code,
    task_type: selected.value.task_type, description: selected.value.description ?? '',
    system_prompt: selected.value.current_version.system_prompt,
    user_template: selected.value.current_version.user_template, change_note: '',
    output_schema_id: selected.value.current_version.output_schema_id,
  })
  editorVisible.value = true
}

async function submit(): Promise<void> {
  const creating = isNew.value
  if (structuredOutputTasks.has(form.task_type) && !hasEnabledOutputSchema(form.output_schema_id)) {
    ElMessage.warning('该 Web AI 任务 Prompt 必须选择启用的输出 Schema')
    return
  }
  try {
    let saved: PromptDefinition
    if (creating) {
      const payload: PromptCreateRequest = {
        name: form.name,
        code: form.code,
        task_type: form.task_type,
        description: form.description || null,
        system_prompt: form.system_prompt,
        user_template: form.user_template,
        output_schema_id: form.output_schema_id,
      }
      saved = await createPrompt(payload)
    } else {
      if (!selected.value!.is_builtin) {
        await updatePrompt(selected.value!.id, {
          name: form.name, description: form.description,
        })
      }
      saved = await updateSystemPromptTemplate(selected.value!.id, {
        system_prompt: form.system_prompt,
        user_template: form.user_template,
        output_schema_id: form.output_schema_id,
      })
    }
    editorVisible.value = false
    ElMessage.success(creating ? '公共 Prompt 已创建' : '公共模板已更新')
    await load(saved.id)
  } catch { ElMessage.error('保存失败，编码可能重复或内容没有变化') }
}

async function restoreBuiltin(): Promise<void> {
  if (!selected.value?.is_builtin) return
  try {
    await ElMessageBox.confirm('当前系统默认内容会被出厂模板覆盖，所有未定制项目将立即使用恢复后的内容。', '恢复系统初始模板', {
      confirmButtonText: '确认恢复', cancelButtonText: '取消', type: 'warning',
    })
    const restored = await restoreSystemPromptTemplate(selected.value.id)
    ElMessage.success('已恢复系统初始模板')
    await load(restored.id)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('恢复系统初始模板失败')
  }
}

function openCopy(): void {
  if (!selected.value) return
  Object.assign(copyForm, {
    name: `${selected.value.name} 副本`, code: `${selected.value.code}_COPY`,
  })
  copyVisible.value = true
}

async function submitCopy(): Promise<void> {
  if (!selected.value) return
  try {
    const copied = await copyPrompt(selected.value.id, copyForm)
    copyVisible.value = false
    ElMessage.success('Prompt 已复制')
    await load(copied.id)
  } catch { ElMessage.error('复制失败，请使用唯一编码') }
}

async function toggle(): Promise<void> {
  if (!selected.value) return
  await updatePrompt(selected.value.id, { enabled: !selected.value.enabled })
  await load(selected.value.id)
}

async function testRender(): Promise<void> {
  if (!selected.value) return
  try {
    renderResult.value = await renderPrompt(selected.value.id, JSON.parse(variablesText.value))
  } catch { ElMessage.error('变量必须是合法 JSON 对象') }
}

onMounted(load)
</script>

<template>
  <div class="requirement-page">
    <header class="page-heading requirement-heading"><div><span class="eyebrow dark">平台模板维护</span><h1>Prompt 中心</h1><p>公共模板不设业务版本号，修改后对未定制项目立即生效；项目差异请在项目工作区中维护版本。</p></div><div class="requirement-actions"><el-button :icon="Refresh" @click="load">刷新</el-button><el-button type="primary" :icon="Plus" @click="openCreate">新建公共 Prompt</el-button></div></header>
    <section class="prompt-workbench public-prompt-workbench" v-loading="loading">
      <aside class="prompt-list"><button v-for="prompt in pagedPrompts" :key="prompt.id" :class="{ active: selected?.id === prompt.id }" @click="selectPrompt(prompt)"><div><strong>{{ prompt.name }}</strong><el-tag :type="prompt.enabled ? 'success' : 'info'" size="small">{{ prompt.enabled ? '启用' : '停用' }}</el-tag></div><code>{{ prompt.code }}</code><span>{{ prompt.task_type }}</span></button><el-pagination v-if="promptTotal" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="promptPage" :page-size="promptPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="promptTotal" @current-change="changePromptPage" @size-change="changePromptPageSize" /></aside>
       <main class="prompt-editor"><template v-if="selected?.current_version"><header><div><span>{{ selected.task_type }}</span><h2>{{ selected.name }}</h2><el-tag type="info" effect="plain">{{ selected.is_builtin ? '系统默认' : '公共模板' }}</el-tag><p class="prompt-schema-summary">当前输出 Schema：{{ outputSchemaLabel(selected.current_version.output_schema_id) }}</p></div><div><template v-if="selected.is_builtin"><el-button @click="restoreBuiltin">恢复系统初始值</el-button></template><template v-else><el-button :icon="CopyDocument" @click="openCopy">复制</el-button><el-button @click="toggle">{{ selected.enabled ? '停用' : '启用' }}</el-button></template><el-button type="primary" @click="openVersion">编辑公共模板</el-button></div></header><el-alert title="公共模板不显示版本号" description="保存会直接更新当前公共内容，所有未做项目定制的项目会立即使用；项目定制版本不受影响。" type="info" :closable="false" /><h4>系统 Prompt</h4><pre>{{ selected.current_version.system_prompt }}</pre><h4>用户模板</h4><pre>{{ selected.current_version.user_template }}</pre><el-button type="primary" plain :icon="VideoPlay" @click="testVisible = true; testRender()">测试模板</el-button></template><el-empty v-else description="选择或创建一个 Prompt" /></main>
    </section>
     <el-dialog v-model="editorVisible" :title="isNew ? '新建公共 Prompt' : '编辑公共模板'" width="860px"><el-form label-position="top"><div v-if="!selected?.is_builtin" class="config-form-grid"><el-form-item label="名称"><el-input v-model="form.name" /></el-form-item><el-form-item label="编码"><el-input v-model="form.code" :disabled="!isNew" /></el-form-item><el-form-item label="任务分类"><el-select v-model="form.task_type" :disabled="!isNew"><el-option v-for="type in taskTypes" :key="type" :value="type" /></el-select></el-form-item></div><el-form-item v-if="!selected?.is_builtin" label="描述"><el-input v-model="form.description" /></el-form-item><el-alert v-if="!isNew" title="保存会直接更新公共模板，不创建或显示版本号；项目已有定制版本不受影响。" type="warning" :closable="false" /><el-form-item label="输出 Schema" :required="requiresOutputSchema"><el-select v-model="form.output_schema_id" clearable filterable :loading="outputSchemasLoading" placeholder="不绑定（其他任务可选）" class="schema-select"><el-option v-for="schema in outputSchemas" :key="schema.id" :label="schema.name" :value="schema.id" /></el-select><span class="field-hint">{{ requiresOutputSchema ? '该 Web AI 任务必须绑定启用的输出 Schema。' : '其他任务可按需绑定，留空表示不使用结构化输出校验。' }}</span></el-form-item><el-form-item label="系统 Prompt"><el-input v-model="form.system_prompt" type="textarea" :rows="7" /></el-form-item><el-form-item label="用户模板"><el-input v-model="form.user_template" type="textarea" :rows="8" placeholder="使用 {{ variable }} 插入变量" /></el-form-item></el-form><template #footer><el-button @click="editorVisible = false">取消</el-button><el-button type="primary" @click="submit">保存</el-button></template></el-dialog>
    <el-dialog v-model="copyVisible" title="复制 Prompt" width="520px"><el-form label-position="top"><el-form-item label="新名称"><el-input v-model="copyForm.name" /></el-form-item><el-form-item label="新编码"><el-input v-model="copyForm.code" /></el-form-item></el-form><template #footer><el-button @click="copyVisible = false">取消</el-button><el-button type="primary" @click="submitCopy">复制</el-button></template></el-dialog>
    <el-dialog v-model="testVisible" title="Prompt 模板测试" width="900px"><el-form-item label="变量 JSON"><el-input v-model="variablesText" type="textarea" :rows="7" @change="testRender" /></el-form-item><el-alert v-if="renderResult?.missing_variables.length" :title="`缺少变量：${renderResult.missing_variables.join(', ')}`" type="warning" :closable="false" /><div class="render-grid"><div><strong>系统 Prompt</strong><pre>{{ renderResult?.system_prompt }}</pre></div><div><strong>用户 Prompt</strong><pre>{{ renderResult?.user_prompt }}</pre></div></div><template #footer><el-button type="primary" @click="testRender">重新渲染</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.public-prompt-workbench {
  grid-template-columns: clamp(360px, 27vw, 420px) minmax(520px, 1fr);
}

.public-prompt-workbench .prompt-list button > div {
  align-items: flex-start;
  gap: 12px;
}

.public-prompt-workbench .prompt-list button strong {
  min-width: 0;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.public-prompt-workbench .prompt-list :deep(.el-tag) {
  flex: 0 0 auto;
  margin-top: 0;
  overflow: visible;
}

@media (max-width: 1100px) {
  .public-prompt-workbench {
    grid-template-columns: 340px minmax(480px, 1fr);
  }
}
</style>
