<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Cpu, Document, Fold, FolderOpened, House, Key, MagicStick, User } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { getApiErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { useProjectWorkspaceStore } from '@/stores/project-workspace'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const workspaceStore = useProjectWorkspaceStore()
const isAdmin = computed(() => authStore.isAdmin)
const logoutLoading = ref(false)

async function logout(): Promise<void> {
  if (logoutLoading.value) return
  const epochAtStart = authStore.identityEpoch
  logoutLoading.value = true
  try {
    const result = await authStore.logoutFromServer()
    if (!result.local_cleared) return
    workspaceStore.clear()
    if (result.revoked) ElMessage.success('服务器 Session 已撤销，已安全退出')
    await router.replace({ name: 'login' })
  } catch (error) {
    if (authStore.identityEpoch !== epochAtStart) return
    const message = getApiErrorMessage(error, '服务器 Session 撤销请求失败。')
    try {
      await ElMessageBox.confirm(
        `${message} 是否仅清除本机登录状态？服务器 Session 的撤销状态将保持未确认。`,
        '服务器退出未确认',
        { confirmButtonText: '仅本地退出', cancelButtonText: '保留登录', type: 'warning' },
      )
    } catch {
      return
    }
    if (authStore.identityEpoch !== epochAtStart || !authStore.signOut(epochAtStart)) return
    workspaceStore.clear()
    ElMessage.warning('已仅清除本机登录状态；服务器 Session 撤销未确认')
    await router.replace({ name: 'login' })
  } finally {
    logoutLoading.value = false
  }
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div>
          <strong>智能测试平台</strong>
          <span>智能测试编排</span>
        </div>
      </div>

      <nav class="navigation" aria-label="平台菜单">
        <span class="nav-section-label">平台入口</span>
        <RouterLink :class="['nav-item', { active: route.name === 'projects' }]" to="/projects"><el-icon><FolderOpened /></el-icon><span>项目门户</span></RouterLink>
        <RouterLink v-if="isAdmin" :class="['nav-item', 'demo-nav', { active: route.name === 'demo' }]" to="/demo"><el-icon><MagicStick /></el-icon><span>AI 主演示</span><small>主线</small></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'dashboard' }]" to="/"><el-icon><House /></el-icon><span>平台概览</span></RouterLink>
        <span class="nav-section-label secondary">公共配置</span>
        <RouterLink :class="['nav-item', { active: route.name === 'model-center' }]" to="/model-center"><el-icon><Key /></el-icon><span>模型中心</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'prompt-center' }]" to="/prompt-center"><el-icon><Document /></el-icon><span>Prompt 中心</span></RouterLink>
        <RouterLink v-if="isAdmin" :class="['nav-item', { active: route.name === 'runners' }]" to="/runners"><el-icon><Cpu /></el-icon><span>Runner 中心</span></RouterLink>
        <RouterLink v-if="isAdmin" :class="['nav-item', { active: route.name === 'users' }]" to="/users"><el-icon><User /></el-icon><span>用户管理</span></RouterLink>
      </nav>
    </aside>

    <main class="main-panel">
      <header class="topbar">
        <div class="topbar-title">
          <el-icon><Fold /></el-icon>
          <span>AI 原生智能测试编排平台</span>
        </div>
        <el-dropdown trigger="click">
          <button class="user-trigger">
            <el-icon><User /></el-icon>
            <span>{{ authStore.user?.display_name ?? '开发管理员' }}</span>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>{{ authStore.user?.username }}</el-dropdown-item>
              <el-dropdown-item :disabled="Boolean(authStore.identityWrite)" @click="router.push({ name: 'change-password' })">修改密码</el-dropdown-item>
              <el-dropdown-item divided :disabled="logoutLoading || Boolean(authStore.identityWrite)" @click="logout">{{ logoutLoading ? '正在退出…' : '退出登录' }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </header>
      <section class="page-content">
        <RouterView />
      </section>
    </main>
  </div>
</template>

<style scoped>
.navigation {
  max-height: calc(100vh - 122px);
  padding-right: 4px;
  overflow-y: auto;
}
.nav-section-label { padding: 0 13px 4px; color: #647da7; font-size: 10px; font-weight: 800; letter-spacing: .14em; }
.nav-section-label.secondary { margin-top: 10px; }
.demo-nav { color: #85e1d5; border: 1px solid rgba(89, 211, 197, .18); background: rgba(30, 167, 153, .08); }
.demo-nav small { margin-left: auto; padding: 2px 6px; border-radius: 8px; color: #d9fffa; background: rgba(45, 190, 175, .22); font-size: 8px; }
</style>
