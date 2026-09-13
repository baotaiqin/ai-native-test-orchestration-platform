<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getApiErrorMessage } from '@/api/http'
import { getAssetRequirementLinks } from '@/api/requirement-links'
import type { RequirementAssetType, RequirementLink, RequirementLinkPage } from '@/types/requirement-link'
import {
  identityIsCurrent,
  isIdentityStorageEvent,
  readRequestIdentity,
} from '@/utils/request-context'

const props = defineProps<{
  assetType: RequirementAssetType
  assetId: number
  projectId: number
  focusedVersionId?: number | null
}>()

const router = useRouter()
const route = useRoute()
const page = ref<RequirementLinkPage | null>(null)
const currentPage = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const error = ref<string | null>(null)
let sequence = 0
let alive = true

async function load(pageNumber = currentPage.value): Promise<void> {
  const identity = readRequestIdentity()
  if (!identity) return
  const context = {
    type: props.assetType,
    assetId: props.assetId,
    projectId: props.projectId,
  }
  const requestSequence = ++sequence
  page.value = null
  error.value = null
  loading.value = true
  try {
    const result = await getAssetRequirementLinks(
      context.type,
      context.assetId,
      pageNumber,
      pageSize.value,
      true,
    )
    if (
      !alive || requestSequence !== sequence || !identityIsCurrent(identity)
      || props.assetType !== context.type || props.assetId !== context.assetId
      || props.projectId !== context.projectId
    ) return
    page.value = result
    currentPage.value = result.page
  } catch (reason) {
    if (alive && requestSequence === sequence && identityIsCurrent(identity)) {
      error.value = getApiErrorMessage(reason, '反向需求关联加载失败')
    }
  } finally {
    if (alive && requestSequence === sequence && identityIsCurrent(identity)) loading.value = false
  }
}

function requirementVersionLabel(link: RequirementLink): string {
  return link.requirement_baseline_known && link.requirement_version
    ? `V${link.requirement_version.version_no}`
    : '需求来源版本未知'
}

function goToRequirement(link: RequirementLink): void {
  const query: Record<string, string> = {
    project_id: String(props.projectId),
    requirement_id: String(link.requirement_id),
    tab: 'links',
  }
  if (link.requirement_version_id) {
    query.requirement_version_id = String(link.requirement_version_id)
  }
  void router.push({ name: route.meta.projectScoped ? 'project-requirements' : 'requirements', params: route.meta.projectScoped ? { projectId: props.projectId } : {}, query })
}

function resetAndLoad(): void {
  sequence += 1
  currentPage.value = 1
  page.value = null
  error.value = null
  void load(1)
}

function onIdentityStorage(event: StorageEvent): void {
  if (!isIdentityStorageEvent(event)) return
  sequence += 1
  page.value = null
  loading.value = false
  error.value = '登录身份已变化，旧身份的关联已清除'
}

watch(() => [props.assetType, props.assetId, props.projectId] as const, resetAndLoad)
onMounted(() => {
  window.addEventListener('storage', onIdentityStorage)
  resetAndLoad()
})
onBeforeUnmount(() => {
  alive = false
  sequence += 1
  window.removeEventListener('storage', onIdentityStorage)
})
</script>

<template>
  <section class="reverse-links" data-testid="reverse-requirement-links">
    <header>
      <div><h3>关联需求</h3><p>{{ assetType === 'TEST_CASE' ? '测试用例（包含 Web 类型）' : '独立 Web 用例' }}<span v-if="focusedVersionId"> · 当前查看指定版本</span></p></div>
      <el-button link type="primary" :loading="loading" @click="load(1)">刷新</el-button>
    </header>
    <el-alert title="这里只读取精确类型的关联历史；不会把同号的另一类资产混入。" type="info" :closable="false" show-icon />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-table v-loading="loading" :data="page?.items ?? []" size="small" table-layout="fixed">
      <el-table-column label="需求" min-width="230"><template #default="{ row }: { row: RequirementLink }"><strong>{{ row.requirement_title }}</strong><span>{{ row.requirement_code }}</span><el-tag v-if="row.requirement_status !== 'ACTIVE'" type="info" size="small">需求已归档</el-tag></template></el-table-column>
      <el-table-column label="记录版本" min-width="180"><template #default="{ row }: { row: RequirementLink }"><span>{{ requirementVersionLabel(row) }}</span><small>{{ row.binding_note }}</small></template></el-table-column>
      <el-table-column label="关联修订" min-width="150"><template #default="{ row }: { row: RequirementLink }"><el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ row.status === 'ACTIVE' ? '当前关联' : '已移除' }}</el-tag><span>{{ row.supersedes_link_id ? '已更新关联' : '首次关联' }}</span></template></el-table-column>
      <el-table-column label="操作" width="110"><template #default="{ row }: { row: RequirementLink }"><el-button link type="primary" @click="goToRequirement(row)">打开需求版本</el-button></template></el-table-column>
    </el-table>
    <el-empty v-if="!loading && !error && page?.total === 0" description="此资产暂无需求关联历史" :image-size="60" />
    <el-pagination v-if="page?.total" v-model:current-page="currentPage" :page-size="pageSize" :page-sizes="[10, 20, 50, 100]" :total="page?.total ?? 0" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="(value: number) => { pageSize = value; currentPage = 1; load(1) }" />
  </section>
</template>

<style scoped>
.reverse-links{display:grid;gap:12px;margin-top:22px;padding-top:18px;border-top:1px solid #e4e7ed}.reverse-links header{display:flex;align-items:center;justify-content:space-between;gap:12px}.reverse-links h3,.reverse-links p{margin:0}.reverse-links p,.reverse-links small{color:#6f7b91}.reverse-links :deep(.cell){display:grid;gap:4px;white-space:normal}
</style>
