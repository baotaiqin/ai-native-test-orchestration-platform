<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  createCiToken,
  createWebhookEndpoint,
  getCiTokens,
  getWebhookDeliveries,
  getWebhookEndpoints,
  retryWebhookDelivery,
  revokeCiToken,
  setWebhookArchived,
} from '@/api/ci-cd'
import { getApiErrorMessage } from '@/api/http'
import { getTestPlans } from '@/api/test-plans'
import type {
  CiTokenMetadata,
  WebhookDelivery,
  WebhookEndpoint,
} from '@/types/ci-cd'
import type { TestPlan } from '@/types/test-plan'
import { formatApiDateTime } from '@/utils/datetime'

type TagType = 'success' | 'warning' | 'info' | 'primary' | 'danger'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number(route.params.projectId || route.query.project_id))
const loading = ref(false)
const saving = ref(false)
const tokenDialogVisible = ref(false)
const webhookDialogVisible = ref(false)
const createdTokenVisible = ref(false)
const createdToken = ref('')
const tokens = ref<CiTokenMetadata[]>([])
const endpoints = ref<WebhookEndpoint[]>([])
const deliveries = ref<WebhookDelivery[]>([])
const plans = ref<TestPlan[]>([])
const actingTokenId = ref<number>()
const actingEndpointId = ref<number>()
const actingDeliveryId = ref<string>()

const tokenForm = reactive({
  name: '',
  expires_in_days: 90,
  allowed_plan_ids: [] as number[],
})

const webhookForm = reactive({
  name: '',
  url: '',
  signing_secret: '',
  max_attempts: 3,
  timeout_seconds: 10,
})

const activePlans = computed(() => plans.value.filter((item) => item.status === 'ACTIVE'))

function tagType(status: string): TagType {
  if (status === 'ACTIVE' || status === 'SUCCEEDED') return 'success'
  if (status === 'FAILED' || status === 'REVOKED' || status === 'EXPIRED') return 'danger'
  if (status === 'PENDING' || status === 'SENDING' || status === 'RETRY') return 'warning'
  return 'info'
}

function tokenStatusText(status: CiTokenMetadata['status']): string {
  return { ACTIVE: '有效', REVOKED: '已撤销', EXPIRED: '已过期' }[status]
}

function deliveryStatusText(status: WebhookDelivery['status']): string {
  return {
    PENDING: '待投递',
    SENDING: '投递中',
    RETRY: '等待重试',
    SUCCEEDED: '成功',
    FAILED: '失败',
  }[status]
}

function planScope(token: CiTokenMetadata): string {
  if (!token.allowed_plan_ids) return '项目全部 Test Plan'
  return token.allowed_plan_ids
    .map((id) => plans.value.find((plan) => plan.id === id)?.name || `#${id}`)
    .join('、')
}

function openTokenDialog(): void {
  tokenForm.name = ''
  tokenForm.expires_in_days = 90
  tokenForm.allowed_plan_ids = []
  tokenDialogVisible.value = true
}

function openWebhookDialog(): void {
  webhookForm.name = ''
  webhookForm.url = ''
  webhookForm.signing_secret = ''
  webhookForm.max_attempts = 3
  webhookForm.timeout_seconds = 10
  webhookDialogVisible.value = true
}

async function saveToken(): Promise<void> {
  if (!tokenForm.name.trim()) {
    ElMessage.warning('请填写 Token 名称')
    return
  }
  saving.value = true
  try {
    const result = await createCiToken({
      project_id: projectId.value,
      name: tokenForm.name.trim(),
      expires_in_days: tokenForm.expires_in_days,
      allowed_plan_ids: tokenForm.allowed_plan_ids.length
        ? [...tokenForm.allowed_plan_ids]
        : undefined,
    })
    createdToken.value = result.token
    tokenDialogVisible.value = false
    createdTokenVisible.value = true
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'CI Token 创建失败'))
  } finally {
    saving.value = false
  }
}

async function copyToken(): Promise<void> {
  try {
    await navigator.clipboard.writeText(createdToken.value)
    ElMessage.success('Token 已复制')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

async function revokeToken(token: CiTokenMetadata): Promise<void> {
  try {
    await ElMessageBox.confirm('撤销后流水线将立即无法继续使用该 Token，是否继续？', '撤销 CI Token', {
      type: 'warning',
      confirmButtonText: '确认撤销',
      cancelButtonText: '返回',
    })
  } catch {
    return
  }
  actingTokenId.value = token.id
  try {
    await revokeCiToken(token.id)
    ElMessage.success('CI Token 已撤销')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'CI Token 撤销失败'))
  } finally {
    actingTokenId.value = undefined
  }
}

async function saveWebhook(): Promise<void> {
  if (!webhookForm.name.trim() || !webhookForm.url.trim()) {
    ElMessage.warning('请填写名称和 HTTPS URL')
    return
  }
  saving.value = true
  try {
    await createWebhookEndpoint({
      project_id: projectId.value,
      name: webhookForm.name.trim(),
      url: webhookForm.url.trim(),
      signing_secret: webhookForm.signing_secret.trim() || undefined,
      events: ['TEST_PLAN_RUN_COMPLETED'],
      enabled: true,
      max_attempts: webhookForm.max_attempts,
      timeout_seconds: webhookForm.timeout_seconds,
    })
    webhookDialogVisible.value = false
    ElMessage.success('Webhook 已创建')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'Webhook 创建失败'))
  } finally {
    saving.value = false
  }
}

async function toggleWebhook(endpoint: WebhookEndpoint): Promise<void> {
  actingEndpointId.value = endpoint.id
  try {
    await setWebhookArchived(endpoint.id, endpoint.status === 'ACTIVE')
    ElMessage.success(endpoint.status === 'ACTIVE' ? 'Webhook 已归档' : 'Webhook 已恢复')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'Webhook 状态更新失败'))
  } finally {
    actingEndpointId.value = undefined
  }
}

async function retryDelivery(delivery: WebhookDelivery): Promise<void> {
  actingDeliveryId.value = delivery.id
  try {
    await retryWebhookDelivery(delivery.id)
    ElMessage.success('Webhook 已重新投递')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, 'Webhook 重试失败'))
  } finally {
    actingDeliveryId.value = undefined
  }
}

async function loadAll(silent = false): Promise<void> {
  if (!Number.isInteger(projectId.value) || projectId.value <= 0) {
    await router.replace({ name: 'projects' })
    return
  }
  if (!silent) loading.value = true
  try {
    const [tokenItems, endpointItems, deliveryItems, planItems] = await Promise.all([
      getCiTokens(projectId.value),
      getWebhookEndpoints(projectId.value),
      getWebhookDeliveries(projectId.value),
      getTestPlans(projectId.value),
    ])
    tokens.value = tokenItems
    endpoints.value = endpointItems
    deliveries.value = deliveryItems
    plans.value = planItems
  } catch (error) {
    if (!silent) ElMessage.error(getApiErrorMessage(error, 'CI/CD 集成数据加载失败'))
  } finally {
    loading.value = false
  }
}

onMounted(() => void loadAll())
</script>

<template>
  <div v-loading="loading" class="integration-page">
    <section class="integration-heading">
      <div>
        <div class="eyebrow">V1 · CI/CD & WEBHOOK</div>
        <h1>CI/CD 集成</h1>
        <p>通过最小权限 Token 从流水线触发 Test Plan，并在聚合终态后向通用 Webhook 发送签名事件。</p>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title">
        <div><h2>CI Token</h2><span>Token 仅在创建时展示一次，服务端只保存摘要</span></div>
        <el-button type="primary" @click="openTokenDialog">创建 Token</el-button>
      </div>
      <el-table :data="tokens" empty-text="尚未创建 CI Token">
        <el-table-column label="名称 / 前缀" min-width="190"><template #default="scope"><strong>{{ scope.row.name }}</strong><small>{{ scope.row.token_prefix }}…</small></template></el-table-column>
        <el-table-column label="计划范围" min-width="230"><template #default="scope">{{ planScope(scope.row) }}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="tagType(scope.row.status)" size="small">{{ tokenStatusText(scope.row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="到期时间" width="180"><template #default="scope">{{ formatApiDateTime(scope.row.expires_at) }}</template></el-table-column>
        <el-table-column label="最近使用" width="180"><template #default="scope">{{ scope.row.last_used_at ? formatApiDateTime(scope.row.last_used_at) : '-' }}</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="scope"><el-button v-if="scope.row.status === 'ACTIVE'" link type="danger" :loading="actingTokenId === scope.row.id" @click="revokeToken(scope.row)">撤销</el-button></template></el-table-column>
      </el-table>
      <div class="api-help">
        <code>POST /api/v1/ci/test-plans/{plan_id}/run</code>
        <span>Header：Authorization: Bearer cit_…；Idempotency-Key: 流水线唯一键</span>
        <code>GET /api/v1/ci/test-plan-runs/{plan_run_id}</code>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title">
        <div><h2>Webhook</h2><span>终态事件使用 HTTPS JSON 投递，可选 HMAC-SHA256 签名</span></div>
        <el-button type="primary" @click="openWebhookDialog">创建 Webhook</el-button>
      </div>
      <div v-if="endpoints.length" class="endpoint-grid">
        <article v-for="endpoint in endpoints" :key="endpoint.id" class="endpoint-card">
          <div><strong>{{ endpoint.name }}</strong><el-tag :type="endpoint.status === 'ACTIVE' ? 'success' : 'info'" size="small">{{ endpoint.status === 'ACTIVE' ? '启用' : '已归档' }}</el-tag></div>
          <code>{{ endpoint.target_hint }}</code>
          <span>超时 {{ endpoint.timeout_seconds }} 秒 · 最多 {{ endpoint.max_attempts }} 次 · {{ endpoint.has_signing_secret ? '已签名' : '无签名' }}</span>
          <el-button link :loading="actingEndpointId === endpoint.id" @click="toggleWebhook(endpoint)">{{ endpoint.status === 'ACTIVE' ? '归档' : '恢复' }}</el-button>
        </article>
      </div>
      <el-empty v-else description="尚未创建 Webhook" />
    </section>

    <section class="panel">
      <div class="panel-title"><div><h2>最近投递</h2><span>最多展示 100 条</span></div></div>
      <el-table :data="deliveries" empty-text="暂无 Webhook 投递记录">
        <el-table-column prop="endpoint_name" label="Webhook" min-width="170" />
        <el-table-column label="Plan Run" min-width="240"><template #default="scope"><small>{{ scope.row.plan_run_id }}</small></template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="tagType(scope.row.status)" size="small">{{ deliveryStatusText(scope.row.status) }}</el-tag></template></el-table-column>
        <el-table-column prop="attempt_count" label="次数" width="70" />
        <el-table-column label="HTTP" width="80"><template #default="scope">{{ scope.row.response_status || '-' }}</template></el-table-column>
        <el-table-column label="结果" min-width="210"><template #default="scope">{{ scope.row.last_error_message || (scope.row.status === 'SUCCEEDED' ? '投递成功' : '-') }}</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="scope"><el-button v-if="scope.row.status === 'FAILED'" link type="primary" :loading="actingDeliveryId === scope.row.id" @click="retryDelivery(scope.row)">重试</el-button></template></el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="tokenDialogVisible" title="创建 CI Token" width="600px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="名称"><el-input v-model="tokenForm.name" maxlength="255" /></el-form-item>
        <el-form-item label="有效期（天）"><el-input-number v-model="tokenForm.expires_in_days" :min="1" :max="365" /></el-form-item>
        <el-form-item label="允许触发的 Test Plan">
          <el-select v-model="tokenForm.allowed_plan_ids" multiple filterable clearable class="full" placeholder="留空表示项目全部 Test Plan">
            <el-option v-for="plan in activePlans" :key="plan.id" :label="plan.name" :value="plan.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="tokenDialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveToken">创建</el-button></template>
    </el-dialog>

    <el-dialog v-model="createdTokenVisible" title="请立即保存 CI Token" width="680px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" title="关闭后无法再次查看；遗失时请撤销并重新创建。" />
      <div class="token-secret"><code>{{ createdToken }}</code><el-button type="primary" @click="copyToken">复制</el-button></div>
    </el-dialog>

    <el-dialog v-model="webhookDialogVisible" title="创建 Webhook" width="640px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="名称"><el-input v-model="webhookForm.name" maxlength="255" /></el-form-item>
        <el-form-item label="HTTPS URL"><el-input v-model="webhookForm.url" type="password" show-password autocomplete="off" placeholder="https://hooks.example.com/…" /></el-form-item>
        <el-form-item label="签名密钥（可选，至少 16 位）"><el-input v-model="webhookForm.signing_secret" type="password" show-password autocomplete="new-password" /></el-form-item>
        <div class="form-grid">
          <el-form-item label="最多尝试次数"><el-input-number v-model="webhookForm.max_attempts" :min="1" :max="5" /></el-form-item>
          <el-form-item label="单次超时（秒）"><el-input-number v-model="webhookForm.timeout_seconds" :min="1" :max="30" /></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button @click="webhookDialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveWebhook">创建</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.integration-page { display: grid; gap: 20px; }
.integration-heading h1 { margin: 4px 0 8px; color: #15213a; font-size: 30px; }
.integration-heading p { margin: 0; color: #66758f; }
.eyebrow { color: #356cff; font-size: 12px; font-weight: 800; letter-spacing: 1.6px; }
.panel { padding: 22px; border: 1px solid #e2e8f2; border-radius: 18px; background: #fff; box-shadow: 0 8px 24px rgba(28,47,81,.04); }
.panel-title { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 18px; }
.panel-title h2 { margin: 0 0 4px; color: #1c2a44; font-size: 18px; }
.panel-title span, small { display: block; color: #8793a8; font-size: 12px; }
.api-help { display: grid; gap: 8px; margin-top: 18px; padding: 14px; border-radius: 10px; color: #53627b; background: #f5f8fd; }
code { overflow-wrap: anywhere; color: #284b81; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.endpoint-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 12px; }
.endpoint-card { display: grid; gap: 10px; padding: 16px; border: 1px solid #e4e9f2; border-radius: 12px; }
.endpoint-card > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.endpoint-card > span { color: #748198; font-size: 12px; }
.endpoint-card .el-button { justify-self: end; }
.full { width: 100%; }
.token-secret { display: flex; align-items: center; gap: 12px; margin-top: 16px; padding: 14px; border: 1px solid #dfe6f1; border-radius: 10px; background: #f7f9fc; }
.token-secret code { flex: 1; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 720px) { .panel-title { align-items: flex-start; flex-direction: column; } .form-grid { grid-template-columns: 1fr; } }
</style>
