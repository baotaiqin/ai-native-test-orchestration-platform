<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { Delete, EditPen, Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { deleteVariable, getVariables, saveVariable } from '@/api/environments'
import type { EnvironmentVariable, VariableType } from '@/types/environment'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const props = defineProps<{ environmentId?: number }>()
const variables = ref<EnvironmentVariable[]>([])
const variablePages = useClientPagination(variables)
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(false)
const form = reactive<{ key: string; value: string; value_type: VariableType; enabled: boolean }>({
  key: '', value: '', value_type: 'STRING', enabled: true,
})
const variableTypes: VariableType[] = ['STRING', 'NUMBER', 'BOOLEAN', 'JSON', 'LIST']

async function load(): Promise<void> {
  if (!props.environmentId) { variables.value = []; return }
  loading.value = true
  try { variables.value = await getVariables(props.environmentId) }
  finally { loading.value = false }
}

function openCreate(): void {
  editing.value = false
  Object.assign(form, { key: '', value: '', value_type: 'STRING', enabled: true })
  dialogVisible.value = true
}

function openEdit(variable: EnvironmentVariable): void {
  editing.value = true
  Object.assign(form, variable)
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!props.environmentId || !/^[A-Za-z_][A-Za-z0-9_.-]*$/.test(form.key)) {
    ElMessage.warning('请输入有效的变量名')
    return
  }
  try {
    await saveVariable(props.environmentId, form.key, {
      value: form.value, value_type: form.value_type, enabled: form.enabled,
    })
    dialogVisible.value = false
    ElMessage.success('变量已保存')
    await load()
  } catch { ElMessage.error('变量保存失败，请检查变量类型和值') }
}

async function remove(variable: EnvironmentVariable): Promise<void> {
  if (!props.environmentId) return
  try {
    await ElMessageBox.confirm(`确定删除变量 ${variable.key}？`, '删除变量', { type: 'warning' })
    await deleteVariable(props.environmentId, variable.key)
    ElMessage.success('变量已删除')
    await load()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error('变量删除失败') }
}

watch(() => props.environmentId, load, { immediate: true })
</script>

<template>
  <section class="configuration-panel">
    <header class="panel-heading"><div><h2>环境变量</h2><p>当前环境运行时可引用的非敏感配置。</p></div><el-button type="primary" :icon="Plus" :disabled="!environmentId" @click="openCreate">添加变量</el-button></header>
    <el-alert v-if="!environmentId" title="请先创建并选择一个环境" type="info" :closable="false" show-icon />
    <el-table v-else v-loading="loading" :data="variablePages.items.value" empty-text="当前环境暂无变量">
      <el-table-column prop="key" label="变量名" min-width="200" />
      <el-table-column label="类型" width="120"><template #default="{ row }: { row: EnvironmentVariable }"><el-tag effect="plain">{{ row.value_type }}</el-tag></template></el-table-column>
      <el-table-column prop="value" label="值" min-width="280" show-overflow-tooltip />
      <el-table-column label="状态" width="100"><template #default="{ row }: { row: EnvironmentVariable }">{{ row.enabled ? '启用' : '停用' }}</template></el-table-column>
      <el-table-column label="操作" width="150"><template #default="{ row }: { row: EnvironmentVariable }"><el-button link type="primary" :icon="EditPen" @click="openEdit(row)">编辑</el-button><el-button link type="danger" :icon="Delete" @click="remove(row)">删除</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="variablePages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="variablePages.page.value" :page-size="variablePages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="variablePages.total.value" @current-change="variablePages.changePage" @size-change="variablePages.changePageSize" />
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑变量' : '添加变量'" width="540px">
      <el-form label-position="top">
        <div class="config-form-grid"><el-form-item label="变量名"><el-input v-model="form.key" :disabled="editing" placeholder="api_version" /></el-form-item><el-form-item label="类型"><el-select v-model="form.value_type"><el-option v-for="type in variableTypes" :key="type" :value="type" /></el-select></el-form-item></div>
        <el-form-item label="变量值"><el-input v-model="form.value" type="textarea" :rows="4" /></el-form-item>
        <el-checkbox v-model="form.enabled">启用变量</el-checkbox>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" @click="submit">保存</el-button></template>
    </el-dialog>
  </section>
</template>
