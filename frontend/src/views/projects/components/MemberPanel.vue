<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Delete, Plus, User } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  addProjectMember,
  getProjectMembers,
  removeProjectMember,
  updateProjectMember,
} from '@/api/projects'
import type { ProjectMember, ProjectRole } from '@/types/project'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { formatApiDateTime } from '@/utils/datetime'

const props = defineProps<{ projectId: number; ownerId: string }>()
const members = ref<ProjectMember[]>([])
const memberPages = useClientPagination(members)
const loading = ref(false)
const dialogVisible = ref(false)
const roles: ProjectRole[] = ['PROJECT_OWNER', 'TESTER', 'VIEWER']
const roleLabels: Record<ProjectRole, string> = {
  PROJECT_OWNER: '项目负责人',
  TESTER: '测试人员',
  VIEWER: '只读成员',
}
const form = reactive<{ user_id: string; role: ProjectRole }>({ user_id: '', role: 'TESTER' })

async function load(): Promise<void> {
  loading.value = true
  try { members.value = await getProjectMembers(props.projectId) }
  finally { loading.value = false }
}

function openCreate(): void {
  Object.assign(form, { user_id: '', role: 'TESTER' })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!/^[A-Za-z0-9_.@-]+$/.test(form.user_id)) {
    ElMessage.warning('请输入有效的用户标识')
    return
  }
  try {
    await addProjectMember(props.projectId, form.user_id, form.role)
    dialogVisible.value = false
    ElMessage.success('成员已添加')
    await load()
  } catch { ElMessage.error('成员添加失败，用户可能已在项目中') }
}

async function changeRole(member: ProjectMember, role: ProjectRole): Promise<void> {
  try {
    await updateProjectMember(props.projectId, member.user_id, role)
    ElMessage.success('成员角色已更新')
    await load()
  } catch { ElMessage.error('项目创建者必须保留负责人角色') }
}

async function remove(member: ProjectMember): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定移除成员 ${member.user_id}？`, '移除成员', { type: 'warning' })
    await removeProjectMember(props.projectId, member.user_id)
    ElMessage.success('成员已移除')
    await load()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error('成员移除失败') }
}

onMounted(load)
</script>

<template>
  <section class="configuration-panel">
    <header class="panel-heading"><div><h2>项目成员</h2><p>V1 支持项目负责人、测试人员、只读成员三种项目角色。</p></div><el-button type="primary" :icon="Plus" @click="openCreate">添加成员</el-button></header>
    <el-table v-loading="loading" :data="memberPages.items.value" empty-text="暂无项目成员">
      <el-table-column label="用户标识" min-width="240"><template #default="{ row }: { row: ProjectMember }"><div class="secret-name"><el-icon><User /></el-icon><strong>{{ row.user_id }}</strong><el-tag v-if="row.user_id === ownerId" size="small">创建者</el-tag></div></template></el-table-column>
      <el-table-column label="角色" width="220"><template #default="{ row }: { row: ProjectMember }"><el-select :model-value="row.role" :disabled="row.user_id === ownerId" @change="changeRole(row, $event)"><el-option v-for="role in roles" :key="role" :label="roleLabels[role]" :value="role" /></el-select></template></el-table-column>
      <el-table-column label="加入时间" min-width="180"><template #default="{ row }: { row: ProjectMember }">{{ formatApiDateTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="操作" width="110"><template #default="{ row }: { row: ProjectMember }"><el-button link type="danger" :icon="Delete" :disabled="row.user_id === ownerId" @click="remove(row)">移除</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="memberPages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="memberPages.page.value" :page-size="memberPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="memberPages.total.value" @current-change="memberPages.changePage" @size-change="memberPages.changePageSize" />
    <el-dialog v-model="dialogVisible" title="添加项目成员" width="500px" append-to-body>
      <el-form label-position="top"><el-form-item label="用户标识"><el-input v-model="form.user_id" placeholder="例如 tester-01" /></el-form-item><el-form-item label="项目角色"><el-select v-model="form.role"><el-option v-for="role in roles" :key="role" :label="roleLabels[role]" :value="role" /></el-select></el-form-item></el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" @click="submit">添加</el-button></template>
    </el-dialog>
  </section>
</template>
