import { http } from '@/api/http'
import type {
  RequirementAssetType,
  RequirementImpactPage,
  RequirementLink,
  RequirementLinkCreatePayload,
  RequirementLinkPage,
  RequirementTracePage,
} from '@/types/requirement-link'

export async function getRequirementLinks(
  requirementId: number,
  page = 1,
  pageSize = 10,
  includeRemoved = true,
): Promise<RequirementLinkPage> {
  const response = await http.get<RequirementLinkPage>(`/requirements/${requirementId}/links`, {
    params: { page, page_size: pageSize, include_removed: includeRemoved },
  })
  return response.data
}

export async function createRequirementLink(
  requirementId: number,
  payload: RequirementLinkCreatePayload,
): Promise<RequirementLink> {
  const response = await http.post<RequirementLink>(`/requirements/${requirementId}/links`, payload)
  return response.data
}

export async function removeRequirementLink(
  requirementId: number,
  linkId: number,
): Promise<RequirementLink> {
  const response = await http.delete<RequirementLink>(
    `/requirements/${requirementId}/links/${linkId}`,
  )
  return response.data
}

export async function getRequirementImpact(
  requirementId: number,
  fromVersionId: number,
  toVersionId: number,
  page = 1,
  pageSize = 10,
): Promise<RequirementImpactPage> {
  const response = await http.get<RequirementImpactPage>(`/requirements/${requirementId}/impact`, {
    params: {
      from_version_id: fromVersionId,
      to_version_id: toVersionId,
      page,
      page_size: pageSize,
    },
  })
  return response.data
}

export async function getAssetRequirementLinks(
  assetType: RequirementAssetType,
  assetId: number,
  page = 1,
  pageSize = 10,
  includeRemoved = true,
): Promise<RequirementLinkPage> {
  const prefix = assetType === 'TEST_CASE' ? '/test-cases' : '/web-cases'
  const response = await http.get<RequirementLinkPage>(`${prefix}/${assetId}/requirements`, {
    params: { page, page_size: pageSize, include_removed: includeRemoved },
  })
  return response.data
}

export async function getRequirementTraceability(
  requirementId: number,
  page = 1,
  pageSize = 10,
): Promise<RequirementTracePage> {
  const response = await http.get<RequirementTracePage>(
    `/requirements/${requirementId}/traceability`,
    { params: { page, page_size: pageSize } },
  )
  return response.data
}
