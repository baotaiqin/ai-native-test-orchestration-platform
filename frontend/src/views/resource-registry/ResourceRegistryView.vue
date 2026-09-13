<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Delete, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'

import {
  executeResourceCleanup, getResourceRegistry, planResourceCleanup,
} from '@/api/resource-registry'
import { getProjects } from '@/api/projects'
import type { CleanupOutcome, CleanupStatus } from '@/types/test-case'
import type {
  CleanupPlan, CleanupExecutionResult, ResourceRegistryEntry,
} from '@/types/resource-registry'
import type { Project } from '@/types/project'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { formatApiDateTime } from '@/utils/datetime'

const projects = ref<Project[]>([])
const route = useRoute()
const projectId = ref<number>()
const runId = ref('')
const status = ref<CleanupStatus | ''>('')
const outcome = ref<CleanupOutcome>('FAILURE')
const retryFailed = ref(false)
const items = ref<ResourceRegistryEntry[]>([])
const {
  items: pagedItems, total: itemTotal, page: itemPage,
  pageSize: itemPageSize, changePage: changeItemPage,
  changePageSize: changeItemPageSize,
} = useClientPagination(items)
const plan = ref<CleanupPlan | null>(null)
const execution = ref<CleanupExecutionResult | null>(null)
const loading = ref(false)
const error = ref('')
const emptyHint = ref('输入运行 ID 后查看资源登记记录')

function apiErrorMessage(value: unknown, fallback: string): string {
  const response = (value as { response?: { data?: { message?: string } } }).response
  return response?.data?.message ?? (value instanceof Error ? value.message : fallback)
}

async function load(): Promise<void> {
  if (!projectId.value || !runId.value.trim()) {
    items.value = []
    emptyHint.value = '输入运行 ID 后查看资源登记记录'
    return
  }
  loading.value = true
  error.value = ''
  plan.value = null
  execution.value = null
  try {
    const result = await getResourceRegistry(projectId.value, runId.value.trim(), status.value || undefined)
    items.value = result.items
    emptyHint.value = result.total ? '' : '当前运行 ID 暂无资源登记'
  } catch (value) {
    items.value = []
    error.value = apiErrorMessage(value, '加载资源登记记录失败')
  } finally { loading.value = false }
}

async function previewPlan(): Promise<void> {
  if (!projectId.value || !runId.value.trim()) { ElMessage.warning('请先选择项目并填写运行 ID'); return }
  loading.value = true
  error.value = ''
  try {
    plan.value = await planResourceCleanup(projectId.value, runId.value.trim(), outcome.value, retryFailed.value)
  } catch (value) { error.value = apiErrorMessage(value, '生成清理计划失败') }
  finally { loading.value = false }
}

async function execute(): Promise<void> {
  if (!projectId.value || !runId.value.trim()) { ElMessage.warning('请先选择项目并填写运行 ID'); return }
  try {
    await ElMessageBox.confirm(
      `将按注册顺序逆序处理运行 ${runId.value.trim()} 的资源；API 清理未配置 Runner 网络处理器时会明确保持“已跳过”。`,
      '确认执行清理', { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' },
    )
  } catch { return }
  loading.value = true
  error.value = ''
  try {
    const result = await executeResourceCleanup(
      projectId.value,
      runId.value.trim(),
      outcome.value,
      retryFailed.value,
    )
    await load()
    execution.value = result
    ElMessage.success('清理结果已记录')
  } catch (value) { error.value = apiErrorMessage(value, '执行清理失败') }
  finally { loading.value = false }
}

function statusType(value: CleanupStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (value === 'CLEANED') return 'success'
  if (value === 'FAILED') return 'danger'
  if (value === 'CLEANING' || value === 'PENDING') return 'warning'
  return 'info'
}

onMounted(async () => {
  try {
    projects.value = (await getProjects()).items
    const requestedProjectId = Number(route.query.project_id)
    projectId.value = projects.value.some((item) => item.id === requestedProjectId)
      ? requestedProjectId
      : projects.value[0]?.id
  } catch (value) {
    error.value = apiErrorMessage(value, '加载项目列表失败')
  }
})
watch([projectId, status], () => { if (runId.value.trim()) void load() })
</script>

<template>
  <div class="registry-page">
    <header class="page-heading registry-heading"><div><span class="eyebrow dark">资源清理</span><h1>资源登记与清理</h1><p>查看项目内指定运行的注册顺序、策略和后进先出清理结果。</p></div><div class="heading-actions"><el-select v-model="projectId" placeholder="选择项目" style="width:220px"><el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" /></el-select><el-input v-model="runId" placeholder="运行 ID，例如 run-20260821-01" style="width:260px" @keyup.enter="load" /><el-button :icon="Refresh" @click="load">刷新</el-button></div></header>
    <section class="registry-toolbar"><el-select v-model="status" clearable placeholder="全部状态" style="width:150px"><el-option v-for="item in ['PENDING', 'CLEANING', 'CLEANED', 'FAILED', 'SKIPPED']" :key="item" :label="item" :value="item" /></el-select><el-select v-model="outcome" style="width:180px"><el-option label="执行成功" value="SUCCESS" /><el-option label="执行失败" value="FAILURE" /><el-option label="已取消" value="CANCELLED" /><el-option label="已超时" value="TIMEOUT" /></el-select><el-checkbox v-model="retryFailed">重试失败项</el-checkbox><el-button @click="previewPlan">预览清理顺序</el-button><el-button type="primary" :icon="Delete" @click="execute">执行清理</el-button></section>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-card v-loading="loading" class="registry-card"><template #header><div class="registry-card-header"><strong>注册记录</strong><span>{{ items.length }} 条 · 序号越大越先清理</span></div></template><el-empty v-if="!items.length" :description="emptyHint" :image-size="90" /><el-table v-else :data="pagedItems" stripe><el-table-column prop="registration_sequence" label="清理序号" width="105" sortable /><el-table-column prop="resource_type" label="类型" width="120" /><el-table-column prop="resource_id" label="资源 ID" min-width="160" /><el-table-column prop="cleanup_type" label="清理方式" width="95" /><el-table-column label="策略 / 配置摘要" min-width="260"><template #default="{ row }"><div>{{ row.cleanup_config.policy }} · {{ row.cleanup_config.method || row.cleanup_config.connection_id || '-' }}</div><small>{{ row.cleanup_config.url || row.cleanup_config.sql_preview || '无摘要' }}</small></template></el-table-column><el-table-column label="状态" width="115"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ row.status }}</el-tag></template></el-table-column><el-table-column prop="attempt_count" label="尝试" width="75" /><el-table-column label="错误 / 时间" min-width="220"><template #default="{ row }"><div v-if="row.last_error" class="registry-error">{{ row.error_code }} · {{ row.last_error }}</div><small>{{ row.cleaned_at ? `清理：${formatApiDateTime(row.cleaned_at)}` : `登记：${formatApiDateTime(row.created_at)}` }}</small></template></el-table-column></el-table><el-pagination v-if="itemTotal" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="itemPage" :page-size="itemPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="itemTotal" @current-change="changeItemPage" @size-change="changeItemPageSize" /></el-card>
    <el-card v-if="plan" class="registry-card"><template #header><strong>清理计划 · {{ plan.outcome }}</strong></template><el-table :data="plan.items" stripe><el-table-column prop="resource.registration_sequence" label="序号" width="80" /><el-table-column prop="resource.resource_id" label="资源" min-width="150" /><el-table-column prop="action" label="动作" width="100" /><el-table-column prop="reason" label="原因" min-width="280" /></el-table></el-card>
    <el-card v-if="execution" class="registry-card"><template #header><strong>本次清理结果</strong></template><el-table :data="execution.items" stripe><el-table-column prop="resource.registration_sequence" label="序号" width="80" /><el-table-column prop="resource.resource_id" label="资源" min-width="150" /><el-table-column prop="status" label="结果" width="110" /><el-table-column prop="error_code" label="错误码" width="190" /><el-table-column prop="message" label="消息" min-width="320" /></el-table></el-card>
  </div>
</template>

<style scoped>
.registry-heading,.heading-actions,.registry-toolbar,.registry-card-header{display:flex;align-items:center;gap:12px}.registry-heading{justify-content:space-between}.registry-toolbar{margin:18px 0;flex-wrap:wrap}.registry-card{margin-top:16px}.registry-card-header{justify-content:space-between}.registry-card-header span,small{color:#7a8599}.registry-error{color:#c45656;white-space:normal}@media(max-width:1100px){.registry-heading{align-items:flex-start;flex-direction:column}.heading-actions{align-items:stretch;flex-wrap:wrap;width:100%}}
</style>
