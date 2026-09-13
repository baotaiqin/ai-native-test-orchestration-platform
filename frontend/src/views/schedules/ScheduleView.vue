<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getApiErrorMessage } from '@/api/http'
import {
  createSchedule,
  getSchedules,
  getScheduleTriggers,
  previewSchedule,
  retryScheduleTrigger,
  setScheduleArchived,
  updateSchedule,
} from '@/api/schedules'
import { getTestPlans } from '@/api/test-plans'
import type {
  SchedulePreviewPayload,
  ScheduleTrigger,
  ScheduleTriggerStatus,
  ScheduleType,
  ScheduleWrite,
  TestSchedule,
} from '@/types/schedule'
import type { TestPlan } from '@/types/test-plan'
import { formatApiDateTime } from '@/utils/datetime'

type TagType = 'success' | 'warning' | 'info' | 'primary' | 'danger'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number(route.params.projectId || route.query.project_id))
const loading = ref(false)
const saving = ref(false)
const previewing = ref(false)
const actingId = ref<number>()
const actingTriggerId = ref<string>()
const dialogVisible = ref(false)
const editingId = ref<number>()
const schedules = ref<TestSchedule[]>([])
const triggers = ref<ScheduleTrigger[]>([])
const plans = ref<TestPlan[]>([])
const previewItems = ref<string[]>([])

const weekdays = [
  { label: '周一', value: 1 },
  { label: '周二', value: 2 },
  { label: '周三', value: 3 },
  { label: '周四', value: 4 },
  { label: '周五', value: 5 },
  { label: '周六', value: 6 },
  { label: '周日', value: 7 },
]

const form = reactive({
  name: '',
  plan_id: undefined as number | undefined,
  schedule_type: 'DAILY' as ScheduleType,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
  daily_time: '09:00',
  weekdays: [1, 2, 3, 4, 5] as number[],
  cron_expression: '0 9 * * 1-5',
  enabled: true,
})

const activePlans = computed(() => plans.value.filter((item) => item.status === 'ACTIVE'))

function statusTag(status: ScheduleTriggerStatus | TestSchedule['status']): TagType {
  if (status === 'ACTIVE' || status === 'DISPATCHED') return 'success'
  if (status === 'FAILED') return 'danger'
  if (status === 'CLAIMED') return 'warning'
  return 'info'
}

function triggerStatusText(status: ScheduleTriggerStatus): string {
  return { CLAIMED: '已认领', DISPATCHED: '已投递', FAILED: '失败', SKIPPED: '已跳过' }[status]
}

function scheduleTypeText(type: ScheduleType): string {
  return { DAILY: '每天', WEEKLY: '每周', CRON: 'Cron' }[type]
}

function scheduleSummary(schedule: TestSchedule): string {
  if (schedule.schedule_type === 'DAILY') return `每天 ${schedule.daily_time}`
  if (schedule.schedule_type === 'WEEKLY') {
    const labels = (schedule.weekdays || []).map(
      (value) => weekdays.find((item) => item.value === value)?.label || String(value),
    )
    return `${labels.join('、')} ${schedule.daily_time}`
  }
  return schedule.cron_expression || '-'
}

function resetForm(): void {
  editingId.value = undefined
  form.name = ''
  form.plan_id = activePlans.value[0]?.id
  form.schedule_type = 'DAILY'
  form.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'
  form.daily_time = '09:00'
  form.weekdays = [1, 2, 3, 4, 5]
  form.cron_expression = '0 9 * * 1-5'
  form.enabled = true
  previewItems.value = []
}

function openCreate(): void {
  resetForm()
  dialogVisible.value = true
}

function editSchedule(schedule: TestSchedule): void {
  editingId.value = schedule.id
  form.name = schedule.name
  form.plan_id = schedule.plan_id
  form.schedule_type = schedule.schedule_type
  form.timezone = schedule.timezone
  form.daily_time = schedule.daily_time || '09:00'
  form.weekdays = schedule.weekdays || [1, 2, 3, 4, 5]
  form.cron_expression = schedule.cron_expression || '0 9 * * 1-5'
  form.enabled = schedule.enabled
  previewItems.value = []
  dialogVisible.value = true
}

function buildExpression(): SchedulePreviewPayload {
  const base: SchedulePreviewPayload = {
    schedule_type: form.schedule_type,
    timezone: form.timezone.trim(),
    count: 5,
  }
  if (form.schedule_type === 'CRON') base.cron_expression = form.cron_expression.trim()
  else base.daily_time = form.daily_time
  if (form.schedule_type === 'WEEKLY') base.weekdays = [...form.weekdays].sort()
  return base
}

function buildPayload(): ScheduleWrite {
  if (!form.name.trim() || !form.plan_id || !form.timezone.trim()) {
    throw new Error('请填写名称、Test Plan 和时区')
  }
  if (form.schedule_type === 'WEEKLY' && !form.weekdays.length) {
    throw new Error('每周任务至少选择一天')
  }
  const expression = buildExpression()
  return {
    project_id: projectId.value,
    plan_id: form.plan_id,
    name: form.name.trim(),
    enabled: form.enabled,
    schedule_type: expression.schedule_type,
    timezone: expression.timezone,
    daily_time: expression.daily_time,
    weekdays: expression.weekdays,
    cron_expression: expression.cron_expression,
  }
}

async function runPreview(): Promise<void> {
  previewing.value = true
  try {
    previewItems.value = await previewSchedule(buildExpression())
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '调度表达式无效'))
  } finally {
    previewing.value = false
  }
}

async function saveSchedule(): Promise<void> {
  let payload: ScheduleWrite
  try {
    payload = buildPayload()
  } catch (error) {
    ElMessage.warning(error instanceof Error ? error.message : '配置不完整')
    return
  }
  saving.value = true
  try {
    if (editingId.value) await updateSchedule(editingId.value, payload)
    else await createSchedule(payload)
    ElMessage.success(editingId.value ? '定时任务已更新' : '定时任务已创建')
    dialogVisible.value = false
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '定时任务保存失败'))
  } finally {
    saving.value = false
  }
}

async function toggleArchive(schedule: TestSchedule): Promise<void> {
  actingId.value = schedule.id
  try {
    await setScheduleArchived(schedule.id, schedule.status === 'ACTIVE')
    ElMessage.success(schedule.status === 'ACTIVE' ? '定时任务已归档' : '定时任务已恢复')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '定时任务状态更新失败'))
  } finally {
    actingId.value = undefined
  }
}

async function retryTrigger(trigger: ScheduleTrigger): Promise<void> {
  actingTriggerId.value = trigger.id
  try {
    await retryScheduleTrigger(trigger.id)
    ElMessage.success('调度触发已重试')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '调度触发重试失败'))
  } finally {
    actingTriggerId.value = undefined
  }
}

async function loadAll(silent = false): Promise<void> {
  if (!Number.isInteger(projectId.value) || projectId.value <= 0) {
    await router.replace({ name: 'projects' })
    return
  }
  if (!silent) loading.value = true
  try {
    const [scheduleItems, triggerItems, planItems] = await Promise.all([
      getSchedules(projectId.value),
      getScheduleTriggers(projectId.value),
      getTestPlans(projectId.value),
    ])
    schedules.value = scheduleItems
    triggers.value = triggerItems
    plans.value = planItems
  } catch (error) {
    if (!silent) ElMessage.error(getApiErrorMessage(error, '定时任务数据加载失败'))
  } finally {
    loading.value = false
  }
}

onMounted(() => void loadAll())
</script>

<template>
  <div v-loading="loading" class="schedule-page">
    <section class="schedule-heading">
      <div>
        <div class="eyebrow">V1 · SCHEDULER</div>
        <h1>定时任务</h1>
        <p>按每天、每周或 Cron 触发 Test Plan；调度器只创建 Run，实际执行仍由 Runner 完成。</p>
      </div>
      <el-button type="primary" :disabled="!activePlans.length" @click="openCreate">新建定时任务</el-button>
    </section>

    <el-alert
      v-if="!activePlans.length"
      type="warning"
      :closable="false"
      show-icon
      title="暂无已启用的 Test Plan"
      description="请先在测试计划页面完成配置与执行前校验。"
    />

    <section class="panel">
      <div class="panel-title"><h2>调度配置</h2><span>{{ schedules.length }} 个任务</span></div>
      <el-empty v-if="!schedules.length" description="尚未创建定时任务" />
      <div v-else class="schedule-grid">
        <article v-for="schedule in schedules" :key="schedule.id" class="schedule-card">
          <div class="card-head">
            <div><h3>{{ schedule.name }}</h3><small>{{ schedule.plan_name }}</small></div>
            <div class="tags">
              <el-tag size="small" effect="light">{{ scheduleTypeText(schedule.schedule_type) }}</el-tag>
              <el-tag size="small" :type="schedule.status === 'ACTIVE' && schedule.enabled ? 'success' : 'info'">
                {{ schedule.status === 'ARCHIVED' ? '已归档' : schedule.enabled ? '运行中' : '已停用' }}
              </el-tag>
            </div>
          </div>
          <div class="expression">{{ scheduleSummary(schedule) }} · {{ schedule.timezone }}</div>
          <dl>
            <div><dt>下次触发</dt><dd>{{ schedule.next_run_at ? formatApiDateTime(schedule.next_run_at) : '-' }}</dd></div>
            <div><dt>上次计划</dt><dd>{{ schedule.last_scheduled_at ? formatApiDateTime(schedule.last_scheduled_at) : '-' }}</dd></div>
          </dl>
          <el-alert
            v-if="schedule.last_error_message"
            type="error"
            :closable="false"
            :title="schedule.last_error_message"
          />
          <div class="card-actions">
            <el-button size="small" :disabled="schedule.status !== 'ACTIVE'" @click="editSchedule(schedule)">编辑</el-button>
            <el-button
              size="small"
              :loading="actingId === schedule.id"
              @click="toggleArchive(schedule)"
            >{{ schedule.status === 'ACTIVE' ? '归档' : '恢复' }}</el-button>
          </div>
        </article>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title"><h2>最近触发</h2><span>最近 {{ triggers.length }} 条</span></div>
      <el-table :data="triggers" empty-text="暂无调度触发记录">
        <el-table-column label="定时任务" min-width="180" prop="schedule_name" />
        <el-table-column label="计划时间" min-width="170">
          <template #default="scope">{{ formatApiDateTime(scope.row.scheduled_for_at) }}</template>
        </el-table-column>
        <el-table-column label="触发状态" width="110">
          <template #default="scope"><el-tag :type="statusTag(scope.row.status)" size="small">{{ triggerStatusText(scope.row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="计划 Run" min-width="210">
          <template #default="scope">
            <span v-if="scope.row.plan_run_id">{{ scope.row.plan_run_status || '已创建' }} · {{ scope.row.plan_run_id }}</span>
            <span v-else class="muted">{{ scope.row.error_message || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="scope">
            <el-button
              v-if="scope.row.status === 'FAILED' && !scope.row.plan_run_id"
              link
              type="primary"
              :loading="actingTriggerId === scope.row.id"
              @click="retryTrigger(scope.row)"
            >重试</el-button>
            <el-button
              v-if="scope.row.plan_run_id"
              link
              type="primary"
              @click="router.push({ name: 'project-test-plans', params: { projectId }, query: { project_id: String(projectId) } })"
            >查看运行</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑定时任务' : '新建定时任务'" width="680px" destroy-on-close>
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="名称"><el-input v-model="form.name" maxlength="255" /></el-form-item>
          <el-form-item label="Test Plan">
            <el-select v-model="form.plan_id" filterable class="full-width">
              <el-option v-for="plan in activePlans" :key="plan.id" :label="plan.name" :value="plan.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="调度类型">
            <el-radio-group v-model="form.schedule_type" @change="previewItems = []">
              <el-radio-button value="DAILY">每天</el-radio-button>
              <el-radio-button value="WEEKLY">每周</el-radio-button>
              <el-radio-button value="CRON">Cron</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="IANA 时区"><el-input v-model="form.timezone" placeholder="Asia/Shanghai" /></el-form-item>
        </div>
        <el-form-item v-if="form.schedule_type !== 'CRON'" label="执行时间">
          <el-time-select v-model="form.daily_time" start="00:00" step="00:15" end="23:45" />
        </el-form-item>
        <el-form-item v-if="form.schedule_type === 'WEEKLY'" label="执行星期">
          <el-checkbox-group v-model="form.weekdays">
            <el-checkbox-button v-for="day in weekdays" :key="day.value" :value="day.value">{{ day.label }}</el-checkbox-button>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item v-if="form.schedule_type === 'CRON'" label="Cron 表达式">
          <el-input v-model="form.cron_expression" placeholder="0 9 * * 1-5" />
          <small class="hint">5 段格式：分 时 日 月 周；支持 *、逗号、范围和步长，周日可用 0 或 7。</small>
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
        <div class="preview-row">
          <el-button :loading="previewing" @click="runPreview">预览未来 5 次</el-button>
          <span v-if="previewItems.length">{{ previewItems.map((item) => formatApiDateTime(item)).join(' · ') }}</span>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSchedule">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.schedule-page { display: grid; gap: 20px; }
.schedule-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.schedule-heading h1 { margin: 4px 0 8px; color: #15213a; font-size: 30px; }
.schedule-heading p { margin: 0; color: #66758f; }
.eyebrow { color: #356cff; font-size: 12px; font-weight: 800; letter-spacing: 1.6px; }
.panel { padding: 22px; border: 1px solid #e2e8f2; border-radius: 18px; background: #fff; box-shadow: 0 8px 24px rgba(28, 47, 81, .04); }
.panel-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.panel-title h2 { margin: 0; color: #1c2a44; font-size: 18px; }
.panel-title span { color: #8793a8; font-size: 13px; }
.schedule-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 14px; }
.schedule-card { display: grid; gap: 14px; padding: 18px; border: 1px solid #e5eaf2; border-radius: 14px; background: linear-gradient(145deg, #fff, #fbfcff); }
.card-head, .card-actions, .tags { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.card-head h3 { margin: 0 0 4px; color: #1c2a44; font-size: 16px; }
.card-head small, .muted, .hint { color: #8793a8; }
.expression { padding: 10px 12px; border-radius: 9px; color: #29446f; background: #f1f5fc; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
dl { display: grid; gap: 8px; margin: 0; }
dl div { display: flex; justify-content: space-between; gap: 16px; }
dt { color: #8793a8; }
dd { margin: 0; color: #34425a; text-align: right; }
.card-actions { justify-content: flex-end; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.full-width { width: 100%; }
.preview-row { display: flex; align-items: flex-start; gap: 12px; color: #5d6b82; font-size: 12px; line-height: 1.7; }
@media (max-width: 760px) { .schedule-heading { flex-direction: column; } .form-grid { grid-template-columns: 1fr; } }
</style>
