import { http } from '@/api/http'
import type {
  RequirementReview, RequirementReviewPage, RequirementReviewResult,
} from '@/types/requirement-review'

export async function getRequirementReviews(
  requirementId: number,
): Promise<RequirementReview[]> {
  const response = await http.get<{ items: RequirementReview[] }>(
    `/requirements/${requirementId}/ai-reviews`,
  )
  return response.data.items
}

export async function getRequirementReview(reviewId: number): Promise<RequirementReview> {
  const response = await http.get<RequirementReview>(`/requirements/ai-reviews/${reviewId}`)
  return response.data
}

export async function createRequirementRevisionPlan(
  reviewId: number,
): Promise<RequirementReview> {
  const response = await http.post<RequirementReview>(
    `/requirements/ai-reviews/${reviewId}/revision-plan`,
  )
  return response.data
}

export async function getProjectRequirementReviews(
  projectId: number, page = 1, pageSize = 10,
): Promise<RequirementReviewPage> {
  const response = await http.get<RequirementReviewPage>(
    `/requirements/projects/${projectId}/ai-reviews`,
    { params: { page, page_size: pageSize } },
  )
  return response.data
}

export async function generateRequirementReview(
  requirementId: number,
  payload: {
    prompt_id: number
    include_parent: boolean
    include_siblings: boolean
    additional_instructions?: string
  },
): Promise<RequirementReview> {
  const response = await http.post<RequirementReview>(
    `/requirements/${requirementId}/ai-reviews`, payload,
  )
  return response.data
}

export async function deleteRequirementReview(reviewId: number): Promise<void> {
  await http.delete(`/requirements/ai-reviews/${reviewId}`)
}

export async function editRequirementReview(
  reviewId: number,
  humanResult: RequirementReviewResult,
  decisionNote?: string,
): Promise<RequirementReview> {
  const response = await http.patch<RequirementReview>(
    `/requirements/ai-reviews/${reviewId}`,
    { human_result: humanResult, decision_note: decisionNote },
  )
  return response.data
}

export async function decideRequirementReview(
  reviewId: number, action: 'ACCEPT' | 'REJECT', decisionNote?: string,
): Promise<RequirementReview> {
  const response = await http.post<RequirementReview>(
    `/requirements/ai-reviews/${reviewId}/decision`,
    { action, decision_note: decisionNote },
  )
  return response.data
}
