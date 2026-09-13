import { createRouter, createWebHistory } from 'vue-router'

import { pinia } from '@/stores'
import { useAuthStore } from '@/stores/auth'
import { useProjectWorkspaceStore } from '@/stores/project-workspace'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/auth/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/AppLayout.vue'),
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/dashboard/DashboardView.vue'),
        },
        {
          path: 'demo',
          name: 'demo',
          component: () => import('@/views/demo/DemoCenterView.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'projects',
          name: 'projects',
          component: () => import('@/views/projects/ProjectListView.vue'),
        },
        {
          path: 'requirements',
          name: 'requirements',
          component: () => import('@/views/requirements/RequirementWorkbenchView.vue'),
        },
        {
          path: 'api-definitions',
          name: 'api-definitions',
          component: () => import('@/views/api-definitions/ApiDefinitionView.vue'),
        },
        {
          path: 'test-cases',
          name: 'test-cases',
          component: () => import('@/views/test-cases/TestCaseView.vue'),
        },
        {
          path: 'datasets',
          name: 'datasets',
          component: () => import('@/views/datasets/DatasetView.vue'),
        },
        {
          path: 'scenarios',
          name: 'scenarios',
          component: () => import('@/views/scenarios/ScenarioView.vue'),
        },
        {
          path: 'web-assets',
          name: 'web-assets',
          component: () => import('@/views/web/WebAssetView.vue'),
        },
        {
          path: 'resource-registry',
          name: 'resource-registry',
          component: () => import('@/views/resource-registry/ResourceRegistryView.vue'),
        },
        {
          path: 'runners',
          name: 'runners',
          component: () => import('@/views/runners/RunnerCenterView.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'users',
          name: 'users',
          component: () => import('@/views/users/UserManagementView.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'change-password',
          name: 'change-password',
          component: () => import('@/views/auth/ChangePasswordView.vue'),
        },
        {
          path: 'runs',
          name: 'runs',
          component: () => import('@/views/runs/RunCenterView.vue'),
        },
        {
          path: 'reports',
          name: 'reports',
          component: () => import('@/views/reports/ReportListView.vue'),
        },
        {
          path: 'reports/:runId',
          name: 'report-detail',
          component: () => import('@/views/reports/ReportDetailView.vue'),
        },
        {
          path: 'evidence',
          name: 'evidence',
          component: () => import('@/views/evidence/EvidenceCenterView.vue'),
        },
        {
          path: 'defect-drafts',
          name: 'defect-drafts',
          component: () => import('@/views/defects/DefectDraftView.vue'),
        },
        {
          path: 'model-center',
          name: 'model-center',
          component: () => import('@/views/model-center/ModelCenterView.vue'),
        },
        {
          path: 'prompt-center',
          name: 'prompt-center',
          component: () => import('@/views/prompt-center/PromptCenterView.vue'),
        },
        {
          path: 'ai-infrastructure',
          name: 'ai-infrastructure',
          component: () => import('@/views/ai-infrastructure/AiInfrastructureView.vue'),
        },
      ],
    },
    {
      path: '/projects/:projectId',
      component: () => import('@/layouts/ProjectWorkspaceLayout.vue'),
      meta: { projectScoped: true },
      children: [
        { path: '', redirect: { name: 'project-overview' } },
        { path: 'overview', name: 'project-overview', component: () => import('@/views/projects/ProjectOverviewView.vue') },
        { path: 'requirements', name: 'project-requirements', component: () => import('@/views/requirements/RequirementWorkbenchView.vue') },
        { path: 'api-definitions', name: 'project-api-definitions', component: () => import('@/views/api-definitions/ApiDefinitionView.vue') },
        { path: 'test-cases', name: 'project-test-cases', component: () => import('@/views/test-cases/TestCaseView.vue') },
        { path: 'datasets', name: 'project-datasets', component: () => import('@/views/datasets/DatasetView.vue') },
        { path: 'scenarios', name: 'project-scenarios', component: () => import('@/views/scenarios/ScenarioView.vue') },
        { path: 'test-plans', name: 'project-test-plans', component: () => import('@/views/test-plans/TestPlanView.vue') },
        { path: 'schedules', name: 'project-schedules', component: () => import('@/views/schedules/ScheduleView.vue') },
        { path: 'ci-cd', name: 'project-ci-cd', component: () => import('@/views/ci-cd/CiCdView.vue') },
        { path: 'performance', name: 'project-performance', component: () => import('@/views/performance/PerformanceView.vue') },
        { path: 'web-assets', name: 'project-web-assets', component: () => import('@/views/web/WebAssetView.vue') },
        { path: 'resource-registry', name: 'project-resource-registry', component: () => import('@/views/resource-registry/ResourceRegistryView.vue') },
        { path: 'runs', name: 'project-runs', component: () => import('@/views/runs/RunCenterView.vue') },
        { path: 'reports', name: 'project-reports', component: () => import('@/views/reports/ReportListView.vue') },
        { path: 'reports/:runId', name: 'project-report-detail', component: () => import('@/views/reports/ReportDetailView.vue') },
        { path: 'evidence', name: 'project-evidence', component: () => import('@/views/evidence/EvidenceCenterView.vue') },
        { path: 'defect-drafts', name: 'project-defect-drafts', component: () => import('@/views/defects/DefectDraftView.vue') },
        { path: 'ai-infrastructure', name: 'project-ai-infrastructure', component: () => import('@/views/ai-infrastructure/AiInfrastructureView.vue') },
        { path: 'prompts', name: 'project-prompts', component: () => import('@/views/prompt-center/ProjectPromptView.vue') },
        { path: 'settings', name: 'project-settings', component: () => import('@/views/projects/ProjectSettingsView.vue') },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore(pinia)
  const workspaceStore = useProjectWorkspaceStore(pinia)
  if (!authStore.isAuthenticated) {
    if (to.meta.public) return true
    workspaceStore.clear()
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  const epochAtStart = authStore.identityEpoch
  try {
    const restored = await authStore.ensureCurrentUser()
    if (!restored) {
      if (authStore.isAuthenticated && authStore.identityEpoch !== epochAtStart) {
        return { path: to.fullPath, replace: true }
      }
      workspaceStore.clear()
      return { name: 'login', query: to.meta.public ? {} : { redirect: to.fullPath } }
    }
  } catch {
    if (authStore.identityEpoch === epochAtStart) {
      if (localStorage.getItem('access_token')) authStore.signOut(epochAtStart)
      else authStore.signOut()
    }
    workspaceStore.clear()
    return { name: 'login', query: to.meta.public ? {} : { redirect: to.fullPath } }
  }
  if (to.name === 'login') {
    return { name: 'dashboard' }
  }
  if (to.meta.adminOnly && !authStore.isAdmin) {
    return { name: 'dashboard' }
  }
  const scopedProjectId = Number(to.params.projectId)
  if (to.meta.projectScoped && Number.isInteger(scopedProjectId) && scopedProjectId > 0
    && to.query.project_id !== String(scopedProjectId)) {
    return { path: to.path, query: { ...to.query, project_id: String(scopedProjectId) }, hash: to.hash, replace: true }
  }
  return true
})

export default router
