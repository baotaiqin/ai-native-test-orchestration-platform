<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Connection, Plus, Promotion } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import {
  createDatabaseConnection,
  getDatabaseConnections,
  testDatabaseConnection,
  updateDatabaseConnection,
} from '@/api/database-connections'
import { getSecrets } from '@/api/secrets'
import type { ConnectionTestResult, DatabaseConnection } from '@/types/database-connection'
import type { Secret } from '@/types/secret'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const props = defineProps<{ projectId: number; environmentId?: number }>()
const connections = ref<DatabaseConnection[]>([])
const connectionPages = useClientPagination(connections)
const secrets = ref<Secret[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const testingId = ref<number | null>(null)
const testResults = ref<Record<number, ConnectionTestResult>>({})
const form = reactive({
  name: '', host: '127.0.0.1', port: 3306, database_name: '', username: '',
  password_secret_id: undefined as number | undefined, ssl_enabled: false,
})
const passwordSecrets = computed(() =>
  secrets.value.filter(
    (secret) =>
      secret.enabled &&
      ['PASSWORD', 'DB_PASSWORD'].includes(secret.secret_type) &&
      (!secret.environment_id || secret.environment_id === props.environmentId),
  ),
)

async function load(): Promise<void> {
  if (!props.environmentId) { connections.value = []; return }
  loading.value = true
  try {
    const [connectionItems, secretItems] = await Promise.all([
      getDatabaseConnections(props.projectId, props.environmentId),
      getSecrets(props.projectId),
    ])
    connections.value = connectionItems
    secrets.value = secretItems
  } finally { loading.value = false }
}

function openCreate(): void {
  Object.assign(form, {
    name: '', host: '127.0.0.1', port: 3306, database_name: '', username: '',
    password_secret_id: undefined, ssl_enabled: false,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!props.environmentId || !form.name || !form.host || !form.database_name || !form.username || !form.password_secret_id) {
    ElMessage.warning('请完整填写数据库连接配置')
    return
  }
  try {
    await createDatabaseConnection({
      project_id: props.projectId, environment_id: props.environmentId,
      name: form.name, host: form.host, port: form.port,
      database_name: form.database_name, username: form.username,
      password_secret_id: form.password_secret_id, ssl_enabled: form.ssl_enabled,
    })
    dialogVisible.value = false
    ElMessage.success('数据库连接已保存')
    await load()
  } catch { ElMessage.error('数据库连接保存失败') }
}

async function testConnection(connection: DatabaseConnection): Promise<void> {
  testingId.value = connection.id
  try {
    const result = await testDatabaseConnection(connection.id)
    testResults.value[connection.id] = result
    if (result.status === 'ok') ElMessage.success(`${result.message}，${result.latency_ms} ms`)
    else ElMessage.error(result.message)
  } catch { ElMessage.error('连接测试请求失败') }
  finally { testingId.value = null }
}

async function toggle(connection: DatabaseConnection): Promise<void> {
  try {
    await updateDatabaseConnection(connection.id, { enabled: !connection.enabled })
    await load()
  } catch { ElMessage.error('连接状态更新失败') }
}

watch([() => props.projectId, () => props.environmentId], load, { immediate: true })
</script>

<template>
  <section class="configuration-panel">
    <header class="panel-heading"><div><h2>MySQL 连接</h2><p>密码只能引用已保存的密钥，测试结果不会暴露敏感连接信息。</p></div><el-button type="primary" :icon="Plus" :disabled="!environmentId" @click="openCreate">添加连接</el-button></header>
    <el-alert v-if="!environmentId" title="请先创建并选择一个环境" type="info" :closable="false" show-icon />
    <el-table v-else v-loading="loading" :data="connectionPages.items.value" empty-text="当前环境暂无数据库连接">
      <el-table-column label="连接" min-width="200"><template #default="{ row }: { row: DatabaseConnection }"><div class="secret-name"><el-icon><Connection /></el-icon><strong>{{ row.name }}</strong></div></template></el-table-column>
      <el-table-column label="地址" min-width="220"><template #default="{ row }: { row: DatabaseConnection }">{{ row.host }}:{{ row.port }}/{{ row.database_name }}</template></el-table-column>
      <el-table-column prop="username" label="用户名" width="140" />
      <el-table-column prop="password_masked" label="密码" width="120" />
      <el-table-column label="测试结果" min-width="160"><template #default="{ row }: { row: DatabaseConnection }"><span v-if="testResults[row.id]" :class="testResults[row.id].status === 'ok' ? 'test-ok' : 'test-failed'">{{ testResults[row.id].status === 'ok' ? `${testResults[row.id].latency_ms} ms` : '连接失败' }}</span><span v-else>--</span></template></el-table-column>
      <el-table-column label="操作" width="170"><template #default="{ row }: { row: DatabaseConnection }"><el-button link type="primary" :icon="Promotion" :loading="testingId === row.id" @click="testConnection(row)">测试</el-button><el-button link :type="row.enabled ? 'warning' : 'success'" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="connectionPages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="connectionPages.page.value" :page-size="connectionPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="connectionPages.total.value" @current-change="connectionPages.changePage" @size-change="connectionPages.changePageSize" />
    <el-dialog v-model="dialogVisible" title="添加 MySQL 连接" width="620px">
      <el-form label-position="top">
        <div class="config-form-grid"><el-form-item label="连接名称"><el-input v-model="form.name" placeholder="本地 MySQL" /></el-form-item><el-form-item label="密码密钥"><el-select v-model="form.password_secret_id" placeholder="选择密码类型密钥"><el-option v-for="secret in passwordSecrets" :key="secret.id" :label="secret.name" :value="secret.id" /></el-select></el-form-item></div>
        <div class="database-address-grid"><el-form-item label="主机"><el-input v-model="form.host" /></el-form-item><el-form-item label="端口"><el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" /></el-form-item></div>
        <div class="config-form-grid"><el-form-item label="数据库"><el-input v-model="form.database_name" /></el-form-item><el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item></div>
        <el-checkbox v-model="form.ssl_enabled">启用 SSL</el-checkbox>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" @click="submit">保存连接</el-button></template>
    </el-dialog>
  </section>
</template>
