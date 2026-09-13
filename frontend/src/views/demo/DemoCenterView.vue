<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check, Connection, Cpu, Document, MagicStick, Refresh, RefreshLeft, Right, TopRight } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'

import { bootstrapDemo, getDemoResetPreview, getDemoStatus, resetDemo } from '@/api/demo'
import { getApiErrorMessage } from '@/api/http'
import { getConnections, getModels } from '@/api/model-center'
import type { DemoBootstrapStatus, DemoProbe, DemoResetPreview } from '@/types/demo'
import type { ModelConfiguration, ModelProviderConnection } from '@/types/model-center'

const router = useRouter()
const loading = ref(false)
const bootstrapping = ref(false)
const status = ref<DemoBootstrapStatus | null>(null)
const probe = ref<DemoProbe | null>(null)
const error = ref<string | null>(null)
const showConfiguration = ref(false)
const resetVisible = ref(false)
const resetLoading = ref(false)
const resetting = ref(false)
const resetPreview = ref<DemoResetPreview | null>(null)
const resetConfirmation = ref('')
const availableModels = ref<ModelConfiguration[]>([])
const modelConnections = ref<ModelProviderConnection[]>([])
const selectedModelId = ref<number>()
const selectedModel = computed(() => availableModels.value.find((item) => item.id === selectedModelId.value) ?? null)
const selectedConnection = computed(() => modelConnections.value.find((item) => item.id === selectedModel.value?.connection_id) ?? null)

const projectQuery = computed(() => status.value?.project_id
  ? { project_id: String(status.value.project_id) }
  : {})
const requirementQuery = computed(() => ({
  ...projectQuery.value,
  ...(status.value?.requirement_id ? { requirement_id: String(status.value.requirement_id) } : {}),
}))
const assetChecks = computed(() => {
  const assets = status.value?.assets
  return [
    { label: '演示项目与公网 Demo 环境', ready: Boolean(assets?.project && assets.environment) },
    { label: '模型中心已验证模型', ready: Boolean(assets?.model) },
    { label: `AI 任务绑定（${assets?.bindings ?? 0}/${assets?.binding_total ?? 8}）`, ready: assets?.bindings === assets?.binding_total },
    { label: `完整需求树（${assets?.requirement_count ?? 0} 个章节）`, ready: Boolean(assets?.requirement) },
    { label: `OpenAPI（${assets?.api_definition_count ?? 0} 个操作）`, ready: Boolean(assets?.openapi) },
    { label: '公开合成运行凭据', ready: Boolean(assets?.runtime_secret) },
  ]
})
const resetCountItems = computed(() => {
  const counts = resetPreview.value?.delete_counts
  return [
    { label: 'AI 与审核记录', value: counts?.ai_records ?? 0 },
    { label: '测试用例', value: counts?.test_cases ?? 0 },
    { label: '测试场景', value: counts?.scenarios ?? 0 },
    { label: '运行记录', value: counts?.runs ?? 0 },
    { label: '证据文件', value: counts?.evidence_files ?? 0 },
    { label: 'Web 资产与录制', value: counts?.web_assets ?? 0 },
    { label: '缺陷草稿', value: counts?.defects ?? 0 },
    { label: '运行配置与数据源', value: counts?.data_sources ?? 0 },
    { label: '项目 Secret', value: counts?.secrets ?? 0 },
  ]
})

async function loadStatus(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    status.value = await getDemoStatus()
    showConfiguration.value = !status.value.ready
  } catch (loadError) {
    error.value = getApiErrorMessage(loadError, 'AI Demo 状态加载失败。')
  } finally {
    loading.value = false
  }
}

async function loadAvailableModels(): Promise<void> {
  try {
    const [models, connections] = await Promise.all([getModels(), getConnections()])
    availableModels.value = models.filter((item) => item.model_type === 'TEXT')
    modelConnections.value = connections
    if (status.value?.model_id && availableModels.value.some((item) => item.id === status.value?.model_id)) {
      selectedModelId.value = status.value.model_id
    } else if (!availableModels.value.some((item) => item.id === selectedModelId.value)) {
      selectedModelId.value = availableModels.value[0]?.id
    }
  } catch (loadError) {
    error.value = getApiErrorMessage(loadError, '模型中心配置加载失败。')
  }
}

function modelOptionLabel(model: ModelConfiguration): string {
  const connection = modelConnections.value.find((item) => item.id === model.connection_id)
  return `${model.name} · ${model.model_name}${connection ? ` · ${connection.name}` : ''}`
}

function openModelCenter(): void {
  void router.push({ name: 'model-center' })
}

function openDemoTarget(): void {
  if (!status.value?.demo_url) {
    ElMessage.warning('尚未配置 Demo 公网地址')
    return
  }
  window.open(status.value.demo_url, '_blank', 'noopener,noreferrer')
}

function openRunnerCenter(): void {
  void router.push({ name: 'runners' })
}

async function initialize(): Promise<void> {
  if (!selectedModelId.value) {
    ElMessage.warning('请先到模型中心配置并启用一个文本模型')
    return
  }
  bootstrapping.value = true
  error.value = null
  probe.value = null
  try {
    const result = await bootstrapDemo({ model_id: selectedModelId.value })
    status.value = result
    probe.value = result.probe
    showConfiguration.value = !result.ready
    if (result.ready) ElMessage.success('演示素材已就绪，现在可以亲自生成 AI 结果')
    else ElMessage.warning(result.probe.summary)
  } catch (bootstrapError) {
    error.value = getApiErrorMessage(bootstrapError, '初始化失败，请检查模型配置后重试。')
  } finally {
    bootstrapping.value = false
  }
}

async function openReset(): Promise<void> {
  resetVisible.value = true
  resetLoading.value = true
  resetConfirmation.value = ''
  resetPreview.value = null
  try {
    resetPreview.value = await getDemoResetPreview()
  } catch (resetError) {
    error.value = getApiErrorMessage(resetError, '重置影响加载失败。')
    resetVisible.value = false
  } finally {
    resetLoading.value = false
  }
}

async function confirmReset(): Promise<void> {
  if (resetConfirmation.value.trim() !== 'AI_DEMO') {
    ElMessage.warning('请输入 AI_DEMO 确认重置')
    return
  }
  if (!resetPreview.value?.can_reset) {
    ElMessage.warning('当前仍有运行或后台任务，暂时不能重置')
    return
  }
  resetting.value = true
  error.value = null
  try {
    const result = await resetDemo(resetConfirmation.value.trim())
    status.value = result.status
    probe.value = null
    showConfiguration.value = !result.status.ready
    resetVisible.value = false
    resetConfirmation.value = ''
    ElMessage.success(result.message)
  } catch (resetError) {
    error.value = getApiErrorMessage(resetError, '主演示数据重置失败。')
  } finally {
    resetting.value = false
  }
}

function openRequirement(): void {
  if (!status.value?.project_id) return
  void router.push({
    name: 'project-requirements',
    params: { projectId: status.value.project_id },
    query: requirementQuery.value,
  })
}

function openProjectPage(name: string, extraQuery: Record<string, string> = {}): void {
  if (!status.value?.project_id) return
  void router.push({
    name,
    params: { projectId: status.value.project_id },
    query: { ...projectQuery.value, ...extraQuery },
  })
}

function openRequirementStage(aiTab: 'reviews' | 'ai-cases'): void {
  if (!status.value?.project_id) return
  void router.push({
    name: 'project-requirements',
    params: { projectId: status.value.project_id },
    query: { ...requirementQuery.value, ai_tab: aiTab },
  })
}

onMounted(async () => {
  await loadStatus()
  await loadAvailableModels()
})
</script>

<template>
  <div class="demo-center" v-loading="loading">
    <section class="demo-hero">
      <div>
        <span class="demo-kicker">在线 AI 主演示</span>
        <h1>从在线商城 Demo 到 AI 测试闭环，现场完整体验</h1>
        <p>上线环境自动准备公网 Demo、演示项目、完整需求树和 OpenAPI。先注册 Runner，再依次体验 AI 需求评审、AI 测试设计、API 场景编排、Web AI 测试设计与 Playwright MCP 自动探索。</p>
        <div class="hero-actions">
          <el-button v-if="status?.ready" type="primary" size="large" @click="openRequirement">
            开始 AI 主演示 <el-icon><Right /></el-icon>
          </el-button>
          <el-button v-else type="primary" size="large" @click="showConfiguration = true">
            选择模型并初始化
          </el-button>
          <el-button v-if="status?.demo_url" type="success" plain size="large" @click="openDemoTarget">
            访问在线 Demo <el-icon><TopRight /></el-icon>
          </el-button>
          <el-button :loading="loading" size="large" @click="loadStatus"><el-icon><Refresh /></el-icon>刷新状态</el-button>
          <el-button v-if="status?.project_id" type="danger" plain size="large" @click="openReset"><el-icon><RefreshLeft /></el-icon>重置主演示数据</el-button>
        </div>
      </div>
      <div class="ai-orbit">
        <div class="orbit-core"><el-icon><MagicStick /></el-icon><strong>AI</strong><span>{{ status?.ready ? '已就绪' : '待配置' }}</span></div>
        <i class="orbit-dot dot-one" /><i class="orbit-dot dot-two" /><i class="orbit-dot dot-three" />
      </div>
    </section>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <el-alert v-if="probe && !probe.success" :title="`模型验证未通过：${probe.summary}`" type="warning" show-icon :closable="false" />

    <section v-if="showConfiguration || !status?.ready" class="setup-card">
      <header>
        <div class="step-number">01</div>
        <div><span>统一复用平台配置</span><h2>选择模型中心中的可用模型</h2></div>
        <el-tag type="success" effect="plain">提交时即时验证连接</el-tag>
      </header>
      <el-alert v-if="!availableModels.length" title="模型中心暂无可用文本模型" description="请先到模型中心配置接入渠道和模型，启用后返回本页选择。服务地址、模型标识和 API Key 只在模型中心维护。" type="warning" show-icon :closable="false" />
      <div v-else class="model-selector">
        <el-select v-model="selectedModelId" filterable size="large" placeholder="选择已启用模型">
          <el-option v-for="model in availableModels" :key="model.id" :label="modelOptionLabel(model)" :value="model.id" />
        </el-select>
        <div v-if="selectedModel && selectedConnection" class="selected-model-summary">
          <div><span>模型配置</span><strong>{{ selectedModel.name }}</strong></div>
          <div><span>模型标识</span><strong>{{ selectedModel.model_name }}</strong></div>
          <div><span>接入渠道</span><strong>{{ selectedConnection.name }}</strong></div>
          <div><span>凭据状态</span><strong>{{ selectedConnection.has_api_key ? `已配置 ${selectedConnection.api_key_masked ?? '••••••••'}` : '免鉴权 / 未配置' }}</strong></div>
        </div>
      </div>
      <div class="model-actions">
        <el-button size="large" @click="openModelCenter">前往模型中心</el-button>
        <el-button type="primary" size="large" :loading="bootstrapping" :disabled="!selectedModelId" @click="initialize">
          {{ bootstrapping ? '正在验证模型并准备演示素材…' : '验证所选模型并准备演示素材' }}
        </el-button>
      </div>
    </section>

    <section class="readiness-grid">
      <article class="readiness-card">
        <header><span>自动初始化清单</span><el-tag :type="status?.ready ? 'success' : 'info'">{{ status?.ready ? '全部完成' : '等待配置' }}</el-tag></header>
        <ul>
          <li v-for="item in assetChecks" :key="item.label" :class="{ ready: item.ready }">
            <span class="check-dot"><el-icon v-if="item.ready"><Check /></el-icon></span>{{ item.label }}
          </li>
        </ul>
        <button v-if="status?.ready" class="reconfigure" @click="showConfiguration = !showConfiguration">更换主演示使用的模型</button>
      </article>

      <article class="readiness-card mainline-card">
        <header><span>现场体验路线</span><strong>按顺序点击进入对应工作区</strong></header>
        <ol>
          <li><button @click="openRunnerCenter"><i><Cpu /></i><div><em>01</em><strong>Runner 注册</strong><span>创建注册 Token，完成 Windows Runner 注册并确认心跳在线</span></div></button></li>
          <li><button :disabled="!status?.ready" @click="openRequirementStage('reviews')"><i><Document /></i><div><em>02</em><strong>AI 需求评审</strong><span>识别需求歧义、缺失规则、异常场景与可测试性问题</span></div></button></li>
          <li><button :disabled="!status?.ready" @click="openRequirementStage('ai-cases')"><i><MagicStick /></i><div><em>03</em><strong>AI 测试设计</strong><span>提取检查点、关联 API，并生成可人工审核的测试用例</span></div></button></li>
          <li><button :disabled="!status?.ready" @click="openProjectPage('project-scenarios')"><i><Connection /></i><div><em>04</em><strong>API 场景编排</strong><span>组合前置、请求、提取、断言与清理节点形成业务闭环</span></div></button></li>
          <li><button :disabled="!status?.ready" @click="openProjectPage('project-web-assets', { tab: 'design' })"><i><MagicStick /></i><div><em>05</em><strong>Web AI 测试设计</strong><span>根据需求与 API 生成 Web 候选用例和抽象操作方案</span></div></button></li>
          <li><button :disabled="!status?.ready" @click="openProjectPage('project-web-assets', { tab: 'design' })"><i><Connection /></i><div><em>06</em><strong>Playwright MCP 自动探索</strong><span>由 Runner 获取真实页面事实并生成待确认的校准草稿</span></div></button></li>
          <li><button :disabled="!status?.ready" @click="openProjectPage('project-runs')"><i><Cpu /></i><div><em>07</em><strong>API / Web 运行执行</strong><span>选择在线 Runner 投递执行，实时查看步骤、状态与 Evidence</span></div></button></li>
          <li><button :disabled="!status?.ready" @click="openProjectPage('project-reports')"><i><Document /></i><div><em>08</em><strong>测试报告与 AI 分析</strong><span>查看报告、失败分析、缺陷草稿与完整审计记录</span></div></button></li>
        </ol>
      </article>
    </section>

    <section class="asset-boundary">
      <article>
        <span class="boundary-label">平台自动准备</span>
        <h3>只提供可靠的演示输入</h3>
        <p>在线商城 Demo、公网环境、覆盖认证和订单等业务的 20 个需求章节、14 个 API 操作及模型任务绑定；系统默认提示词由平台统一提供。</p>
      </article>
      <article class="experience-side">
        <span class="boundary-label">由你现场体验</span>
        <h3>所有 AI 成果从零产生</h3>
        <p>Runner 注册、AI 需求评审、AI 测试设计、API 场景编排、Web AI 测试设计、Playwright MCP 自动探索、运行报告与失败分析。</p>
      </article>
    </section>

    <section v-if="status?.ready" class="launch-panel">
      <div><span>下一步</span><h2>从“智能商城完整演示需求”开始</h2><p>先选择一个业务章节做 AI 评审，再确认检查点与平台推荐的 API 闭环，最后创建后台生成任务。结果是否进入正式资产由你决定。</p></div>
      <div class="launch-actions">
        <el-button type="success" plain size="large" @click="openDemoTarget">访问在线 Demo <el-icon><TopRight /></el-icon></el-button>
        <el-button type="primary" size="large" @click="openRequirement">打开需求工作台</el-button>
        <el-button size="large" @click="openProjectPage('project-api-definitions')">查看 API 定义</el-button>
        <el-button size="large" @click="openProjectPage('project-ai-infrastructure')">查看 AI 记录</el-button>
      </div>
    </section>

    <el-dialog v-model="resetVisible" title="重置 AI 主演示数据" width="660px" :close-on-click-modal="!resetting" :close-on-press-escape="!resetting" :show-close="!resetting">
      <div v-loading="resetLoading" class="demo-reset-dialog">
        <el-alert title="只重置 AI_DEMO 项目，不影响其他项目" description="操作会永久删除现场生成的成果，并恢复系统内置需求、OpenAPI、任务绑定和公开合成凭据。平台级系统默认提示词、模型配置及已加密 API Key 不受影响。" type="error" show-icon :closable="false" />
        <template v-if="resetPreview">
          <div class="reset-count-grid">
            <div v-for="item in resetCountItems" :key="item.label"><span>{{ item.label }}</span><strong>{{ item.value }}</strong></div>
          </div>
          <div class="reset-preserved"><strong>保留内容</strong><span v-for="item in resetPreview.preserved" :key="item">{{ item }}</span></div>
          <el-alert v-if="resetPreview.blockers.length" title="当前不能重置" :description="resetPreview.blockers.join('；')" type="warning" show-icon :closable="false" />
          <el-form-item label="输入 AI_DEMO 确认不可撤销操作" class="reset-confirmation">
            <el-input v-model="resetConfirmation" :disabled="resetting || !resetPreview.can_reset" autocomplete="off" placeholder="AI_DEMO" />
          </el-form-item>
        </template>
      </div>
      <template #footer>
        <el-button :disabled="resetting" @click="resetVisible = false">取消</el-button>
        <el-button type="danger" :loading="resetting" :disabled="!resetPreview?.can_reset || resetConfirmation.trim() !== 'AI_DEMO'" @click="confirmReset">{{ resetting ? '正在重置并恢复素材…' : '确认重置' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.demo-center { max-width: 1480px; margin: 0 auto; }
.demo-hero { position: relative; display: grid; min-height: 286px; grid-template-columns: minmax(0, 1fr) 280px; align-items: center; overflow: hidden; padding: 42px 52px; border-radius: 24px; color: white; background: radial-gradient(circle at 82% 30%, rgba(86, 227, 207, .22), transparent 25%), linear-gradient(125deg, #101e3c 0%, #193f74 56%, #126d72 100%); box-shadow: 0 20px 50px rgba(23, 57, 105, .2); }
.demo-hero::after { position: absolute; width: 520px; height: 520px; right: -180px; top: -310px; border: 1px solid rgba(255,255,255,.13); border-radius: 50%; box-shadow: 0 0 0 72px rgba(255,255,255,.025), 0 0 0 144px rgba(255,255,255,.018); content: ''; }
.demo-kicker { color: #71e5d7; font-size: 11px; font-weight: 800; letter-spacing: .2em; }
.demo-hero h1 { max-width: 760px; margin: 15px 0 13px; font-size: 34px; line-height: 1.22; }
.demo-hero p { max-width: 830px; margin: 0; color: #bed2e8; line-height: 1.8; }
.hero-actions { position: relative; z-index: 2; display: flex; gap: 12px; margin-top: 26px; }
.ai-orbit { position: relative; z-index: 1; display: grid; width: 190px; height: 190px; place-items: center; justify-self: center; border: 1px solid rgba(126, 228, 216, .32); border-radius: 50%; box-shadow: 0 0 0 25px rgba(255,255,255,.025), inset 0 0 45px rgba(77, 214, 200, .08); }
.orbit-core { display: grid; width: 112px; height: 112px; place-content: center; border-radius: 30px; text-align: center; background: linear-gradient(145deg, rgba(91,144,245,.86), rgba(34,181,169,.88)); box-shadow: 0 16px 38px rgba(0,0,0,.25); }
.orbit-core .el-icon { margin: 0 auto 3px; font-size: 25px; }.orbit-core strong { font-size: 25px; }.orbit-core span { margin-top: 2px; color: #c9fff8; font-size: 9px; letter-spacing: .18em; }
.orbit-dot { position: absolute; width: 10px; height: 10px; border-radius: 50%; background: #78eadc; box-shadow: 0 0 16px #78eadc; }.dot-one { left: 16px; top: 50px; }.dot-two { right: 18px; bottom: 42px; }.dot-three { left: 72px; bottom: -5px; }
.demo-center > .el-alert { margin-top: 18px; }
.setup-card { margin-top: 22px; padding: 28px 32px; border: 1px solid #dfe7f2; border-radius: 18px; background: #fff; box-shadow: 0 10px 28px rgba(35,54,91,.06); }
.setup-card > header { display: flex; align-items: center; gap: 15px; }.setup-card header > .el-tag { margin-left: auto; }.step-number { display: grid; width: 45px; height: 45px; place-items: center; border-radius: 13px; color: #356fc7; font-weight: 800; background: #edf4ff; }.setup-card header span { color: #7f8ca0; font-size: 11px; }.setup-card h2 { margin: 5px 0 0; font-size: 20px; }
.setup-card > .el-alert { margin-top: 22px; }
.model-selector { margin-top: 22px; }
.model-selector > .el-select { width: 100%; }
.selected-model-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
.selected-model-summary > div { min-width: 0; padding: 13px 15px; border: 1px solid #e6ebf3; border-radius: 11px; background: #f8faff; }
.selected-model-summary span, .selected-model-summary strong { display: block; }
.selected-model-summary span { color: #8a96a8; font-size: 11px; }
.selected-model-summary strong { margin-top: 5px; overflow-wrap: anywhere; color: #34445f; font-size: 13px; }
.model-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.readiness-grid { display: grid; grid-template-columns: .8fr 1.2fr; gap: 20px; margin-top: 22px; }.readiness-card { padding: 26px; border: 1px solid #e2e8f1; border-radius: 18px; background: #fff; box-shadow: 0 8px 24px rgba(35,54,91,.045); }.readiness-card > header { display: flex; align-items: center; justify-content: space-between; }.readiness-card header > span { color: #477ccd; font-size: 11px; font-weight: 800; letter-spacing: .13em; }.readiness-card ul, .readiness-card ol { margin: 22px 0 0; padding: 0; list-style: none; }.readiness-card ul { display: grid; gap: 13px; }.readiness-card ul li { display: flex; align-items: center; gap: 10px; color: #7c8798; }.readiness-card ul li.ready { color: #263a58; }.check-dot { display: grid; width: 23px; height: 23px; place-items: center; border: 1px solid #dbe3ef; border-radius: 8px; color: white; background: #f3f6fa; }.ready .check-dot { border-color: #25aa96; background: #25aa96; }.reconfigure { margin-top: 21px; padding: 0; border: 0; color: #477ccd; background: transparent; cursor: pointer; }
.mainline-card ol { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }.mainline-card li { min-height: 158px; border: 1px solid #e7ecf4; border-radius: 14px; background: linear-gradient(180deg, #fbfdff, #f6f9fd); }.mainline-card li button { width: 100%; height: 100%; padding: 17px; border: 0; color: inherit; text-align: left; background: transparent; cursor: pointer; }.mainline-card li button:hover:not(:disabled) { border-radius: 14px; background: #eef6ff; }.mainline-card li button:disabled { cursor: not-allowed; opacity: .55; }.mainline-card li i { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 10px; color: #3d79d3; background: #eaf2ff; }.mainline-card li strong, .mainline-card li span { display: block; }.mainline-card li strong { margin-top: 14px; font-size: 13px; }.mainline-card li span { margin-top: 7px; color: #8490a3; font-size: 11px; line-height: 1.6; }
.mainline-card li em { display: block; margin-top: 12px; color: #7f91aa; font-size: 11px; font-style: normal; font-weight: 700; letter-spacing: .12em; }.mainline-card li em + strong { margin-top: 4px; }
.asset-boundary { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 22px; }.asset-boundary article { padding: 24px 26px; border: 1px solid #d9e5f4; border-radius: 18px; background: linear-gradient(135deg, #f7faff, #fff); }.asset-boundary .experience-side { border-color: #bee2dc; background: linear-gradient(135deg, #f3fbfa, #fff); }.boundary-label { color: #4678c6; font-size: 12px; font-weight: 800; letter-spacing: .12em; }.experience-side .boundary-label { color: #168d7d; }.asset-boundary h3 { margin: 8px 0; font-size: 18px; }.asset-boundary p { margin: 0; color: #718095; font-size: 14px; line-height: 1.75; }
.launch-panel { display: flex; align-items: center; justify-content: space-between; gap: 30px; margin-top: 22px; padding: 26px 30px; border: 1px solid #bcd8d5; border-radius: 18px; background: linear-gradient(100deg, #f3f8ff, #edfafa); }.launch-panel span { color: #168d7d; font-size: 10px; font-weight: 800; letter-spacing: .18em; }.launch-panel h2 { margin: 5px 0 7px; }.launch-panel p { max-width: 760px; margin: 0; color: #718095; }.launch-actions { display: flex; flex: 0 0 auto; gap: 10px; }
.demo-reset-dialog { min-height: 180px; }.reset-count-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 18px 0; }.reset-count-grid > div { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border: 1px solid #e4e9f1; border-radius: 10px; background: #f8fafc; }.reset-count-grid span { color: #6f7d91; font-size: 12px; }.reset-count-grid strong { color: #253854; font-size: 18px; }.reset-preserved { display: grid; gap: 5px; margin-bottom: 16px; padding: 14px; border-radius: 10px; color: #52657f; background: #f1f8f6; }.reset-preserved strong { color: #237c6f; }.reset-preserved span { font-size: 12px; }.reset-preserved span::before { margin-right: 6px; content: '✓'; color: #20a68f; }.reset-confirmation { margin: 18px 0 0; }
@media (max-width: 1320px) { .readiness-grid { grid-template-columns: 1fr; } }
@media (max-width: 900px) { .demo-hero { grid-template-columns: 1fr; padding: 32px; }.ai-orbit { display: none; }.selected-model-summary, .mainline-card ol, .asset-boundary { grid-template-columns: 1fr 1fr; }.launch-panel { align-items: flex-start; flex-direction: column; }.launch-actions { flex-wrap: wrap; } }
@media (max-width: 620px) { .demo-hero h1 { font-size: 28px; }.hero-actions, .launch-actions, .model-actions { align-items: stretch; flex-direction: column; width: 100%; }.selected-model-summary, .mainline-card ol, .asset-boundary, .reset-count-grid { grid-template-columns: 1fr; }.setup-card > header { align-items: flex-start; flex-wrap: wrap; }.setup-card header > .el-tag { margin-left: 0; } }
</style>
