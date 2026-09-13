<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import { getApiErrorMessage } from '@/api/http'
import { getProjects } from '@/api/projects'
import { getReports } from '@/api/reports'
import type { Project } from '@/types/project'
import type { ReportSummary } from '@/types/reports'
import type { RunStatus, RunType } from '@/types/run'
import {
  formatReportDate,
  formatReportDuration,
  runStatusLabel,
  runStatusTagType,
  runTypeLabel,
} from '@/utils/report-display'

const route = useRoute()
const router = useRouter()

const projects = ref<Project[]>([])
const projectsLoading = ref(false)
const projectsError = ref<string | null>(null)
const projectId = ref<number>()
const runType = ref<RunType>()
const status = ref<RunStatus>()
const environmentId = ref<number>()
const dateRange = ref<[string, string] | null>(null)
const items = ref<ReportSummary[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const error = ref<string | null>(null)
let listSequence = 0

const currentProject = computed(() => projects.value.find((item) => item.id === projectId.value) ?? null)
const runTypes: RunType[] = ['API_CASE', 'SCENARIO', 'WEB_CASE']
const statuses: RunStatus[] = [
  'CREATED', 'QUEUED', 'ASSIGNED', 'RUNNING', 'CANCELLING',
  'SUCCESS', 'FAILED', 'CANCELLED', 'TIMEOUT',
]

function queryProjectId(): number | undefined {
  const value = Array.isArray(route.query.project_id) ? route.query.project_id[0] : route.query.project_id
  if (typeof value !== 'string' || !/^[1-9]\d*$/.test(value)) return undefined
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) ? parsed : undefined
}

function projectLabel(project: Project): string {
  return `${project.name}（${project.code}）${project.status === 'ARCHIVED' ? ' · 已归档' : ''}`
}

function targetLabel(item: ReportSummary): string {
  return item.target.asset_name || item.target.asset_code || `${runTypeLabel(item.run_type)}资产`
}

function reportError(errorValue: unknown): string {
  const statusCode = (errorValue as { response?: { status?: number } }).response?.status
  if (statusCode === 403 || statusCode === 404) return '无权访问该项目报告，或项目不存在。'
  return getApiErrorMessage(errorValue, '报告目录加载失败，请稍后重试。')
}

async function loadReports(resetPage = false): Promise<void> {
  if (!projectId.value) {
    items.value = []
    total.value = 0
    return
  }
  if (resetPage) page.value = 1
  const requestSequence = ++listSequence
  const selectedProject = projectId.value
  loading.value = true
  error.value = null
  try {
    const response = await getReports({
      project_id: selectedProject,
      run_type: runType.value,
      status: status.value,
      environment_id: environmentId.value,
      created_from: dateRange.value?.[0],
      created_to: dateRange.value?.[1],
      page: page.value,
      page_size: pageSize.value,
    })
    if (requestSequence !== listSequence || projectId.value !== selectedProject) return
    items.value = response.items
    total.value = response.total
    page.value = response.page
  } catch (loadError) {
    if (requestSequence !== listSequence || projectId.value !== selectedProject) return
    items.value = []
    total.value = 0
    error.value = reportError(loadError)
  } finally {
    if (requestSequence === listSequence) loading.value = false
  }
}

async function selectProject(value: number): Promise<void> {
  projectId.value = value
  await router.replace({
    name: route.meta.projectScoped ? 'project-reports' : 'reports',
    params: route.meta.projectScoped ? { projectId: value } : {},
    query: { project_id: String(value) },
  })
  await loadReports(true)
}

async function resetFilters(): Promise<void> {
  runType.value = undefined
  status.value = undefined
  environmentId.value = undefined
  dateRange.value = null
  await loadReports(true)
}

async function changePage(value: number): Promise<void> {
  page.value = value
  await loadReports()
}

function openReport(item: ReportSummary): void {
  void router.push({
    name: route.meta.projectScoped ? 'project-report-detail' : 'report-detail',
    params: route.meta.projectScoped ? { projectId: projectId.value, runId: item.run_id } : { runId: item.run_id },
  })
}

async function loadProjects(): Promise<void> {
  projectsLoading.value = true
  projectsError.value = null
  try {
    const response = await getProjects(true)
    projects.value = response.items
    const requested = queryProjectId()
    projectId.value = requested && projects.value.some((item) => item.id === requested)
      ? requested
      : projects.value.find((item) => item.status === 'ACTIVE')?.id ?? projects.value[0]?.id
    await loadReports(true)
  } catch (loadError) {
    projects.value = []
    projectsError.value = getApiErrorMessage(loadError, '可访问项目加载失败，请稍后重试。')
  } finally {
    projectsLoading.value = false
  }
}

onMounted(loadProjects)
</script>

<template>
  <div class="reports-page">
    <header class="page-heading reports-heading">
      <div>
        <span class="eyebrow dark">只读运行报告</span>
        <h1>测试报告</h1>
        <p>按执行时锁定版本读取真实结果；报告不会重新运行、重算或补造历史。</p>
      </div>
      <div class="heading-actions">
        <el-tag v-if="currentProject?.status === 'ARCHIVED'" type="info">归档项目历史只读</el-tag>
        <el-button :icon="Refresh" :loading="loading" :disabled="!projectId" @click="loadReports()">刷新</el-button>
      </div>
    </header>

    <el-alert v-if="projectsError" :title="projectsError" type="error" show-icon :closable="false">
      <template #default><el-button link type="primary" @click="loadProjects">重试加载项目</el-button></template>
    </el-alert>

    <el-card class="filter-card" shadow="never">
      <div class="filter-grid">
        <label>
          <span>项目</span>
          <el-select
            :model-value="projectId"
            filterable
            :loading="projectsLoading"
            placeholder="选择可访问项目"
            @update:model-value="selectProject"
          >
            <el-option v-for="project in projects" :key="project.id" :label="projectLabel(project)" :value="project.id" />
          </el-select>
        </label>
        <label>
          <span>运行类型</span>
          <el-select v-model="runType" clearable placeholder="全部类型">
            <el-option v-for="item in runTypes" :key="item" :label="runTypeLabel(item)" :value="item" />
          </el-select>
        </label>
        <label>
          <span>状态</span>
          <el-select v-model="status" clearable placeholder="全部状态">
            <el-option v-for="item in statuses" :key="item" :label="runStatusLabel(item)" :value="item" />
          </el-select>
        </label>
        <label>
          <span>环境 ID</span>
          <el-input-number v-model="environmentId" :min="1" :max="2147483647" controls-position="right" placeholder="全部环境" />
        </label>
        <label class="date-field">
          <span>创建日期（Asia/Shanghai）</span>
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
          />
        </label>
        <div class="filter-actions">
          <el-button type="primary" :icon="Search" :loading="loading" :disabled="!projectId" @click="loadReports(true)">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </div>
      </div>
    </el-card>

    <el-alert v-if="error" class="page-alert" :title="error" type="error" show-icon :closable="false">
      <template #default><el-button link type="primary" @click="loadReports()">重试</el-button></template>
    </el-alert>

    <el-card class="report-list-card" shadow="never">
      <el-table v-loading="loading" :data="items" row-key="run_id" table-layout="fixed" @row-click="openReport">
        <el-table-column label="运行" min-width="190">
          <template #default="{ row }"><div class="primary-cell">{{ row.run_code }}</div><code>{{ row.run_id }}</code></template>
        </el-table-column>
        <el-table-column label="目标与锁定版本" min-width="230">
          <template #default="{ row }">
            <div class="primary-cell">{{ targetLabel(row) }}</div>
            <span class="secondary-cell">{{ runTypeLabel(row.run_type) }}<template v-if="row.target.asset_code"> · {{ row.target.asset_code }}</template> · {{ row.target.version_no ? `V${row.target.version_no}` : '已锁定执行版本' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="runStatusTagType(row.status)">{{ runStatusLabel(row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="运行原计数" min-width="195"><template #default="{ row }">总 {{ row.recorded_counts.total }} · 成功 {{ row.recorded_counts.passed }} · 失败 {{ row.recorded_counts.failed }} · 超时 {{ row.recorded_counts.timeout }}</template></el-table-column>
        <el-table-column label="用例实际计数" min-width="195"><template #default="{ row }">总 {{ row.case_status_counts.total }} · 成功 {{ row.case_status_counts.success }} · 失败 {{ row.case_status_counts.failed }} · 取消 {{ row.case_status_counts.cancelled }}</template></el-table-column>
        <el-table-column label="创建时间" width="175"><template #default="{ row }">{{ formatReportDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="耗时" width="130"><template #default="{ row }">{{ formatReportDuration(row.duration) }}</template></el-table-column>
        <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="openReport(row)">查看报告</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!loading && !error && !items.length" description="当前筛选条件下暂无报告" :image-size="72" />
      <div v-if="total" class="pagination-row">
        <el-pagination :current-page="page" :page-size="pageSize" :page-sizes="[10, 20, 50, 100]" :total="total" layout="total, sizes, prev, pager, next" @current-change="changePage" @size-change="(value: number) => { pageSize = value; loadReports(true) }" />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.reports-page { max-width: 1580px; margin: 0 auto; }
.reports-heading { align-items: center; }
.heading-actions { display: flex; align-items: center; gap: 10px; }
.filter-card, .report-list-card { border: 1px solid #e3e9f2; border-radius: 16px; }
.filter-card { margin-bottom: 18px; }
.filter-grid { display: grid; grid-template-columns: 1.35fr 1fr 1fr 1fr 2fr auto; gap: 14px; align-items: end; }
.filter-grid label { display: grid; gap: 7px; min-width: 0; color: #69758a; font-size: 12px; }
.filter-grid .el-select, .filter-grid .el-input-number, .filter-grid .el-date-editor { width: 100%; }
.filter-actions { display: flex; gap: 8px; }
.page-alert { margin-bottom: 16px; }
.report-list-card { overflow: hidden; }
.report-list-card :deep(.el-table__row) { cursor: pointer; }
.primary-cell { color: #243650; font-weight: 650; }
.secondary-cell, code { display: block; margin-top: 5px; overflow: hidden; color: #7d899c; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.pagination-row { display: flex; align-items: center; justify-content: space-between; padding: 18px 4px 2px; color: #7c8799; font-size: 12px; }
@media (max-width: 1360px) { .filter-grid { grid-template-columns: repeat(3, 1fr); } }
</style>
