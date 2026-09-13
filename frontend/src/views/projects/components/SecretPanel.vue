<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { Key, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { createSecret, getSecrets, rotateSecret, updateSecret } from '@/api/secrets'
import type { Secret, SecretType } from '@/types/secret'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const props = defineProps<{ projectId: number; environmentId?: number }>()
const secrets = ref<Secret[]>([])
const secretPages = useClientPagination(secrets)
const loading = ref(false)
const dialogVisible = ref(false)
const rotateVisible = ref(false)
const rotatingSecret = ref<Secret | null>(null)
const rotateValue = ref('')
const loginCredentialVisible = ref(false)
const savingLoginCredential = ref(false)
const loginCredentialForm = reactive({ username: '', password: '' })
const LOGIN_USERNAME_SECRET = 'AI_TEST_USERNAME'
const LOGIN_PASSWORD_SECRET = 'AI_TEST_PASSWORD'
const secretTypes: SecretType[] = ['PASSWORD', 'TOKEN', 'API_KEY', 'DB_PASSWORD', 'CLIENT_SECRET']
const secretTypeLabels: Record<SecretType, string> = {
  PASSWORD: '密码',
  TOKEN: 'Token',
  API_KEY: 'API Key',
  DB_PASSWORD: '数据库密码',
  CLIENT_SECRET: '客户端密钥',
}
const form = reactive<{ name: string; secret_type: SecretType; value: string; project_scope: boolean }>({
  name: '', secret_type: 'PASSWORD', value: '', project_scope: false,
})

async function load(): Promise<void> {
  loading.value = true
  try { secrets.value = await getSecrets(props.projectId, props.environmentId) }
  finally { loading.value = false }
}

function openCreate(): void {
  Object.assign(form, { name: '', secret_type: 'PASSWORD', value: '', project_scope: false })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!/^[A-Za-z_][A-Za-z0-9_.-]+$/.test(form.name) || !form.value) {
    ElMessage.warning('请填写有效的密钥名称和值')
    return
  }
  try {
    await createSecret({
      project_id: props.projectId,
      environment_id: form.project_scope ? null : props.environmentId,
      name: form.name,
      secret_type: form.secret_type,
      value: form.value,
    })
    dialogVisible.value = false
    form.value = ''
    ElMessage.success('密钥已加密保存')
    await load()
  } catch { ElMessage.error('密钥保存失败，名称可能已存在') }
}

function openRotate(secret: Secret): void {
  rotatingSecret.value = secret
  rotateValue.value = ''
  rotateVisible.value = true
}

async function rotate(): Promise<void> {
  if (!rotatingSecret.value || !rotateValue.value) return
  try {
    await rotateSecret(rotatingSecret.value.id, rotateValue.value)
    rotateValue.value = ''
    rotateVisible.value = false
    ElMessage.success('密钥已轮换')
    await load()
  } catch { ElMessage.error('密钥轮换失败') }
}

async function toggle(secret: Secret): Promise<void> {
  try {
    await updateSecret(secret.id, { enabled: !secret.enabled })
    ElMessage.success(secret.enabled ? '密钥已停用' : '密钥已启用')
    await load()
  } catch { ElMessage.error('密钥状态更新失败') }
}

function openLoginCredential(): void {
  Object.assign(loginCredentialForm, { username: '', password: '' })
  loginCredentialVisible.value = true
}

async function saveLoginCredential(): Promise<void> {
  if (!loginCredentialForm.username.trim() || !loginCredentialForm.password) {
    ElMessage.warning('请填写测试用户名和密码')
    return
  }
  savingLoginCredential.value = true
  try {
    const allSecrets = await getSecrets(props.projectId)
    const upsert = async (name: string, value: string): Promise<void> => {
      const existing = allSecrets.find((item) => item.name === name)
      if (existing) {
        await rotateSecret(existing.id, value)
        if (!existing.enabled) await updateSecret(existing.id, { enabled: true })
        return
      }
      await createSecret({
        project_id: props.projectId,
        environment_id: null,
        name,
        secret_type: 'PASSWORD',
        value,
      })
    }
    await upsert(LOGIN_USERNAME_SECRET, loginCredentialForm.username.trim())
    await upsert(LOGIN_PASSWORD_SECRET, loginCredentialForm.password)
    Object.assign(loginCredentialForm, { username: '', password: '' })
    loginCredentialVisible.value = false
    ElMessage.success('AI 测试登录凭据已加密保存，后续生成将自动使用引用')
    await load()
  } catch {
    ElMessage.error('测试登录凭据保存失败')
  } finally {
    savingLoginCredential.value = false
  }
}

watch([() => props.projectId, () => props.environmentId], load, { immediate: true })
</script>

<template>
  <section class="configuration-panel">
    <header class="panel-heading"><div><h2>密钥管理</h2><p>值由 Windows DPAPI 加密保存，接口和日志均不返回明文。</p></div><div><el-button @click="openLoginCredential">配置 AI 测试登录账号</el-button><el-button type="primary" :icon="Plus" @click="openCreate">添加密钥</el-button></div></header>
    <el-alert title="AI 生成可执行登录及受保护接口用例时，会自动引用项目测试账号，不会把账号密码明文写入用例。" type="info" :closable="false" show-icon style="margin-bottom: 16px" />
    <el-table v-loading="loading" :data="secretPages.items.value" empty-text="当前范围暂无密钥">
      <el-table-column label="名称" min-width="220"><template #default="{ row }: { row: Secret }"><div class="secret-name"><el-icon><Key /></el-icon><strong>{{ row.name }}</strong></div></template></el-table-column>
      <el-table-column label="类型" width="150"><template #default="{ row }: { row: Secret }">{{ secretTypeLabels[row.secret_type] }}</template></el-table-column>
      <el-table-column prop="masked_value" label="值" width="150" />
      <el-table-column label="作用域" width="130"><template #default="{ row }: { row: Secret }">{{ row.environment_id ? '当前环境' : '项目级' }}</template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="{ row }: { row: Secret }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="操作" width="170"><template #default="{ row }: { row: Secret }"><el-button link type="primary" :icon="Refresh" @click="openRotate(row)">轮换</el-button><el-button link :type="row.enabled ? 'warning' : 'success'" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="secretPages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="secretPages.page.value" :page-size="secretPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="secretPages.total.value" @current-change="secretPages.changePage" @size-change="secretPages.changePageSize" />
    <el-dialog v-model="dialogVisible" title="添加密钥" width="540px">
      <el-form label-position="top">
        <div class="config-form-grid"><el-form-item label="密钥名称"><el-input v-model="form.name" placeholder="MYSQL_PASSWORD" /></el-form-item><el-form-item label="类型"><el-select v-model="form.secret_type"><el-option v-for="type in secretTypes" :key="type" :label="secretTypeLabels[type]" :value="type" /></el-select></el-form-item></div>
        <el-form-item label="密钥值"><el-input v-model="form.value" type="password" show-password autocomplete="new-password" /></el-form-item>
        <el-checkbox v-model="form.project_scope">项目级密钥（所有环境可引用）</el-checkbox>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" @click="submit">加密保存</el-button></template>
    </el-dialog>
    <el-dialog v-model="rotateVisible" :title="`轮换 ${rotatingSecret?.name ?? '密钥'}`" width="480px">
      <el-alert title="保存后旧值不可恢复，调用方需同步更新。" type="warning" :closable="false" show-icon />
      <el-input v-model="rotateValue" type="password" show-password placeholder="输入新值" style="margin-top: 18px" />
      <template #footer><el-button @click="rotateVisible = false">取消</el-button><el-button type="primary" @click="rotate">确认轮换</el-button></template>
    </el-dialog>
    <el-dialog v-model="loginCredentialVisible" title="配置 AI 测试登录账号" width="520px">
      <el-alert title="只需配置一次。系统分别保存为 AI_TEST_USERNAME 和 AI_TEST_PASSWORD，AI 只能看到引用名称。" type="info" :closable="false" show-icon />
      <el-form label-position="top" style="margin-top: 18px">
        <el-form-item label="测试用户名"><el-input v-model="loginCredentialForm.username" autocomplete="off" placeholder="用于测试环境登录的账号" /></el-form-item>
        <el-form-item label="测试密码"><el-input v-model="loginCredentialForm.password" type="password" show-password autocomplete="new-password" placeholder="用于测试环境登录的密码" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="loginCredentialVisible = false">取消</el-button><el-button type="primary" :loading="savingLoginCredential" @click="saveLoginCredential">加密保存</el-button></template>
    </el-dialog>
  </section>
</template>
