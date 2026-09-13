<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { MagicStick } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { getPrompts } from '@/api/prompt-center'
import { promptOptionLabel } from '@/utils/prompt-display'
import {
  decideRequirementReview, editRequirementReview, generateRequirementReview,
} from '@/api/requirement-reviews'
import type { PromptDefinition } from '@/types/prompt-center'
import type {
  RequirementReview, RequirementReviewResult,
} from '@/types/requirement-review'
import { showAiGenerationError } from '@/utils/ai-error'

const props = defineProps<{ requirementId: number }>()
const emit = defineEmits<{
  reviewCreated: []
  openReviewRecords: []
  createVersionFromReview: [review: RequirementReview]
}>()
const prompts = ref<PromptDefinition[]>([])
const selected = ref<RequirementReview | null>(null)
const generateVisible = ref(false)
const detailVisible = ref(false)
const generating = ref(false)
const generateForm = reactive({
  promptId: undefined as number | undefined,
  includeParent: true, includeSiblings: false, instructions: '',
})
const editForm = reactive({
  clarity_issues: '', ambiguity: '', missing_rules: '', exception_gaps: '',
  testability: '', acceptance_criteria_suggestions: '', overall_summary: '', note: '',
})
const displayResult = computed(() =>
  selected.value?.human_result ?? selected.value?.structured_result,
)
const statusType = {
  DRAFT: 'warning', ACCEPTED: 'success', REJECTED: 'danger',
} as const
const statusLabel = {
  DRAFT: '草稿', ACCEPTED: '已确认', REJECTED: '已拒绝',
} as const
const generationStatusType = {
  QUEUED: 'info', RUNNING: 'warning', SUCCEEDED: 'success', FAILED: 'danger',
} as const
const generationStatusLabel = {
  QUEUED: '等待生成', RUNNING: '正在生成', SUCCEEDED: '生成成功', FAILED: '生成失败',
} as const

async function openGenerate(): Promise<void> {
  if (!prompts.value.length) {
    prompts.value = (await getPrompts(false)).filter(
      (item) => item.task_type === 'REQUIREMENT_REVIEW',
    )
  }
  generateForm.promptId = prompts.value[0]?.id
  generateForm.instructions = ''
  generateVisible.value = true
}

async function generate(): Promise<void> {
  if (!generateForm.promptId) { ElMessage.warning('请先在 Prompt 中心创建需求评审 Prompt'); return }
  generating.value = true
  try {
    const review = await generateRequirementReview(props.requirementId, {
      prompt_id: generateForm.promptId,
      include_parent: generateForm.includeParent,
      include_siblings: generateForm.includeSiblings,
      additional_instructions: generateForm.instructions || undefined,
    })
    generateVisible.value = false
    ElMessage.success(review.reused ? '已有相同评审任务正在执行' : 'AI 评审任务已创建，可离开页面后查看')
    emit('reviewCreated')
    emit('openReviewRecords')
  } catch (error) { showAiGenerationError(error, 'AI 需求评审生成失败') }
  finally { generating.value = false }
}

function lines(values: string[]): string { return values.join('\n') }
function parseLines(value: string): string[] {
  return value.split('\n').map((item) => item.trim()).filter(Boolean)
}

function openDetail(review: RequirementReview): void {
  selected.value = review
  const result = review.human_result ?? review.structured_result
  if (result) {
    Object.assign(editForm, {
      clarity_issues: lines(result.clarity_issues), ambiguity: lines(result.ambiguity),
      missing_rules: lines(result.missing_rules), exception_gaps: lines(result.exception_gaps),
      testability: lines(result.testability),
      acceptance_criteria_suggestions: lines(result.acceptance_criteria_suggestions),
      overall_summary: result.overall_summary, note: review.decision_note ?? '',
    })
  }
  detailVisible.value = true
}

defineExpose({ openDetail })

function formResult(): RequirementReviewResult {
  return {
    clarity_issues: parseLines(editForm.clarity_issues),
    ambiguity: parseLines(editForm.ambiguity),
    missing_rules: parseLines(editForm.missing_rules),
    exception_gaps: parseLines(editForm.exception_gaps),
    testability: parseLines(editForm.testability),
    acceptance_criteria_suggestions: parseLines(editForm.acceptance_criteria_suggestions),
    overall_summary: editForm.overall_summary,
  }
}

async function saveEdit(): Promise<boolean> {
  if (!selected.value) return false
  try {
    selected.value = await editRequirementReview(
      selected.value.id, formResult(), editForm.note || undefined,
    )
    ElMessage.success('人工调整已保存')
    emit('reviewCreated')
    return true
  } catch { ElMessage.error('评审已决策，不能继续编辑'); return false }
}

async function decide(action: 'ACCEPT' | 'REJECT'): Promise<void> {
  if (!selected.value) return
  try {
    if (action === 'ACCEPT') {
      await ElMessageBox.confirm(
        '确认后会冻结当前评审结论并保留审计，不会自动修改需求，也不会自动生成测试用例。若问题较多，可再选择“基于评审创建新版本”。',
        '确认 AI 评审',
        { type: 'info', confirmButtonText: '确认并冻结', cancelButtonText: '继续检查' },
      )
    }
    if (action === 'ACCEPT' && !(await saveEdit())) return
    selected.value = await decideRequirementReview(
      selected.value.id, action, editForm.note || undefined,
    )
    ElMessage.success(action === 'ACCEPT' ? '评审结论已确认并冻结；需求内容未被修改' : '评审建议已拒绝')
    emit('reviewCreated')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error('评审决策失败')
  }
}

function createVersionFromReview(): void {
  if (!selected.value || selected.value.status !== 'ACCEPTED') return
  detailVisible.value = false
  emit('createVersionFromReview', selected.value)
}

function documentVersion(review: RequirementReview): number | null {
  const value = review.context_snapshot.document_version
  if (!value || typeof value !== 'object' || !("version_no" in value)) return null
  const versionNo = (value as { version_no?: unknown }).version_no
  return typeof versionNo === 'number' ? versionNo : null
}
</script>

<template>
  <div class="review-panel-content">
    <section class="ai-review-feature">
      <div class="ai-review-icon"><el-icon><MagicStick /></el-icon></div>
      <div class="ai-review-copy"><strong>AI 需求评审</strong><span>在生成测试用例前，先检查需求歧义、缺失规则、异常场景和可测试性。</span><small>AI 只生成评审草稿；你可以修改、接受或拒绝，不会自动改写需求。</small></div>
      <div class="ai-review-actions"><el-button @click="emit('openReviewRecords')">本需求评审记录</el-button><el-button type="primary" :icon="MagicStick" @click="openGenerate">开始 AI 评审</el-button></div>
    </section>
    <div class="review-record-hint"><span>这里仅查看当前需求的评审；项目全部记录从页面顶部“AI 记录”进入。</span></div>
    <el-dialog v-model="generateVisible" title="生成 AI 需求评审" width="620px"><el-form label-position="top"><el-form-item label="需求评审 Prompt"><el-select v-model="generateForm.promptId"><el-option v-for="prompt in prompts" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" /></el-select></el-form-item><el-form-item label="上下文"><el-checkbox v-model="generateForm.includeParent">关联父需求</el-checkbox><el-checkbox v-model="generateForm.includeSiblings">关联兄弟需求</el-checkbox></el-form-item><el-form-item label="附加说明"><el-input v-model="generateForm.instructions" type="textarea" :rows="4" /></el-form-item><el-alert title="当前需求的全部子需求会自动纳入评审范围。提交后立即生成后台记录，可以关闭窗口或离开页面。" type="info" :closable="false" /></el-form><template #footer><el-button @click="generateVisible = false">{{ generating ? '关闭，后台继续' : '关闭' }}</el-button><el-button type="primary" :loading="generating" @click="generate">{{ generating ? '正在创建任务…' : '创建后台评审任务' }}</el-button></template></el-dialog>
    <el-drawer v-model="detailVisible" title="AI 需求评审详情" size="60%" append-to-body>
      <template v-if="selected">
        <div class="review-detail-header"><div><el-tag :type="generationStatusType[selected.generation_status]">{{ generationStatusLabel[selected.generation_status] }}</el-tag><el-tag v-if="selected.generation_status === 'SUCCEEDED'" :type="statusType[selected.status]">{{ statusLabel[selected.status] }}</el-tag><span v-if="documentVersion(selected)">完整需求文档 V{{ documentVersion(selected) }}</span></div><div v-if="selected.generation_status === 'SUCCEEDED' && selected.status === 'DRAFT'"><el-button @click="saveEdit">保存编辑</el-button><el-button type="danger" plain @click="decide('REJECT')">拒绝</el-button><el-button type="success" @click="decide('ACCEPT')">确认评审</el-button></div><div v-else-if="selected.status === 'ACCEPTED'" class="revision-entry"><span>AI 自动归类建议并生成修改前后对比</span><el-button class="revision-entry-button" type="primary" size="large" :icon="MagicStick" @click="createVersionFromReview">AI 辅助修订并创建新版</el-button></div></div>
        <el-alert v-if="selected.generation_status === 'QUEUED' || selected.generation_status === 'RUNNING'" title="AI 正在后台生成评审" description="可以关闭详情窗口，稍后从评审记录重新查看。" type="info" :closable="false" show-icon />
        <el-alert v-else-if="selected.generation_status === 'FAILED'" :title="selected.error_message || 'AI 评审生成失败'" description="请检查模型连接、输出结构或 Prompt 后重新发起评审。" type="error" :closable="false" show-icon />
        <template v-if="displayResult">
          <el-collapse><el-collapse-item title="技术追踪信息" name="technical"><span>评审记录 {{ selected.id }} · 需求版本记录 {{ selected.requirement_version_id }} · AI 调用记录 {{ selected.ai_call_id ?? '生成中暂未产生' }}</span></el-collapse-item></el-collapse><el-form label-position="top" class="review-form"><div class="config-form-grid"><el-form-item label="清晰度问题（每行一条）"><el-input v-model="editForm.clarity_issues" type="textarea" :rows="5" :disabled="selected.status !== 'DRAFT'" /></el-form-item><el-form-item label="歧义（每行一条）"><el-input v-model="editForm.ambiguity" type="textarea" :rows="5" :disabled="selected.status !== 'DRAFT'" /></el-form-item><el-form-item label="缺失规则"><el-input v-model="editForm.missing_rules" type="textarea" :rows="5" :disabled="selected.status !== 'DRAFT'" /></el-form-item><el-form-item label="异常场景缺口"><el-input v-model="editForm.exception_gaps" type="textarea" :rows="5" :disabled="selected.status !== 'DRAFT'" /></el-form-item><el-form-item label="可测试性"><el-input v-model="editForm.testability" type="textarea" :rows="5" :disabled="selected.status !== 'DRAFT'" /></el-form-item><el-form-item label="验收标准建议"><el-input v-model="editForm.acceptance_criteria_suggestions" type="textarea" :rows="5" :disabled="selected.status !== 'DRAFT'" /></el-form-item></div><el-form-item label="总体结论"><el-input v-model="editForm.overall_summary" type="textarea" :rows="4" :disabled="selected.status !== 'DRAFT'" /></el-form-item><el-form-item label="人工审核说明"><el-input v-model="editForm.note" :disabled="selected.status !== 'DRAFT'" /></el-form-item></el-form>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.ai-review-feature{display:grid;grid-template-columns:42px minmax(0,1fr);gap:10px;margin:4px 0 16px;padding:14px;border:1px solid #d7d3f7;border-radius:12px;background:linear-gradient(145deg,#faf9ff 0%,#f1efff 100%);box-shadow:0 8px 22px rgba(83,68,174,.08)}
.ai-review-icon{display:grid;width:42px;height:42px;place-items:center;border-radius:12px;background:linear-gradient(145deg,#6857d9,#8c7af0);color:#fff;font-size:21px;box-shadow:0 8px 18px rgba(104,87,217,.22)}
.ai-review-copy{display:flex;min-width:0;flex-direction:column;gap:5px}.ai-review-copy strong{color:#332c6b;font-size:16px;line-height:1.4}.ai-review-copy span{color:#5d587c;font-size:13px;line-height:1.55}.ai-review-copy small{color:#85809e;font-size:12px;line-height:1.45}
.ai-review-actions{display:grid;grid-column:1/-1;grid-template-columns:minmax(0,1fr);gap:8px;padding-top:4px}.ai-review-actions :deep(.el-badge),.ai-review-actions :deep(.el-button){width:100%;min-width:0;margin-left:0}.ai-review-actions :deep(.el-badge){display:flex}
.review-record-hint{display:flex;flex-direction:column;align-items:center;gap:4px;padding:18px 8px;color:#7b8496;font-size:12px;text-align:center}
.revision-entry{display:flex;align-items:flex-end;flex-direction:column;gap:8px}.revision-entry span{color:#4e466f;font-size:13px;font-weight:600}.revision-entry-button{min-height:48px;padding:0 22px!important;border:2px solid #7562d8!important;background:#f1efff!important;color:#241c52!important;font-size:16px!important;font-weight:800!important;letter-spacing:.2px;box-shadow:0 7px 18px rgba(82,70,204,.18)}.revision-entry-button:hover,.revision-entry-button:focus{border-color:#5c46d6!important;background:#e7e3ff!important;color:#171234!important;box-shadow:0 8px 20px rgba(82,70,204,.24)}
</style>
