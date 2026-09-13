<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, Connection, Cpu, Key, SetUp, Tickets } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getEnvironments } from '@/api/environments'
import { getProject } from '@/api/projects'
import { useAuthStore } from '@/stores/auth'
import DatabaseConnectionPanel from '@/views/projects/components/DatabaseConnectionPanel.vue'
import EnvironmentPanel from '@/views/projects/components/EnvironmentPanel.vue'
import ModelBindingPanel from '@/views/projects/components/ModelBindingPanel.vue'
import SecretPanel from '@/views/projects/components/SecretPanel.vue'
import VariablePanel from '@/views/projects/components/VariablePanel.vue'
import type { Environment } from '@/types/environment'
import type { Project } from '@/types/project'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const projectId = computed(() => Number(route.params.projectId))
const project = ref<Project | null>(null)
const environments = ref<Environment[]>([])
const selectedEnvironmentId = ref<number | undefined>()
const activeTab = ref('environments')
const loading = ref(true)
const canConfigure = computed(() => Boolean(
  authStore.identityVerified
  && (authStore.isAdmin || project.value?.current_user_role === 'PROJECT_OWNER'),
))

async function loadEnvironments(preferredId?: number): Promise<void> {
  environments.value = await getEnvironments(projectId.value)
  const availableIds = new Set(environments.value.map((item) => item.id))
  if (preferredId && availableIds.has(preferredId)) selectedEnvironmentId.value = preferredId
  else if (!selectedEnvironmentId.value || !availableIds.has(selectedEnvironmentId.value)) {
    selectedEnvironmentId.value =
      environments.value.find((item) => item.is_default)?.id ?? environments.value[0]?.id
  }
}

async function loadPage(): Promise<void> {
  if (!Number.isInteger(projectId.value) || projectId.value <= 0) {
    await router.replace({ name: 'projects' })
    return
  }
  loading.value = true
  try {
    project.value = await getProject(projectId.value)
    await loadEnvironments()
  } catch {
    ElMessage.error('运行配置加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadPage)
</script>

<template>
  <div v-loading="loading" class="project-settings-page">
    <header class="settings-heading">
      <div class="settings-title-row">
        <el-button circle :icon="ArrowLeft" @click="router.push({ name: 'project-overview', params: { projectId } })" />
        <div>
          <span class="eyebrow dark">运行配置</span>
          <h1>{{ project?.name ?? '运行配置' }}</h1>
          <p>{{ project?.code }} · 环境、变量、密钥、数据库连接与模型绑定</p>
        </div>
      </div>
      <el-select
        v-model="selectedEnvironmentId"
        clearable
        placeholder="选择环境"
        style="width: 220px"
      >
        <el-option
          v-for="environment in environments"
          :key="environment.id"
          :label="`${environment.name}${environment.is_default ? '（默认）' : ''}`"
          :value="environment.id"
        />
      </el-select>
    </header>

    <el-alert
      v-if="project && !canConfigure"
      title="当前角色只能查看项目，运行配置由管理员或项目负责人维护。"
      type="info"
      :closable="false"
      show-icon
    />
    <el-tabs v-else v-model="activeTab" class="settings-tabs">
      <el-tab-pane name="environments">
        <template #label><span class="tab-label"><el-icon><SetUp /></el-icon>环境</span></template>
        <EnvironmentPanel
          :project-id="projectId"
          :environments="environments"
          @changed="loadEnvironments"
          @select="selectedEnvironmentId = $event"
        />
      </el-tab-pane>
      <el-tab-pane name="variables">
        <template #label><span class="tab-label"><el-icon><Tickets /></el-icon>环境变量</span></template>
        <VariablePanel :environment-id="selectedEnvironmentId" />
      </el-tab-pane>
      <el-tab-pane name="secrets">
        <template #label><span class="tab-label"><el-icon><Key /></el-icon>密钥</span></template>
        <SecretPanel :project-id="projectId" :environment-id="selectedEnvironmentId" />
      </el-tab-pane>
      <el-tab-pane name="connections">
        <template #label><span class="tab-label"><el-icon><Connection /></el-icon>MySQL 连接</span></template>
        <DatabaseConnectionPanel
          :project-id="projectId"
          :environment-id="selectedEnvironmentId"
        />
      </el-tab-pane>
      <el-tab-pane name="models">
        <template #label><span class="tab-label"><el-icon><Cpu /></el-icon>模型绑定</span></template>
        <ModelBindingPanel :project-id="projectId" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
