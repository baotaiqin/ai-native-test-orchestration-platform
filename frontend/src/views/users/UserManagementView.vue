<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Edit, Plus, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import { createUser, getUsers, updateUser } from '@/api/auth'
import { getApiErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import type { ManagedUser, PlatformRole, UserStatus } from '@/types/auth'
import { formatApiDateTime } from '@/utils/datetime'

const router = useRouter()
const authStore = useAuthStore()
const users = ref<ManagedUser[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const error = ref<string | null>(null)
const createVisible = ref(false)
const editVisible = ref(false)
const writing = ref(false)
const createFormRef = ref<FormInstance>()
const editFormRef = ref<FormInstance>()
const editingUser = ref<ManagedUser | null>(null)
const createForm = reactive({
  username: '',
  display_name: '',
  password: '',
  platform_role: 'USER' as PlatformRole,
  status: 'ACTIVE' as UserStatus,
})
const editForm = reactive({
  display_name: '',
  platform_role: 'USER' as PlatformRole,
  status: 'ACTIVE' as UserStatus,
})
const canManage = computed(() => authStore.identityVerified && authStore.isAdmin)
let alive = true
let listSequence = 0
let writeSequence = 0

const createRules: FormRules = {
  username: [{
    validator: (_rule, value: unknown, callback) => {
      if (typeof value !== 'string' || value.length > 64 || !value.trim()) callback(new Error('请输入 1～64 个字符的用户名'))
      else callback()
    },
    trigger: 'blur',
  }],
  display_name: [{
    validator: (_rule, value: unknown, callback) => {
      if (typeof value !== 'string' || value.length > 128 || !value.trim()) callback(new Error('请输入 1～128 个字符的显示名称'))
      else callback()
    },
    trigger: 'blur',
  }],
  password: [{
    validator: (_rule, value: unknown, callback) => {
      if (typeof value !== 'string' || value.length < 8 || value.length > 256) callback(new Error('初始密码长度必须为 8～256 个字符'))
      else callback()
    },
    trigger: 'blur',
  }],
}

const editRules: FormRules = {
  display_name: createRules.display_name,
}

function contextIsCurrent(epoch: string): boolean {
  return alive && canManage.value && authStore.identityEpoch === epoch
}

function resetCreateForm(): void {
  createForm.username = ''
  createForm.display_name = ''
  createForm.password = ''
  createForm.platform_role = 'USER'
  createForm.status = 'ACTIVE'
  createFormRef.value?.clearValidate()
}

function closeCreate(): void {
  createVisible.value = false
  resetCreateForm()
}

function closeEdit(): void {
  editVisible.value = false
  editingUser.value = null
  editForm.display_name = ''
  editForm.platform_role = 'USER'
  editForm.status = 'ACTIVE'
  editFormRef.value?.clearValidate()
}

function invalidatePageOperations(): void {
  listSequence += 1
  writeSequence += 1
  loading.value = false
  writing.value = false
  error.value = null
  users.value = []
  total.value = 0
  closeCreate()
  closeEdit()
}

async function loadUsers(requestedPage = 1): Promise<void> {
  if (!canManage.value || loading.value) return
  const epochAtStart = authStore.identityEpoch
  const requestSequence = ++listSequence
  loading.value = true
  error.value = null
  try {
    const response = await getUsers({ page: requestedPage, page_size: pageSize.value })
    if (!contextIsCurrent(epochAtStart) || requestSequence !== listSequence) return
    users.value = response.items
    total.value = response.total
    page.value = response.page
  } catch (loadError) {
    if (contextIsCurrent(epochAtStart) && requestSequence === listSequence) {
      error.value = getApiErrorMessage(loadError, '用户列表加载失败，请稍后重试。')
    }
  } finally {
    if (contextIsCurrent(epochAtStart) && requestSequence === listSequence) loading.value = false
  }
}

function openCreate(): void {
  if (!canManage.value || writing.value) return
  resetCreateForm()
  createVisible.value = true
}

function openEdit(item: ManagedUser): void {
  if (!canManage.value || writing.value) return
  editingUser.value = item
  editForm.display_name = item.display_name
  editForm.platform_role = item.platform_role
  editForm.status = item.status
  editVisible.value = true
}

function beginWrite(epoch: string): number | null {
  if (writing.value || !contextIsCurrent(epoch)) return null
  writing.value = true
  return ++writeSequence
}

async function submitCreate(): Promise<void> {
  if (!canManage.value || writing.value) return
  const epochAtEntry = authStore.identityEpoch
  const valid = await createFormRef.value?.validate().catch(() => false)
  if (!valid || !contextIsCurrent(epochAtEntry)) return
  const operation = beginWrite(epochAtEntry)
  if (operation === null) return
  try {
    await createUser({
      username: createForm.username,
      display_name: createForm.display_name,
      password: createForm.password,
      platform_role: createForm.platform_role,
      status: createForm.status,
    })
    if (!contextIsCurrent(epochAtEntry) || operation !== writeSequence) return
    closeCreate()
    ElMessage.success('用户已创建；用户名创建后不可修改')
    await loadUsers(1)
  } catch (writeError) {
    if (contextIsCurrent(epochAtEntry) && operation === writeSequence) {
      ElMessage.error(getApiErrorMessage(writeError, '用户创建失败，请稍后重试。'))
    }
  } finally {
    createForm.password = ''
    if (contextIsCurrent(epochAtEntry) && operation === writeSequence) writing.value = false
  }
}

async function submitEdit(): Promise<void> {
  const target = editingUser.value
  if (!target || !canManage.value || writing.value) return
  const epochAtEntry = authStore.identityEpoch
  const valid = await editFormRef.value?.validate().catch(() => false)
  if (!valid || !contextIsCurrent(epochAtEntry) || editingUser.value?.id !== target.id) return
  const operation = beginWrite(epochAtEntry)
  if (operation === null) return
  try {
    const updated = await updateUser(target.id, {
      display_name: editForm.display_name,
      platform_role: editForm.platform_role,
      status: editForm.status,
    })
    if (!contextIsCurrent(epochAtEntry) || operation !== writeSequence) return
    closeEdit()
    ElMessage.success('用户资料已更新')
    if (updated.id === authStore.user?.id) {
      await authStore.refreshUser()
      return
    }
    await loadUsers(page.value)
  } catch (writeError) {
    if (contextIsCurrent(epochAtEntry) && operation === writeSequence) {
      ElMessage.error(getApiErrorMessage(writeError, '用户更新失败，请稍后重试。'))
    }
  } finally {
    if (contextIsCurrent(epochAtEntry) && operation === writeSequence) writing.value = false
  }
}

function roleLabel(role: PlatformRole): string {
  return role === 'ADMIN' ? '平台管理员' : '普通用户'
}

function statusLabel(status: UserStatus): string {
  return status === 'ACTIVE' ? '启用' : '停用'
}

function dateLabel(value: string): string {
  return formatApiDateTime(value, value)
}

watch(
  [() => authStore.identityEpoch, () => authStore.identityVerified, () => authStore.isAdmin],
  () => {
    invalidatePageOperations()
    if (!authStore.isAuthenticated) {
      void router.replace({ name: 'login' })
    } else if (authStore.identityVerified && !authStore.isAdmin) {
      void router.replace({ name: 'dashboard' })
    } else if (canManage.value) {
      void loadUsers(1)
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  alive = false
  invalidatePageOperations()
})
</script>

<template>
  <div class="user-page">
    <header class="page-heading user-heading">
      <div>
        <span class="eyebrow dark">平台用户管理</span>
        <h1>用户管理</h1>
        <p>仅平台 ADMIN 可管理账号；项目成员角色仍在各项目内独立配置。</p>
      </div>
      <div class="heading-actions">
        <el-button :icon="Refresh" :loading="loading" :disabled="!canManage" @click="loadUsers(page)">刷新</el-button>
        <el-button type="primary" :icon="Plus" :disabled="!canManage || writing" @click="openCreate">创建用户</el-button>
      </div>
    </header>

    <el-alert
      title="平台角色与项目角色互不替代"
      description="这里仅维护平台 ADMIN / USER 与账号启停；不提供删除、密码读取、密码重置或公共注册。"
      type="info"
      show-icon
      :closable="false"
      class="scope-alert"
    />
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false">
      <template #default><el-button link type="primary" @click="loadUsers(page)">重试</el-button></template>
    </el-alert>

    <el-card class="users-card" shadow="never">
      <el-table v-loading="loading" :data="users" table-layout="fixed">
        <el-table-column label="用户名" min-width="170">
          <template #default="{ row }"><strong>{{ row.username }}</strong><small>{{ row.id }}</small></template>
        </el-table-column>
        <el-table-column prop="display_name" label="显示名称" min-width="160" />
        <el-table-column label="平台角色" width="130">
          <template #default="{ row }"><el-tag :type="row.platform_role === 'ADMIN' ? 'danger' : 'info'">{{ roleLabel(row.platform_role) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="账号状态" width="110">
          <template #default="{ row }"><el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ statusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="auth_version" label="认证版本" width="105" />
        <el-table-column label="创建 / 更新" min-width="195">
          <template #default="{ row }"><span>{{ dateLabel(row.created_at) }}</span><small>{{ dateLabel(row.updated_at) }}</small></template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }"><el-button link type="primary" :icon="Edit" :disabled="writing || !canManage" @click="openEdit(row)">编辑</el-button></template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !error && !users.length" description="暂无用户" />
      <el-pagination
        v-if="total"
        class="pagination"
        :current-page="page"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next"
        @current-change="loadUsers"
        @size-change="(value: number) => { pageSize = value; loadUsers(1) }"
      />
    </el-card>

    <el-dialog v-model="createVisible" title="创建平台用户" width="560px" :close-on-click-modal="false" @closed="resetCreateForm">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-position="top">
        <el-form-item label="用户名" prop="username"><el-input v-model="createForm.username" maxlength="64" autocomplete="off" placeholder="创建后不可修改" /></el-form-item>
        <el-form-item label="显示名称" prop="display_name"><el-input v-model="createForm.display_name" maxlength="128" /></el-form-item>
        <el-form-item label="初始密码" prop="password"><el-input v-model="createForm.password" type="password" maxlength="256" autocomplete="new-password" show-password /></el-form-item>
        <div class="two-column-form">
          <el-form-item label="平台角色"><el-select v-model="createForm.platform_role"><el-option label="普通用户" value="USER" /><el-option label="平台管理员" value="ADMIN" /></el-select></el-form-item>
          <el-form-item label="账号状态"><el-select v-model="createForm.status"><el-option label="启用" value="ACTIVE" /><el-option label="停用" value="DISABLED" /></el-select></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button :disabled="writing" @click="closeCreate">取消</el-button><el-button type="primary" :loading="writing" @click="submitCreate">创建</el-button></template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑平台用户" width="560px" :close-on-click-modal="false" @closed="closeEdit">
      <el-form ref="editFormRef" :model="editForm" :rules="editRules" label-position="top">
        <el-form-item label="用户名"><el-input :model-value="editingUser?.username ?? ''" disabled /><small>用户名创建后不可修改</small></el-form-item>
        <el-form-item label="显示名称" prop="display_name"><el-input v-model="editForm.display_name" maxlength="128" /></el-form-item>
        <div class="two-column-form">
          <el-form-item label="平台角色"><el-select v-model="editForm.platform_role"><el-option label="普通用户" value="USER" /><el-option label="平台管理员" value="ADMIN" /></el-select></el-form-item>
          <el-form-item label="账号状态"><el-select v-model="editForm.status"><el-option label="启用" value="ACTIVE" /><el-option label="停用" value="DISABLED" /></el-select></el-form-item>
        </div>
        <el-alert title="角色或状态变化会使该用户的旧 Session 失效；系统必须保留至少一个可用平台管理员。" type="warning" :closable="false" />
      </el-form>
      <template #footer><el-button :disabled="writing" @click="closeEdit">取消</el-button><el-button type="primary" :loading="writing" @click="submitEdit">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.user-page { max-width: 1380px; margin: 0 auto; }
.user-heading, .heading-actions { display: flex; align-items: center; }
.user-heading { justify-content: space-between; gap: 18px; }
.heading-actions { gap: 10px; }
.scope-alert { margin-bottom: 14px; }
.users-card { margin-top: 14px; border: 1px solid #e1e8f2; border-radius: 16px; }
.users-card strong, .users-card span, .users-card small { display: block; overflow-wrap: anywhere; }
.users-card small { margin-top: 4px; color: #8793a7; font-size: 10px; }
.pagination { justify-content: flex-end; margin-top: 16px; }
.two-column-form { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.two-column-form .el-select { width: 100%; }
@media (max-width: 800px) { .user-heading { align-items: flex-start; flex-direction: column; } .two-column-form { grid-template-columns: 1fr; } }
</style>
