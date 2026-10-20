import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssistantPage from './AssistantPage'

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const ANSWER_WITH_EVIDENCE = {
  answer: 'The Harbor engagement has a fee cap of $50,000.',
  evidence: [
    {
      kind: 'document',
      reference: 'Document #1 ("harbor-memo.txt")',
      excerpt: 'The Harbor engagement has a fee cap.',
    },
  ],
  evidence_sufficient: true,
  machine_generated: true,
  review_required: true,
  model: 'qwen3:4b',
}

const ANSWER_INSUFFICIENT = {
  answer: 'I cannot determine that from the available records.',
  evidence: [],
  evidence_sufficient: false,
  machine_generated: true,
  review_required: true,
  model: 'qwen3:4b',
}

const PROPOSAL = {
  proposal_id: 'abc123def456abc123def456abc12345',
  action_type: 'create_task',
  status: 'pending_approval',
  proposed: {
    title: 'Prepare Harbor disclosure schedule',
    description: 'Prepare the initial disclosure schedule.',
    matter_id: 1,
    matter_title: 'Harbor merger diligence',
    due_date: '2026-02-10',
  },
  evidence: [
    {
      kind: 'matter',
      reference: 'Matter #1 (Harbor merger diligence)',
      excerpt: 'title: Harbor merger diligence; status: open',
    },
  ],
  notice:
    'This is a machine-generated proposal only. No task has been created or changed.',
  machine_generated: true,
  review_required: true,
  model: 'qwen3:4b',
}

const PROPOSAL_RESPONSE = {
  proposal: PROPOSAL,
  reason: null,
  machine_generated: true,
  review_required: true,
}

function record(overrides: Record<string, unknown> = {}) {
  return {
    ...PROPOSAL,
    created_task_id: null,
    execution_error: null,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    decided_at: null,
    executed_at: null,
    ...overrides,
  }
}

async function submitQuestion(text: string) {
  fireEvent.change(screen.getByLabelText(/^Question$/i), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole('button', { name: /^Ask$/i }))
}

async function requestProposal(text: string) {
  fireEvent.change(screen.getByRole('textbox', { name: /^Request$/i }), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole('button', { name: /Request proposal/i }))
}

describe('AssistantPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the initial empty state with an example question', () => {
    render(<AssistantPage />)
    expect(screen.getByText(/No question asked yet/i)).toBeInTheDocument()
    expect(screen.getByText(/fee cap and scope/i)).toBeInTheDocument()
    expect(
      screen.getByText(/approvals\s+record the accountable user/i),
    ).toBeInTheDocument()
  })

  it('submits a question and renders the answer with evidence', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(ANSWER_WITH_EVIDENCE))

    render(<AssistantPage />)
    await submitQuestion('What is the fee cap?')

    expect(
      await screen.findByText(/fee cap of \$50,000/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/Document #1/)).toBeInTheDocument()
    expect(screen.getByText(/fee cap\./i)).toBeInTheDocument()
    expect(
      screen.getByText(/AI-generated — review required/i),
    ).toBeInTheDocument()
  })

  it('explains when evidence is insufficient', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(ANSWER_INSUFFICIENT))

    render(<AssistantPage />)
    await submitQuestion('Anything about Zebra?')

    expect(
      await screen.findByText(/not enough evidence in the workspace/i),
    ).toBeInTheDocument()
  })

  it('shows a clear error when the backend is unavailable', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('network down'))

    render(<AssistantPage />)
    await submitQuestion('Is the backend up?')

    expect(
      await screen.findByText(/Could not reach the backend/i),
    ).toBeInTheDocument()
  })

  it('renders a proposal and approves it, showing the confirmed task ID', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PROPOSAL_RESPONSE)) // propose
      .mockResolvedValueOnce(jsonResponse(record())) // GET proposal
      .mockResolvedValueOnce(
        jsonResponse({
          proposal_id: PROPOSAL.proposal_id,
          status: 'executed',
          created_task: {
            id: 7,
            matter_id: 1,
            title: PROPOSAL.proposed.title,
            status: 'todo',
            priority: 'medium',
            created_at: '2026-01-01T00:00:00+00:00',
          },
          execution_error: null,
          notice: 'Approved by a human. The task below was created exactly once.',
        }),
      ) // approve
      .mockResolvedValueOnce(
        jsonResponse(record({ status: 'executed', created_task_id: 7 })),
      ) // GET proposal after

    render(<AssistantPage />)
    await requestProposal('Add a task for Harbor')

    expect(
      await screen.findByText('Prepare Harbor disclosure schedule'),
    ).toBeInTheDocument()
    expect(screen.getByText(PROPOSAL.proposal_id)).toBeInTheDocument()
    expect(screen.getAllByText('create_task').length).toBeGreaterThan(0)
    expect(screen.getByText('2026-02-10')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^Approve$/i }))

    expect(await screen.findByText(/Task #7 created/i)).toBeInTheDocument()
    expect(await screen.findByText(/Created task ID: 7/i)).toBeInTheDocument()
  })

  it('renders a rejection success', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PROPOSAL_RESPONSE))
      .mockResolvedValueOnce(jsonResponse(record()))
      .mockResolvedValueOnce(
        jsonResponse({
          proposal_id: PROPOSAL.proposal_id,
          status: 'rejected',
          created_task: null,
          execution_error: null,
          notice: 'Rejected by a human. No task was created.',
        }),
      )
      .mockResolvedValueOnce(jsonResponse(record({ status: 'rejected' })))

    render(<AssistantPage />)
    await requestProposal('Add a task for Harbor')
    await screen.findByText('Prepare Harbor disclosure schedule')

    fireEvent.click(screen.getByRole('button', { name: /^Reject$/i }))

    expect(
      await screen.findByText(/Rejected by a human\. No task was created\./i),
    ).toBeInTheDocument()
    expect(screen.getByText(/already/i)).toBeInTheDocument()
  })

  it('prevents duplicate clicks while an approval is in flight', async () => {
    let resolveApprove: ((value: Response) => void) | undefined
    const pending = new Promise<Response>((resolve) => {
      resolveApprove = resolve
    })

    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PROPOSAL_RESPONSE))
      .mockResolvedValueOnce(jsonResponse(record()))
      .mockReturnValueOnce(pending) // approve hangs
      .mockResolvedValueOnce(
        jsonResponse(record({ status: 'executed', created_task_id: 9 })),
      )

    render(<AssistantPage />)
    await requestProposal('Add a task for Harbor')
    await screen.findByText('Prepare Harbor disclosure schedule')

    const approve = screen.getByRole('button', { name: /^Approve$/i })
    fireEvent.click(approve)
    fireEvent.click(approve)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Approving/i })).toBeDisabled(),
    )
    // Only the initial propose, GET, and a single approve were issued.
    const approveCalls = vi
      .mocked(fetch)
      .mock.calls.filter(([url]) =>
        String(url).endsWith(`/proposals/${PROPOSAL.proposal_id}/approve`),
      )
    expect(approveCalls).toHaveLength(1)

    resolveApprove!(
      jsonResponse({
        proposal_id: PROPOSAL.proposal_id,
        status: 'executed',
        created_task: {
          id: 9,
          matter_id: 1,
          title: 'x',
          status: 'todo',
          priority: 'medium',
          created_at: '2026-01-01T00:00:00+00:00',
        },
        execution_error: null,
        notice: 'Approved by a human.',
      }),
    )
    expect(await screen.findByText(/Task #9 created/i)).toBeInTheDocument()
  })

  it('handles a 409 already-decided response and reflects persisted state', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PROPOSAL_RESPONSE))
      .mockResolvedValueOnce(jsonResponse(record()))
      .mockResolvedValueOnce(
        jsonResponse({ detail: 'already decided' }, 409),
      ) // approve conflicts
      .mockResolvedValueOnce(
        jsonResponse(record({ status: 'executed', created_task_id: 3 })),
      ) // status refresh shows real outcome

    render(<AssistantPage />)
    await requestProposal('Add a task for Harbor')
    await screen.findByText('Prepare Harbor disclosure schedule')

    fireEvent.click(screen.getByRole('button', { name: /^Approve$/i }))

    expect(
      await screen.findByText(/already been decided/i),
    ).toBeInTheDocument()
    expect(await screen.findByText(/Created task ID: 3/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /^Approve$/i }),
    ).toBeDisabled()
  })

  it('refreshes persisted status after an ambiguous network failure', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PROPOSAL_RESPONSE))
      .mockResolvedValueOnce(jsonResponse(record()))
      .mockRejectedValueOnce(new TypeError('connection reset')) // approve ambiguous
      .mockResolvedValueOnce(
        jsonResponse(record({ status: 'executed', created_task_id: 5 })),
      ) // refresh reveals it actually executed

    render(<AssistantPage />)
    await requestProposal('Add a task for Harbor')
    await screen.findByText('Prepare Harbor disclosure schedule')

    fireEvent.click(screen.getByRole('button', { name: /^Approve$/i }))

    expect(
      await screen.findByText(/Could not reach the backend/i),
    ).toBeInTheDocument()
    // The refreshed persisted state is authoritative.
    expect(await screen.findByText(/Created task ID: 5/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /^Approve$/i }),
    ).toBeDisabled()
  })

  it('does not show a false success when the backend fails', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PROPOSAL_RESPONSE))
      .mockResolvedValueOnce(jsonResponse(record()))
      .mockResolvedValueOnce(
        jsonResponse({ detail: 'Execution failed' }, 422),
      ) // approve invalid
      .mockResolvedValueOnce(jsonResponse(record({ status: 'failed' })))

    render(<AssistantPage />)
    await requestProposal('Add a task for Harbor')
    await screen.findByText('Prepare Harbor disclosure schedule')

    fireEvent.click(screen.getByRole('button', { name: /^Approve$/i }))

    expect(
      await screen.findByText(/stored payload is invalid or was tampered/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Task #.* created/i)).not.toBeInTheDocument()
  })

  it('shows the no-proposal state when the assistant cannot act safely', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        proposal: null,
        reason: 'No suitable existing matter was found.',
        machine_generated: true,
        review_required: true,
      }),
    )

    render(<AssistantPage />)
    await requestProposal('Do something vague')

    expect(
      await screen.findByText(/No suitable existing matter was found/i),
    ).toBeInTheDocument()
  })
})
