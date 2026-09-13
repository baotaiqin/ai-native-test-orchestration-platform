<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  ArrowLeft,
  Connection,
  DataAnalysis,
  Delete,
  Document,
  FolderOpened,
  House,
  MagicStick,
  Operation,
  Timer,
  Promotion,
  Setting,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getApiErrorMessage } from '@/api/http'
import { useProjectWorkspaceStore } from '@/stores/project-workspace'

const route = useRoute()
const router = useRouter()
const workspaceStore = useProjectWorkspaceStore()
const switcherLoading = ref(false)

const projectId = computed(() => Number(route.params.projectId))
const project = computed(() => workspaceStore.currentProject)

async function loadWorkspace(): Promise<void> {
  if (!Number.isInteger(projectId.value) || projectId.value <= 0) {
    await router.replace({ name: 'projects' })
    return
  }
  try {
    await workspaceStore.selectProject(projectId.value)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '项目不存在或当前账号无权访问'))
    await router.replace({ name: 'projects' })
  }
}

async function loadSwitcher(): Promise<void> {
  if (workspaceStore.projects.length) return
  switcherLoading.value = true
  try {
    await workspaceStore.loadProjects()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '项目列表加载失败'))
  } finally {
    switcherLoading.value = false
  }
}

function switchProject(value: number): void {
  if (value === projectId.value) return
  void router.push({ name: 'project-overview', params: { projectId: value } })
}

function projectRoute(name: string): { name: string; params: { projectId: number }; query: { project_id: string } } {
  return { name, params: { projectId: projectId.value }, query: { project_id: String(projectId.value) } }
}

watch(projectId, () => void loadWorkspace())
onMounted(() => {
  void loadWorkspace()
  void loadSwitcher()
})
</script>

<template>
  <div class="project-workspace-shell">
    <aside class="project-sidebar">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div><strong>智能测试平台</strong><span>项目专属工作区</span></div>
      </div>
      <nav class="navigation" aria-label="项目菜单">
        <span class="nav-section-label">项目工作区</span>
        <RouterLink :class="['nav-item', { active: route.name === 'project-overview' }]" :to="projectRoute('project-overview')"><el-icon><House /></el-icon><span>项目概览</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-requirements' }]" :to="projectRoute('project-requirements')"><el-icon><Document /></el-icon><span>需求管理</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-api-definitions' }]" :to="projectRoute('project-api-definitions')"><el-icon><Connection /></el-icon><span>API 定义</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-test-cases' }]" :to="projectRoute('project-test-cases')"><el-icon><Operation /></el-icon><span>测试用例</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-datasets' }]" :to="projectRoute('project-datasets')"><el-icon><FolderOpened /></el-icon><span>数据集</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-scenarios' }]" :to="projectRoute('project-scenarios')"><el-icon><Connection /></el-icon><span>测试编排</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-web-assets' }]" :to="projectRoute('project-web-assets')"><el-icon><Connection /></el-icon><span>Web 自动化</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-test-plans' }]" :to="projectRoute('project-test-plans')"><el-icon><Operation /></el-icon><span>测试计划</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-schedules' }]" :to="projectRoute('project-schedules')"><el-icon><Timer /></el-icon><span>定时任务</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-ci-cd' }]" :to="projectRoute('project-ci-cd')"><el-icon><Promotion /></el-icon><span>CI/CD 集成</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-performance' }]" :to="projectRoute('project-performance')"><el-icon><DataAnalysis /></el-icon><span>性能测试</span></RouterLink>
        <span class="nav-section-label secondary">执行与结果</span>
        <RouterLink :class="['nav-item', { active: route.name === 'project-runs' }]" :to="projectRoute('project-runs')"><el-icon><DataAnalysis /></el-icon><span>运行中心</span></RouterLink>
        <RouterLink :class="['nav-item', { active: ['project-reports', 'project-report-detail'].includes(String(route.name)) }]" :to="projectRoute('project-reports')"><el-icon><DataAnalysis /></el-icon><span>测试报告</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-evidence' }]" :to="projectRoute('project-evidence')"><el-icon><FolderOpened /></el-icon><span>证据中心</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-defect-drafts' }]" :to="projectRoute('project-defect-drafts')"><el-icon><Document /></el-icon><span>缺陷草稿</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-ai-infrastructure' }]" :to="projectRoute('project-ai-infrastructure')"><el-icon><MagicStick /></el-icon><span>AI 输出与审计</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-prompts' }]" :to="projectRoute('project-prompts')"><el-icon><Document /></el-icon><span>项目提示词</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-resource-registry' }]" :to="projectRoute('project-resource-registry')"><el-icon><Delete /></el-icon><span>资源清理</span></RouterLink>
        <RouterLink :class="['nav-item', { active: route.name === 'project-settings' }]" :to="projectRoute('project-settings')"><el-icon><Setting /></el-icon><span>运行配置</span></RouterLink>
      </nav>
    </aside>

    <section class="project-main-panel">
      <header class="workspace-bar">
        <div class="workspace-identity">
          <el-button text :icon="ArrowLeft" @click="router.push({ name: 'projects' })">项目门户</el-button>
          <span class="workspace-divider" />
          <div class="project-monogram">{{ project?.name.slice(0, 1).toUpperCase() || '项' }}</div>
          <span class="workspace-label">当前项目</span>
          <el-select
            :model-value="projectId"
            class="workspace-project-select"
            :loading="switcherLoading || workspaceStore.loading"
            placeholder="切换项目"
            @update:model-value="switchProject"
          >
            <el-option
              v-for="item in workspaceStore.projects"
              :key="item.id"
              :label="`${item.name}（${item.code}）${item.status === 'ARCHIVED' ? ' · 已归档' : ''}`"
              :value="item.id"
            />
          </el-select>
          <el-tag v-if="project" :type="project.status === 'ACTIVE' ? 'success' : 'info'" effect="light">
            {{ project.status === 'ACTIVE' ? '进行中' : '已归档' }}
          </el-tag>
        </div>
      </header>

      <main class="project-workspace-content">
        <RouterView :key="route.fullPath" />
      </main>
    </section>
  </div>
</template>

<style scoped>
.project-workspace-shell { display: flex; min-width: 0; min-height: 100vh; background: #f4f7fb; }
.project-sidebar { position: fixed; inset: 0 auto 0 0; z-index: 20; width: 250px; padding: 24px 18px; color: #e9f2ff; background: linear-gradient(180deg,#101b37 0%,#142a52 100%); }
.project-sidebar .navigation { max-height: calc(100vh - 122px); padding-right: 4px; overflow-y: auto; }
.project-main-panel { width: calc(100% - 250px); min-width: 0; margin-left: 250px; }
.workspace-bar { display: flex; height: 68px; align-items: center; padding: 0 32px; border-bottom: 1px solid #e3e9f2; background: rgba(255,255,255,.94); box-shadow: 0 5px 18px rgba(35,54,91,.035); backdrop-filter: blur(12px); }
.workspace-identity { display: flex; min-width: 0; align-items: center; gap: 12px; }
.workspace-divider { width: 1px; height: 32px; background: #e3e8f1; }
.project-monogram { display: grid; width: 40px; height: 40px; place-items: center; border-radius: 12px; color: #fff; font-weight: 800; background: linear-gradient(135deg,#416fe5,#17a899); box-shadow: 0 7px 18px rgba(50,100,205,.2); }
.workspace-label { flex: 0 0 auto; color: #8a94a6; font-size: 11px; font-weight: 700; letter-spacing: .08em; }
.workspace-project-select { width: 260px; }
:deep(.workspace-project-select .el-select__wrapper) { box-shadow: none; font-weight: 700; }
.project-workspace-content { padding: 28px 32px 40px; }
.nav-section-label { padding: 0 13px 4px; color: #647da7; font-size: 10px; font-weight: 800; letter-spacing: .14em; }
.nav-section-label.secondary { margin-top: 10px; }
.project-workspace-content :deep(.requirement-heading .requirement-actions > .el-select:first-child),
.project-workspace-content :deep(.api-heading .requirement-actions > .el-select:first-child),
.project-workspace-content :deep(.case-heading .case-actions > .el-select:first-child),
.project-workspace-content :deep(.dataset-heading .dataset-actions > .el-select:first-child),
.project-workspace-content :deep(.scenario-heading .heading-actions > .el-select:first-child),
.project-workspace-content :deep(.web-assets-heading .heading-actions > .el-select),
.project-workspace-content :deep(.registry-heading .heading-actions > .el-select:first-child),
.project-workspace-content :deep(.projects-page > .page-heading .requirement-actions > .el-select:first-child) { display: none; }
.project-workspace-content :deep(.project-selector-card) { display: none; }
.project-workspace-content :deep(.reports-page .filter-grid > label:first-child),
.project-workspace-content :deep(.evidence-center-page .filter-row > label:first-child),
.project-workspace-content :deep(.defect-page .filter-grid > label:first-child) { display: none; }
</style>
