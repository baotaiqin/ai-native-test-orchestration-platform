import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from './http'
import type {
  WebHealingProposalCreateRequest,
  WebHealingProposalDecisionRequest,
  WebHealingProposalListResponse,
  WebHealingProposalRejectRequest,
  WebHealingProposalResponse,
  WebHealingProposalValidateRequest,
} from '@/types/web-healing'

export const WEB_HEALING_GENERATION_TIMEOUT_MS = AI_GENERATION_REQUEST_TIMEOUT_MS

function proposalPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}/web-healing-proposals`
}

export async function generateWebHealingProposal(
  runId: string,
  payload: WebHealingProposalCreateRequest,
): Promise<WebHealingProposalResponse> {
  const response = await http.post<WebHealingProposalResponse>(proposalPath(runId), payload, {
    timeout: AI_GENERATION_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

export async function getWebHealingProposals(
  runId: string,
  caseRunId: number,
): Promise<WebHealingProposalListResponse> {
  const response = await http.get<WebHealingProposalListResponse>(proposalPath(runId), {
    params: { case_run_id: caseRunId },
  })
  return response.data
}

export async function acceptWebHealingProposal(
  runId: string,
  proposalId: number,
  payload: WebHealingProposalDecisionRequest,
): Promise<WebHealingProposalResponse> {
  const response = await http.post<WebHealingProposalResponse>(
    `${proposalPath(runId)}/${proposalId}/accept`, payload,
  )
  return response.data
}

export async function validateWebHealingProposal(
  runId: string,
  proposalId: number,
  payload: WebHealingProposalValidateRequest,
): Promise<WebHealingProposalResponse> {
  const response = await http.post<WebHealingProposalResponse>(
    `${proposalPath(runId)}/${proposalId}/validate`, payload,
  )
  return response.data
}

export async function rejectWebHealingProposal(
  runId: string,
  proposalId: number,
  payload: WebHealingProposalRejectRequest,
): Promise<WebHealingProposalResponse> {
  const response = await http.post<WebHealingProposalResponse>(
    `${proposalPath(runId)}/${proposalId}/reject`, payload,
  )
  return response.data
}
