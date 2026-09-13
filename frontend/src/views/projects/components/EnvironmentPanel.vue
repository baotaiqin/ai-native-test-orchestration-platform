<script setup lang="ts">
import { reactive, ref } from 'vue'
import { EditPen, Plus, Select } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import {
  createEnvironment,
  setDefaultEnvironment,
  updateEnvironment,
} from '@/api/environments'
import type { Environment } from '@/types/environment'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const props = defineProps<{ projectId: number; environments: Environment[] }>()
const environmentPages = useClientPagination(() => props.environments)
const emit = defineEmits<{
  changed: [preferredId?: number]
  select: [environmentId: number]
}>()
const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({ name: '', code: '', base_url: '', description: '', is_default: false })

function openCreate(): void {
  editingId.value = null
  Object.assign(form, { name: '', code: '', base_url: '', description: '', is_default: false })
  dialogVisible.value = true
}

function openEdit(environment: Environment): void {
  editingId.value = environment.id
  Object.assign(form, {
    name: environment.name,
    code: environment.code,
    base_url: environment.base_url ?? '',
    description: environment.description ?? '',
    is_default: environment.is_default,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (form.name.trim().length < 2 || !/^[A-Za-z][A-Za-z0-9_-]+$/.test(form.code)) {
    ElMessage.warning('请填写有效的环境名称和编码')
    return
  }
  submitting.value = true
  try {
    const payload = {
      name: form.name.trim(),
      code: form.code.trim(),
      base_url: form.base_url.trim() || null,
      description: form.description.trim() || null,
    }
    const environment = editingId.value
      ? await updateEnvironment(editingId.value, payload)
      : await createEnvironment({ ...payload, project_id: props.projectId, is_default: form.is_default })
    dialogVisible.value = false
    ElMessage.success(editingId.value ? '环境已更新' : '环境已创建')
    emit('changed', environment.id)
    emit('select', environment.id)
  } catch {
    ElMessage.error('环境保存失败，请检查编码和地址')
  } finally {
    submitting.value = false
  }
}

async function makeDefault(environment: Environment): Promise<void> {
  try {
    await setDefaultEnvironment(environment.id)
    ElMessage.success('默认环境已切换')
    emit('changed', environment.id)
    emit('select', environment.id)
  } catch {
    ElMessage.error('默认环境切换失败')
  }
}

async function toggleEnabled(environment: Environment): Promise<void> {
  try {
    await updateEnvironment(environment.id, { enabled: !environment.enabled })
    ElMessage.success(environment.enabled ? '环境已停用' : '环境已启用')
    emit('changed', environment.id)
  } catch {
    ElMessage.error(environment.is_default ? '默认环境不能停用' : '环境状态更新失败')
  }
}
</script>

<template>
  <section class="configuration-panel">
    <header class="panel-heading">
      <div><h2>测试环境</h2><p>管理不同部署环境的基础地址和启用状态。</p></div>
      <el-button type="primary" :icon="Plus" @click="openCreate">创建环境</el-button>
    </header>
    <el-table :data="environmentPages.items.value" empty-text="尚未创建环境">
      <el-table-column label="环境" min-width="220">
        <template #default="{ row }: { row: Environment }">
          <div class="config-primary"><strong>{{ row.name }}</strong><span>{{ row.code }}</span></div>
        </template>
      </el-table-column>
      <el-table-column prop="base_url" label="Base URL" min-width="260">
        <template #default="{ row }: { row: Environment }">{{ row.base_url || '--' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="160">
        <template #default="{ row }: { row: Environment }">
          <el-tag v-if="row.is_default" type="primary">默认</el-tag>
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }: { row: Environment }">
          <el-button link type="primary" :icon="EditPen" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="!row.is_default" link type="success" :icon="Select" @click="makeDefault(row)">设为默认</el-button>
          <el-button link :type="row.enabled ? 'warning' : 'success'" @click="toggleEnabled(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-if="environmentPages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="environmentPages.page.value" :page-size="environmentPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="environmentPages.total.value" @current-change="environmentPages.changePage" @size-change="environmentPages.changePageSize" />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑环境' : '创建环境'" width="580px">
      <el-form label-position="top">
        <div class="config-form-grid">
          <el-form-item label="环境名称"><el-input v-model="form.name" placeholder="开发环境" /></el-form-item>
          <el-form-item label="环境编码"><el-input v-model="form.code" placeholder="DEV" /></el-form-item>
        </div>
        <el-form-item label="Base URL"><el-input v-model="form.base_url" placeholder="http://127.0.0.1:8000" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="3" /></el-form-item>
        <el-checkbox v-if="!editingId" v-model="form.is_default">设为默认环境</el-checkbox>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="submitting" @click="submit">保存</el-button></template>
    </el-dialog>
  </section>
</template>
