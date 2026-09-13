import type { WebAssertion } from '@/types/web'
import { makeWebLocator } from '@/utils/web-actions'

export const WEB_ASSERTION_TYPES = [
  'ASSERT_VISIBLE',
  'ASSERT_EXISTS',
  'ASSERT_ENABLED',
  'ASSERT_HIDDEN',
  'ASSERT_CLICKABLE',
  'ASSERT_TEXT',
  'ASSERT_TEXT_EQUAL',
  'ASSERT_INPUT_VALUE',
  'ASSERT_URL',
  'ASSERT_TITLE',
  'ASSERT_ATTRIBUTE',
  'ASSERT_ELEMENT_COUNT',
  'ASSERT_DOWNLOAD_SUCCESS',
  'ASSERT_NETWORK_REQUEST',
  'ASSERT_SCREENSHOT_VISUAL_COMPARE',
  'ASSERT_AI_SEMANTIC',
] as const satisfies readonly WebAssertion['type'][]

type ExpectedAssertion = Extract<WebAssertion, { expected: string }>

export function makeWebAssertion(type: WebAssertion['type']): WebAssertion {
  const timeout_ms = 30000
  switch (type) {
    case 'ASSERT_VISIBLE':
    case 'ASSERT_EXISTS':
    case 'ASSERT_ENABLED':
    case 'ASSERT_HIDDEN':
    case 'ASSERT_CLICKABLE':
      return { type, locator: makeWebLocator(), timeout_ms }
    case 'ASSERT_TEXT':
    case 'ASSERT_TEXT_EQUAL':
    case 'ASSERT_INPUT_VALUE':
    case 'ASSERT_ELEMENT_COUNT':
      return { type, locator: makeWebLocator(), expected: '', timeout_ms }
    case 'ASSERT_ATTRIBUTE':
      return { type, locator: makeWebLocator(), key: '', expected: '', timeout_ms }
    case 'ASSERT_URL':
    case 'ASSERT_TITLE':
      return { type, expected: '', timeout_ms }
    case 'ASSERT_DOWNLOAD_SUCCESS':
    case 'ASSERT_NETWORK_REQUEST':
    case 'ASSERT_SCREENSHOT_VISUAL_COMPARE':
      return { type, expected: '', timeout_ms }
    case 'ASSERT_AI_SEMANTIC':
      return { type, prompt_id: null, criteria: '', confidence_threshold: 0.8, timeout_ms }
  }
}

export function hasWebAssertionExpected(assertion: WebAssertion): assertion is ExpectedAssertion {
  return 'expected' in assertion
}

export function webAssertionRequiresNonEmptyExpected(assertion: WebAssertion): boolean {
  return assertion.type === 'ASSERT_TEXT'
    || assertion.type === 'ASSERT_URL'
    || assertion.type === 'ASSERT_ELEMENT_COUNT'
    || assertion.type === 'ASSERT_NETWORK_REQUEST'
    || assertion.type === 'ASSERT_SCREENSHOT_VISUAL_COMPARE'
}

export function webAssertionTypeLabel(type: WebAssertion['type']): string {
  switch (type) {
    case 'ASSERT_VISIBLE': return 'ASSERT_VISIBLE · 元素存在且可见'
    case 'ASSERT_EXISTS': return 'ASSERT_EXISTS · 元素存在（可隐藏）'
    case 'ASSERT_ENABLED': return 'ASSERT_ENABLED · 元素处于启用状态'
    case 'ASSERT_HIDDEN': return 'ASSERT_HIDDEN · 元素隐藏或不存在'
    case 'ASSERT_CLICKABLE': return 'ASSERT_CLICKABLE · 元素可点击（仅试探，不执行点击）'
    case 'ASSERT_TEXT': return 'ASSERT_TEXT · 文本包含'
    case 'ASSERT_TEXT_EQUAL': return 'ASSERT_TEXT_EQUAL · 文本完整相等'
    case 'ASSERT_INPUT_VALUE': return 'ASSERT_INPUT_VALUE · 输入值完整相等'
    case 'ASSERT_URL': return 'ASSERT_URL · URL 匹配'
    case 'ASSERT_TITLE': return 'ASSERT_TITLE · 页面标题完整相等'
    case 'ASSERT_ATTRIBUTE': return 'ASSERT_ATTRIBUTE · 元素属性完整相等'
    case 'ASSERT_ELEMENT_COUNT': return 'ASSERT_ELEMENT_COUNT · 元素数量相等'
    case 'ASSERT_DOWNLOAD_SUCCESS': return 'ASSERT_DOWNLOAD_SUCCESS · 下载已成功'
    case 'ASSERT_NETWORK_REQUEST': return 'ASSERT_NETWORK_REQUEST · 已发出匹配请求'
    case 'ASSERT_SCREENSHOT_VISUAL_COMPARE': return 'ASSERT_SCREENSHOT_VISUAL_COMPARE · 截图与基线完全一致'
    case 'ASSERT_AI_SEMANTIC': return 'ASSERT_AI_SEMANTIC · AI 页面语义'
  }
}

export function webAssertionExpectedPlaceholder(assertion: ExpectedAssertion): string {
  switch (assertion.type) {
    case 'ASSERT_TEXT': return '包含的文本（必填）'
    case 'ASSERT_TEXT_EQUAL': return '完整相等文本（允许空字符串）'
    case 'ASSERT_INPUT_VALUE': return '完整相等输入值（允许空字符串）'
    case 'ASSERT_URL': return '匹配的 URL（必填）'
    case 'ASSERT_TITLE': return '完整相等页面标题（允许空字符串）'
    case 'ASSERT_ATTRIBUTE': return '属性完整相等值（允许空字符串）'
    case 'ASSERT_ELEMENT_COUNT': return '元素数量，例如 1'
    case 'ASSERT_DOWNLOAD_SUCCESS': return '期望文件名（留空则任意成功下载）'
    case 'ASSERT_NETWORK_REQUEST': return '请求 URL 包含的文本（必填）'
    case 'ASSERT_SCREENSHOT_VISUAL_COMPARE': return 'PNG 基线 Base64（请使用文件选择器）'
  }
}

function expectedSummary(value: string, emptyAllowed: boolean): string {
  if (value === '') return emptyAllowed ? '期望空字符串' : '待填写期望值'
  if (/^\s+$/.test(value)) return `期望 ${value.length} 个空白字符`
  return `期望「${value}」`
}

export function webAssertionSummary(assertion: WebAssertion): string {
  switch (assertion.type) {
    case 'ASSERT_VISIBLE': return '元素存在且可见'
    case 'ASSERT_EXISTS': return '元素存在（可隐藏）'
    case 'ASSERT_ENABLED': return '元素处于启用状态'
    case 'ASSERT_HIDDEN': return '元素隐藏或不存在'
    case 'ASSERT_CLICKABLE': return '元素可点击（仅试探，不执行点击）'
    case 'ASSERT_TEXT': return `文本包含 · ${expectedSummary(assertion.expected, false)}`
    case 'ASSERT_TEXT_EQUAL': return `文本完整相等 · ${expectedSummary(assertion.expected, true)}`
    case 'ASSERT_INPUT_VALUE': return `输入值完整相等 · ${expectedSummary(assertion.expected, true)}`
    case 'ASSERT_URL': return `URL 匹配 · ${expectedSummary(assertion.expected, false)}`
    case 'ASSERT_TITLE': return `页面标题完整相等 · ${expectedSummary(assertion.expected, true)}`
    case 'ASSERT_ATTRIBUTE': return `${assertion.key || '待填写属性'} 完整相等 · ${expectedSummary(assertion.expected, true)}`
    case 'ASSERT_ELEMENT_COUNT': return `元素数量 · ${assertion.expected || '待填写数量'}`
    case 'ASSERT_DOWNLOAD_SUCCESS': return `下载成功 · ${assertion.expected || '任意文件名'}`
    case 'ASSERT_NETWORK_REQUEST': return `请求 URL 包含 · ${expectedSummary(assertion.expected, false)}`
    case 'ASSERT_SCREENSHOT_VISUAL_COMPARE': return assertion.expected ? '截图与已选择 PNG 基线完全一致' : '待选择 PNG 基线'
    case 'ASSERT_AI_SEMANTIC': return `AI 页面语义 · ${assertion.criteria || '待填写判断标准'}`
  }
}

export function validWebAssertionExpected(assertion: WebAssertion): boolean {
  const requiresExpected = assertion.type === 'ASSERT_TEXT'
    || assertion.type === 'ASSERT_TEXT_EQUAL'
    || assertion.type === 'ASSERT_INPUT_VALUE'
    || assertion.type === 'ASSERT_URL'
    || assertion.type === 'ASSERT_TITLE'
    || assertion.type === 'ASSERT_ATTRIBUTE'
    || assertion.type === 'ASSERT_ELEMENT_COUNT'
    || assertion.type === 'ASSERT_DOWNLOAD_SUCCESS'
    || assertion.type === 'ASSERT_NETWORK_REQUEST'
    || assertion.type === 'ASSERT_SCREENSHOT_VISUAL_COMPARE'
  if (!requiresExpected) return !('expected' in assertion)
  if (!hasWebAssertionExpected(assertion)) return false
  const maxLength = assertion.type === 'ASSERT_SCREENSHOT_VISUAL_COMPARE'
    ? 1_400_000
    : assertion.type === 'ASSERT_URL' || assertion.type === 'ASSERT_NETWORK_REQUEST'
    ? 2048
    : assertion.type === 'ASSERT_DOWNLOAD_SUCCESS'
      ? 255
      : 10000
  if (typeof assertion.expected !== 'string' || assertion.expected.length > maxLength) return false
  if (assertion.type === 'ASSERT_ELEMENT_COUNT'
    && !/^(0|[1-9][0-9]{0,5})$/.test(assertion.expected)) return false
  if (assertion.type === 'ASSERT_SCREENSHOT_VISUAL_COMPARE'
    && !/^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(assertion.expected)) return false
  return !webAssertionRequiresNonEmptyExpected(assertion) || Boolean(assertion.expected.trim())
}
