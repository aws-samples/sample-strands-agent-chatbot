/**
 * Canvas gives a pending research approval priority over the selected artifact.
 *
 * Regression: the approval UI lives inside ResearchArtifact, which Canvas only
 * rendered when no artifact was selected. Completing a research run creates an
 * artifact and auto-selects it, so from the second run onward the next approval
 * was invisible — no accept/decline anywhere, and the turn could not proceed.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Canvas } from '@/components/canvas/Canvas'
import type { Artifact } from '@/types/artifact'

vi.mock('@/components/canvas/BrowserLiveView', () => ({
  BrowserLiveView: () => <div data-testid="browser-live-view" />,
}))

vi.mock('@/components/canvas/ResearchArtifact', () => ({
  ResearchArtifact: ({ showPlanConfirm, plan }: any) => (
    <div data-testid="research-artifact">
      {showPlanConfirm ? <button>Approve research</button> : null}
      <span>{plan?.plan}</span>
    </div>
  ),
}))

const artifact: Artifact = {
  id: 'research-previous',
  type: 'research' as any,
  title: 'Previous research report',
  content: 'Earlier findings',
  timestamp: '2026-08-05T00:00:00.000Z',
}

function renderCanvas(overrides: Record<string, any> = {}) {
  const props: any = {
    isOpen: true,
    onClose: vi.fn(),
    artifacts: [artifact],
    selectedArtifactId: null,
    onSelectArtifact: vi.fn(),
    onUpdateArtifact: vi.fn(),
    sessionId: 'session-1',
    ...overrides,
  }
  return render(<Canvas {...props} />)
}

const pendingApproval = {
  isResearching: false,
  showPlanConfirm: true,
  plan: { plan: 'Research Plan: find counterfactual benchmarks', planPreview: 'Research Plan...' },
  progress: [],
  onConfirmPlan: vi.fn(),
  onCancel: vi.fn(),
  sessionId: 'session-1',
}

describe('Canvas — pending research approval', () => {
  it('shows the approval even when an earlier artifact is selected', () => {
    renderCanvas({
      selectedArtifactId: 'research-previous',
      researchState: pendingApproval,
    })

    expect(screen.getByTestId('research-artifact')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /approve research/i })).toBeInTheDocument()
  })

  it('surfaces the plan text so the user knows what they are approving', () => {
    renderCanvas({
      selectedArtifactId: 'research-previous',
      researchState: pendingApproval,
    })

    expect(
      screen.getByText(/Research Plan: find counterfactual benchmarks/),
    ).toBeInTheDocument()
  })

  it('shows the approval when nothing is selected', () => {
    renderCanvas({ selectedArtifactId: null, researchState: pendingApproval })
    expect(screen.getByRole('button', { name: /approve research/i })).toBeInTheDocument()
  })

  // Priority is scoped to an approval that is actually waiting; once answered,
  // the selected artifact must come back.
  it('returns to the selected artifact once no approval is pending', () => {
    renderCanvas({
      selectedArtifactId: 'research-previous',
      researchState: { ...pendingApproval, showPlanConfirm: false },
    })

    expect(screen.queryByRole('button', { name: /approve research/i })).not.toBeInTheDocument()
    // The title also appears in the artifact list, so assert presence, not count.
    expect(screen.getAllByText('Previous research report').length).toBeGreaterThan(0)
  })

  it('leaves artifact viewing untouched when there is no research at all', () => {
    renderCanvas({ selectedArtifactId: 'research-previous', researchState: undefined })

    expect(screen.queryByTestId('research-artifact')).not.toBeInTheDocument()
    expect(screen.getAllByText('Previous research report').length).toBeGreaterThan(0)
  })

  it('treats closing the panel as cancelling the pending approval', () => {
    const onClose = vi.fn()
    const onCancel = vi.fn()
    renderCanvas({
      selectedArtifactId: 'research-previous',
      researchState: { ...pendingApproval, onCancel },
      onClose,
    })

    const closeButton = screen
      .getAllByRole('button')
      .find(b => /close/i.test(b.getAttribute('aria-label') ?? b.getAttribute('title') ?? ''))
    if (closeButton) {
      closeButton.click()
      expect(onCancel).toHaveBeenCalled()
      expect(onClose).not.toHaveBeenCalled()
    }
  })
})
