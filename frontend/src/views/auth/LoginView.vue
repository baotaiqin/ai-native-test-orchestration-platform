<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Connection, Lock, User } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import axios from 'axios'

import type { ApiErrorBody } from '@/api/http'
import { getPublicDemoCredentials } from '@/config/runtime'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const publicDemoCredentials = getPublicDemoCredentials()
const form = reactive({
  username: publicDemoCredentials?.username ?? '',
  password: publicDemoCredentials?.password ?? '',
})
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit(): Promise<void> {
  if (submitting.value) return
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const signedIn = await authStore.signIn({ username: form.username, password: form.password })
    if (!signedIn) return
    ElMessage.success('登录成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.push(redirect)
  } catch (error) {
    const message = axios.isAxiosError<ApiErrorBody>(error)
      ? error.response?.data?.message ?? '后端服务不可用，请检查启动状态'
      : '登录失败'
    ElMessage.error(message)
  } finally {
    form.password = ''
    submitting.value = false
  }
}

onBeforeUnmount(() => {
  form.password = ''
})
</script>

<template>
  <main class="login-page">
    <section class="login-intro">
      <div class="intro-badge"><el-icon><Connection /></el-icon> AI 原生智能测试</div>
      <h1>从需求理解到<br /><span>执行证据</span>的测试闭环</h1>
      <p>将需求、测试资产、Runner、AI 分析与人工反馈连接成一套可追溯的智能测试编排平台。</p>
      <div class="flow-line">
        <span>需求</span><i></i><span>用例</span><i></i><span>运行</span><i></i><span>证据</span>
      </div>
    </section>

    <section class="login-panel">
      <div class="login-card">
        <header>
          <div class="login-logo">AI</div>
          <div>
            <h2>欢迎体验</h2>
            <p>登录线上演示环境，进入测试工作台</p>
          </div>
        </header>
        <el-alert
          v-if="publicDemoCredentials"
          class="public-demo-notice"
          title="公开 Demo 账号已自动填入，直接登录即可体验"
          description="此账号信息会对公网访客公开；演示环境请勿保存正式资产或复用其他系统密码。"
          type="warning"
          :closable="false"
          show-icon
        />
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="submit">
          <el-form-item label="用户名" prop="username">
            <el-input v-model="form.username" size="large" :prefix-icon="User" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              type="password"
              size="large"
              :prefix-icon="Lock"
              autocomplete="current-password"
              show-password
            />
          </el-form-item>
          <el-button type="primary" size="large" :loading="submitting" class="login-button" @click="submit">
            {{ publicDemoCredentials ? '进入公开 Demo' : '登录平台' }}
          </el-button>
        </el-form>
      </div>
    </section>
  </main>
</template>
