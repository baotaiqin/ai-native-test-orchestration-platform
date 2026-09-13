<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Lock } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import { getApiErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const form = reactive({
  current_password: '',
  new_password: '',
  confirm_password: '',
})

function clearPasswords(): void {
  form.current_password = ''
  form.new_password = ''
  form.confirm_password = ''
  formRef.value?.clearValidate()
}

const rules: FormRules = {
  current_password: [{
    validator: (_rule, value: unknown, callback) => {
      if (typeof value !== 'string' || value.length < 1 || value.length > 256) {
        callback(new Error('当前密码长度必须为 1～256 个字符'))
      } else callback()
    },
    trigger: 'blur',
  }],
  new_password: [{
    validator: (_rule, value: unknown, callback) => {
      if (typeof value !== 'string' || value.length < 8 || value.length > 256) {
        callback(new Error('新密码长度必须为 8～256 个字符'))
      } else callback()
    },
    trigger: 'blur',
  }],
  confirm_password: [{
    validator: (_rule, value: unknown, callback) => {
      if (value !== form.new_password) callback(new Error('两次输入的新密码不一致'))
      else callback()
    },
    trigger: 'blur',
  }],
}

async function submit(): Promise<void> {
  if (submitting.value || !authStore.identityVerified || !authStore.isAuthenticated) return
  const epochAtEntry = authStore.identityEpoch
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid || authStore.identityEpoch !== epochAtEntry
    || !authStore.identityVerified || !authStore.isAuthenticated) return
  submitting.value = true
  try {
    const result = await authStore.changePassword({
      current_password: form.current_password,
      new_password: form.new_password,
    })
    if (!result.changed || !result.reauthentication_required || !result.local_cleared) return
    clearPasswords()
    ElMessage.success('密码已修改，旧 Session 已失效，请重新登录')
    await router.replace({ name: 'login' })
  } catch (error) {
    if (authStore.identityEpoch === epochAtEntry) {
      ElMessage.error(getApiErrorMessage(error, '密码修改失败，请核对当前密码后重试。'))
    }
  } finally {
    form.current_password = ''
    submitting.value = false
  }
}

onBeforeUnmount(clearPasswords)
watch(() => authStore.identityEpoch, clearPasswords)
</script>

<template>
  <div class="password-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow dark">账户安全</span>
        <h1>修改本人密码</h1>
        <p>成功后当前用户的全部旧 Session 都会失效，需要重新登录。</p>
      </div>
    </header>

    <el-card class="password-card" shadow="never">
      <el-alert
        title="密码只会随本次加密请求发送"
        description="页面不会把密码写入本地存储、URL 或日志；输入中的空白字符会原样保留。"
        type="info"
        show-icon
        :closable="false"
      />
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" class="password-form">
        <el-form-item label="当前密码" prop="current_password">
          <el-input v-model="form.current_password" type="password" autocomplete="current-password" show-password :prefix-icon="Lock" />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="form.new_password" type="password" autocomplete="new-password" show-password :prefix-icon="Lock" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input v-model="form.confirm_password" type="password" autocomplete="new-password" show-password :prefix-icon="Lock" />
        </el-form-item>
        <div class="form-actions">
          <el-button @click="router.back()">取消</el-button>
          <el-button type="primary" :loading="submitting" :disabled="Boolean(authStore.identityWrite)" @click="submit">修改密码</el-button>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.password-page { max-width: 760px; margin: 0 auto; }
.password-card { border: 1px solid #e1e8f2; border-radius: 16px; }
.password-form { max-width: 520px; margin-top: 20px; }
.form-actions { display: flex; justify-content: flex-end; gap: 10px; }
</style>
