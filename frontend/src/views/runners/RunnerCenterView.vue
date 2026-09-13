<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { CopyDocument, Cpu, Key, Refresh, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createRegistrationToken,
  getRegistrationTokens,
  getRunner,
  getRunners,
  revokeRegistrationToken,
  revokeRunner,
} from '@/api/runners'
import { getApiErrorMessage } from '@/api/http'
import type {
  OnlineStatus,
  RegistrationTokenMetadata,
  RegistrationTokenStatus,
  Runner,
  RunnerCapability,
  RunnerStatus,
} from '@/types/runner'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { parseApiDateTime } from '@/utils/datetime'

const POLL_INTERVAL_MS = 15_000

const runners = ref<Runner[]>([])
const runnerTotal = ref(0)
const tokens = ref<RegistrationTokenMetadata[]>([])
const {
  items: pagedRunners, total: pagedRunnerTotal, page: runnerPage,
  pageSize: runnerPageSize, changePage: changeRunnerPage,
  changePageSize: changeRunnerPageSize,
} = useClientPagination(runners)
const {
  items: pagedTokens, total: pagedTokenTotal, page: tokenPage,
  pageSize: tokenPageSize, changePage: changeTokenPage,
  changePageSize: changeTokenPageSize,
} = useClientPagination(tokens)
const tokenTotal = ref(0)
const inFlight = ref(false)
const pageLoading = ref(false)
const runnerError = ref('')
const tokenError = ref('')
const lastRefreshedAt = ref<string | null>(null)
const actionKey = ref('')

const detailVisible = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const selectedRunner = ref<Runner | null>(null)

const createTokenVisible = ref(false)
const createTokenSubmitting = ref(false)
const tokenExpiresInSeconds = ref<number | undefined>()
const tokenResultVisible = ref(false)
const plaintextToken = ref('')
const createdTokenExpiresAt = ref<string | null>(null)

let pollTimer: number | undefined

const pageError = computed(() => {
  const messages = [runnerError.value && `Runner 列表：${runnerError.value}`, tokenError.value && `Token 列表：${tokenError.value}`]
  return messages.filter(Boolean).join('；')
})

const onlineCount = computed(() => runners.value.filter(
  (runner) => runner.status === 'ACTIVE' && displayedOnlineStatus(runner) === 'ONLINE',
).length)
const offlineCount = computed(() => runners.value.filter(
  (runner) => runner.status === 'ACTIVE' && displayedOnlineStatus(runner) === 'OFFLINE',
).length)
const unknownOrRevokedCount = computed(() => runners.value.filter(
  (runner) => runner.status === 'REVOKED' || displayedOnlineStatus(runner) === 'UNKNOWN',
).length)

function displayedOnlineStatus(runner: Runner): OnlineStatus {
  return runner.redis_available ? runner.online_status : 'UNKNOWN'
}

function apiMessage(error: unknown, fallback: string): string {
  return getApiErrorMessage(error, fallback)
}

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = parseApiDateTime(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

function runnerStatusLabel(status: RunnerStatus): string {
  return status === 'ACTIVE' ? '有效' : '已撤销'
}

function runnerStatusType(status: RunnerStatus): 'success' | 'danger' {
  return status === 'ACTIVE' ? 'success' : 'danger'
}

function onlineStatusLabel(status: OnlineStatus): string {
  return status === 'ONLINE' ? 'ONLINE · 在线' : status === 'OFFLINE' ? 'OFFLINE · 离线' : 'UNKNOWN · 未知'
}

function onlineStatusType(status: OnlineStatus): 'success' | 'danger' | 'warning' {
  if (status === 'ONLINE') return 'success'
  if (status === 'OFFLINE') return 'danger'
  return 'warning'
}

function tokenStatusLabel(status: RegistrationTokenStatus): string {
  const labels: Record<RegistrationTokenStatus, string> = {
    ACTIVE: '有效',
    CONSUMED: '已消费',
    REVOKED: '已撤销',
    EXPIRED: '已过期',
  }
  return labels[status]
}

function tokenStatusType(status: RegistrationTokenStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'ACTIVE') return 'success'
  if (status === 'EXPIRED') return 'warning'
  if (status === 'REVOKED') return 'danger'
  return 'info'
}

function capabilitySummary(capabilities: RunnerCapability[]): string {
  if (!capabilities.length) return '未上报'
  return capabilities.map((item) => `${item.name}${item.status === 'READY' ? '' : ' · 不可用'}`).join(' · ')
}

function slotSummary(runner: Runner): string {
  if (!runner.slots.length) return '未上报'
  return runner.slots.map((slot) => `${slot.type} ${slot.available}/${slot.total}`).join(' · ')
}

async function loadPage(options: { silent?: boolean } = {}): Promise<void> {
  const silent = options.silent ?? false
  if (inFlight.value) return
  inFlight.value = true
  if (!silent) pageLoading.value = true
  const failures: string[] = []
  try {
    const [runnerResult, tokenResult] = await Promise.allSettled([
      getRunners(),
      getRegistrationTokens(),
    ])

    if (runnerResult.status === 'fulfilled') {
      runners.value = runnerResult.value.items
      runnerTotal.value = runnerResult.value.total
      runnerError.value = ''
    } else {
      runnerError.value = apiMessage(runnerResult.reason, 'Runner 列表加载失败')
      failures.push(`Runner 列表加载失败：${runnerError.value}`)
    }

    if (tokenResult.status === 'fulfilled') {
      tokens.value = tokenResult.value.items
      tokenTotal.value = tokenResult.value.total
      tokenError.value = ''
    } else {
      tokenError.value = apiMessage(tokenResult.reason, '注册 Token 列表加载失败')
      failures.push(`Token 列表加载失败：${tokenError.value}`)
    }

    if (runnerResult.status === 'fulfilled' || tokenResult.status === 'fulfilled') {
      lastRefreshedAt.value = new Date().toISOString()
    }
    if (failures.length && !silent) ElMessage.error(failures[0])
  } finally {
    inFlight.value = false
    if (!silent) pageLoading.value = false
  }
}

async function openRunnerDetail(runner: Runner): Promise<void> {
  selectedRunner.value = runner
  detailVisible.value = true
  detailLoading.value = true
  detailError.value = ''
  try {
    selectedRunner.value = await getRunner(runner.id)
  } catch (error) {
    detailError.value = apiMessage(error, 'Runner 详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function handleRevokeRunner(runner: Runner): Promise<void> {
  if (runner.status !== 'ACTIVE') return
  try {
    await ElMessageBox.confirm(
      `撤销后 Runner ${runner.name} 将不能继续使用当前 credential 注册心跳，确认继续？`,
      '撤销 Runner',
      { type: 'warning', confirmButtonText: '确认撤销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  actionKey.value = `runner:${runner.id}`
  try {
    const revoked = await revokeRunner(runner.id)
    selectedRunner.value = revoked
    ElMessage.success('Runner 已撤销')
    await loadPage({ silent: true })
  } catch (error) {
    ElMessage.error(apiMessage(error, 'Runner 撤销失败'))
  } finally {
    actionKey.value = ''
  }
}

function openCreateToken(): void {
  tokenExpiresInSeconds.value = undefined
  createTokenVisible.value = true
}

function resetCreateTokenForm(): void {
  tokenExpiresInSeconds.value = undefined
  createTokenSubmitting.value = false
}

async function submitCreateToken(): Promise<void> {
  const expiresIn = tokenExpiresInSeconds.value
  if (expiresIn !== undefined && (!Number.isInteger(expiresIn) || expiresIn < 60 || expiresIn > 3600)) {
    ElMessage.warning('有效期必须是 60～3600 秒的整数')
    return
  }
  createTokenSubmitting.value = true
  try {
    const created = await createRegistrationToken(
      expiresIn === undefined ? {} : { expires_in_seconds: expiresIn },
    )
    plaintextToken.value = created.token
    createdTokenExpiresAt.value = created.expires_at
    createTokenVisible.value = false
    tokenResultVisible.value = true
    await loadPage({ silent: true })
  } catch (error) {
    ElMessage.error(apiMessage(error, '注册 Token 创建失败'))
  } finally {
    createTokenSubmitting.value = false
  }
}

function clearTokenResult(): void {
  plaintextToken.value = ''
  createdTokenExpiresAt.value = null
}

async function copyToken(): Promise<void> {
  if (!plaintextToken.value) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(plaintextToken.value)
    ElMessage.success('Token 已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择 Token 复制；Token 不会被自动保存')
  }
}

async function handleRevokeToken(token: RegistrationTokenMetadata): Promise<void> {
  if (token.status !== 'ACTIVE') return
  try {
    await ElMessageBox.confirm(
      `撤销后 Token ${token.id} 将不能再用于 Runner 注册，确认继续？`,
      '撤销注册 Token',
      { type: 'warning', confirmButtonText: '确认撤销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  actionKey.value = `token:${token.id}`
  try {
    await revokeRegistrationToken(token.id)
    ElMessage.success('注册 Token 已撤销')
    await loadPage({ silent: true })
  } catch (error) {
    ElMessage.error(apiMessage(error, '注册 Token 撤销失败'))
  } finally {
    actionKey.value = ''
  }
}

function startPolling(): void {
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
  if (document.visibilityState !== 'visible') return
  pollTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') void loadPage({ silent: true })
  }, POLL_INTERVAL_MS)
}

function stopPolling(): void {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function handleVisibilityChange(): void {
  if (document.visibilityState === 'visible') {
    startPolling()
    void loadPage({ silent: true })
  } else {
    stopPolling()
  }
}

onMounted(() => {
  document.addEventListener('visibilitychange', handleVisibilityChange)
  startPolling()
  void loadPage()
})

onUnmounted(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <div class="runners-page">
    <header class="page-heading runner-heading">
      <div>
        <span class="eyebrow dark">运行执行器</span>
        <div class="runner-title-row">
          <h1>Runner 中心</h1>
          <el-tag type="warning" effect="plain">V1 · Runner 控制面</el-tag>
        </div>
        <p>管理 Windows Runner 注册凭据，观察心跳在线状态与执行能力。</p>
      </div>
      <div class="requirement-actions">
        <span v-if="lastRefreshedAt" class="last-refresh">更新于 {{ formatDateTime(lastRefreshedAt) }}</span>
        <el-button :icon="Refresh" :loading="pageLoading" @click="loadPage()">刷新</el-button>
        <el-button type="primary" :icon="Key" @click="openCreateToken">创建注册 Token</el-button>
      </div>
    </header>

    <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" show-icon class="runner-alert" />

    <section class="runner-metrics">
      <article><div class="metric-icon"><el-icon><Cpu /></el-icon></div><div><span>Runner 总数</span><strong>{{ runnerTotal }}</strong><small>包含已撤销</small></div></article>
      <article class="metric-success"><div class="metric-icon"><el-icon><View /></el-icon></div><div><span>在线</span><strong>{{ onlineCount }}</strong><small>Redis 可用且有心跳</small></div></article>
      <article class="metric-danger"><div class="metric-icon"><el-icon><View /></el-icon></div><div><span>离线</span><strong>{{ offlineCount }}</strong><small>Redis 可用但无心跳</small></div></article>
      <article class="metric-warning"><div class="metric-icon"><el-icon><Key /></el-icon></div><div><span>未知或已撤销</span><strong>{{ unknownOrRevokedCount }}</strong><small>Redis 不可用或已撤销</small></div></article>
    </section>

    <section class="runner-card-grid">
      <el-card class="runner-card" shadow="never" v-loading="pageLoading">
        <template #header>
          <div class="card-heading"><div><strong>Runner 列表</strong><span>{{ runnerTotal }} 个注册记录</span></div><el-tag size="small" effect="plain">15 秒自动刷新</el-tag></div>
        </template>
        <el-table v-if="runners.length" :data="pagedRunners" stripe row-key="id" max-height="480">
          <el-table-column label="名称 / 主机名" min-width="200">
            <template #default="{ row }: { row: Runner }">
              <div class="runner-name-cell"><strong>{{ row.name }}</strong><span>{{ row.hostname }}</span><code>{{ row.id }}</code></div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }: { row: Runner }"><el-tag :type="runnerStatusType(row.status)" size="small">{{ runnerStatusLabel(row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="在线状态" width="150">
            <template #default="{ row }: { row: Runner }">
              <el-tooltip v-if="!row.redis_available" content="Redis 状态不可用，暂不判断 Runner 是否离线" placement="top">
                <el-tag :type="onlineStatusType(displayedOnlineStatus(row))" size="small">{{ onlineStatusLabel(displayedOnlineStatus(row)) }}</el-tag>
              </el-tooltip>
              <el-tag v-else :type="onlineStatusType(displayedOnlineStatus(row))" size="small">{{ onlineStatusLabel(displayedOnlineStatus(row)) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="最后心跳" min-width="170"><template #default="{ row }: { row: Runner }">{{ formatDateTime(row.last_heartbeat_at) }}</template></el-table-column>
          <el-table-column label="标签" min-width="160"><template #default="{ row }: { row: Runner }"><div class="tag-list"><el-tag v-for="tag in row.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag><span v-if="!row.tags.length" class="muted">未上报</span></div></template></el-table-column>
          <el-table-column label="能力" min-width="210"><template #default="{ row }: { row: Runner }"><span class="summary-text">{{ capabilitySummary(row.capabilities) }}</span></template></el-table-column>
          <el-table-column label="任务槽位" min-width="190"><template #default="{ row }: { row: Runner }"><span class="summary-text">{{ slotSummary(row) }}</span></template></el-table-column>
          <el-table-column label="操作" width="155" fixed="right">
            <template #default="{ row }: { row: Runner }">
              <el-button link type="primary" @click="openRunnerDetail(row)">详情</el-button>
              <el-button v-if="row.status === 'ACTIVE'" link type="danger" :loading="actionKey === `runner:${row.id}`" @click="handleRevokeRunner(row)">撤销</el-button>
              <span v-else class="muted">不可操作</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else :description="runnerError || '当前还没有注册 Runner'" :image-size="90" />
        <el-pagination v-if="pagedRunnerTotal" class="records-pagination" small layout="total, sizes, prev, pager, next" :current-page="runnerPage" :page-size="runnerPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="pagedRunnerTotal" @current-change="changeRunnerPage" @size-change="changeRunnerPageSize" />
      </el-card>

      <el-card class="runner-card token-card" shadow="never" v-loading="pageLoading">
        <template #header><div class="card-heading"><div><strong>注册 Token</strong><span>{{ tokenTotal }} 条元数据</span></div><el-button type="primary" link :icon="Key" @click="openCreateToken">新建</el-button></div></template>
        <el-table v-if="tokens.length" :data="pagedTokens" stripe row-key="id" max-height="480">
          <el-table-column label="状态" width="140"><template #default="{ row }: { row: RegistrationTokenMetadata }"><el-tag :type="tokenStatusType(row.status)" size="small">{{ tokenStatusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="有效期" min-width="165"><template #default="{ row }: { row: RegistrationTokenMetadata }">{{ formatDateTime(row.expires_at) }}</template></el-table-column>
          <el-table-column label="消费 / 撤销" min-width="160"><template #default="{ row }: { row: RegistrationTokenMetadata }"><div>{{ row.consumed_at ? `消费：${formatDateTime(row.consumed_at)}` : '未消费' }}</div><small>{{ row.revoked_at ? `撤销：${formatDateTime(row.revoked_at)}` : '未撤销' }}</small></template></el-table-column>
          <el-table-column label="Runner" min-width="150"><template #default="{ row }: { row: RegistrationTokenMetadata }"><code>{{ row.consumed_runner_id ?? '—' }}</code></template></el-table-column>
          <el-table-column label="创建" min-width="145"><template #default="{ row }: { row: RegistrationTokenMetadata }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
          <el-table-column label="操作" width="100" fixed="right"><template #default="{ row }: { row: RegistrationTokenMetadata }"><el-button v-if="row.status === 'ACTIVE'" link type="danger" :loading="actionKey === `token:${row.id}`" @click="handleRevokeToken(row)">撤销</el-button><span v-else class="muted">不可操作</span></template></el-table-column>
        </el-table>
        <el-empty v-else :description="tokenError || '当前还没有注册 Token'" :image-size="90" />
        <el-pagination v-if="pagedTokenTotal" class="records-pagination" small layout="total, sizes, prev, pager, next" :current-page="tokenPage" :page-size="tokenPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="pagedTokenTotal" @current-change="changeTokenPage" @size-change="changeTokenPageSize" />
      </el-card>
    </section>

    <el-drawer v-model="detailVisible" title="Runner 详情" size="620px">
      <div v-loading="detailLoading" class="runner-detail">
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon />
        <template v-if="selectedRunner">
          <div class="detail-title"><div><span class="eyebrow dark">Runner 详情</span><h2>{{ selectedRunner.name }}</h2><code>{{ selectedRunner.id }}</code></div><div class="detail-tags"><el-tag :type="runnerStatusType(selectedRunner.status)">{{ runnerStatusLabel(selectedRunner.status) }}</el-tag><el-tag :type="onlineStatusType(displayedOnlineStatus(selectedRunner))">{{ onlineStatusLabel(displayedOnlineStatus(selectedRunner)) }}</el-tag></div></div>
          <el-descriptions title="系统信息" :column="2" border class="detail-descriptions">
            <el-descriptions-item label="主机名">{{ selectedRunner.hostname }}</el-descriptions-item><el-descriptions-item label="IP">{{ selectedRunner.ip_address ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="操作系统">{{ selectedRunner.os ?? '—' }}</el-descriptions-item><el-descriptions-item label="CPU">{{ selectedRunner.cpu ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="内存">{{ selectedRunner.ram ?? '—' }}</el-descriptions-item><el-descriptions-item label="磁盘">{{ selectedRunner.disk ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="Python">{{ selectedRunner.python ?? '—' }}</el-descriptions-item><el-descriptions-item label="Chrome">{{ selectedRunner.chrome ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="Playwright">{{ selectedRunner.playwright ?? '—' }}</el-descriptions-item><el-descriptions-item label="Java">{{ selectedRunner.java ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="JMeter">{{ selectedRunner.jmeter ?? '—' }}</el-descriptions-item><el-descriptions-item label="心跳间隔">{{ selectedRunner.heartbeat_interval_seconds }} 秒</el-descriptions-item>
            <el-descriptions-item label="最后心跳">{{ formatDateTime(selectedRunner.last_heartbeat_at) }}</el-descriptions-item><el-descriptions-item label="撤销时间">{{ formatDateTime(selectedRunner.revoked_at) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(selectedRunner.created_at) }}</el-descriptions-item><el-descriptions-item label="更新时间">{{ formatDateTime(selectedRunner.updated_at) }}</el-descriptions-item>
          </el-descriptions>
          <section class="detail-section"><header><strong>执行能力</strong><span>{{ selectedRunner.capabilities.length }} 项</span></header><el-empty v-if="!selectedRunner.capabilities.length" description="未上报执行能力" :image-size="60" /><div v-else class="capability-list"><div v-for="capability in selectedRunner.capabilities" :key="capability.name" class="capability-item"><div><strong>{{ capability.name }}</strong><span v-if="capability.status === 'UNAVAILABLE'">{{ capability.reason }}</span></div><el-tag :type="capability.status === 'READY' ? 'success' : 'warning'" size="small">{{ capability.status === 'READY' ? '就绪' : '不可用' }}</el-tag></div></div></section>
          <section class="detail-section"><header><strong>任务槽位</strong><span>{{ selectedRunner.slots.length }} 组</span></header><el-empty v-if="!selectedRunner.slots.length" description="未上报任务槽位" :image-size="60" /><el-table v-else :data="selectedRunner.slots" size="small" border><el-table-column prop="type" label="类型" /><el-table-column prop="total" label="总量" /><el-table-column prop="available" label="可用" /></el-table></section>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="createTokenVisible" title="创建注册 Token" width="520px" :close-on-click-modal="false" @closed="resetCreateTokenForm">
      <el-alert title="Token 只会在创建成功后显示一次。关闭结果窗口后无法再次查看，请立即复制并安全保存。" type="warning" :closable="false" show-icon />
      <el-form label-position="top" class="token-form"><el-form-item label="有效期（秒）"><el-input-number v-model="tokenExpiresInSeconds" :min="60" :max="3600" :step="60" controls-position="right" /><div class="form-hint">留空使用后端默认有效期；可选范围为 60～3600 秒。</div></el-form-item></el-form>
      <template #footer><el-button @click="createTokenVisible = false">取消</el-button><el-button type="primary" :loading="createTokenSubmitting" @click="submitCreateToken">创建并显示 Token</el-button></template>
    </el-dialog>

    <el-dialog v-model="tokenResultVisible" title="注册 Token 创建成功" width="640px" :close-on-click-modal="false" :show-close="true" @close="clearTokenResult">
      <el-alert title="这是 Token 明文唯一展示机会。关闭后无法再次查看；请不要写入浏览器存储、URL 或日志。" type="warning" :closable="false" show-icon />
      <div class="token-result"><div class="token-result-meta"><span>令牌仅在此处显示一次</span><span>有效期至 {{ formatDateTime(createdTokenExpiresAt) }}</span></div><el-input :model-value="plaintextToken" type="password" show-password readonly class="token-secret-input" /><el-button type="primary" :icon="CopyDocument" @click="copyToken">复制 Token</el-button></div>
      <template #footer><el-button type="primary" @click="tokenResultVisible = false">我已安全保存，关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.runners-page { max-width: 1580px; margin: 0 auto; }
.runner-heading { align-items: center; }
.runner-title-row { display: flex; align-items: center; gap: 12px; }
.runner-title-row h1 { margin-bottom: 0; }
.last-refresh { color: #8792a5; font-size: 11px; }
.runner-alert { margin-bottom: 16px; }
.runner-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
.runner-metrics article { display: flex; align-items: center; gap: 14px; padding: 18px 20px; border: 1px solid #e4eaf3; border-radius: 14px; background: white; box-shadow: 0 6px 18px rgba(35,54,91,.04); }
.metric-icon { display: grid; width: 42px; height: 42px; flex: 0 0 auto; place-items: center; border-radius: 12px; color: #4e83e5; background: #edf3ff; }
.metric-success .metric-icon { color: #169b84; background: #e8f8f4; }
.metric-danger .metric-icon { color: #d25555; background: #fff0f0; }
.metric-warning .metric-icon { color: #d68a30; background: #fff6e9; }
.runner-metrics span, .runner-metrics small { display: block; color: #8893a6; font-size: 11px; }
.runner-metrics strong { display: block; margin: 4px 0; color: #213557; font-size: 26px; }
.runner-card-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(520px, .95fr); gap: 18px; }
.runner-card { min-width: 0; overflow: hidden; border-color: #e2e8f1; }
.card-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-heading strong, .card-heading span { display: block; }
.card-heading span { margin-top: 4px; color: #8792a5; font-size: 11px; }
.runner-name-cell strong, .runner-name-cell span, .runner-name-cell code { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.runner-name-cell span { margin-top: 3px; color: #718099; font-size: 12px; }
.runner-name-cell code { max-width: 175px; margin-top: 4px; color: #9aa4b5; font-size: 10px; }
.tag-list { display: flex; flex-wrap: wrap; gap: 4px; }
.summary-text { color: #56647a; font-size: 12px; line-height: 1.5; }
.muted { color: #9aa4b5; font-size: 12px; }
.token-card :deep(.el-table) { font-size: 12px; }
.token-card small { color: #8a94a6; font-size: 10px; }
.runner-detail { min-height: 700px; }
.detail-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 20px; padding-bottom: 18px; border-bottom: 1px solid #edf0f5; }
.detail-title h2 { margin: 6px 0; font-size: 23px; }
.detail-title code { color: #7d8ba4; font-size: 11px; }
.detail-tags { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.detail-descriptions { margin-bottom: 24px; }
.detail-section { margin-top: 22px; }
.detail-section > header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.detail-section > header span { color: #8792a5; font-size: 11px; }
.capability-list { display: grid; gap: 8px; }
.capability-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 13px; border: 1px solid #e5eaf2; border-radius: 9px; background: #fbfcfe; }
.capability-item strong, .capability-item span { display: block; }
.capability-item span { margin-top: 4px; color: #d08a30; font-size: 11px; }
.token-form { margin-top: 22px; }
.form-hint { margin-top: 7px; color: #8792a5; font-size: 11px; }
.token-result { display: grid; gap: 12px; margin-top: 22px; }
.token-result-meta { display: flex; justify-content: space-between; color: #728097; font-size: 12px; }
.token-secret-input :deep(input) { font-family: "JetBrains Mono", Consolas, monospace; letter-spacing: .06em; }
@media (max-width: 1400px) {
  .runner-card-grid { grid-template-columns: 1fr; }
}
@media (max-width: 1100px) {
  .runner-heading { align-items: flex-start; flex-direction: column; }
  .runner-metrics { grid-template-columns: repeat(2, 1fr); }
  .requirement-actions { flex-wrap: wrap; }
}
</style>
