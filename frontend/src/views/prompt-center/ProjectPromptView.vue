<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Refresh, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'

import { getOutputSchemas } from '@/api/ai-infrastructure'
import { getApiErrorMessage } from '@/api/http'
import {
  getProjectPromptTemplates,
  getProjectPromptVersions,
  restoreProjectPromptDefault,
  saveProjectPromptVersion,
} from '@/api/prompt-center'
import type { OutputSchema } from '@/types/ai-infrastructure'
import type { ProjectPromptTemplate, PromptVersion } from '@/types/prompt-center'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { formatApiDateTime } from '@/utils/datetime'

const route = useRoute()
const projectId = computed(() => Number(route.params.projectId))
const templates = ref<ProjectPromptTemplate[]>([])
const selected = ref<ProjectPromptTemplate | null>(null)
const versions = ref<PromptVersion[]>([])
const schemas = ref<OutputSchema[]>([])
const loading = ref(false)
const saving = ref(false)
const {
  items: pagedTemplates, total: templateTotal, page: templatePage,
  pageSize: templatePageSize, changePage: changeTemplatePage,
  changePageSize: changeTemplatePageSize,
} = useClientPagination(templates)
const form = reactive({
  system_prompt: '',
  user_template: '',
  output_schema_id: null as number | null,
  change_note: '',
})
const initialSnapshot = ref('')
const snapshot = computed(() => JSON.stringify({
  system_prompt: form.system_prompt,
  user_template: form.user_template,
  output_schema_id: form.output_schema_id,
}))
const dirty = computed(() => Boolean(selected.value) && snapshot.value !== initialSnapshot.value)

function fillForm(item: ProjectPromptTemplate): void {
  Object.assign(form, {
    system_prompt: item.system_prompt,
    user_template: item.user_template,
    output_schema_id: item.output_schema_id,
    change_note: '',
  })
  initialSnapshot.value = snapshot.value
}

async function selectTemplate(item: ProjectPromptTemplate): Promise<void> {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('当前修改尚未保存，切换模板会丢弃这些输入。', '切换提示词', {
        confirmButtonText: '丢弃并切换', cancelButtonText: '继续编辑', type: 'warning',
      })
    } catch { return }
  }
  selected.value = item
  fillForm(item)
  versions.value = await getProjectPromptVersions(projectId.value, item.base_prompt_id)
}

async function load(preferredId?: number): Promise<void> {
  loading.value = true
  try {
    const [loadedTemplates, loadedSchemas] = await Promise.all([
      getProjectPromptTemplates(projectId.value), getOutputSchemas(false),
    ])
    templates.value = loadedTemplates
    schemas.value = loadedSchemas
    const next = loadedTemplates.find((item) => item.base_prompt_id === preferredId)
      ?? loadedTemplates.find((item) => item.base_prompt_id === selected.value?.base_prompt_id)
      ?? loadedTemplates[0]
    if (next) {
      selected.value = next
      fillForm(next)
      versions.value = await getProjectPromptVersions(projectId.value, next.base_prompt_id)
    }
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '项目提示词加载失败'))
  } finally { loading.value = false }
}

async function save(): Promise<void> {
  if (!selected.value) return
  if (!form.change_note.trim()) {
    ElMessage.warning('请填写本次修改说明')
    return
  }
  saving.value = true
  try {
    const saved = await saveProjectPromptVersion(projectId.value, selected.value.base_prompt_id, {
      system_prompt: form.system_prompt,
      user_template: form.user_template,
      output_schema_id: form.output_schema_id,
      change_note: form.change_note.trim(),
    })
    ElMessage.success(`项目提示词 V${saved.project_version_no} 已保存并立即生效`)
    await load(saved.base_prompt_id)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '项目提示词保存失败'))
  } finally { saving.value = false }
}

async function restoreDefault(): Promise<void> {
  if (!selected.value || selected.value.using_system_default) return
  try {
    await ElMessageBox.confirm(
      '恢复后，后续 AI 调用会立即改用平台系统默认模板；项目历史版本仍保留。',
      '恢复系统默认',
      { confirmButtonText: '恢复系统默认', cancelButtonText: '取消', type: 'warning' },
    )
    await restoreProjectPromptDefault(projectId.value, selected.value.base_prompt_id)
    ElMessage.success('已恢复系统默认提示词')
    await load(selected.value.base_prompt_id)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(getApiErrorMessage(error, '恢复失败'))
  }
}

function useHistoricalVersion(version: PromptVersion): void {
  Object.assign(form, {
    system_prompt: version.system_prompt,
    user_template: version.user_template,
    output_schema_id: version.output_schema_id,
    change_note: `基于项目 V${version.version_no} 恢复`,
  })
  ElMessage.info('历史内容已载入编辑区；点击保存后才会创建新版本并生效')
}

onMounted(() => void load())
</script>

<template>
  <div class="project-prompts-page" v-loading="loading">
    <header class="page-heading">
      <div><span class="eyebrow dark">项目 AI 配置</span><h1>项目提示词</h1><p>默认继承平台唯一的系统模板；保存修改会创建项目版本，并自动用于本项目后续 AI 调用。</p></div>
      <el-button :icon="Refresh" @click="load(selected?.base_prompt_id)">刷新</el-button>
    </header>
    <el-alert title="平台系统模板不会在 Demo 重置中创建或修改" description="“系统默认”始终由平台统一维护；只有点击“保存项目版本”后，本项目才产生定制版本。恢复系统默认不会重新调用 AI，也不会删除历史。" type="info" show-icon :closable="false" />
    <section class="prompt-layout">
      <aside class="template-list">
        <button v-for="item in pagedTemplates" :key="item.base_prompt_id" :class="{ active: selected?.base_prompt_id === item.base_prompt_id }" @click="selectTemplate(item)">
          <div><strong>{{ item.name }}</strong><el-tag :type="item.using_system_default ? 'info' : 'success'" size="small">{{ item.using_system_default ? '系统默认' : `项目 V${item.project_version_no}` }}</el-tag></div>
          <code>{{ item.task_type }}</code><span>{{ item.description }}</span>
        </button>
        <el-pagination v-if="templateTotal" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="templatePage" :page-size="templatePageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="templateTotal" @current-change="changeTemplatePage" @size-change="changeTemplatePageSize" />
      </aside>
      <main v-if="selected" class="editor-card">
        <header><div><h2>{{ selected.name }}</h2><span :class="['save-state', { dirty }]">{{ dirty ? '已修改，尚未保存' : '内容已保存' }}</span></div><div><el-button :icon="RefreshLeft" :disabled="selected.using_system_default" @click="restoreDefault">恢复系统默认</el-button><el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存项目版本</el-button></div></header>
        <el-form label-position="top">
          <el-form-item label="系统 Prompt"><el-input v-model="form.system_prompt" type="textarea" :rows="9" /></el-form-item>
          <el-form-item label="用户模板"><el-input v-model="form.user_template" type="textarea" :rows="10" /></el-form-item>
          <div class="form-row"><el-form-item label="输出 Schema"><el-select v-model="form.output_schema_id" clearable filterable><el-option v-for="schema in schemas" :key="schema.id" :label="`${schema.name}（V${schema.version_no}）`" :value="schema.id" /></el-select></el-form-item><el-form-item label="本次修改说明（必填）"><el-input v-model="form.change_note" maxlength="500" show-word-limit /></el-form-item></div>
        </el-form>
      </main>
      <aside v-if="selected" class="version-list">
        <header><strong>项目版本历史</strong><span>{{ versions.length }} 条</span></header>
        <el-empty v-if="!versions.length" description="当前项目尚未定制，正在使用系统默认" :image-size="70" />
        <button v-for="version in versions" :key="version.id" @click="useHistoricalVersion(version)"><div><strong>项目 V{{ version.version_no }}</strong><span>{{ formatApiDateTime(version.created_at) }}</span></div><p>{{ version.change_note || '无变更说明' }}</p><small>载入后需保存才会生效</small></button>
      </aside>
    </section>
  </div>
</template>

<style scoped>
.project-prompts-page { max-width: 1500px; margin: 0 auto; }.page-heading { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 18px; }.page-heading h1 { margin: 5px 0; }.page-heading p { margin: 0; color: #748198; }.prompt-layout { display: grid; grid-template-columns: 280px minmax(520px, 1fr) 250px; gap: 16px; margin-top: 16px; }.template-list,.editor-card,.version-list { overflow: hidden; border: 1px solid #e2e8f1; border-radius: 14px; background: #fff; }.template-list button,.version-list button { display: grid; width: 100%; gap: 7px; padding: 15px; border: 0; border-bottom: 1px solid #edf0f5; text-align: left; background: #fff; cursor: pointer; }.template-list button.active { background: #edf5ff; box-shadow: inset 3px 0 #409eff; }.template-list button > div,.editor-card > header,.version-list > header,.version-list button > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.template-list code,.template-list span,.version-list span,.version-list small { color: #7a879b; font-size: 12px; }.editor-card { padding: 20px; }.editor-card > header { margin-bottom: 18px; }.editor-card h2 { margin: 0 0 6px; }.save-state { color: #41a06f; font-size: 12px; }.save-state.dirty { color: #e39422; }.form-row { display: grid; grid-template-columns: 1fr 1.4fr; gap: 14px; }.form-row :deep(.el-select) { width: 100%; }.version-list > header { padding: 16px; border-bottom: 1px solid #edf0f5; }.version-list button p { margin: 0; color: #46566e; }.version-list button:hover { background: #f7faff; }@media (max-width: 1180px) { .prompt-layout { grid-template-columns: 240px 1fr; }.version-list { grid-column: 1 / -1; } }@media (max-width: 760px) { .prompt-layout { grid-template-columns: 1fr; }.form-row { grid-template-columns: 1fr; } }
</style>
