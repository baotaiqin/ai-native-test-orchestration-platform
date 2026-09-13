import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'

export const RECORD_PAGE_SIZES = [10, 20, 50, 100] as const

export function useClientPagination<T>(
  source: MaybeRefOrGetter<readonly T[]>,
  initialPageSize = 10,
) {
  const page = ref(1)
  const pageSize = ref(initialPageSize)
  const total = computed(() => toValue(source).length)
  const items = computed(() => {
    const start = (page.value - 1) * pageSize.value
    return toValue(source).slice(start, start + pageSize.value)
  })

  function changePage(value: number): void {
    page.value = value
  }

  function changePageSize(value: number): void {
    pageSize.value = value
    page.value = 1
  }

  watch([total, pageSize], ([count, size]) => {
    page.value = Math.min(page.value, Math.max(1, Math.ceil(count / size)))
  })

  return { items, total, page, pageSize, changePage, changePageSize }
}
