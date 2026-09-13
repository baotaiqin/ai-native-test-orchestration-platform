<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getEnvironments } from '@/api/environments'
import { getApiErrorMessage } from '@/api/http'
import { getScenarios } from '@/api/scenarios'
import { getTestCases } from '@/api/test-cases'
import {
  cancelTestPlanRun,
  createTestPlan,
  getTestPlanRunnerOptions,
  getTestPlanRuns,
  getTestPlans,
  runTestPlan,
  retryTestPlanRun,
  setTestPlanArchived,
  updateTestPlan,
  validateTestPlan,
} from '@/api/test-plans'
import { getWebCases } from '@/api/web-assets'
import type { Environment } from '@/types/environment'
import type { RunStatus, RunType } from '@/types/run'
import type { Scenario } from '@/types/scenario'
import type { TestCaseAsset } from '@/types/test-case'
import type {
  TestPlan,
  TestPlanItemInput,
  TestPlanRun,
  TestPlanRunnerOption,
  TestPlanWrite,
} from '@/types/test-plan'
import type { WebCaseResponse } from '@/types/web'
import { formatApiDateTime } from '@/utils/datetime'

type TagType = 'success' | 'warning' | 'info' | 'primary' | 'danger'
interface DraftItem {
  target_type: RunType
  target_id?: number
  enabled: boolean
}

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number(route.params.projectId || route.query.project_id))
const loading = ref(false)
const saving = ref(false)
const actingPlanId = ref<number>()
const actingRunId = ref<string>()
const dialogVisible = ref(false)
const detailVisible = ref(false)
const editingId = ref<number>()
const plans = ref<TestPlan[]>([])
const runs = ref<TestPlanRun[]>([])
const environments = ref<Environment[]>([])
const runners = ref<TestPlanRunnerOption[]>([])
const cases = ref<TestCaseAsset[]>([])
const scenarios = ref<Scenario[]>([])
const webCases = ref<WebCaseResponse[]>([])
const detailRun = ref<TestPlanRun>()
let pollTimer: number | undefined

const form = reactive({
  name: '',
  description: '',
  environment_id: undefined as number | undefined,
  runner_id: undefined as string | undefined,
  variables_text: '{}',
  items: [{ target_type: 'API_CASE', target_id: undefined, enabled: true }] as DraftItem[],
})

const activeCases = computed(() =>
  cases.value.filter(
    (item) => item.case_type === 'API' && item.status === 'ACTIVE' && item.current_version_id,
  ),
)
const activeScenarios = computed(() =>
  scenarios.value.filter((item) => item.status !== 'ARCHIVED' && item.current_version_id),
)
const approvedWebCases = computed(() =>
  webCases.value.filter((item) => item.status === 'APPROVED' && item.current_version_id),
)
const enabledEnvironments = computed(() => environments.value.filter((item) => item.enabled))
const hasActiveRuns = computed(() =>
  runs.value.some((item) => !['SUCCESS', 'FAILED', 'CANCELLED', 'TIMEOUT'].includes(item.status)),
)

function statusTag(status: RunStatus | TestPlan['status']): TagType {
  if (status === 'SUCCESS' || status === 'ACTIVE') return 'success'
  if (status === 'FAILED' || status === 'TIMEOUT') return 'danger'
  if (status === 'RUNNING' || status === 'ASSIGNED') return 'primary'
  if (status === 'QUEUED' || status === 'CANCELLING') return 'warning'
  return 'info'
}

function statusText(status: RunStatus | TestPlan['status']): string {
  return {
    ACTIVE: '启用',
    ARCHIVED: '已归档',
    CREATED: '已创建',
    QUEUED: '排队中',
    ASSIGNED: '已分配',
    RUNNING: '运行中',
    CANCELLING: '取消中',
    SUCCESS: '成功',
    FAILED: '失败',
    CANCELLED: '已取消',
    TIMEOUT: '超时',
  }[status]
}

function targetTypeText(type: RunType): string {
  return { API_CASE: 'API 用例', SCENARIO: '测试编排', WEB_CASE: 'Web 用例' }[type]
}

function triggerTypeText(type: TestPlanRun['trigger_type']): string {
  return { MANUAL: '手动', SCHEDULE: '定时', API: 'CI/CD API' }[type]
}

function targetOptions(item: DraftItem): Array<{ id: number; name: string; code: string }> {
  if (item.target_type === 'API_CASE') return activeCases.value
  if (item.target_type === 'SCENARIO') return activeScenarios.value
  return approvedWebCases.value
}

function runnerSummary(runner: TestPlanRunnerOption): string {
  const slots = runner.slots.map((item) => `${item.type} ${item.available}`).join(' / ')
  return `${runner.name} · ${slots}`
}

function resetForm(): void {
  editingId.value = undefined
  form.name = ''
  form.description = ''
  form.environment_id = enabledEnvironments.value.find((item) => item.is_default)?.id
  form.runner_id = runners.value[0]?.id
  form.variables_text = '{}'
  form.items = [{ target_type: 'API_CASE', target_id: undefined, enabled: true }]
}

function openCreate(): void {
  resetForm()
  dialogVisible.value = true
}

function editPlan(plan: TestPlan): void {
  editingId.value = plan.id
  form.name = plan.name
  form.description = plan.description || ''
  form.environment_id = plan.environment_id
  form.runner_id = plan.runner_id
  form.variables_text = JSON.stringify(plan.runtime_variables, null, 2)
  form.items = plan.items.map((item) => ({
    target_type: item.target_type,
    target_id: item.case_id || item.scenario_id || item.web_case_id || undefined,
    enabled: item.enabled,
  }))
  dialogVisible.value = true
}

function addItem(): void {
  if (form.items.length < 100) {
    form.items.push({ target_type: 'API_CASE', target_id: undefined, enabled: true })
  }
}

function removeItem(index: number): void {
  if (form.items.length > 1) form.items.splice(index, 1)
}

function moveItem(index: number, offset: number): void {
  const next = index + offset
  if (next < 0 || next >= form.items.length) return
  const [item] = form.items.splice(index, 1)
  form.items.splice(next, 0, item)
}

function changeTargetType(item: DraftItem): void {
  item.target_id = undefined
}

function itemPayload(item: DraftItem): TestPlanItemInput {
  const target = targetOptions(item).find((option) => option.id === item.target_id)
  const currentVersionId =
    item.target_type === 'API_CASE'
      ? activeCases.value.find((option) => option.id === item.target_id)?.current_version_id
      : item.target_type === 'SCENARIO'
        ? activeScenarios.value.find((option) => option.id === item.target_id)?.current_version_id
        : approvedWebCases.value.find((option) => option.id === item.target_id)?.current_version_id
  if (!target || !currentVersionId) throw new Error('执行项目标或固定版本无效')
  if (item.target_type === 'API_CASE') {
    return {
      target_type: item.target_type,
      case_id: target.id,
      case_version_id: currentVersionId,
      enabled: item.enabled,
    }
  }
  if (item.target_type === 'SCENARIO') {
    return {
      target_type: item.target_type,
      scenario_id: target.id,
      scenario_version_id: currentVersionId,
      enabled: item.enabled,
    }
  }
  return {
    target_type: item.target_type,
    web_case_id: target.id,
    web_case_version_id: currentVersionId,
    enabled: item.enabled,
  }
}

function buildPayload(): TestPlanWrite {
  if (!form.name.trim() || !form.environment_id || !form.runner_id) {
    throw new Error('请填写名称并选择环境与 Runner')
  }
  if (!form.items.some((item) => item.enabled)) throw new Error('至少启用一个执行项')
  let runtimeVariables: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(form.variables_text || '{}')
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('计划变量必须是 JSON 对象')
    }
    runtimeVariables = parsed as Record<string, unknown>
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : '计划变量 JSON 格式无效')
  }
  return {
    project_id: projectId.value,
    name: form.name.trim(),
    description: form.description.trim() || undefined,
    environment_id: form.environment_id,
    runner_id: form.runner_id,
    runtime_variables: runtimeVariables,
    execution_mode: 'PARALLEL',
    failure_strategy: 'CONTINUE',
    items: form.items.map(itemPayload),
  }
}

async function savePlan(): Promise<void> {
  let payload: TestPlanWrite
  try {
    payload = buildPayload()
  } catch (error) {
    ElMessage.warning(error instanceof Error ? error.message : '配置不完整')
    return
  }
  saving.value = true
  try {
    if (editingId.value) await updateTestPlan(editingId.value, payload)
    else await createTestPlan(payload)
    ElMessage.success(editingId.value ? 'Test Plan 已更新' : 'Test Plan 已创建')
    dialogVisible.value = false
    await loadAll()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'Test Plan 保存失败'))
  } finally {
    saving.value = false
  }
}

async function checkPlan(plan: TestPlan): Promise<void> {
  actingPlanId.value = plan.id
  try {
    const result = await validateTestPlan(plan.id)
    if (result.valid) {
      ElMessage.success(`${result.item_count} 个执行项校验通过`)
      return
    }
    const lines = result.items.flatMap((item) =>
      item.issues.map((issue) => `#${item.sequence_no} ${item.target_name}: ${issue.message}`),
    )
    await ElMessageBox.alert(lines.join('\n') || '计划校验未通过', '校验结果', {
      confirmButtonText: '知道了',
    })
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'Test Plan 校验失败'))
  } finally {
    actingPlanId.value = undefined
  }
}

async function startPlan(plan: TestPlan): Promise<void> {
  actingPlanId.value = plan.id
  try {
    const result = await runTestPlan(plan.id)
    ElMessage.success(`已创建并投递 ${result.plan_run.total} 个子 Run`)
    detailRun.value = result.plan_run
    detailVisible.value = true
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'Test Plan 启动失败'))
  } finally {
    actingPlanId.value = undefined
  }
}

async function toggleArchive(plan: TestPlan): Promise<void> {
  actingPlanId.value = plan.id
  try {
    await setTestPlanArchived(plan.id, plan.status === 'ACTIVE')
    ElMessage.success(plan.status === 'ACTIVE' ? 'Test Plan 已归档' : 'Test Plan 已恢复')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'Test Plan 状态更新失败'))
  } finally {
    actingPlanId.value = undefined
  }
}

async function cancelPlanRun(run: TestPlanRun): Promise<void> {
  try {
    await ElMessageBox.confirm('将取消该计划中所有尚未结束的子 Run，是否继续？', '取消计划运行', {
      type: 'warning',
      confirmButtonText: '确认取消',
      cancelButtonText: '返回',
    })
  } catch {
    return
  }
  actingRunId.value = run.id
  try {
    const updated = await cancelTestPlanRun(run.id)
    if (detailRun.value?.id === updated.id) detailRun.value = updated
    ElMessage.success('取消请求已提交')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '取消计划运行失败'))
  } finally {
    actingRunId.value = undefined
  }
}

async function retryPlanRun(run: TestPlanRun): Promise<void> {
  actingRunId.value = run.id
  try {
    const updated = await retryTestPlanRun(run.id)
    if (detailRun.value?.id === updated.id) detailRun.value = updated
    ElMessage.success('待投递子 Run 已重新投递')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '重试投递失败'))
  } finally {
    actingRunId.value = undefined
  }
}

function showRun(run: TestPlanRun): void {
  detailRun.value = run
  detailVisible.value = true
}

async function loadAll(silent = false): Promise<void> {
  if (!Number.isInteger(projectId.value) || projectId.value <= 0) {
    await router.replace({ name: 'projects' })
    return
  }
  if (!silent) loading.value = true
  try {
    const [planItems, runItems, environmentItems, runnerItems, caseItems, scenarioItems, webItems] =
      await Promise.all([
        getTestPlans(projectId.value),
        getTestPlanRuns(projectId.value),
        getEnvironments(projectId.value),
        getTestPlanRunnerOptions(projectId.value),
        getTestCases(projectId.value),
        getScenarios(projectId.value),
        getWebCases(projectId.value),
      ])
    plans.value = planItems
    runs.value = runItems
    environments.value = environmentItems
    runners.value = runnerItems
    cases.value = caseItems
    scenarios.value = scenarioItems
    webCases.value = webItems.items
    if (detailRun.value) {
      detailRun.value = runs.value.find((item) => item.id === detailRun.value?.id) || detailRun.value
    }
  } catch (error) {
    if (!silent) ElMessage.error(getApiErrorMessage(error, 'Test Plan 数据加载失败'))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadAll()
  pollTimer = window.setInterval(() => {
    if (hasActiveRuns.value) void loadAll(true)
  }, 3000)
})
onUnmounted(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <div v-loading="loading" class="plan-page">
    <section class="plan-heading">
      <div>
        <div class="eyebrow">V1 · TEST PLAN</div>
        <h1>测试计划</h1>
        <p>组合已审批的 API、Scenario 与 Web 固定版本，一次校验并并行投递到同一 Runner。</p>
      </div>
      <el-button type="primary" @click="openCreate">新建测试计划</el-button>
    </section>

    <el-alert
      v-if="!runners.length"
      type="warning"
      :closable="false"
      show-icon
      title="暂无在线且有可用 API/Web Slot 的 Runner"
      description="计划可以查看，但新建、校验和执行需要 Runner 心跳在线。"
    />

    <section class="panel">
      <div class="panel-title"><h2>计划配置</h2><span>{{ plans.length }} 个计划</span></div>
      <el-empty v-if="!plans.length" description="尚未创建测试计划" />
      <div v-else class="plan-grid">
        <article v-for="plan in plans" :key="plan.id" class="plan-card">
          <div class="card-head">
            <div><h3>{{ plan.name }}</h3><p>{{ plan.description || '暂无说明' }}</p></div>
            <el-tag :type="statusTag(plan.status)" effect="light">{{ statusText(plan.status) }}</el-tag>
          </div>
          <div class="facts">
            <span>执行项 <strong>{{ plan.items.filter((item) => item.enabled).length }}</strong></span>
            <span>模式 <strong>并行</strong></span>
            <span>失败策略 <strong>继续</strong></span>
          </div>
          <div class="chips">
            <el-tag v-for="item in plan.items" :key="item.id" :type="item.enabled ? 'info' : 'info'" :effect="item.enabled ? 'light' : 'plain'">
              #{{ item.sequence_no }} {{ targetTypeText(item.target_type) }} · {{ item.target_name }}
            </el-tag>
          </div>
          <div class="card-actions">
            <el-button size="small" :loading="actingPlanId === plan.id" @click="checkPlan(plan)">执行前校验</el-button>
            <el-button size="small" :disabled="plan.status !== 'ACTIVE'" @click="editPlan(plan)">编辑</el-button>
            <el-button size="small" @click="toggleArchive(plan)">{{ plan.status === 'ACTIVE' ? '归档' : '恢复' }}</el-button>
            <el-button type="primary" size="small" :loading="actingPlanId === plan.id" :disabled="plan.status !== 'ACTIVE'" @click="startPlan(plan)">立即运行</el-button>
          </div>
        </article>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title"><h2>最近运行</h2><span>聚合状态会随子 Run 自动刷新</span></div>
      <el-table :data="runs" empty-text="暂无计划运行" @row-click="showRun">
        <el-table-column label="计划 / Run" min-width="250">
          <template #default="scope"><strong>{{ scope.row.plan_name }}</strong><small>{{ scope.row.id }}</small></template>
        </el-table-column>
        <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="statusTag(scope.row.status)" effect="light">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="触发" width="100"><template #default="scope">{{ triggerTypeText(scope.row.trigger_type) }}</template></el-table-column>
        <el-table-column label="进度" width="170"><template #default="scope">{{ scope.row.passed }} 成功 / {{ scope.row.failed }} 失败 / {{ scope.row.total }} 总数</template></el-table-column>
        <el-table-column label="开始时间" width="180"><template #default="scope">{{ formatApiDateTime(scope.row.started_at || scope.row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="scope"><template v-if="!['SUCCESS','FAILED','CANCELLED','TIMEOUT'].includes(scope.row.status)"><el-button link type="primary" :loading="actingRunId === scope.row.id" @click.stop="retryPlanRun(scope.row)">重试投递</el-button><el-button link type="danger" :loading="actingRunId === scope.row.id" @click.stop="cancelPlanRun(scope.row)">取消</el-button></template><el-button v-else link @click.stop="showRun(scope.row)">详情</el-button></template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑测试计划' : '新建测试计划'" width="860px" destroy-on-close>
      <el-form label-position="top">
        <div class="two-columns">
          <el-form-item label="计划名称"><el-input v-model="form.name" maxlength="255" show-word-limit /></el-form-item>
          <el-form-item label="执行环境"><el-select v-model="form.environment_id" class="full"><el-option v-for="item in enabledEnvironments" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
        </div>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="2" maxlength="2000" show-word-limit /></el-form-item>
        <el-form-item label="固定 Runner"><el-select v-model="form.runner_id" class="full" filterable><el-option v-for="item in runners" :key="item.id" :label="runnerSummary(item)" :value="item.id" /></el-select><div class="form-hint">一个计划使用同一 Runner；混合 Web 目标时该 Runner 必须同时具备对应 Capability 与 Slot。</div></el-form-item>
        <el-form-item label="计划变量（JSON 对象）"><el-input v-model="form.variables_text" type="textarea" :rows="4" placeholder='{"tenant_id":"demo"}' /><div class="form-hint">变量随子 Run 固化；敏感命名、保留字段与超过 64KB 的内容会被服务端拒绝。</div></el-form-item>
        <div class="items-title"><strong>执行项</strong><el-button size="small" @click="addItem">添加执行项</el-button></div>
        <div v-for="(item, index) in form.items" :key="index" class="item-row">
          <span class="sequence">#{{ index + 1 }}</span>
          <el-select v-model="item.target_type" class="type-select" @change="changeTargetType(item)"><el-option label="API 用例" value="API_CASE" /><el-option label="测试编排" value="SCENARIO" /><el-option label="Web 用例" value="WEB_CASE" /></el-select>
          <el-select v-model="item.target_id" class="target-select" filterable placeholder="选择已固定版本的目标"><el-option v-for="option in targetOptions(item)" :key="option.id" :label="`${option.name}（${option.code}）`" :value="option.id" /></el-select>
          <el-switch v-model="item.enabled" inline-prompt active-text="启" inactive-text="停" />
          <el-button-group><el-button size="small" :disabled="index === 0" @click="moveItem(index,-1)">↑</el-button><el-button size="small" :disabled="index === form.items.length - 1" @click="moveItem(index,1)">↓</el-button></el-button-group>
          <el-button link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">移除</el-button>
        </div>
      </el-form>
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="savePlan">保存计划</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="计划运行详情" size="720px">
      <template v-if="detailRun">
        <div class="run-summary"><div><span>状态</span><el-tag :type="statusTag(detailRun.status)">{{ statusText(detailRun.status) }}</el-tag></div><div><span>成功</span><strong>{{ detailRun.passed }}</strong></div><div><span>失败</span><strong>{{ detailRun.failed }}</strong></div><div><span>总数</span><strong>{{ detailRun.total }}</strong></div></div>
        <el-table :data="detailRun.items" class="detail-table">
          <el-table-column prop="sequence_no" label="#" width="48" />
          <el-table-column label="执行目标" min-width="190"><template #default="scope"><strong>{{ scope.row.target_name }}</strong><small>{{ targetTypeText(scope.row.target_type) }}</small></template></el-table-column>
          <el-table-column label="子 Run" min-width="170"><template #default="scope"><span>{{ scope.row.run_code || '创建失败' }}</span><small v-if="scope.row.error_message" class="error-text">{{ scope.row.error_message }}</small></template></el-table-column>
          <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="statusTag(scope.row.status)" effect="light">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.plan-page { display: grid; gap: 20px; }
.plan-heading { display: flex; align-items: flex-start; justify-content: space-between; }
.plan-heading h1 { margin: 4px 0 8px; color: #12213d; font-size: 30px; }
.plan-heading p { margin: 0; color: #6f7d94; }
.eyebrow { color: #3f6de0; font-size: 11px; font-weight: 800; letter-spacing: .15em; }
.panel { padding: 22px; border: 1px solid #e2e8f2; border-radius: 16px; background: #fff; box-shadow: 0 8px 26px rgba(41,61,99,.04); }
.panel-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.panel-title h2 { margin: 0; color: #182846; font-size: 18px; }
.panel-title span { color: #909caf; font-size: 12px; }
.plan-grid { display: grid; grid-template-columns: repeat(auto-fill,minmax(390px,1fr)); gap: 16px; }
.plan-card { padding: 18px; border: 1px solid #e4e9f2; border-radius: 14px; background: linear-gradient(145deg,#fff,#fbfcff); }
.card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.card-head h3 { margin: 0 0 5px; color: #1a2b4b; }
.card-head p { margin: 0; color: #8490a3; font-size: 12px; }
.facts { display: flex; gap: 22px; margin: 17px 0 13px; color: #7b8799; font-size: 12px; }
.facts strong { margin-left: 4px; color: #31415f; }
.chips { display: flex; min-height: 32px; flex-wrap: wrap; gap: 7px; }
.card-actions { display: flex; justify-content: flex-end; margin-top: 17px; }
.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.full { width: 100%; }
.form-hint { margin-top: 5px; color: #98a3b3; font-size: 12px; line-height: 1.5; }
.items-title { display: flex; align-items: center; justify-content: space-between; margin: 10px 0; }
.item-row { display: flex; align-items: center; gap: 9px; padding: 10px; border: 1px solid #e6ebf3; border-radius: 10px; }
.item-row + .item-row { margin-top: 8px; }
.sequence { width: 28px; color: #8b97aa; font-size: 12px; }
.type-select { width: 125px; }
.target-select { flex: 1; }
.run-summary { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; }
.run-summary > div { display: grid; gap: 7px; padding: 14px; border-radius: 10px; background: #f5f7fb; }
.run-summary span { color: #8995a7; font-size: 12px; }
.run-summary strong { color: #223555; font-size: 22px; }
.detail-table { margin-top: 18px; }
small { display: block; margin-top: 4px; color: #929daf; font-size: 11px; }
.error-text { color: #d65c5c; white-space: normal; }
@media (max-width: 900px) { .two-columns { grid-template-columns: 1fr; } .item-row { flex-wrap: wrap; } .target-select { min-width: 260px; } }
</style>
