<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getEnvironments } from '@/api/environments'
import { getApiErrorMessage } from '@/api/http'
import {
  comparePerformanceRuns,
  createPerformanceProfile,
  generatePerformanceAnalysis,
  getPerformanceAnalyses,
  getPerformanceProfiles,
  getPerformanceRunnerOptions,
  getPerformanceRuns,
  startPerformanceRun,
} from '@/api/performance'
import { getPrompts } from '@/api/prompt-center'
import { cancelRun, forceStopRun } from '@/api/runs'
import { getScenarios } from '@/api/scenarios'
import { getTestCases } from '@/api/test-cases'
import type { Environment } from '@/types/environment'
import type {
  PerformanceAnalysis,
  PerformanceAnalysisVerdict,
  PerformanceComparison,
  PerformanceEngine,
  PerformanceLoadMode,
  PerformanceProfile,
  PerformanceRun,
  PerformanceRunnerOption,
  PerformanceStepStage,
  PerformanceTargetType,
} from '@/types/performance'
import type { PromptDefinition } from '@/types/prompt-center'
import type { Scenario } from '@/types/scenario'
import type { TestCaseAsset } from '@/types/test-case'
import { formatApiDateTime } from '@/utils/datetime'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number(route.params.projectId || route.query.project_id))
const loading = ref(false)
const saving = ref(false)
const comparing = ref(false)
const runningProfileId = ref<number>()
const changingRunId = ref<string>()
const dialogVisible = ref(false)
const comparisonVisible = ref(false)
const detailVisible = ref(false)
const analysisVisible = ref(false)
const analysisLoading = ref(false)
const generatingAnalysis = ref(false)
const profiles = ref<PerformanceProfile[]>([])
const runs = ref<PerformanceRun[]>([])
const cases = ref<TestCaseAsset[]>([])
const scenarios = ref<Scenario[]>([])
const environments = ref<Environment[]>([])
const runners = ref<PerformanceRunnerOption[]>([])
const comparison = ref<PerformanceComparison>()
const detailRun = ref<PerformanceRun>()
const analysisRun = ref<PerformanceRun>()
const analyses = ref<PerformanceAnalysis[]>([])
const analysisPrompts = ref<PromptDefinition[]>([])
const analysisPromptId = ref<number>()
const analysisInstructions = ref('')
const selectedRunIds = ref<string[]>([])
let pollTimer: number | undefined

const form = reactive({
  name: '',
  target_type: 'API_CASE' as PerformanceTargetType,
  case_id: undefined as number | undefined,
  scenario_id: undefined as number | undefined,
  engine: 'PYTHON_HTTP' as PerformanceEngine,
  cleanup_mode: 'NONE' as 'NONE' | 'RESOURCE_CLEANUP' | 'BATCH_CLEANUP',
  concurrency: 5,
  iterations: 50,
  load_mode: 'FIXED_ITERATIONS' as PerformanceLoadMode,
  target_rps: 10,
  duration_seconds: 10,
  step_stages: [
    { concurrency: 2, duration_seconds: 10 },
    { concurrency: 5, duration_seconds: 20 },
  ] as PerformanceStepStage[],
  warmup_iterations: 5,
  request_timeout_ms: 30_000,
  stream_protocol: 'SSE' as 'SSE' | 'NDJSON',
  completion_marker: '[DONE]',
  require_completion_marker: true,
  max_error_rate_percent: 1,
  max_p95_ms: 500,
  max_p99_ms: 1000,
  max_ttft_p95_ms: 800,
  min_rps: 10,
  min_success_rate_percent: 99,
})
const launch = reactive<Record<number, { environment_id?: number; runner_id?: string }>>({})

const compatibleCases = computed(() =>
  cases.value.filter(
    (item) => item.case_type === 'API' && item.status === 'ACTIVE' && item.current_version_id,
  ),
)
const compatibleScenarios = computed(() =>
  scenarios.value.filter((item) => item.status !== 'ARCHIVED' && item.current_version_id),
)
const enabledEnvironments = computed(() => environments.value.filter((item) => item.enabled))
const plannedRpsRequests = computed(() => Math.ceil(form.target_rps * form.duration_seconds))
const stepDuration = computed(() =>
  form.step_stages.reduce((total, stage) => total + stage.duration_seconds, 0),
)
const completedRuns = computed(() => runs.value.filter((item) => item.metrics))

watch(
  () => form.target_type,
  (value) => {
    if (value === 'SCENARIO') form.engine = 'PYTHON_HTTP'
  },
)
watch(
  () => form.engine,
  (value) => {
    if (value === 'PYTHON_STREAM' || value === 'JMETER') form.cleanup_mode = 'NONE'
    if (value === 'JMETER' && !['FIXED_ITERATIONS', 'FIXED_RPS'].includes(form.load_mode)) {
      form.load_mode = 'FIXED_ITERATIONS'
    }
  },
)

function runnerSupportsProfile(runner: PerformanceRunnerOption, profile: PerformanceProfile): boolean {
  const required = new Set<string>()
  if (profile.target_type === 'API_CASE') required.add('API')
  if (profile.engine === 'PYTHON_STREAM') required.add('SSE')
  if (profile.engine === 'JMETER') required.add('JMETER')
  return [...required].every((capability) => runner.ready_capabilities.includes(capability))
}

function profileRunners(profile: PerformanceProfile): PerformanceRunnerOption[] {
  return runners.value.filter((runner) => runnerSupportsProfile(runner, profile))
}

function launchState(profile: PerformanceProfile): { environment_id?: number; runner_id?: string } {
  if (!launch[profile.id]) {
    launch[profile.id] = {
      environment_id: enabledEnvironments.value.find((item) => item.is_default)?.id,
      runner_id: profileRunners(profile)[0]?.id,
    }
  }
  return launch[profile.id]
}

async function loadAll(silent = false): Promise<void> {
  if (!Number.isInteger(projectId.value) || projectId.value <= 0) {
    await router.replace({ name: 'projects' })
    return
  }
  loading.value = !silent
  try {
    const [
      profileItems,
      runItems,
      caseItems,
      scenarioItems,
      environmentItems,
      runnerItems,
      promptItems,
    ] =
      await Promise.all([
        getPerformanceProfiles(projectId.value),
        getPerformanceRuns(projectId.value),
        getTestCases(projectId.value),
        getScenarios(projectId.value),
        getEnvironments(projectId.value),
        getPerformanceRunnerOptions(projectId.value),
        getPrompts(false, 'PERFORMANCE_ANALYSIS').catch(() => []),
      ])
    profiles.value = profileItems
    runs.value = runItems
    cases.value = caseItems
    scenarios.value = scenarioItems
    environments.value = environmentItems
    runners.value = runnerItems
    analysisPrompts.value = promptItems
    if (!analysisPromptId.value) analysisPromptId.value = promptItems[0]?.id
  } catch (error) {
    if (!silent) ElMessage.error(getApiErrorMessage(error, '性能测试数据加载失败'))
  } finally {
    loading.value = false
  }
}

function addStage(): void {
  if (form.step_stages.length < 20) {
    form.step_stages.push({ concurrency: form.concurrency, duration_seconds: 10 })
  }
}

function removeStage(index: number): void {
  if (form.step_stages.length > 1) form.step_stages.splice(index, 1)
}

async function saveProfile(): Promise<void> {
  const hasTarget = form.target_type === 'API_CASE' ? form.case_id : form.scenario_id
  if (!form.name.trim() || !hasTarget) {
    ElMessage.warning('请填写配置名称并选择性能目标')
    return
  }
  if (form.load_mode === 'FIXED_RPS' && plannedRpsRequests.value > 10_000) {
    ElMessage.warning('固定 RPS 的计划请求数不能超过 10000')
    return
  }
  if (form.load_mode === 'FIXED_ITERATIONS' && form.iterations < form.concurrency) {
    ElMessage.warning('固定迭代的统计迭代数不能小于并发数')
    return
  }
  if (form.load_mode === 'STEP_LOAD' && stepDuration.value > 3600) {
    ElMessage.warning('阶梯负载总持续时长不能超过 3600 秒')
    return
  }
  saving.value = true
  try {
    await createPerformanceProfile({
      project_id: projectId.value,
      name: form.name.trim(),
      target_type: form.target_type,
      case_id: form.target_type === 'API_CASE' ? form.case_id : undefined,
      scenario_id: form.target_type === 'SCENARIO' ? form.scenario_id : undefined,
      concurrency: form.concurrency,
      iterations: form.iterations,
      load_mode: form.load_mode,
      target_rps: form.load_mode === 'FIXED_RPS' ? form.target_rps : undefined,
      duration_seconds: ['FIXED_RPS', 'FIXED_CONCURRENCY'].includes(form.load_mode)
        ? form.duration_seconds
        : undefined,
      step_stages: form.load_mode === 'STEP_LOAD' ? form.step_stages : undefined,
      warmup_iterations: form.warmup_iterations,
      request_timeout_ms: form.request_timeout_ms,
      engine: form.engine,
      cleanup_mode: form.cleanup_mode,
      stream_config:
        form.engine === 'PYTHON_STREAM'
          ? {
              protocol: form.stream_protocol,
              completion_marker: form.completion_marker,
              require_completion_marker: form.require_completion_marker,
            }
          : {
              protocol: 'SSE',
              completion_marker: '[DONE]',
              require_completion_marker: true,
            },
      sla: {
        max_error_rate: form.max_error_rate_percent / 100,
        max_p95_ms: form.max_p95_ms,
        max_p99_ms: form.max_p99_ms,
        max_ttft_p95_ms:
          form.engine === 'PYTHON_STREAM' ? form.max_ttft_p95_ms : undefined,
        min_rps: form.min_rps,
        min_success_rate: form.min_success_rate_percent / 100,
      },
    })
    dialogVisible.value = false
    ElMessage.success('性能测试配置已创建')
    await loadAll()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '配置创建失败'))
  } finally {
    saving.value = false
  }
}

async function runProfile(profile: PerformanceProfile): Promise<void> {
  const selected = launchState(profile)
  if (!selected.environment_id || !selected.runner_id) {
    ElMessage.warning('请选择运行环境和具备所需能力的性能 Runner')
    return
  }
  runningProfileId.value = profile.id
  try {
    const result = await startPerformanceRun(profile.id, {
      environment_id: selected.environment_id,
      runner_id: selected.runner_id,
    })
    ElMessage.success(
      result.dispatch.outbox_status === 'PUBLISHED'
        ? '性能 Run 已投递'
        : 'Run 已创建，投递失败可在运行中心重试',
    )
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '性能 Run 启动失败'))
  } finally {
    runningProfileId.value = undefined
  }
}

async function stopRun(run: PerformanceRun, force = false): Promise<void> {
  try {
    await ElMessageBox.confirm(
      force ? '强制停止会立即终止隔离进程，Cleanup 可能无法完成。' : '确认取消该性能 Run？',
      force ? '强制停止' : '取消运行',
      { type: force ? 'warning' : 'info' },
    )
  } catch {
    return
  }
  changingRunId.value = run.run_id
  try {
    if (force) await forceStopRun(run.run_id)
    else await cancelRun(run.run_id)
    ElMessage.success(force ? '已提交强制停止' : '已提交取消请求')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '运行状态更新失败'))
  } finally {
    changingRunId.value = undefined
  }
}

function handleRunSelection(rows: PerformanceRun[]): void {
  selectedRunIds.value = rows.slice(0, 5).map((row) => row.run_id)
  if (rows.length > 5) ElMessage.warning('一次最多对比 5 个 Run')
}

async function compareSelected(): Promise<void> {
  if (selectedRunIds.value.length < 2) {
    ElMessage.warning('请至少选择两个已完成的性能 Run')
    return
  }
  comparing.value = true
  try {
    comparison.value = await comparePerformanceRuns(projectId.value, selectedRunIds.value)
    comparisonVisible.value = true
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '运行对比失败'))
  } finally {
    comparing.value = false
  }
}

function openDetail(run: PerformanceRun): void {
  detailRun.value = run
  detailVisible.value = true
}

async function openAnalysis(run: PerformanceRun): Promise<void> {
  analysisRun.value = run
  analysisVisible.value = true
  analysisLoading.value = true
  try {
    analyses.value = await getPerformanceAnalyses(run.run_id)
  } catch (error) {
    analyses.value = []
    ElMessage.error(getApiErrorMessage(error, 'AI 性能分析记录加载失败'))
  } finally {
    analysisLoading.value = false
  }
}

async function createAnalysis(): Promise<void> {
  if (!analysisRun.value || !analysisPromptId.value) {
    ElMessage.warning('暂无可用的性能分析 Prompt')
    return
  }
  generatingAnalysis.value = true
  try {
    const result = await generatePerformanceAnalysis(analysisRun.value.run_id, {
      prompt_id: analysisPromptId.value,
      additional_instructions: analysisInstructions.value.trim() || undefined,
    })
    analyses.value = [result, ...analyses.value.filter((item) => item.id !== result.id)]
    analysisInstructions.value = ''
    ElMessage.success('AI 性能分析已生成，SLA 结论保持平台原判定')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'AI 性能分析生成失败'))
  } finally {
    generatingAnalysis.value = false
  }
}

function analysisVerdictText(verdict: PerformanceAnalysisVerdict): string {
  if (verdict === 'MEETS_SLA') return '满足 SLA'
  if (verdict === 'MISSES_SLA') return '未满足 SLA'
  return '未配置 SLA'
}

function analysisVerdictType(
  verdict: PerformanceAnalysisVerdict,
): 'success' | 'danger' | 'info' {
  if (verdict === 'MEETS_SLA') return 'success'
  if (verdict === 'MISSES_SLA') return 'danger'
  return 'info'
}

function openRunCenter(runId: string): void {
  void router.push({
    name: 'project-runs',
    params: { projectId: projectId.value },
    query: { project_id: String(projectId.value), run_id: runId },
  })
}

function loadModeText(profile: PerformanceProfile): string {
  if (profile.load_mode === 'FIXED_RPS') {
    return `固定 RPS · ${profile.target_rps}/s · ${profile.duration_seconds}s`
  }
  if (profile.load_mode === 'FIXED_CONCURRENCY') {
    return `固定并发 · ${profile.concurrency} VU · ${profile.duration_seconds}s`
  }
  if (profile.load_mode === 'STEP_LOAD') {
    return `阶梯负载 · ${profile.step_stages?.length || 0} 阶段`
  }
  return '固定并发 / 迭代'
}

function statusType(status: string): 'success' | 'danger' | 'warning' | 'info' | 'primary' {
  if (status === 'SUCCESS') return 'success'
  if (status === 'FAILED' || status === 'TIMEOUT') return 'danger'
  if (['RUNNING', 'ASSIGNED'].includes(status)) return 'primary'
  if (['QUEUED', 'CREATED', 'CANCELLING'].includes(status)) return 'warning'
  return 'info'
}

onMounted(() => {
  void loadAll()
  pollTimer = window.setInterval(() => {
    if (
      runs.value.some(
        (item) => !['SUCCESS', 'FAILED', 'CANCELLED', 'TIMEOUT'].includes(item.status),
      )
    ) {
      void loadAll(true)
    }
  }, 5000)
})
onUnmounted(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <div v-loading="loading" class="performance-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">V1 · Performance</p>
        <h1>性能测试</h1>
        <p>复用 API 或 Scenario，执行 HTTP、SSE/LLM 与 JMeter 负载并确定性判定 SLA。</p>
      </div>
      <el-button type="primary" @click="dialogVisible = true">新建性能配置</el-button>
    </div>

    <el-alert
      v-if="!runners.length"
      type="warning"
      :closable="false"
      show-icon
      title="暂无可用性能 Runner"
    >
      <template #default>
        Runner 需在线且 PERFORMANCE Slot 大于 0；SSE/JMeter 还需相应 Capability 为 READY。
      </template>
    </el-alert>

    <section class="card">
      <h2>性能配置</h2>
      <el-empty v-if="!profiles.length" description="尚未创建性能配置" />
      <div v-else class="profile-grid">
        <article v-for="profile in profiles" :key="profile.id" class="profile-card">
          <div class="profile-title">
            <div>
              <strong>{{ profile.name }}</strong>
              <span v-if="profile.target_type === 'API_CASE'">
                API Case #{{ profile.case_id }} · V{{ profile.case_version_id }}
              </span>
              <span v-else>
                Scenario #{{ profile.scenario_id }} · V{{ profile.scenario_version_id }}
              </span>
            </div>
            <div class="tag-row">
              <el-tag>{{ profile.engine }}</el-tag>
              <el-tag type="info">{{ profile.cleanup_mode }}</el-tag>
            </div>
          </div>
          <div class="load-mode">{{ loadModeText(profile) }}</div>
          <div class="metric-strip">
            <span><b>{{ profile.concurrency }}</b>最大并发</span>
            <span><b>{{ profile.iterations }}</b>样本上限</span>
            <span><b>{{ profile.warmup_iterations }}</b>预热</span>
            <span><b>{{ profile.request_timeout_ms }}</b>ms 超时</span>
          </div>
          <div class="sla-line">
            SLA：错误率 ≤ {{ ((profile.sla.max_error_rate || 0) * 100).toFixed(2) }}%
            · P95 ≤ {{ profile.sla.max_p95_ms }}ms · RPS ≥ {{ profile.sla.min_rps }}
          </div>
          <div class="launch-row">
            <el-select v-model="launchState(profile).environment_id" placeholder="运行环境">
              <el-option
                v-for="item in enabledEnvironments"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
            </el-select>
            <el-select v-model="launchState(profile).runner_id" placeholder="性能 Runner">
              <el-option
                v-for="item in profileRunners(profile)"
                :key="item.id"
                :label="`${item.name} · 可用 ${item.performance_slots_available}`"
                :value="item.id"
              />
            </el-select>
            <el-button
              type="primary"
              :loading="runningProfileId === profile.id"
              @click="runProfile(profile)"
            >
              启动
            </el-button>
          </div>
        </article>
      </div>
    </section>

    <section class="card">
      <div class="section-heading">
        <h2>最近运行</h2>
        <el-button
          :disabled="selectedRunIds.length < 2"
          :loading="comparing"
          @click="compareSelected"
        >
          对比所选（{{ selectedRunIds.length }}）
        </el-button>
      </div>
      <el-table :data="completedRuns" empty-text="暂无已完成性能运行" @selection-change="handleRunSelection">
        <el-table-column type="selection" width="44" />
        <el-table-column label="Run" min-width="170">
          <template #default="{ row }">
            <el-button link type="primary" @click="openRunCenter(row.run_id)">{{ row.run_code }}</el-button>
            <small>{{ formatApiDateTime(row.created_at) }}</small>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }"><el-tag :type="statusType(row.status)">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="请求 / 成功" width="130">
          <template #default="{ row }">{{ `${row.metrics.request_count} / ${row.metrics.success_count}` }}</template>
        </el-table-column>
        <el-table-column label="P95 / P99" width="145">
          <template #default="{ row }">{{ `${row.metrics.p95_ms} / ${row.metrics.p99_ms} ms` }}</template>
        </el-table-column>
        <el-table-column label="RPS / TPS" width="135">
          <template #default="{ row }">{{ `${row.metrics.rps} / ${row.metrics.tps}` }}</template>
        </el-table-column>
        <el-table-column label="错误率" width="100">
          <template #default="{ row }">{{ `${(row.metrics.error_rate * 100).toFixed(2)}%` }}</template>
        </el-table-column>
        <el-table-column label="SLA" min-width="190">
          <template #default="{ row }">
            <span v-if="!row.sla_results.length">—</span>
            <el-tag
              v-for="sla in row.sla_results"
              v-else
              :key="sla.metric"
              size="small"
              :type="sla.passed ? 'success' : 'danger'"
            >
              {{ sla.metric }} {{ sla.passed ? 'PASS' : 'FAIL' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link @click="openDetail(row)">指标</el-button>
            <el-button link type="primary" @click="openAnalysis(row)">AI 分析</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-divider content-position="left">进行中的运行</el-divider>
      <el-table
        :data="runs.filter((item) => !['SUCCESS', 'FAILED', 'CANCELLED', 'TIMEOUT'].includes(item.status))"
        empty-text="当前没有进行中的性能运行"
      >
        <el-table-column prop="run_code" label="Run" min-width="180" />
        <el-table-column label="状态" width="130">
          <template #default="{ row }"><el-tag :type="statusType(row.status)">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button
              v-if="row.status !== 'CANCELLING'"
              link
              type="warning"
              :loading="changingRunId === row.run_id"
              @click="stopRun(row)"
            >取消</el-button>
            <el-button
              v-else
              link
              type="danger"
              :loading="changingRunId === row.run_id"
              @click="stopRun(row, true)"
            >强制停止</el-button>
            <el-button link @click="openRunCenter(row.run_id)">运行中心</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="dialogVisible" title="新建性能测试配置" width="760px">
      <el-form label-position="top">
        <el-form-item label="配置名称" required><el-input v-model="form.name" maxlength="255" /></el-form-item>
        <div class="form-grid">
          <el-form-item label="目标类型">
            <el-radio-group v-model="form.target_type">
              <el-radio-button value="API_CASE">API 用例</el-radio-button>
              <el-radio-button value="SCENARIO">Scenario</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="执行引擎">
            <el-select v-model="form.engine" class="wide">
              <el-option label="Python HTTP" value="PYTHON_HTTP" />
              <el-option v-if="form.target_type === 'API_CASE'" label="Python SSE / LLM" value="PYTHON_STREAM" />
              <el-option v-if="form.target_type === 'API_CASE'" label="JMeter" value="JMETER" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item v-if="form.target_type === 'API_CASE'" label="复用 API 用例" required>
          <el-select v-model="form.case_id" filterable class="wide">
            <el-option v-for="item in compatibleCases" :key="item.id" :label="`${item.code} · ${item.name}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="复用 Scenario" required>
          <el-select v-model="form.scenario_id" filterable class="wide">
            <el-option v-for="item in compatibleScenarios" :key="item.id" :label="`${item.code} · ${item.name}`" :value="item.id" />
          </el-select>
          <small>含服务端 AI 断言的 Scenario 不进入性能压测；HTTP、控制流、SQL、脚本和 Cleanup 可复用。</small>
        </el-form-item>

        <div class="form-grid">
          <el-form-item label="负载模型">
            <el-select v-model="form.load_mode" class="wide">
              <el-option label="固定迭代" value="FIXED_ITERATIONS" />
              <el-option label="固定 RPS" value="FIXED_RPS" />
              <el-option v-if="form.engine !== 'JMETER'" label="固定并发 / 时长" value="FIXED_CONCURRENCY" />
              <el-option v-if="form.engine !== 'JMETER'" label="阶梯负载" value="STEP_LOAD" />
            </el-select>
          </el-form-item>
          <el-form-item label="Cleanup">
            <el-select v-model="form.cleanup_mode" class="wide" :disabled="form.engine !== 'PYTHON_HTTP'">
              <el-option label="不执行" value="NONE" />
              <el-option label="每次迭代清理" value="RESOURCE_CLEANUP" />
              <el-option label="负载结束后批量清理" value="BATCH_CLEANUP" />
            </el-select>
          </el-form-item>
          <el-form-item label="最大并发"><el-input-number v-model="form.concurrency" :min="1" :max="100" /></el-form-item>
          <el-form-item v-if="form.load_mode === 'FIXED_ITERATIONS'" label="统计迭代">
            <el-input-number v-model="form.iterations" :min="form.concurrency" :max="10000" />
          </el-form-item>
          <template v-else-if="form.load_mode === 'FIXED_RPS'">
            <el-form-item label="目标 RPS"><el-input-number v-model="form.target_rps" :min="0.1" :max="10000" :step="1" /></el-form-item>
            <el-form-item label="持续时长(s)"><el-input-number v-model="form.duration_seconds" :min="1" :max="3600" /></el-form-item>
            <el-form-item label="计划请求数"><span>{{ plannedRpsRequests }}（上限 10000）</span></el-form-item>
          </template>
          <template v-else-if="form.load_mode === 'FIXED_CONCURRENCY'">
            <el-form-item label="持续时长(s)"><el-input-number v-model="form.duration_seconds" :min="1" :max="3600" /></el-form-item>
            <el-form-item label="样本上限"><el-input-number v-model="form.iterations" :min="1" :max="10000" /></el-form-item>
          </template>
          <el-form-item label="Warm Up 迭代">
            <el-input-number v-model="form.warmup_iterations" :min="0" :max="1000" />
          </el-form-item>
          <el-form-item label="单请求超时(ms)"><el-input-number v-model="form.request_timeout_ms" :min="100" :max="120000" /></el-form-item>
        </div>

        <div v-if="form.load_mode === 'STEP_LOAD'" class="stage-editor">
          <div class="section-heading"><strong>阶梯阶段（总计 {{ stepDuration }} 秒）</strong><el-button size="small" @click="addStage">增加阶段</el-button></div>
          <div v-for="(stage, index) in form.step_stages" :key="index" class="stage-row">
            <span>阶段 {{ index + 1 }}</span>
            <el-input-number v-model="stage.concurrency" :min="1" :max="100" />
            <span>VU</span>
            <el-input-number v-model="stage.duration_seconds" :min="1" :max="3600" />
            <span>秒</span>
            <el-button link type="danger" @click="removeStage(index)">删除</el-button>
          </div>
          <el-form-item label="样本上限"><el-input-number v-model="form.iterations" :min="1" :max="10000" /></el-form-item>
        </div>

        <div v-if="form.engine === 'PYTHON_STREAM'" class="stream-box">
          <el-divider content-position="left">SSE / LLM 流式协议</el-divider>
          <div class="form-grid">
            <el-form-item label="协议"><el-select v-model="form.stream_protocol"><el-option label="SSE" value="SSE" /><el-option label="NDJSON" value="NDJSON" /></el-select></el-form-item>
            <el-form-item label="完成标记"><el-input v-model="form.completion_marker" /></el-form-item>
            <el-form-item label="必须收到完成标记"><el-switch v-model="form.require_completion_marker" /></el-form-item>
          </div>
        </div>

        <el-divider content-position="left">确定性 SLA</el-divider>
        <div class="form-grid three">
          <el-form-item label="最大错误率(%)"><el-input-number v-model="form.max_error_rate_percent" :min="0" :max="100" :step="0.1" /></el-form-item>
          <el-form-item label="最小成功率(%)"><el-input-number v-model="form.min_success_rate_percent" :min="0" :max="100" :step="0.1" /></el-form-item>
          <el-form-item label="最小 RPS"><el-input-number v-model="form.min_rps" :min="0.01" :max="100000" /></el-form-item>
          <el-form-item label="最大 P95(ms)"><el-input-number v-model="form.max_p95_ms" :min="1" :max="120000" /></el-form-item>
          <el-form-item label="最大 P99(ms)"><el-input-number v-model="form.max_p99_ms" :min="1" :max="120000" /></el-form-item>
          <el-form-item v-if="form.engine === 'PYTHON_STREAM'" label="最大 TTFT P95(ms)"><el-input-number v-model="form.max_ttft_p95_ms" :min="1" :max="120000" /></el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveProfile">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="comparisonVisible" title="性能运行对比" width="860px">
      <el-table v-if="comparison" :data="comparison.items">
        <el-table-column prop="run_code" label="Run" min-width="170" />
        <el-table-column label="基线" width="80"><template #default="{ row }"><el-tag v-if="row.run_id === comparison?.baseline_run_id">基线</el-tag></template></el-table-column>
        <el-table-column label="RPS / Δ" width="140"><template #default="{ row }">{{ row.metrics.rps }} / {{ row.delta_from_baseline.rps }}</template></el-table-column>
        <el-table-column label="P95 / Δ(ms)" width="150"><template #default="{ row }">{{ row.metrics.p95_ms }} / {{ row.delta_from_baseline.p95_ms }}</template></el-table-column>
        <el-table-column label="P99 / Δ(ms)" width="150"><template #default="{ row }">{{ row.metrics.p99_ms }} / {{ row.delta_from_baseline.p99_ms }}</template></el-table-column>
        <el-table-column label="错误率 / Δ"><template #default="{ row }">{{ (row.metrics.error_rate * 100).toFixed(2) }}% / {{ (row.delta_from_baseline.error_rate * 100).toFixed(2) }}%</template></el-table-column>
      </el-table>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="性能指标" size="55%">
      <template v-if="detailRun?.metrics">
        <div class="detail-grid">
          <span>请求 <b>{{ detailRun.metrics.request_count }}</b></span><span>峰值 VU <b>{{ detailRun.metrics.active_users_peak }}</b></span>
          <span>平均 <b>{{ detailRun.metrics.average_ms }}ms</b></span><span>P50 / P75 / P90 <b>{{ detailRun.metrics.p50_ms }} / {{ detailRun.metrics.p75_ms }} / {{ detailRun.metrics.p90_ms }}ms</b></span>
          <span>收 / 发 <b>{{ detailRun.metrics.received_bytes }} / {{ detailRun.metrics.sent_bytes }} bytes</b></span><span>TTFT P95 <b>{{ detailRun.metrics.ttft_p95_ms ?? '—' }}ms</b></span>
          <span>输出 Token <b>{{ detailRun.metrics.output_tokens ?? '—' }}</b></span><span>Token/s <b>{{ detailRun.metrics.tokens_per_second ?? '—' }}</b></span>
        </div>
        <h3>按秒趋势</h3>
        <el-table :data="detailRun.metrics.trends" max-height="420">
          <el-table-column prop="second" label="秒" width="80" />
          <el-table-column prop="requests" label="请求" />
          <el-table-column prop="success" label="成功" />
          <el-table-column prop="fail" label="失败" />
          <el-table-column prop="average_ms" label="平均(ms)" />
          <el-table-column prop="p95_ms" label="P95(ms)" />
        </el-table>
      </template>
    </el-drawer>

    <el-drawer
      v-model="analysisVisible"
      :title="`AI 性能分析 · ${analysisRun?.run_code || ''}`"
      size="60%"
    >
      <div v-loading="analysisLoading" class="analysis-panel">
        <el-alert
          title="SLA 由平台根据原始指标确定性判定，AI 仅提供解释、瓶颈假设和优化建议，不能修改结论。"
          type="warning"
          :closable="false"
          show-icon
        />
        <div class="analysis-create">
          <el-select
            v-model="analysisPromptId"
            class="wide"
            placeholder="选择性能分析 Prompt"
            :disabled="generatingAnalysis"
          >
            <el-option
              v-for="prompt in analysisPrompts"
              :key="prompt.id"
              :label="prompt.name"
              :value="prompt.id"
            />
          </el-select>
          <el-input
            v-model="analysisInstructions"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="可选：补充希望重点关注的方向。该内容按不可信输入处理。"
            :disabled="generatingAnalysis"
          />
          <el-button
            type="primary"
            :loading="generatingAnalysis"
            :disabled="!analysisPrompts.length"
            @click="createAnalysis"
          >
            生成分析
          </el-button>
          <small v-if="!analysisPrompts.length">
            尚无启用的 PERFORMANCE_ANALYSIS Prompt；可在 Prompt 中心恢复系统默认模板。
          </small>
        </div>

        <el-empty v-if="!analysisLoading && !analyses.length" description="尚未生成 AI 性能分析" />
        <article
          v-for="analysis in analyses"
          v-else
          :key="analysis.id"
          class="analysis-card"
        >
          <header>
            <div>
              <el-tag :type="analysisVerdictType(analysis.source_sla_verdict)">
                {{ analysisVerdictText(analysis.source_sla_verdict) }}
              </el-tag>
              <el-tag v-if="analysis.structured_result?.needs_human_review" type="warning">
                需人工复核
              </el-tag>
            </div>
            <small>{{ formatApiDateTime(analysis.created_at) }} · {{ analysis.actual_model || '未知模型' }}</small>
          </header>
          <template v-if="analysis.structured_result">
            <p class="analysis-summary">{{ analysis.structured_result.summary }}</p>
            <h3>指标发现</h3>
            <div class="finding-list">
              <div
                v-for="finding in analysis.structured_result.findings"
                :key="`${finding.category}-${finding.metric}`"
                class="finding-item"
              >
                <div>
                  <el-tag size="small" effect="plain">{{ finding.category }}</el-tag>
                  <strong>{{ finding.metric }} = {{ finding.observed }}</strong>
                </div>
                <p>{{ finding.observation }}</p>
                <small>建议：{{ finding.recommendation }}</small>
              </div>
            </div>
            <h3>优化建议</h3>
            <ol>
              <li v-for="item in analysis.structured_result.recommendations" :key="item">{{ item }}</li>
            </ol>
            <el-collapse v-if="analysis.structured_result.bottleneck_hypotheses.length">
              <el-collapse-item title="瓶颈假设与验证步骤">
                <div
                  v-for="item in analysis.structured_result.bottleneck_hypotheses"
                  :key="item.description"
                  class="hypothesis"
                >
                  <strong>{{ item.description }}（置信度 {{ Math.round(item.confidence * 100) }}%）</strong>
                  <small>依据：{{ item.evidence_metrics.join('、') }}</small>
                  <ol><li v-for="step in item.validation_steps" :key="step">{{ step }}</li></ol>
                </div>
              </el-collapse-item>
            </el-collapse>
          </template>
        </article>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.performance-page { display: grid; gap: 20px; }
.page-heading,.section-heading,.profile-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.page-heading h1 { margin: 0; color: #17233f; font-size: 30px; }
.page-heading p { margin: 7px 0 0; color: #66738c; }
.eyebrow { color: #416fe5 !important; font-size: 11px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.card { padding: 22px; border: 1px solid #e5eaf2; border-radius: 16px; background: #fff; box-shadow: 0 8px 28px rgba(31,48,82,.05); }
.card h2 { margin: 0 0 18px; color: #263653; font-size: 18px; }
.section-heading h2 { margin: 0; }
.profile-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(440px,1fr)); gap: 14px; }
.profile-card { padding: 18px; border: 1px solid #e6ebf3; border-radius: 13px; background: #fbfcff; }
.profile-title strong,.profile-title span,small { display: block; }
.profile-title span,small,.sla-line { margin-top: 5px; color: #7b879b; font-size: 12px; }
.tag-row { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.metric-strip { display: flex; flex-wrap: wrap; gap: 10px; margin: 15px 0; }
.metric-strip span { padding: 7px 10px; border-radius: 8px; color: #5d6980; background: #eef3fb; font-size: 12px; }
.metric-strip b { margin-right: 3px; color: #23416f; }
.load-mode { margin-top: 12px; color: #416fe5; font-size: 12px; font-weight: 700; }
.launch-row { display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; margin-top: 15px; }
.wide { width: 100%; }
.form-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 0 18px; }
.form-grid.three { grid-template-columns: repeat(3,1fr); }
.stage-editor,.stream-box { margin: 4px 0 18px; padding: 14px; border-radius: 10px; background: #f7f9fd; }
.stage-row { display: grid; grid-template-columns: 70px 1fr 30px 1fr 30px 50px; align-items: center; gap: 8px; margin: 10px 0; }
.detail-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 12px; margin-bottom: 24px; }
.detail-grid span { padding: 12px; border-radius: 8px; background: #f3f6fb; color: #66738c; }
.detail-grid b { color: #203657; }
.analysis-panel { display: grid; gap: 18px; }
.analysis-create { display: grid; gap: 12px; padding: 16px; border-radius: 10px; background: #f7f9fd; }
.analysis-create .el-button { justify-self: start; }
.analysis-card { padding: 18px; border: 1px solid #e4eaf4; border-radius: 12px; }
.analysis-card header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.analysis-card header > div { display: flex; gap: 8px; }
.analysis-card h3 { margin: 18px 0 10px; color: #263653; font-size: 15px; }
.analysis-summary { color: #44516a; line-height: 1.7; }
.finding-list { display: grid; gap: 10px; }
.finding-item { padding: 12px; border-radius: 8px; background: #f7f9fd; }
.finding-item > div { display: flex; align-items: center; gap: 8px; }
.finding-item p { margin: 8px 0 4px; color: #44516a; }
.hypothesis { display: grid; gap: 6px; margin-bottom: 16px; }
:deep(.el-table .el-tag + .el-tag) { margin-left: 6px; }
</style>
