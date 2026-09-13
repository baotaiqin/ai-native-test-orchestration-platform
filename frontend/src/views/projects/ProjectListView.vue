<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ArrowRight, EditPen, FolderAdd, FolderRemove, RefreshLeft, Search, Setting, User } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { useRouter } from 'vue-router'

import {
  archiveProject,
  createProject,
  getProjects,
  restoreProject,
  updateProject,
} from '@/api/projects'
import type { ApiErrorBody } from '@/api/http'
import type { Project, ProjectPayload } from '@/types/project'
import { useAuthStore } from '@/stores/auth'
import { useProjectWorkspaceStore } from '@/stores/project-workspace'
import MemberPanel from '@/views/projects/components/MemberPanel.vue'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { formatApiDateTime } from '@/utils/datetime'

const projects = ref<Project[]>([])
const router = useRouter()
const authStore = useAuthStore()
const workspaceStore = useProjectWorkspaceStore()
const loading = ref(false)
const submitting = ref(false)
const includeArchived = ref(false)
const keyword = ref('')
const dialogVisible = ref(false)
const memberDialogVisible = ref(false)
const governanceProject = ref<Project | null>(null)
const editingProjectId = ref<number | null>(null)
const formRef = ref<FormInstance>()
const form = reactive<ProjectPayload>({ name: '', code: '', description: '' })
const canCreateProject = computed(() => authStore.identityVerified && authStore.isAdmin)

function canManageProject(project: Project): boolean {
  return Boolean(
    authStore.identityVerified
    && (authStore.isAdmin || project.current_user_role === 'PROJECT_OWNER'),
  )
}

const rules: FormRules<ProjectPayload> = {
  name: [
    { required: true, message: '请输入项目名称', trigger: 'blur' },
    { min: 2, max: 128, message: '项目名称长度为 2～128 个字符', trigger: 'blur' },
  ],
  code: [
    { required: true, message: '请输入项目编码', trigger: 'blur' },
    {
      pattern: /^[A-Za-z][A-Za-z0-9_-]*$/,
      message: '以字母开头，仅允许字母、数字、下划线和短横线',
      trigger: 'blur',
    },
  ],
}

const filteredProjects = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return projects.value
  return projects.value.filter(
    (project) =>
      project.name.toLowerCase().includes(query) || project.code.toLowerCase().includes(query),
  )
})
const {
  items: pagedProjects,
  total: projectTotal,
  page: projectPage,
  pageSize: projectPageSize,
  changePage: changeProjectPage,
  changePageSize: changeProjectPageSize,
} = useClientPagination(filteredProjects)

const activeCount = computed(
  () => projects.value.filter((project) => project.status === 'ACTIVE').length,
)
const archivedCount = computed(
  () => projects.value.filter((project) => project.status === 'ARCHIVED').length,
)

function apiMessage(error: unknown, fallback: string): string {
  const body = (error as { response?: { data?: ApiErrorBody } }).response?.data
  return body?.message ?? fallback
}

async function loadProjects(): Promise<void> {
  loading.value = true
  try {
    const response = await getProjects(includeArchived.value)
    projects.value = response.items
  } catch (error) {
    ElMessage.error(apiMessage(error, '项目列表加载失败'))
  } finally {
    loading.value = false
  }
}

function resetForm(): void {
  editingProjectId.value = null
  form.name = ''
  form.code = ''
  form.description = ''
  formRef.value?.clearValidate()
}

function openCreateDialog(): void {
  resetForm()
  dialogVisible.value = true
}

function openEditDialog(project: Project): void {
  editingProjectId.value = project.id
  form.name = project.name
  form.code = project.code
  form.description = project.description ?? ''
  dialogVisible.value = true
}

async function submitProject(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const payload = {
      name: form.name.trim(),
      code: form.code.trim(),
      description: form.description?.trim() || null,
    }
    if (editingProjectId.value) {
      await updateProject(editingProjectId.value, payload)
      ElMessage.success('项目已更新')
    } else {
      await createProject(payload)
      ElMessage.success('项目已创建')
    }
    dialogVisible.value = false
    await loadProjects()
  } catch (error) {
    ElMessage.error(apiMessage(error, editingProjectId.value ? '项目更新失败' : '项目创建失败'))
  } finally {
    submitting.value = false
  }
}

async function changeArchiveStatus(project: Project): Promise<void> {
  const archiving = project.status === 'ACTIVE'
  const action = archiving ? '归档' : '恢复'
  try {
    await ElMessageBox.confirm(
      archiving ? '归档后项目将从默认列表隐藏，且不能继续执行任务。' : '恢复后项目可继续编辑和使用。',
      `${action}项目「${project.name}」`,
      { confirmButtonText: action, cancelButtonText: '取消', type: archiving ? 'warning' : 'info' },
    )
    if (archiving) await archiveProject(project.id)
    else await restoreProject(project.id)
    ElMessage.success(`项目已${action}`)
    await loadProjects()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiMessage(error, `${action}失败`))
  }
}

function enterProject(project: Project): void {
  workspaceStore.setProject(project)
  void router.push({ name: 'project-overview', params: { projectId: project.id } })
}

function openMembers(project: Project): void {
  governanceProject.value = project
  memberDialogVisible.value = true
}

onMounted(loadProjects)
</script>

<template>
  <div class="projects-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow dark">开始工作</span>
        <h1>项目门户</h1>
        <p>在这里维护项目资料、成员和生命周期；进入项目后处理测试业务与运行配置。</p>
      </div>
      <el-button v-if="canCreateProject" type="primary" :icon="FolderAdd" @click="openCreateDialog">创建项目</el-button>
    </header>

    <section class="project-metrics">
      <article><span>当前项目</span><strong>{{ projects.length }}</strong><small>当前筛选范围</small></article>
      <article><span>活跃项目</span><strong>{{ activeCount }}</strong><small>可继续开发与执行</small></article>
      <article><span>已归档</span><strong>{{ archivedCount }}</strong><small>保留历史数据</small></article>
    </section>

    <section class="project-table-card">
      <header class="project-toolbar">
        <el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索项目名称或编码" />
        <div class="toolbar-actions">
          <el-checkbox v-model="includeArchived" @change="loadProjects">显示归档项目</el-checkbox>
          <el-button :icon="RefreshLeft" :loading="loading" @click="loadProjects">刷新</el-button>
        </div>
      </header>

      <el-table v-loading="loading" :data="pagedProjects" empty-text="暂无项目，点击右上角创建第一个项目" @row-dblclick="enterProject">
        <el-table-column label="项目" min-width="260">
          <template #default="{ row }: { row: Project }">
            <div class="project-name-cell">
              <div class="project-avatar">{{ row.name.slice(0, 1).toUpperCase() }}</div>
              <div><strong>{{ row.name }}</strong><span>{{ row.description || '暂无项目描述' }}</span></div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="code" label="项目编码" min-width="150" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }: { row: Project }">
            <el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'" effect="light">
              {{ row.status === 'ACTIVE' ? '活跃' : '已归档' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="170">
          <template #default="{ row }: { row: Project }">
            {{ formatApiDateTime(row.updated_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="450" fixed="right">
          <template #default="{ row }: { row: Project }">
            <el-button :type="row.status === 'ACTIVE' ? 'primary' : 'info'" :plain="row.status !== 'ACTIVE'" :icon="ArrowRight" @click="enterProject(row)">{{ row.status === 'ACTIVE' ? '进入项目' : '查看历史' }}</el-button>
            <el-button v-if="canManageProject(row)" link type="primary" :icon="User" @click="openMembers(row)">成员</el-button>
            <el-button v-if="row.status === 'ACTIVE' && canManageProject(row)" link type="primary" :icon="EditPen" @click="openEditDialog(row)">编辑</el-button>
            <el-button v-if="row.status === 'ACTIVE' && canManageProject(row)" link type="primary" :icon="Setting" @click="router.push({ name: 'project-settings', params: { projectId: row.id } })">运行配置</el-button>
            <el-button v-if="canManageProject(row)" link :type="row.status === 'ACTIVE' ? 'warning' : 'success'" :icon="row.status === 'ACTIVE' ? FolderRemove : RefreshLeft" @click="changeArchiveStatus(row)">
              {{ row.status === 'ACTIVE' ? '归档' : '恢复' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="projectTotal" class="records-pagination" background layout="total, sizes, prev, pager, next" :current-page="projectPage" :page-size="projectPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="projectTotal" @current-change="changeProjectPage" @size-change="changeProjectPageSize" />
    </section>

    <el-dialog v-model="dialogVisible" :title="editingProjectId ? '编辑项目' : '创建项目'" width="560px" destroy-on-close @closed="resetForm">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <div class="project-form-grid">
          <el-form-item label="项目名称" prop="name">
            <el-input v-model="form.name" maxlength="128" placeholder="例如：订单自动化平台" />
          </el-form-item>
          <el-form-item label="项目编码" prop="code">
            <el-input v-model="form.code" maxlength="32" placeholder="例如：ORDER_API" />
          </el-form-item>
        </div>
        <el-form-item label="项目描述" prop="description">
          <el-input v-model="form.description" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="说明项目目标和测试范围" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitProject">
          {{ editingProjectId ? '保存修改' : '创建项目' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="memberDialogVisible" :title="`${governanceProject?.name ?? '项目'} · 成员与权限`" width="920px" destroy-on-close @closed="governanceProject = null">
      <MemberPanel
        v-if="governanceProject"
        :project-id="governanceProject.id"
        :owner-id="governanceProject.owner_id"
      />
    </el-dialog>
  </div>
</template>
