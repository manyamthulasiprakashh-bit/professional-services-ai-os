import { apiGet, apiPost } from './http'
import type {
  ActionProposalResponse,
  AssistantResponse,
  ProposalDecision,
  ProposalRecord,
} from '../types/assistant'

/** Ask the read-only assistant a question. */
export function askQuestion(
  question: string,
  signal?: AbortSignal,
): Promise<AssistantResponse> {
  return apiPost<AssistantResponse>('/assistant/ask', { question }, signal)
}

/** Request a proposed task action (never executes it). */
export function proposeAction(
  request: string,
  signal?: AbortSignal,
): Promise<ActionProposalResponse> {
  return apiPost<ActionProposalResponse>(
    '/assistant/propose-action',
    { request },
    signal,
  )
}

/** Fetch the persisted state of a proposal. */
export function fetchProposal(
  proposalId: string,
  signal?: AbortSignal,
): Promise<ProposalRecord> {
  return apiGet<ProposalRecord>(`/assistant/proposals/${proposalId}`, signal)
}

/** Approve a persisted proposal. The server uses only the stored payload. */
export function approveProposal(
  proposalId: string,
  signal?: AbortSignal,
): Promise<ProposalDecision> {
  return apiPost<ProposalDecision>(
    `/assistant/proposals/${proposalId}/approve`,
    {},
    signal,
  )
}

/** Reject a pending proposal. No task is created. */
export function rejectProposal(
  proposalId: string,
  signal?: AbortSignal,
): Promise<ProposalDecision> {
  return apiPost<ProposalDecision>(
    `/assistant/proposals/${proposalId}/reject`,
    {},
    signal,
  )
}
