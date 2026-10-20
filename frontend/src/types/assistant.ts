import type { Task } from './business'

export type ActionType = 'create_task'

export type ProposalStatus =
  | 'pending_approval'
  | 'approved'
  | 'executed'
  | 'rejected'
  | 'failed'

/** A supporting business-record reference built server-side. */
export interface AssistantEvidence {
  kind: string
  reference: string
  excerpt: string
}

/** Read-only assistant answer from POST /assistant/ask. */
export interface AssistantResponse {
  answer: string
  evidence: AssistantEvidence[]
  evidence_sufficient: boolean
  machine_generated: boolean
  review_required: boolean
  model: string
}

/** Server-validated task payload inside a proposal. */
export interface ProposedTask {
  title: string
  description: string
  matter_id: number
  matter_title: string
  due_date: string | null
}

/** A freshly proposed (always pending) action from propose-action. */
export interface ActionProposal {
  proposal_id: string
  action_type: ActionType
  status: 'pending_approval'
  proposed: ProposedTask
  evidence: AssistantEvidence[]
  notice: string
  machine_generated: boolean
  review_required: boolean
  model: string
}

export interface ActionProposalResponse {
  proposal: ActionProposal | null
  reason: string | null
  machine_generated: boolean
  review_required: boolean
}

/** A persisted proposal as stored on the server. */
export interface ProposalRecord {
  proposal_id: string
  action_type: ActionType
  status: ProposalStatus
  proposed: ProposedTask
  evidence: AssistantEvidence[]
  model: string
  created_task_id: number | null
  execution_error: string | null
  created_at: string
  updated_at: string
  decided_at: string | null
  executed_at: string | null
  notice: string
}

/** Result of approving or rejecting a proposal. */
export interface ProposalDecision {
  proposal_id: string
  status: ProposalStatus
  created_task: Task | null
  execution_error: string | null
  notice: string
}
