<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Plus, Refresh, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'

import { getProjects } from '@/api/projects'
import { getDatabaseConnections } from '@/api/database-connections'
import {
  archiveDataset, createDataset, createDatasetVersion, getDatasetVersions, getDatasets,
  previewCsv, previewDatasetIterations, previewExcel, previewExcelSheets, previewFaker,
  previewMysql,
} from '@/api/datasets'
import type { Project } from '@/types/project'
import type {
  Dataset, DatasetIterationPreview, DatasetPreview, DatasetSourceType,
  DatasetVersion,
} from '@/types/dataset'
import type { DatabaseConnection } from '@/types/database-connection'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { formatApiDateTime } from '@/utils/datetime'

const projects = ref<Project[]>([])
const route = useRoute()
const projectId = ref<number>()
const datasets = ref<Dataset[]>([])
const connections = ref<DatabaseConnection[]>([])
const selected = ref<Dataset | null>(null)
const versions = ref<DatasetVersion[]>([])
const preview = ref<DatasetPreview | null>(null)
const sourceType = ref<DatasetSourceType>('CSV')
const datasetName = ref('')
const delimiter = ref(',')
const selectedFile = ref<File | null>(null)
const sheetNames = ref<string[]>([])
const sheetName = ref('')
const headerRow = ref(1)
const fakerFields = ref('[{"name":"email","generator":"email"},{"name":"user_id","generator":"uuid"}]')
const fakerRowCount = ref(10)
const fakerSeed = ref<number | null>(42)
const mysqlConnectionId = ref<number>()
const mysqlSql = ref('SELECT 1 AS sample')
const mysqlParams = ref('{}')
const mysqlMaxRows = ref(100)
const previewLoading = ref(false)
const saving = ref(false)
const loading = ref(false)
const editorVisible = ref(false)
const detailVisible = ref(false)
const iterationVisible = ref(false)
const iterationPreview = ref<DatasetIterationPreview | null>(null)
const iterationPrefix = ref('data')
const iterationOffset = ref(0)
const versionTargetId = ref<number | null>(null)
const {
  items: pagedDatasets, total: datasetTotal, page: datasetPage,
  pageSize: datasetPageSize, changePage: changeDatasetPage,
  changePageSize: changeDatasetPageSize,
} = useClientPagination(datasets)
const datasetVersionPages = useClientPagination(versions)

const currentProject = computed(() => projects.value.find((item) => item.id === projectId.value))
const previewColumns = computed(() => preview.value?.columns.map((column) => column.name) ?? [])
const previewRows = computed(() => preview.value?.rows ?? [])

function errorMessage(error: unknown, fallback: string): string {
  const response = (error as { response?: { data?: { message?: string } } }).response
  return response?.data?.message ?? (error instanceof Error ? error.message : fallback)
}

function resetEditor(): void {
  datasetName.value = ''
  sourceType.value = 'CSV'
  delimiter.value = ','
  selectedFile.value = null
  sheetNames.value = []
  sheetName.value = ''
  headerRow.value = 1
  fakerFields.value = '[{"name":"email","generator":"email"},{"name":"user_id","generator":"uuid"}]'
  fakerRowCount.value = 10
  fakerSeed.value = 42
  mysqlConnectionId.value = connections.value[0]?.id
  mysqlSql.value = 'SELECT 1 AS sample'
  mysqlParams.value = '{}'
  mysqlMaxRows.value = 100
  preview.value = null
  versionTargetId.value = null
}

async function loadDatasets(): Promise<void> {
  if (!projectId.value) { datasets.value = []; return }
  loading.value = true
  try {
    datasets.value = (await getDatasets(projectId.value)).items
    connections.value = await getDatabaseConnections(projectId.value)
    if (!mysqlConnectionId.value) mysqlConnectionId.value = connections.value[0]?.id
  } catch (error) {
    ElMessage.error(errorMessage(error, '数据集加载失败'))
  } finally { loading.value = false }
}

function onFileChange(event: Event): void {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  sheetNames.value = []
  sheetName.value = ''
}

async function chooseExcelSheet(): Promise<void> {
  if (!selectedFile.value) return
  try {
    sheetNames.value = await previewExcelSheets(selectedFile.value)
    sheetName.value = sheetNames.value[0] ?? ''
  } catch (error) {
    ElMessage.error(errorMessage(error, '工作表读取失败'))
  }
}

function parseJson(raw: string, label: string): unknown {
  try { return JSON.parse(raw) as unknown } catch { throw new Error(`${label} 必须是有效 JSON`) }
}

async function makePreview(): Promise<void> {
  previewLoading.value = true
  try {
    if (sourceType.value === 'CSV') {
      if (!selectedFile.value) throw new Error('请选择 CSV 文件')
      preview.value = await previewCsv(selectedFile.value, delimiter.value)
    } else if (sourceType.value === 'EXCEL') {
      if (!selectedFile.value) throw new Error('请选择 XLSX 文件')
      if (!sheetNames.value.length) await chooseExcelSheet()
      preview.value = await previewExcel(selectedFile.value, sheetName.value, headerRow.value)
    } else if (sourceType.value === 'FAKER') {
      const fields = parseJson(fakerFields.value, '模拟数据字段配置')
      if (!Array.isArray(fields)) throw new Error('模拟数据字段配置必须是数组')
      preview.value = await previewFaker({ fields: fields as { name: string; generator: string }[], row_count: fakerRowCount.value, seed: fakerSeed.value })
    } else {
      if (!projectId.value || !mysqlConnectionId.value) throw new Error('请选择项目内已启用的 MySQL 连接')
      const params = parseJson(mysqlParams.value, 'MySQL Params')
      if (!(Array.isArray(params) || (typeof params === 'object' && params !== null))) throw new Error('Params 必须是 JSON 对象或数组')
      preview.value = await previewMysql(projectId.value, { connection_id: mysqlConnectionId.value, sql: mysqlSql.value, params: params as Record<string, unknown> | unknown[], max_rows: mysqlMaxRows.value })
    }
    ElMessage.success(`预览成功，共 ${preview.value?.row_count ?? 0} 行`)
  } catch (error) {
    preview.value = null
    ElMessage.error(errorMessage(error, '预览失败'))
  } finally { previewLoading.value = false }
}

function openCreate(): void {
  resetEditor()
  editorVisible.value = true
}

function openVersion(): void {
  if (!selected.value?.current_version) return
  resetEditor()
  versionTargetId.value = selected.value.id
  datasetName.value = selected.value.name
  sourceType.value = selected.value.current_version.source_type
  preview.value = {
    source_type: selected.value.current_version.source_type,
    columns: selected.value.current_version.columns,
    row_count: selected.value.current_version.row_count,
    rows: selected.value.current_version.snapshot.slice(0, 100),
    snapshot: selected.value.current_version.snapshot,
    source_metadata: selected.value.current_version.source_metadata,
    config: selected.value.current_version.config,
  }
  editorVisible.value = true
}

async function saveDataset(): Promise<void> {
  if (!projectId.value || !datasetName.value.trim()) { ElMessage.warning('请填写数据集名称'); return }
  if (!preview.value) { ElMessage.warning('请先预览并确认数据'); return }
  saving.value = true
  try {
    const payload = {
      source_type: preview.value.source_type,
      config: preview.value.config,
      source_metadata: preview.value.source_metadata,
      columns: preview.value.columns,
      snapshot: preview.value.snapshot,
    }
    if (versionTargetId.value) {
      await createDatasetVersion(versionTargetId.value, payload)
      ElMessage.success('数据集新版本已保存，旧版本保持不变')
    } else {
      await createDataset({ project_id: projectId.value, name: datasetName.value.trim(), ...payload })
      ElMessage.success('数据集已保存')
    }
    editorVisible.value = false
    await loadDatasets()
  } catch (error) {
    ElMessage.error(errorMessage(error, '数据集保存失败'))
  } finally { saving.value = false }
}

async function showDetail(row: Dataset): Promise<void> {
  selected.value = row
  try {
    versions.value = await getDatasetVersions(row.id)
    selected.value = { ...row, current_version: versions.value.find((item) => item.id === row.current_version_id) }
    detailVisible.value = true
  } catch (error) { ElMessage.error(errorMessage(error, '版本读取失败')) }
}

async function archiveSelected(): Promise<void> {
  if (!selected.value) return
  await ElMessageBox.confirm('归档后数据集不能再创建版本，确认继续？', '归档数据集', { type: 'warning' })
  await archiveDataset(selected.value.id)
  ElMessage.success('数据集已归档')
  detailVisible.value = false
  await loadDatasets()
}

async function openIterations(): Promise<void> {
  if (!selected.value) return
  iterationVisible.value = true
  iterationPreview.value = null
  try {
    iterationPreview.value = await previewDatasetIterations({ dataset_id: selected.value.id, prefix: iterationPrefix.value, limit: 20, offset: iterationOffset.value })
  } catch (error) { ElMessage.error(errorMessage(error, '迭代数据预览失败')) }
}

onMounted(async () => {
  try {
    projects.value = (await getProjects()).items
    const requestedProjectId = Number(route.query.project_id)
    projectId.value = projects.value.some((item) => item.id === requestedProjectId)
      ? requestedProjectId
      : projects.value[0]?.id
  } catch (error) { ElMessage.error(errorMessage(error, '项目加载失败')) }
})
watch(projectId, () => { selected.value = null; void loadDatasets() })
</script>

<template>
  <div class="dataset-page">
    <header class="page-heading dataset-heading">
      <div><span class="eyebrow dark">数据驱动资产</span><h1>数据集</h1><p>保存可复用、可预览、可追溯的数据驱动快照，支持受控 MySQL 只读查询。</p></div>
      <div class="dataset-actions"><el-select v-model="projectId" placeholder="选择项目" style="width:220px"><el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" /></el-select><el-button :icon="Refresh" @click="loadDatasets">刷新</el-button><el-button type="primary" :icon="Plus" :disabled="!projectId" @click="openCreate">新建数据集</el-button></div>
    </header>

    <el-card v-loading="loading" shadow="never" class="dataset-card">
      <el-table :data="pagedDatasets" stripe @row-click="showDetail"><el-table-column prop="name" label="名称" min-width="240" /><el-table-column label="版本" width="120"><template #default="{ row }">{{ row.current_version_id ? '已有版本' : '暂无版本' }}</template></el-table-column><el-table-column prop="status" label="状态" width="110"><template #default="{ row }"><el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ row.status === 'ACTIVE' ? '有效' : '已归档' }}</el-tag></template></el-table-column><el-table-column label="更新时间" width="190"><template #default="{ row }">{{ formatApiDateTime(row.updated_at) }}</template></el-table-column></el-table>
      <el-pagination v-if="datasetTotal" class="records-pagination" background layout="total, sizes, prev, pager, next" :current-page="datasetPage" :page-size="datasetPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="datasetTotal" @current-change="changeDatasetPage" @size-change="changeDatasetPageSize" />
      <el-empty v-if="!loading && !datasets.length" description="暂无数据集，请从 CSV、XLSX、模拟数据或项目 MySQL 查询创建" />
    </el-card>

    <el-dialog v-model="editorVisible" :title="versionTargetId ? '创建数据集新版本' : '新建数据集'" width="980px" destroy-on-close>
      <el-form label-position="top"><el-form-item label="数据集名称"><el-input v-model="datasetName" :disabled="Boolean(versionTargetId)" maxlength="255" placeholder="例如：订单接口回归数据" /></el-form-item><el-tabs v-model="sourceType" type="border-card"><el-tab-pane label="CSV" name="CSV"><el-form-item label="文件"><input type="file" accept=".csv,text/csv" @change="onFileChange" /><span class="file-hint">支持 UTF-8 / UTF-8 BOM；最大 10 MB</span></el-form-item><el-form-item label="分隔符"><el-select v-model="delimiter"><el-option label="逗号 ," value="," /><el-option label="分号 ;" value=";" /><el-option label="制表符" value="\t" /></el-select></el-form-item></el-tab-pane><el-tab-pane label="Excel" name="EXCEL"><el-form-item label="文件"><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" @change="onFileChange" /><span class="file-hint">V1 仅支持 .xlsx，不支持旧 .xls</span></el-form-item><div class="dataset-form-grid"><el-form-item label="工作表"><el-select v-model="sheetName" placeholder="先点击读取工作表" no-data-text="未读取"><el-option v-for="sheet in sheetNames" :key="sheet" :label="sheet" :value="sheet" /></el-select><el-button :icon="Upload" @click="chooseExcelSheet">读取工作表</el-button></el-form-item><el-form-item label="表头行（从 1 开始）"><el-input-number v-model="headerRow" :min="1" :max="10000" /></el-form-item></div></el-tab-pane><el-tab-pane label="模拟数据" name="FAKER"><el-form-item label="字段配置 JSON"><el-input v-model="fakerFields" type="textarea" :rows="5" class="code-input" /><small>可用生成器：uuid、email、username、first_name、last_name、integer、word、boolean</small></el-form-item><div class="dataset-form-grid"><el-form-item label="行数"><el-input-number v-model="fakerRowCount" :min="1" :max="10000" /></el-form-item><el-form-item label="随机种子"><el-input-number v-model="fakerSeed" :controls="false" /></el-form-item></div></el-tab-pane><el-tab-pane label="MySQL" name="MYSQL"><el-alert title="只读查询结果在创建数据集版本时固化；正式运行锁定该版本并逐行审计，不在执行中重新查询。" type="info" :closable="false" show-icon /><el-form-item label="项目内已启用连接"><el-select v-model="mysqlConnectionId" placeholder="选择连接"><el-option v-for="connection in connections.filter((item) => item.enabled)" :key="connection.id" :label="`${connection.name} · ${connection.database_name}`" :value="connection.id" /></el-select></el-form-item><el-form-item label="只读 SQL"><el-input v-model="mysqlSql" type="textarea" :rows="4" class="code-input" placeholder="SELECT ... WHERE id = %(id)s" /></el-form-item><div class="dataset-form-grid"><el-form-item label="参数 JSON"><el-input v-model="mysqlParams" type="textarea" :rows="3" class="code-input" /></el-form-item><el-form-item label="最大行数"><el-input-number v-model="mysqlMaxRows" :min="1" :max="10000" /></el-form-item></div></el-tab-pane></el-tabs><div class="dataset-preview-toolbar"><el-button type="primary" :loading="previewLoading" @click="makePreview">预览数据</el-button><span v-if="preview">已解析 {{ preview.row_count }} 行、{{ preview.columns.length }} 列</span></div><div v-if="preview" class="dataset-preview-table"><el-table :data="previewRows" stripe max-height="300"><el-table-column label="#" width="70"><template #default="{ row }">{{ row.row_index }}</template></el-table-column><el-table-column v-for="column in previewColumns" :key="column" :prop="`data.${column}`" :label="column" min-width="150"><template #default="{ row }">{{ row.data[column] }}</template></el-table-column></el-table></div></el-form>
      <template #footer><el-button @click="editorVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!preview" @click="saveDataset">确认保存快照</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="数据集详情与版本历史" size="62%"><template v-if="selected"><div class="drawer-title"><div><h2>{{ selected.name }}</h2><span>{{ selected.status === 'ACTIVE' ? '有效' : '已归档' }} · {{ versions.length ? `当前 V${versions[0]?.version_no}` : '暂无版本' }}</span></div><div><el-button v-if="selected.status === 'ACTIVE'" @click="openVersion">创建新版本</el-button><el-button @click="openIterations">迭代数据预览</el-button><el-button v-if="selected.status === 'ACTIVE'" type="danger" plain @click="archiveSelected">归档</el-button></div></div><el-timeline><el-timeline-item v-for="version in datasetVersionPages.items.value" :key="version.id" :timestamp="formatApiDateTime(version.created_at)" placement="top"><el-card shadow="never"><template #header><strong>V{{ version.version_no }} · {{ version.source_type }} · {{ version.row_count }} 行</strong></template><el-descriptions :column="2" border><el-descriptions-item label="版本">V{{ version.version_no }}</el-descriptions-item><el-descriptions-item label="创建人">{{ version.created_by }}</el-descriptions-item><el-descriptions-item label="列" :span="2">{{ version.columns.map((column) => column.name).join(', ') }}</el-descriptions-item><el-descriptions-item label="配置" :span="2"><pre>{{ JSON.stringify(version.config, null, 2) }}</pre></el-descriptions-item></el-descriptions></el-card></el-timeline-item></el-timeline><el-pagination v-if="datasetVersionPages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="datasetVersionPages.page.value" :page-size="datasetVersionPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="datasetVersionPages.total.value" @current-change="datasetVersionPages.changePage" @size-change="datasetVersionPages.changePageSize" /></template></el-drawer>

    <el-dialog v-model="iterationVisible" title="迭代上下文预览" width="950px"><el-form inline><el-form-item label="上下文前缀"><el-input v-model="iterationPrefix" placeholder="data" /></el-form-item><el-form-item label="起始位置"><el-input-number v-model="iterationOffset" :min="0" /></el-form-item><el-button type="primary" @click="openIterations">刷新预览</el-button></el-form><el-alert title="每行对应一次迭代；行号从 1 开始，数据列不会覆盖保留的上下文键。" type="info" :closable="false" show-icon /><el-table v-if="iterationPreview" :data="iterationPreview.items" stripe max-height="420"><el-table-column prop="row_index" label="行号" width="100" /><el-table-column label="参数快照" min-width="300"><template #default="{ row }"><pre>{{ JSON.stringify(row.parameters, null, 2) }}</pre></template></el-table-column><el-table-column label="合并上下文" min-width="360"><template #default="{ row }"><pre>{{ JSON.stringify(row.context, null, 2) }}</pre></template></el-table-column></el-table><el-empty v-else description="正在读取迭代数据预览" /></el-dialog>
  </div>
</template>

<style scoped>
.dataset-heading,.dataset-actions,.drawer-title{display:flex;align-items:center;gap:12px}.dataset-heading,.drawer-title{justify-content:space-between}.dataset-page{max-width:1580px;margin:0 auto}.dataset-card{min-height:620px}.dataset-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.file-hint{display:block;margin-left:10px;color:#8792a5;font-size:12px}.dataset-preview-toolbar{display:flex;align-items:center;gap:15px;margin:18px 0 10px;color:#69778d;font-size:13px}.dataset-preview-table{overflow:hidden;border:1px solid #e4e9f1;border-radius:10px}.drawer-title{margin-bottom:22px}.drawer-title h2{margin:6px 0}.drawer-title span{color:#8792a5;font-size:12px}.drawer-title>div:last-child{display:flex;gap:8px}.code-input :deep(textarea),pre{font-family:JetBrains Mono,Consolas,monospace}pre{margin:0;padding:10px;overflow:auto;background:#f7f8fa;border-radius:6px;font-size:12px;white-space:pre-wrap}@media(max-width:900px){.dataset-heading{align-items:flex-start;flex-direction:column}.dataset-form-grid{grid-template-columns:1fr}.drawer-title{align-items:flex-start;flex-direction:column}}
</style>
