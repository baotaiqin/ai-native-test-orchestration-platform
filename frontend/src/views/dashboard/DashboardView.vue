<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Connection, DataAnalysis, Document, Refresh, TrendCharts, Warning } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import { getDashboard } from '@/api/dashboard'
import { getApiErrorMessage } from '@/api/http'
import { getProjects } from '@/api/projects'
import type { DashboardResponse } from '@/types/dashboard'
import type { Project } from '@/types/project'
import type { DashboardRecentRun } from '@/types/dashboard'
import { formatReportDate, runStatusLabel, runStatusTagType, runTypeLabel } from '@/utils/report-display'

const router = useRouter()
const projects = ref<Project[]>([])
const projectsLoading = ref(false)
const projectsError = ref<string | null>(null)
const projectScope = ref<number | 'ALL'>('ALL')
const dashboard = ref<DashboardResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
let requestSequence = 0

const selectedProjectId = computed(() => projectScope.value === 'ALL' ? undefined : projectScope.value)
const rateLabel = computed(() => {
  const rate = dashboard.value?.today_runs.success_rate
  if (!rate || rate.value === null) return '暂无已评估结果'
  return `${(rate.value * 100).toFixed(1)}%（${rate.numerator}/${rate.denominator}）`
})
const runnerHeadline = computed(() => {
  const runners = dashboard.value?.runners
  if (!runners) return '加载中'
  if (runners.visibility === 'ADMIN_ONLY' || runners.status === 'HIDDEN') return '仅管理员可见'
  if (runners.status === 'UNKNOWN' || runners.available === false) return '状态未知'
  return `${runners.online ?? '未知'} 在线`
})
const runnerDetail = computed(() => {
  const runners = dashboard.value?.runners
  if (!runners || runners.visibility === 'ADMIN_ONLY' || runners.status === 'HIDDEN') {
    return 'Runner 属于管理员资源，本页不暴露数量。'
  }
  if (runners.status === 'UNKNOWN' || runners.available === false) {
    return '心跳存储当前不可用，未知不等于 0 台在线。'
  }
  return `注册 ${runners.registered_total ?? '未知'} · 活跃 ${runners.active_total ?? '未知'} · 离线 ${runners.offline ?? '未知'} · 未知 ${runners.unknown ?? '未知'}`
})

function dashboardError(value: unknown): string {
  const statusCode = (value as { response?: { status?: number } }).response?.status
  if (statusCode === 403 || statusCode === 404) return '项目不存在，或当前用户无权查看该项目统计。'
  return getApiErrorMessage(value, 'Dashboard 加载失败，请稍后重试。')
}

async function loadDashboard(): Promise<void> {
  const sequence = ++requestSequence
  const requestedProjectId = selectedProjectId.value
  loading.value = true
  error.value = null
  try {
    const response = await getDashboard(requestedProjectId)
    if (sequence !== requestSequence || selectedProjectId.value !== requestedProjectId) return
    dashboard.value = response
  } catch (loadError) {
    if (sequence !== requestSequence || selectedProjectId.value !== requestedProjectId) return
    dashboard.value = null
    error.value = dashboardError(loadError)
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

async function selectProject(value: number | 'ALL'): Promise<void> {
  projectScope.value = value
  await loadDashboard()
}

function openReport(item: DashboardRecentRun): void {
  void router.push({ name: 'report-detail', params: { runId: item.run_id } })
}

async function loadProjects(): Promise<void> {
  projectsLoading.value = true
  projectsError.value = null
  try {
    const response = await getProjects(false)
    projects.value = response.items.filter((item) => item.status === 'ACTIVE')
    if (projectScope.value !== 'ALL' && !projects.value.some((item) => item.id === projectScope.value)) {
      projectScope.value = 'ALL'
    }
    await loadDashboard()
  } catch (loadError) {
    projects.value = []
    projectsError.value = getApiErrorMessage(loadError, '活动项目列表加载失败，请稍后重试。')
  } finally {
    projectsLoading.value = false
  }
}

onMounted(loadProjects)
</script>

<template>
  <div class="dashboard-page">
    <header class="dashboard-hero">
      <div><span class="eyebrow">业务概览</span><h1>测试业务概览</h1><p>统计由服务端按当前用户可见的活动项目聚合；刷新不会修改运行或资产状态。</p></div>
      <div class="dashboard-actions">
        <el-select :model-value="projectScope" :loading="projectsLoading" style="width: 260px" @update:model-value="selectProject">
          <el-option label="全部活动项目" value="ALL" />
          <el-option v-for="project in projects" :key="project.id" :label="`${project.name}（${project.code}）`" :value="project.id" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="loadDashboard">刷新</el-button>
      </div>
    </header>

    <el-alert v-if="projectsError" :title="projectsError" type="error" show-icon :closable="false"><template #default><el-button link type="primary" @click="loadProjects">重试加载项目</el-button></template></el-alert>
    <el-alert v-if="error" class="dashboard-alert" :title="error" type="error" show-icon :closable="false"><template #default><el-button link type="primary" @click="loadDashboard">重试</el-button></template></el-alert>

    <div v-if="loading && !dashboard" class="dashboard-loading" v-loading="true" />
    <template v-if="dashboard">
      <section class="scope-note"><span>统计生成于 {{ formatReportDate(dashboard.generated_at) }}</span><span>{{ dashboard.timezone }} 自然日：{{ formatReportDate(dashboard.date_range.start_utc) }} 至 {{ formatReportDate(dashboard.date_range.end_utc_exclusive) }}（结束不含）</span></section>

      <section class="metric-grid" v-loading="loading">
        <article><div class="metric-icon blue"><el-icon><Document /></el-icon></div><span>活动项目</span><strong>{{ dashboard.active_project_count }}</strong><small>仅统计当前用户可见的有效项目</small></article>
        <article><div class="metric-icon teal"><el-icon><DataAnalysis /></el-icon></div><span>测试资产</span><strong>{{ dashboard.cases.case_total }}</strong><small>API {{ dashboard.cases.api_cases }} · Web {{ dashboard.cases.web_cases }} · Scenario {{ dashboard.cases.scenarios }}（单列）</small></article>
        <article><div class="metric-icon purple"><el-icon><TrendCharts /></el-icon></div><span>今日运行</span><strong>{{ dashboard.today_runs.total }}</strong><small>成功 {{ dashboard.today_runs.success }} · 未完成 {{ dashboard.today_runs.unfinished }}</small></article>
        <article><div class="metric-icon teal"><el-icon><TrendCharts /></el-icon></div><span>今日成功率</span><strong class="rate-value">{{ rateLabel }}</strong><small>成功数 ÷ 已完成运行数</small></article>
        <article><div class="metric-icon red"><el-icon><Warning /></el-icon></div><span>失败 / 超时 / 取消</span><strong>{{ dashboard.today_runs.failed }} / {{ dashboard.today_runs.timeout }} / {{ dashboard.today_runs.cancelled }}</strong><small>超时与取消独立；取消不进入成功率分母</small></article>
        <article><div class="metric-icon amber"><el-icon><Document /></el-icon></div><span>待人工审核</span><strong>{{ dashboard.pending_reviews.total }}</strong><small>需求 {{ dashboard.pending_reviews.requirement_reviews }} · 用例建议 {{ dashboard.pending_reviews.ai_case_suggestions }} · 录制 {{ dashboard.pending_reviews.web_recording_ai_suggestions }} · Healing {{ dashboard.pending_reviews.web_healing_proposals }}</small></article>
      </section>

      <section class="dashboard-grid">
        <el-card class="recent-card" shadow="never">
          <div class="section-heading"><div><span>运行记录</span><h2>最近运行</h2></div><RouterLink to="/reports">全部报告</RouterLink></div>
          <el-table :data="dashboard.recent_runs" table-layout="fixed" @row-click="openReport">
            <el-table-column label="运行" min-width="170"><template #default="{ row }"><strong>{{ row.run_code }}</strong><small>{{ row.project_name }} · {{ runTypeLabel(row.run_type) }}</small></template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="runStatusTagType(row.status)">{{ runStatusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="创建时间" width="175"><template #default="{ row }">{{ formatReportDate(row.created_at) }}</template></el-table-column>
            <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" @click.stop="openReport(row)">报告</el-button></template></el-table-column>
          </el-table>
          <el-empty v-if="!dashboard.recent_runs.length" description="当前范围暂无最近运行" :image-size="64" />
        </el-card>

        <el-card class="runner-card" shadow="never">
          <div class="section-heading"><div><span>执行器状态</span><h2>Runner 概览</h2></div><el-icon><Connection /></el-icon></div>
          <strong>{{ runnerHeadline }}</strong>
          <p>{{ runnerDetail }}</p>
          <el-alert v-if="dashboard.runners.status === 'UNKNOWN'" title="Runner 状态未知" description="当前无法读取心跳；不会把未知显示为 0。" type="warning" show-icon :closable="false" />
          <el-alert v-else-if="dashboard.runners.visibility === 'ADMIN_ONLY'" title="管理员资源" description="当前身份无权查看全局 Runner 数量。" type="info" show-icon :closable="false" />
        </el-card>
      </section>

      <section class="definition-grid">
        <article><strong>资产口径</strong><p>{{ dashboard.cases.definition }}</p></article>
        <article><strong>今日成功率口径</strong><p>{{ dashboard.today_runs.success_rate.definition }}</p></article>
        <article><strong>待审核口径</strong><p>{{ dashboard.pending_reviews.definition }}</p></article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page { max-width: 1580px; margin: 0 auto; }
.dashboard-hero { display: flex; align-items: center; justify-content: space-between; min-height: 150px; padding: 30px 34px; border-radius: 18px; color: white; background: linear-gradient(118deg, #183365, #235b9d 60%, #168d8f); box-shadow: 0 16px 40px rgba(31, 77, 136, .18); }
.dashboard-hero h1 { margin: 10px 0 8px; font-size: 29px; }
.dashboard-hero p { margin: 0; color: #c5d9ed; }
.dashboard-actions { display: flex; gap: 10px; position: relative; z-index: 1; }
.dashboard-alert { margin-top: 16px; }
.dashboard-loading { min-height: 360px; }
.scope-note { display: flex; justify-content: space-between; gap: 20px; margin: 15px 0; color: #78859a; font-size: 11px; }
.metric-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.metric-grid article { position: relative; min-height: 138px; padding: 18px 20px; border: 1px solid #e3e9f2; border-radius: 15px; background: white; }
.metric-grid span, .metric-grid small { display: block; color: #7d899c; font-size: 11px; }
.metric-grid strong { display: block; margin: 8px 0; color: #243a5d; font-size: 25px; }
.metric-grid small { max-width: 88%; line-height: 1.55; }
.metric-icon { position: absolute; top: 18px; right: 18px; display: grid; width: 38px; height: 38px; place-items: center; border-radius: 11px; }
.metric-icon.blue { color: #3979d7; background: #edf4ff; }.metric-icon.teal { color: #168f83; background: #eaf8f5; }.metric-icon.purple { color: #7557c7; background: #f2effc; }.metric-icon.red { color: #d45050; background: #fff0f0; }.metric-icon.amber { color: #c98324; background: #fff6e9; }
.rate-value { font-size: 20px !important; }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(320px, .7fr); gap: 16px; margin-top: 16px; }
.recent-card, .runner-card { border: 1px solid #e3e9f2; border-radius: 16px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.section-heading span { color: #4f83e4; font-size: 10px; font-weight: 700; letter-spacing: .15em; }
.section-heading h2 { margin: 5px 0 0; font-size: 19px; }
.section-heading a { color: #3777d5; font-size: 12px; text-decoration: none; }
.recent-card :deep(.el-table__row) { cursor: pointer; }
.recent-card strong, .recent-card small { display: block; }.recent-card small { margin-top: 4px; color: #8490a3; font-size: 10px; }
.runner-card > strong { display: block; margin: 28px 0 8px; color: #213b64; font-size: 27px; }
.runner-card > p { min-height: 42px; color: #77849a; line-height: 1.6; }
.definition-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
.definition-grid article { padding: 16px 18px; border: 1px dashed #bdcbe0; border-radius: 13px; background: #f8fbff; }
.definition-grid strong { color: #344b6c; font-size: 12px; }.definition-grid p { margin: 7px 0 0; color: #78859a; font-size: 11px; line-height: 1.6; }
</style>
