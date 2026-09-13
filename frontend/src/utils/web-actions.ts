import type { WebAction, WebAssertion, WebLocator } from '@/types/web'

export const WEB_ACTION_TYPES = [
  'GOTO',
  'RELOAD',
  'BACK',
  'FORWARD',
  'NEW_TAB',
  'SWITCH_TAB',
  'CLOSE_TAB',
  'FILL',
  'CLICK',
  'DOUBLE_CLICK',
  'RIGHT_CLICK',
  'CLEAR',
  'HOVER',
  'DRAG_DROP',
  'UPLOAD',
  'DOWNLOAD',
  'SELECT',
  'CHECK',
  'UNCHECK',
  'RADIO',
  'PRESS',
  'ENTER',
  'TAB',
  'WAIT_ELEMENT',
  'WAIT_URL',
  'WAIT_NETWORK_IDLE',
  'WAIT_TIME',
  'WAIT_TEXT',
  'COOKIE',
  'LOCAL_STORAGE',
  'SESSION_STORAGE',
  'JS_EVAL',
] as const satisfies readonly WebAction['type'][]

export const WEB_TAB_ALIAS_HELP = '别名以字母开头，仅可包含字母、数字、下划线或连字符，最长 64 个字符；初始页别名为 main。'

const WEB_TAB_ALIAS = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/
const WEB_URL_TEMPLATE = /\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}/g

export const WEB_PAGE_ACTION_TYPES = [
  'RELOAD',
  'BACK',
  'FORWARD',
  'WAIT_NETWORK_IDLE',
] as const satisfies readonly WebAction['type'][]

export const WEB_LOCATOR_ONLY_ACTION_TYPES = [
  'CLICK',
  'DOUBLE_CLICK',
  'RIGHT_CLICK',
  'CLEAR',
  'HOVER',
  'CHECK',
  'UNCHECK',
  'RADIO',
  'ENTER',
  'TAB',
  'WAIT_ELEMENT',
  'WAIT_TEXT',
  'DRAG_DROP',
  'UPLOAD',
  'DOWNLOAD',
] as const satisfies readonly WebAction['type'][]

type WebLocatorItem = Extract<WebAction | WebAssertion, { locator: WebLocator }>

export function makeWebLocator(): WebLocator {
  return { strategy: 'css', value: '', element_version_id: null }
}

export function makeWebAction(type: WebAction['type']): WebAction {
  const base = { timeout_ms: 30000, failure_policy: 'STOP' as const }
  switch (type) {
    case 'GOTO':
    case 'WAIT_URL':
      return { ...base, type, url: '' }
    case 'NEW_TAB':
      return { ...base, type, url: '', value: '' }
    case 'SWITCH_TAB':
    case 'CLOSE_TAB':
      return { ...base, type, value: '' }
    case 'FILL':
    case 'SELECT':
    case 'DRAG_DROP':
    case 'WAIT_TEXT':
      return { ...base, type, locator: makeWebLocator(), value: '' }
    case 'UPLOAD':
      return { ...base, type, locator: makeWebLocator(), key: '', value: '' }
    case 'DOWNLOAD':
      return { ...base, type, locator: makeWebLocator(), value: '' }
    case 'WAIT_TIME':
      return { ...base, type, value: '1000' }
    case 'COOKIE':
    case 'LOCAL_STORAGE':
    case 'SESSION_STORAGE':
      return { ...base, type, key: '', value: '' }
    case 'JS_EVAL':
      return { ...base, type, value: '' }
    case 'PRESS':
      return { ...base, type, locator: makeWebLocator(), key: '' }
    case 'CLICK':
    case 'DOUBLE_CLICK':
    case 'RIGHT_CLICK':
    case 'CLEAR':
    case 'HOVER':
    case 'CHECK':
    case 'UNCHECK':
    case 'RADIO':
    case 'ENTER':
    case 'TAB':
    case 'WAIT_ELEMENT':
      return { ...base, type, locator: makeWebLocator() }
    case 'RELOAD':
    case 'BACK':
    case 'FORWARD':
    case 'WAIT_NETWORK_IDLE':
      return { ...base, type }
  }
}

export function hasWebLocator(item: WebAction | WebAssertion): item is WebLocatorItem {
  return 'locator' in item
}

export function validWebTabAlias(value: string, allowMain = true): boolean {
  return WEB_TAB_ALIAS.test(value) && (allowMain || value !== 'main')
}

export function validWebUrlOrTemplate(value: string): boolean {
  const normalized = value.trim()
  if (!normalized || normalized.includes('\r') || normalized.includes('\n')) return false
  const withoutTemplates = normalized.replace(WEB_URL_TEMPLATE, '')
  if (withoutTemplates.includes('{{') || withoutTemplates.includes('}}')) return false
  if (normalized.startsWith('{{')) return /^\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}/.test(normalized)
  try {
    const parsed = new URL(normalized)
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:')
      && Boolean(parsed.host)
      && !parsed.username
      && !parsed.password
  } catch {
    return false
  }
}

export function webActionSummary(action: WebAction): string {
  switch (action.type) {
    case 'GOTO': return `GOTO · ${action.url || '待填写 URL'}`
    case 'RELOAD': return 'RELOAD · 重载当前页面'
    case 'BACK': return 'BACK · 浏览器后退'
    case 'FORWARD': return 'FORWARD · 浏览器前进'
    case 'NEW_TAB': return `NEW_TAB · ${action.value || '待填写别名'} · ${action.url || '待填写 URL'}`
    case 'SWITCH_TAB': return `SWITCH_TAB · 切换到 ${action.value || '待填写别名'}`
    case 'CLOSE_TAB': return `CLOSE_TAB · 关闭 ${action.value || '待填写别名'}`
    case 'FILL': return `FILL · ${action.value || '待填写值'}`
    case 'CLICK': return 'CLICK · 点击目标元素'
    case 'DOUBLE_CLICK': return 'DOUBLE_CLICK · 双击目标元素'
    case 'RIGHT_CLICK': return 'RIGHT_CLICK · 右击目标元素'
    case 'CLEAR': return 'CLEAR · 清空目标输入'
    case 'HOVER': return 'HOVER · 悬停目标元素'
    case 'DRAG_DROP': return `DRAG_DROP · 拖到 ${action.value || '待填写目标 CSS Locator'}`
    case 'UPLOAD': return `UPLOAD · ${action.key || '待选择文件'}`
    case 'DOWNLOAD': return `DOWNLOAD · ${action.value || '接受任意安全文件名'}`
    case 'SELECT': return `SELECT · ${action.value || '待填写值'}`
    case 'CHECK': return 'CHECK · 选中目标复选框'
    case 'UNCHECK': return 'UNCHECK · 取消目标复选框'
    case 'RADIO': return 'RADIO · 选择目标单选框'
    case 'PRESS': return `PRESS · ${action.key || '待填写按键'}`
    case 'ENTER': return 'ENTER · 在目标元素按 Enter'
    case 'TAB': return 'TAB · 在目标元素按 Tab'
    case 'WAIT_ELEMENT': return 'WAIT_ELEMENT · 等待目标元素'
    case 'WAIT_URL': return `WAIT_URL · ${action.url || '待填写 URL'}`
    case 'WAIT_NETWORK_IDLE': return 'WAIT_NETWORK_IDLE · 等待网络空闲'
    case 'WAIT_TIME': return `WAIT_TIME · ${action.value || '0'} ms`
    case 'WAIT_TEXT': return `WAIT_TEXT · 等待包含「${action.value || '待填写文本'}」`
    case 'COOKIE': return `COOKIE · 设置 ${action.key || '待填写名称'}`
    case 'LOCAL_STORAGE': return `LOCAL_STORAGE · 设置 ${action.key || '待填写键'}`
    case 'SESSION_STORAGE': return `SESSION_STORAGE · 设置 ${action.key || '待填写键'}`
    case 'JS_EVAL': return 'JS_EVAL · 执行页面脚本'
  }
}
