<script setup lang="ts">
import { computed } from 'vue'
import { Connection, DataAnalysis, Document, Operation, Setting } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import { useProjectWorkspaceStore } from '@/stores/project-workspace'

const route = useRoute()
const router = useRouter()
const workspaceStore = useProjectWorkspaceStore()
const projectId = computed(() => Number(route.params.projectId))
const project = computed(() => workspaceStore.currentProject)

const actions = [
  { name: 'project-requirements', title: '整理需求', description: '录入需求、发起 AI 评审并生成用例建议。', icon: Document, tone: 'blue' },
  { name: 'project-api-definitions', title: '导入 API', description: '导入 OpenAPI 契约并沉淀接口测试资产。', icon: Connection, tone: 'cyan' },
  { name: 'project-test-cases', title: '维护测试用例', description: '创建或审查 API、Web 与手工测试用例。', icon: Operation, tone: 'violet' },
  { name: 'project-runs', title: '发起运行', description: '选择环境和资产执行，并查看实时结果。', icon: DataAnalysis, tone: 'green' },
]

function open(name: string): void {
  void router.push({ name, params: { projectId: projectId.value }, query: { project_id: String(projectId.value) } })
}
</script>

<template>
  <div class="project-overview-page">
    <section class="overview-hero">
      <div>
        <span>项目工作区</span>
        <h1>{{ project?.name || '正在加载项目…' }}</h1>
        <p>{{ project?.description || '从需求、测试资产到运行报告，所有工作都固定在当前项目上下文中。' }}</p>
        <div class="project-meta"><code>{{ project?.code }}</code><span>项目 ID {{ projectId }}</span></div>
      </div>
      <div class="hero-orbit"><strong>AI</strong><span>测试闭环</span></div>
    </section>

    <section class="overview-section">
      <header><div><span>推荐流程</span><h2>从这里开始本项目工作</h2></div><p>进入工作区后无需在每个页面重复选择项目。</p></header>
      <div class="quick-action-grid">
        <button v-for="(item, index) in actions" :key="item.name" type="button" class="quick-action" @click="open(item.name)">
          <span :class="['quick-icon', item.tone]"><el-icon><component :is="item.icon" /></el-icon></span>
          <small>步骤 {{ index + 1 }}</small>
          <strong>{{ item.title }}</strong>
          <p>{{ item.description }}</p>
          <span class="action-link">进入页面 →</span>
        </button>
      </div>
    </section>

    <section class="overview-footer-card">
      <div><el-icon><Setting /></el-icon><span><strong>首次使用先完成运行配置</strong><small>配置环境、密钥和模型绑定后，后续测试流程可以直接复用。</small></span></div>
      <el-button @click="open('project-settings')">打开运行配置</el-button>
    </section>
  </div>
</template>

<style scoped>
.project-overview-page { max-width: 1480px; margin: 0 auto; }
.overview-hero { position: relative; display: flex; min-height: 210px; align-items: center; justify-content: space-between; overflow: hidden; padding: 38px 42px; border-radius: 22px; color: #fff; background: radial-gradient(circle at 82% 15%,rgba(78,218,205,.23),transparent 27%),linear-gradient(120deg,#152a55,#245493 60%,#147f82); box-shadow: 0 18px 44px rgba(30,67,122,.17); }
.overview-hero > div:first-child { position: relative; z-index: 1; max-width: 780px; }
.overview-hero > div > span:first-child { color: #74e1d4; font-size: 11px; font-weight: 800; letter-spacing: .16em; }
.overview-hero h1 { margin: 12px 0 10px; font-size: 32px; }
.overview-hero p { margin: 0; color: #c4d8ee; line-height: 1.7; }
.project-meta { display: flex; gap: 12px; margin-top: 22px; }
.project-meta code, .project-meta span { padding: 6px 10px; border: 1px solid rgba(255,255,255,.15); border-radius: 8px; color: #dceaff; background: rgba(5,24,52,.22); font-size: 11px; }
.hero-orbit { position: relative; z-index: 1; display: grid; width: 126px; height: 126px; flex: 0 0 auto; place-content: center; border: 1px solid rgba(255,255,255,.24); border-radius: 50%; text-align: center; box-shadow: inset 0 0 0 11px rgba(255,255,255,.045),0 0 0 34px rgba(255,255,255,.025); }
.hero-orbit strong { font-size: 30px; }
.hero-orbit span { margin-top: 4px; color: #82e4d8; font-size: 11px; }
.overview-section { margin-top: 22px; padding: 28px; border: 1px solid #e3e9f2; border-radius: 18px; background: #fff; box-shadow: 0 8px 24px rgba(35,54,91,.045); }
.overview-section > header { display: flex; align-items: flex-end; justify-content: space-between; }
.overview-section header span { color: #4078d2; font-size: 10px; font-weight: 800; letter-spacing: .15em; }
.overview-section h2 { margin: 6px 0 0; font-size: 21px; }
.overview-section header p { margin: 0; color: #8993a5; font-size: 13px; }
.quick-action-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 15px; margin-top: 24px; }
.quick-action { position: relative; min-height: 210px; padding: 20px; border: 1px solid #e5eaf2; border-radius: 15px; color: #25324a; background: #fbfcfe; text-align: left; cursor: pointer; transition: transform .16s ease,border-color .16s ease,box-shadow .16s ease; }
.quick-action:hover { border-color: #a8bfea; box-shadow: 0 12px 26px rgba(48,78,135,.09); transform: translateY(-2px); }
.quick-icon { display: grid; width: 42px; height: 42px; place-items: center; border-radius: 12px; color: #3974da; background: #eaf1ff; font-size: 20px; }
.quick-icon.cyan { color: #158f91; background: #e6f7f6; }.quick-icon.violet { color: #7255d6; background: #f0ecff; }.quick-icon.green { color: #198c68; background: #e8f7f0; }
.quick-action small { display: block; margin-top: 18px; color: #98a2b3; font-size: 10px; }
.quick-action strong { display: block; margin-top: 5px; font-size: 17px; }
.quick-action p { margin: 8px 0 22px; color: #7b879a; font-size: 12px; line-height: 1.65; }
.action-link { position: absolute; bottom: 18px; color: #376fce; font-size: 12px; font-weight: 700; }
.overview-footer-card { display: flex; align-items: center; justify-content: space-between; margin-top: 18px; padding: 18px 22px; border: 1px dashed #b8c9e4; border-radius: 15px; background: linear-gradient(90deg,#f4f8ff,#f1fbf9); }
.overview-footer-card > div { display: flex; align-items: center; gap: 13px; color: #4078d2; }
.overview-footer-card strong, .overview-footer-card small { display: block; }.overview-footer-card small { margin-top: 4px; color: #8190a6; }
@media (max-width: 1320px) { .quick-action-grid { grid-template-columns: repeat(2,1fr); } }
</style>
