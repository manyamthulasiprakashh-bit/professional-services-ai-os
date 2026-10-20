import { useCallback, useState } from 'react'
import {
  approveProposal,
  askQuestion,
  fetchProposal,
  proposeAction,
  rejectProposal,
} from '../api/assistant'
import { ApiError } from '../api/http'
import type {
  AssistantResponse,
  ProposalRecord,
  ProposalStatus,
} from '../types/assistant'

const EXAMPLE_QUESTION =
  'What is the fee cap and scope described in the Harbor engagement documents?'

const STATUS_LABELS: Record<ProposalStatus, string> = {
  pending_approval: 'Pending approval',
  approved: 'Approved',
  executed: 'Executed — task created',
  rejected: 'Rejected',
  failed: 'Execution failed',
}

type AnswerState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'loaded'; answer: AssistantResponse }
  | { kind: 'error'; message: string }

type ProposalState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'loaded'; proposal: ProposalRecord }
  | { kind: 'none'; reason: string }
  | { kind: 'error'; message: string }

function describeError(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/**
 * Map a decision failure to a message, distinguishing already-decided
 * conflicts from invalid/tampered payloads and unavailable services.
 */
function decisionFailureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 404:
        return 'This proposal no longer exists on the server.'
      case 409:
        return 'This proposal has already been decided. Showing the stored result below.'
      case 422:
        return 'The server refused to execute this proposal because its stored payload is invalid or was tampered with.'
      case 503:
        return 'The database is busy, so the decision was not applied. Try again shortly.'
      default:
        return error.message
    }
  }
  return 'Unexpected error while applying the decision.'
}

export default function AssistantPage() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<AnswerState>({ kind: 'idle' })

  const [request, setRequest] = useState('')
  const [proposal, setProposal] = useState<ProposalState>({ kind: 'idle' })

  const [decidingAction, setDecidingAction] = useState<
    'approve' | 'reject' | null
  >(null)
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null)
  const [decisionIsError, setDecisionIsError] = useState(false)

  const onAsk = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      const trimmed = question.trim()
      if (!trimmed) return
      setAnswer({ kind: 'loading' })
      try {
        setAnswer({ kind: 'loaded', answer: await askQuestion(trimmed) })
      } catch (error) {
        setAnswer({
          kind: 'error',
          message: describeError(
            error,
            'Unexpected error while asking the assistant.',
          ),
        })
      }
    },
    [question],
  )

  const onPropose = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      const trimmed = request.trim()
      if (!trimmed) return
      setProposal({ kind: 'loading' })
      setDecisionMessage(null)
      try {
        const response = await proposeAction(trimmed)
        if (!response.proposal) {
          setProposal({
            kind: 'none',
            reason: response.reason ?? 'No safe, actionable proposal was found.',
          })
          return
        }
        // Read back the persisted record so the UI reflects server state.
        const persisted = await fetchProposal(response.proposal.proposal_id)
        setProposal({ kind: 'loaded', proposal: persisted })
      } catch (error) {
        setProposal({
          kind: 'error',
          message: describeError(
            error,
            'Unexpected error while requesting a proposal.',
          ),
        })
      }
    },
    [request],
  )

  const refreshProposal = useCallback(async (proposalId: string) => {
    try {
      setProposal({ kind: 'loaded', proposal: await fetchProposal(proposalId) })
    } catch (error) {
      setProposal({
        kind: 'error',
        message: describeError(
          error,
          'Could not retrieve the proposal status from the server.',
        ),
      })
    }
  }, [])

  const onDecide = useCallback(
    async (action: 'approve' | 'reject') => {
      if (proposal.kind !== 'loaded') return
      const proposalId = proposal.proposal.proposal_id
      setDecidingAction(action)
      setDecisionMessage(null)
      setDecisionIsError(false)
      try {
        const decision =
          action === 'approve'
            ? await approveProposal(proposalId)
            : await rejectProposal(proposalId)
        // Success path: reflect the confirmed persisted state.
        setProposal({
          kind: 'loaded',
          proposal: await fetchProposal(proposalId),
        })
        if (decision.status === 'executed' && decision.created_task) {
          setDecisionMessage(
            `Task #${decision.created_task.id} created. ${decision.notice}`,
          )
        } else {
          setDecisionMessage(decision.notice)
        }
      } catch (error) {
        // Any failure is ambiguous about persisted state: always re-read the
        // server before offering another attempt. Never show success here.
        setDecisionIsError(true)
        setDecisionMessage(decisionFailureMessage(error))
        await refreshProposal(proposalId)
      } finally {
        setDecidingAction(null)
      }
    },
    [proposal, refreshProposal],
  )

  const deciding = decidingAction !== null
  const currentStatus =
    proposal.kind === 'loaded' ? proposal.proposal.status : null
  const canDecide = currentStatus === 'pending_approval' && !deciding

  return (
    <section className="page" aria-labelledby="assistant-heading">
      <h2 id="assistant-heading">AI Assistant</h2>

      <p className="assistant__demo-note" role="note">
        Requests are authenticated with your signed-in session, and approvals
        record the accountable user. The assistant never executes an action on
        its own — nothing changes until you approve it.
      </p>

      {/* --- Question & answer --- */}
      <form
        className="card form"
        onSubmit={onAsk}
        aria-label="Ask the assistant"
      >
        <h3>Ask a question</h3>
        <p className="assistant__hint">
          Answers use the records stored in this workspace. Every answer is
          AI-generated and must be reviewed by a human against the cited
          evidence.
        </p>
        <label className="form__field">
          <span>Question</span>
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={EXAMPLE_QUESTION}
            maxLength={2000}
          />
        </label>
        <button className="button" type="submit" disabled={!question.trim()}>
          Ask
        </button>
      </form>

      {answer.kind === 'idle' && (
        <p className="page__message" role="status">
          No question asked yet. Try something like: “{EXAMPLE_QUESTION}”
        </p>
      )}

      {answer.kind === 'loading' && (
        <p className="page__message" role="status">
          <span className="status__spinner" aria-hidden="true" /> Asking the
          local model…
        </p>
      )}

      {answer.kind === 'error' && (
        <div className="page__error" role="alert">
          <strong>Assistant unavailable</strong>
          <p>{answer.message}</p>
        </div>
      )}

      {answer.kind === 'loaded' && (
        <section className="card answer" aria-labelledby="answer-heading">
          <h3 id="answer-heading">
            Answer
            <span className="pill pill--machine" style={{ marginLeft: '0.5rem' }}>
              AI-generated — review required
            </span>
          </h3>

          <p className="answer__text">{answer.answer.answer}</p>

          <div className="answer__evidence">
            <h4>Evidence from your records</h4>
            {!answer.answer.evidence_sufficient ? (
              <p className="answer__insufficient" role="status">
                There is not enough evidence in the workspace to support this
                answer. Treat it as an unverified model opinion.
              </p>
            ) : answer.answer.evidence.length === 0 ? (
              <p className="answer__insufficient" role="status">
                No supporting records were cited for this answer.
              </p>
            ) : (
              <ul className="evidence-list">
                {answer.answer.evidence.map((item, index) => (
                  <li key={index} className="evidence-list__item">
                    <span className="evidence-list__reference">
                      {item.reference}
                    </span>
                    <span className="evidence-list__kind">{item.kind}</span>
                    <p className="evidence-list__excerpt">{item.excerpt}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <p className="answer__model">Model: {answer.answer.model}</p>
        </section>
      )}

      {/* --- Proposed action workflow --- */}
      <form
        className="card form"
        onSubmit={onPropose}
        aria-label="Request a task proposal"
        style={{ marginTop: '2rem' }}
      >
        <h3>Propose a task</h3>
        <p className="assistant__hint">
          The assistant proposes a single <code>create_task</code> action against
          an existing matter. Nothing is created until you approve it.
        </p>
        <label className="form__field">
          <span>Request</span>
          <input
            type="text"
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            placeholder="e.g. Add a task to prepare the Harbor disclosure schedule"
            maxLength={2000}
          />
        </label>
        <button className="button" type="submit" disabled={!request.trim()}>
          Request proposal
        </button>
      </form>

      {proposal.kind === 'loading' && (
        <p className="page__message" role="status">
          <span className="status__spinner" aria-hidden="true" /> Drafting a
          proposal…
        </p>
      )}

      {proposal.kind === 'error' && (
        <div className="page__error" role="alert">
          <strong>Could not create a proposal</strong>
          <p>{proposal.message}</p>
        </div>
      )}

      {proposal.kind === 'none' && (
        <p className="page__message" role="status">
          No proposal was produced. {proposal.reason}
        </p>
      )}

      {proposal.kind === 'loaded' && (
        <section className="card proposal" aria-labelledby="proposal-heading">
          <h3 id="proposal-heading">
            Proposed action
            <span
              className={`pill pill--proposal-${proposal.proposal.status}`}
              style={{ marginLeft: '0.5rem' }}
            >
              {STATUS_LABELS[proposal.proposal.status]}
            </span>
          </h3>

          <dl className="proposal__meta">
            <div>
              <dt>Proposal ID</dt>
              <dd className="proposal__id">{proposal.proposal.proposal_id}</dd>
            </div>
            <div>
              <dt>Action type</dt>
              <dd>{proposal.proposal.action_type}</dd>
            </div>
          </dl>

          <div className="proposal__payload">
            <h4>{proposal.proposal.proposed.title}</h4>
            <p>{proposal.proposal.proposed.description}</p>
            <dl className="proposal__meta">
              <div>
                <dt>Matter</dt>
                <dd>
                  {proposal.proposal.proposed.matter_title} (#
                  {proposal.proposal.proposed.matter_id})
                </dd>
              </div>
              <div>
                <dt>Due date</dt>
                <dd>{proposal.proposal.proposed.due_date ?? 'None'}</dd>
              </div>
            </dl>
          </div>

          <div className="proposal__evidence">
            <h4>Evidence</h4>
            {proposal.proposal.evidence.length === 0 ? (
              <p className="answer__insufficient">No evidence cited.</p>
            ) : (
              <ul className="evidence-list">
                {proposal.proposal.evidence.map((item, index) => (
                  <li key={index} className="evidence-list__item">
                    <span className="evidence-list__reference">
                      {item.reference}
                    </span>
                    <span className="evidence-list__kind">{item.kind}</span>
                    <p className="evidence-list__excerpt">{item.excerpt}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {proposal.proposal.created_task_id !== null && (
            <p className="proposal__created" role="status">
              Created task ID: {proposal.proposal.created_task_id}
            </p>
          )}

          {proposal.proposal.execution_error && (
            <p className="form__error" role="alert">
              Execution error: {proposal.proposal.execution_error}
            </p>
          )}

          <div className="proposal__actions">
            <button
              type="button"
              className="button"
              disabled={!canDecide}
              onClick={() => onDecide('approve')}
            >
              {decidingAction === 'approve' ? 'Approving…' : 'Approve'}
            </button>
            <button
              type="button"
              className="button button--secondary"
              disabled={!canDecide}
              onClick={() => onDecide('reject')}
            >
              {decidingAction === 'reject' ? 'Rejecting…' : 'Reject'}
            </button>
          </div>

          {currentStatus && currentStatus !== 'pending_approval' && (
            <p className="proposal__decided" role="status">
              This proposal is already <strong>{STATUS_LABELS[currentStatus]}</strong>
              . Approval is no longer available.
            </p>
          )}

          {decisionMessage && (
            <p
              className={
                decisionIsError ? 'form__error' : 'form__success'
              }
              role={decisionIsError ? 'alert' : 'status'}
            >
              {decisionMessage}
            </p>
          )}

          <p className="proposal__notice">{proposal.proposal.notice}</p>
        </section>
      )}
    </section>
  )
}
