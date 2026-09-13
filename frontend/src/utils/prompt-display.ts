import type { PromptDefinition } from '@/types/prompt-center'

export function promptVersionLabel(prompt: PromptDefinition): string {
  if (prompt.scope === 'SYSTEM') return prompt.is_builtin ? '系统默认' : '公共模板'
  return prompt.current_version?.version_no
    ? `项目 V${prompt.current_version.version_no}`
    : '无可用项目版本'
}

export function promptOptionLabel(prompt: PromptDefinition, includeTask = false): string {
  return [prompt.name, includeTask ? prompt.task_type : null, promptVersionLabel(prompt)]
    .filter(Boolean)
    .join(' · ')
}
