import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { getProject, getProjects } from '@/api/projects'
import type { Project } from '@/types/project'

const LAST_PROJECT_KEY = 'ai-test:last-project-id'

export const useProjectWorkspaceStore = defineStore('project-workspace', () => {
  const projects = ref<Project[]>([])
  const currentProject = ref<Project | null>(null)
  const loading = ref(false)
  let requestSequence = 0

  const activeProjects = computed(() => projects.value.filter((item) => item.status === 'ACTIVE'))

  function rememberProject(project: Project): void {
    currentProject.value = project
    localStorage.setItem(LAST_PROJECT_KEY, String(project.id))
  }

  async function loadProjects(): Promise<Project[]> {
    const response = await getProjects(true)
    projects.value = response.items
    return response.items
  }

  async function selectProject(projectId: number): Promise<Project> {
    if (currentProject.value?.id === projectId) return currentProject.value
    const sequence = ++requestSequence
    loading.value = true
    try {
      const cached = projects.value.find((item) => item.id === projectId)
      const project = cached ?? await getProject(projectId)
      if (sequence === requestSequence) rememberProject(project)
      return project
    } finally {
      if (sequence === requestSequence) loading.value = false
    }
  }

  function setProject(project: Project): void {
    rememberProject(project)
  }

  function lastProjectId(): number | null {
    const value = Number(localStorage.getItem(LAST_PROJECT_KEY))
    return Number.isInteger(value) && value > 0 ? value : null
  }

  function clear(): void {
    requestSequence += 1
    currentProject.value = null
    projects.value = []
    loading.value = false
  }

  return { projects, activeProjects, currentProject, loading, loadProjects, selectProject, setProject, lastProjectId, clear }
})
